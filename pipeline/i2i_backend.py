"""
i2i_backend.py — V5 backend abstraction for image-to-image generation.

Each backend implements:
    edit(image_paths: list[Path], prompt: str) -> bytes
    name: str                     # for logs + cost log
    cost: float                   # per-call USD estimate

Currently 3 backends are implemented:
    - fal_nb2       : fal-ai/nano-banana-2/edit       ~$0.03/call   (existing)
    - fal_nb_pro    : fal-ai/nano-banana-pro/edit     ~$0.06/call   (V5 Pro option)
    - google_flash  : Vertex AI gemini-2.5-flash-image ~$0.04/call (V5 direct)

Pure Google "Pro" requires AI Studio API key (gemini-2.5-pro-image-preview) —
not yet wired. Plug it in by adding a GoogleProBackend class with the same
interface and registering it in BACKENDS.
"""

from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Protocol


class I2IBackend(Protocol):
    name: str
    cost: float

    def edit(self, image_paths: list[Path | str], prompt: str) -> bytes:
        """Run I2I from one or more source images + a text prompt. Returns raw PNG bytes."""


# ---------------------------------------------------------------------------
# Fal backends (NB-2 + NB-Pro) — wrap the existing fal_helper functions
# ---------------------------------------------------------------------------

class FalBackend:
    """Fal.ai backend. Used by both NB-2 (cheap) and NB-Pro (high quality)."""

    # Resolved lazily so the module can be imported without fal_client installed
    _model_id: str

    def __init__(self, model_id: str, name: str, cost: float):
        self._model_id = model_id
        self.name = name
        self.cost = cost

    def edit(self, image_paths, prompt):
        """Accepts local Path/str (uploaded automatically) OR pre-uploaded
        Fal URL (used directly). Single value or list — both supported.
        """
        import urllib.request
        from fal_helper import upload_file, nano_banana_edit, init_fal

        if not os.environ.get("FAL_KEY"):
            for candidate in (
                Path.cwd() / ".env",
                Path(__file__).resolve().parent / ".env",
                Path(__file__).resolve().parent.parent / ".env",
                Path(__file__).resolve().parent.parent.parent / ".env",
            ):
                if candidate.exists():
                    init_fal(str(candidate))
                    break

        # Normalise input → list, then resolve each item to a Fal URL
        items = image_paths if isinstance(image_paths, list) else [image_paths]
        urls = []
        for item in items:
            s = str(item)
            if s.startswith("http://") or s.startswith("https://"):
                urls.append(s)
            else:
                urls.append(upload_file(s))
        url_arg = urls[0] if len(urls) == 1 else urls

        out_url = nano_banana_edit(
            image_url=url_arg,
            prompt=prompt,
            model=self._model_id,
            aspect_ratio="1:1",
            thinking_level="high",
        )
        with urllib.request.urlopen(out_url) as r:
            return r.read()


# ---------------------------------------------------------------------------
# Google Vertex AI backend (Flash only — Pro not exposed on Vertex catalog)
# ---------------------------------------------------------------------------

class GoogleVertexBackend:
    """Google Vertex AI backend. Service-account auth via
    GOOGLE_APPLICATION_CREDENTIALS or explicit creds path."""

    _client = None  # cached genai.Client across calls

    def __init__(self, model_id: str, name: str, cost: float,
                 project: str, location: str = "us-central1",
                 service_account_path: str | None = None,
                 image_size: str | None = None,
                 aspect_ratio: str = "1:1"):
        """image_size: '1K' | '2K' (Nano Banana Pro / Gemini 3 image only).
        Leave None for default model resolution."""
        self._model_id = model_id
        self.name = name
        self.cost = cost
        self._project = project
        self._location = location
        self._sa_path = service_account_path
        self._image_size = image_size
        self._aspect_ratio = aspect_ratio

    def _get_client(self):
        if self._client is not None:
            return self._client
        # Ensure ADC points at the service account JSON
        if self._sa_path and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self._sa_path
        from google import genai
        self._client = genai.Client(
            vertexai=True,
            project=self._project,
            location=self._location,
        )
        return self._client

    def edit(self, image_paths, prompt):
        """Accepts local Path/str OR Fal URL (downloaded transparently).
        Retries automatically on 429 RESOURCE_EXHAUSTED (per-minute quota)
        with exponential backoff."""
        import time as _time
        import urllib.request
        from google.genai import types

        client = self._get_client()
        items = image_paths if isinstance(image_paths, list) else [image_paths]
        parts = []
        for item in items:
            s = str(item)
            if s.startswith("http://") or s.startswith("https://"):
                with urllib.request.urlopen(s) as r:
                    data = r.read()
            else:
                with open(s, "rb") as f:
                    data = f.read()
            parts.append(types.Part.from_bytes(data=data, mime_type="image/png"))
        parts.append(prompt)

        # Optional config for 2K Nano Banana Pro / Gemini 3 image models.
        config = None
        if self._image_size or self._aspect_ratio:
            try:
                image_cfg = types.ImageConfig(
                    image_size=self._image_size,
                    aspect_ratio=self._aspect_ratio,
                )
                config = types.GenerateContentConfig(
                    response_modalities=[types.Modality.IMAGE],
                    image_config=image_cfg,
                )
            except (AttributeError, TypeError):
                # SDK too old, or ImageConfig signature changed — fall back.
                config = None

        # Retry-with-backoff on 429 (Vertex per-minute quota bursts)
        max_attempts = 5
        backoff = 8.0  # seconds — Vertex per-minute quota resets in ~60s
        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                kwargs = {"model": self._model_id, "contents": parts}
                if config is not None:
                    kwargs["config"] = config
                r = client.models.generate_content(**kwargs)
                cands = r.candidates or []
                for cand in cands:
                    content = getattr(cand, "content", None)
                    for part in (getattr(content, "parts", None) or []) if content else []:
                        inline = getattr(part, "inline_data", None)
                        if inline and inline.data:
                            return inline.data
                finish = (cands[0].finish_reason
                          if cands and getattr(cands[0], "finish_reason", None)
                          else "UNKNOWN")
                feedback = getattr(r, "prompt_feedback", None)
                text_bits: list[str] = []
                for cand in cands:
                    content = getattr(cand, "content", None)
                    for part in (getattr(content, "parts", None) or []) if content else []:
                        txt = getattr(part, "text", None)
                        if txt:
                            text_bits.append(txt)
                detail = (
                    f"finish_reason={finish}"
                    + (f"  prompt_feedback={feedback}" if feedback else "")
                    + (f"  text='{' '.join(text_bits)[:300]}'" if text_bits else "")
                )
                raise RuntimeError(
                    f"Google Vertex {self._model_id} returned no image — {detail}"
                )
            except Exception as e:
                msg = str(e)
                last_err = e
                is_429 = "429" in msg or "RESOURCE_EXHAUSTED" in msg
                is_5xx = any(c in msg for c in ("500", "502", "503", "504", "UNAVAILABLE"))
                if (is_429 or is_5xx) and attempt < max_attempts:
                    wait = backoff * attempt   # 8s, 16s, 24s, 32s
                    print(f"  [RETRY {attempt}/{max_attempts}] {type(e).__name__}: "
                          f"{msg[:120]}... sleeping {wait:.0f}s")
                    _time.sleep(wait)
                    continue
                raise
        raise last_err  # unreachable, but appeases the linter


# ---------------------------------------------------------------------------
# Google AI Studio backend (api-key auth — different from Vertex SA)
# ---------------------------------------------------------------------------

class GoogleAIStudioBackend:
    """Uses the Gemini API directly with an API key (Google AI Studio).
    Access to `gemini-3-pro-image-preview` (Nano Banana Pro / 2K) is gated on
    the key, not on the Vertex project — so this works when Vertex doesn't."""

    _client = None

    def __init__(self, model_id: str, name: str, cost: float,
                 api_key: str,
                 image_size: str | None = None,
                 aspect_ratio: str = "1:1"):
        self._model_id = model_id
        self.name = name
        self.cost = cost
        self._api_key = api_key
        self._image_size = image_size
        self._aspect_ratio = aspect_ratio

    def _get_client(self):
        if self._client is not None:
            return self._client
        from google import genai
        self._client = genai.Client(api_key=self._api_key)
        return self._client

    def edit(self, image_paths, prompt):
        import time as _time
        import urllib.request
        from google.genai import types

        client = self._get_client()
        items = image_paths if isinstance(image_paths, list) else [image_paths]
        parts = []
        for item in items:
            s = str(item)
            if s.startswith("http://") or s.startswith("https://"):
                with urllib.request.urlopen(s) as r:
                    data = r.read()
            else:
                with open(s, "rb") as f:
                    data = f.read()
            parts.append(types.Part.from_bytes(data=data, mime_type="image/png"))
        parts.append(prompt)

        config = None
        if self._image_size or self._aspect_ratio:
            try:
                image_cfg = types.ImageConfig(
                    image_size=self._image_size,
                    aspect_ratio=self._aspect_ratio,
                )
                config = types.GenerateContentConfig(
                    response_modalities=[types.Modality.IMAGE],
                    image_config=image_cfg,
                )
            except (AttributeError, TypeError):
                config = None

        max_attempts = 5
        backoff = 8.0
        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                kwargs = {"model": self._model_id, "contents": parts}
                if config is not None:
                    kwargs["config"] = config
                r = client.models.generate_content(**kwargs)
                # Iterate defensively — any of these can be None when the
                # model refuses (safety, recitation, etc.).
                cands = r.candidates or []
                for cand in cands:
                    content = getattr(cand, "content", None)
                    parts_iter = getattr(content, "parts", None) if content else None
                    for part in (parts_iter or []):
                        inline = getattr(part, "inline_data", None)
                        if inline and inline.data:
                            return inline.data
                # No image came back. Surface the most useful diagnostic.
                finish = (cands[0].finish_reason
                          if cands and getattr(cands[0], "finish_reason", None)
                          else "UNKNOWN")
                safety = getattr(cands[0], "safety_ratings", None) if cands else None
                feedback = getattr(r, "prompt_feedback", None)
                # Collect any text output (model may have returned a textual
                # refusal explaining why).
                text_bits: list[str] = []
                for cand in cands:
                    content = getattr(cand, "content", None)
                    for part in (getattr(content, "parts", None) or []):
                        txt = getattr(part, "text", None)
                        if txt:
                            text_bits.append(txt)
                detail = (
                    f"finish_reason={finish}"
                    + (f"  prompt_feedback={feedback}" if feedback else "")
                    + (f"  safety={safety}" if safety else "")
                    + (f"  text='{' '.join(text_bits)[:300]}'" if text_bits else "")
                )
                raise RuntimeError(
                    f"Google AI Studio {self._model_id} returned no image — {detail}"
                )
            except Exception as e:
                msg = str(e)
                last_err = e
                is_429 = "429" in msg or "RESOURCE_EXHAUSTED" in msg
                is_5xx = any(c in msg for c in ("500", "502", "503", "504", "UNAVAILABLE"))
                if (is_429 or is_5xx) and attempt < max_attempts:
                    wait = backoff * attempt
                    print(f"  [RETRY {attempt}/{max_attempts}] {type(e).__name__}: "
                          f"{msg[:120]}... sleeping {wait:.0f}s")
                    _time.sleep(wait)
                    continue
                raise
        raise last_err  # type: ignore


# ---------------------------------------------------------------------------
# Registry — discover/configure backends by name
# ---------------------------------------------------------------------------

def get_backend(name: str, *, service_account_path: str | None = None,
                project: str | None = None) -> I2IBackend:
    """Factory. Backend names:
      "fal_nb2"       — Fal Nano-Banana 2 (existing default)
      "fal_nb_pro"    — Fal Nano-Banana Pro (V5 Pro option)
      "google_flash"  — Vertex AI gemini-2.5-flash-image (V5 direct)
    """
    if name == "fal_nb2":
        return FalBackend(
            model_id="fal-ai/nano-banana-2/edit",
            name="fal_nb2",
            cost=0.030,
        )
    if name == "fal_nb_pro":
        return FalBackend(
            model_id="fal-ai/nano-banana-pro/edit",
            name="fal_nb_pro",
            cost=0.060,
        )
    if name in ("google_nb2_2k", "google_nb_pro_2k"):
        # Google AI Studio (api-key) path. Two model variants:
        #   google_nb2_2k    → gemini-3.1-flash-image-preview  (Nano Banana 2, ~$0.04)
        #   google_nb_pro_2k → gemini-3-pro-image-preview     (Nano Banana Pro, ~$0.14)
        api_key = os.environ.get("GOOGLE_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            for candidate in (
                Path(__file__).resolve().parent.parent / ".env",
                Path(__file__).resolve().parent.parent.parent / ".env",
            ):
                if candidate.exists():
                    for line in candidate.read_text().splitlines():
                        if line.startswith("GOOGLE_KEY=") or line.startswith("GEMINI_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break
                    if api_key:
                        break
        if not api_key:
            raise SystemExit(
                f"[ERR] {name}: no GOOGLE_KEY / GEMINI_API_KEY in env or .env"
            )
        model_id, cost = (
            ("gemini-3.1-flash-image-preview", 0.040)
            if name == "google_nb2_2k"
            else ("gemini-3-pro-image-preview", 0.140)
        )
        return GoogleAIStudioBackend(
            model_id=model_id,
            name=name,
            cost=cost,
            api_key=api_key,
            image_size="2K",
            aspect_ratio="1:1",
        )

    if name == "google_flash":
        # Auto-discover service account path if not supplied
        if not service_account_path:
            for c in (
                Path(__file__).resolve().parent.parent.parent / "gemini_service_account.json",
                Path(__file__).resolve().parent.parent / "gemini_service_account.json",
                Path(__file__).resolve().parent.parent.parent.parent / "gemini_service_account.json",
            ):
                if c.exists():
                    service_account_path = str(c)
                    break
        if not service_account_path:
            raise SystemExit(
                f"[ERR] {name} backend: no gemini_service_account.json found. "
                "Place it at the package root or pass service_account_path."
            )
        if not project:
            import json
            with open(service_account_path) as f:
                project = json.load(f).get("project_id")
            if not project:
                raise SystemExit("[ERR] service account JSON missing project_id")

        return GoogleVertexBackend(
            model_id="gemini-2.5-flash-image",
            name="google_flash",
            cost=0.040,
            project=project,
            location="us-central1",
            service_account_path=service_account_path,
        )

    raise ValueError(
        f"Unknown backend {name!r}. "
        f"Known: fal_nb2, fal_nb_pro, google_flash, google_nb2_2k"
    )


BACKEND_CHOICES = ("google_nb2_2k", "google_nb_pro_2k", "google_flash", "fal_nb2", "fal_nb_pro")
