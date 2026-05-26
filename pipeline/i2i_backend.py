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
                 service_account_path: str | None = None):
        self._model_id = model_id
        self.name = name
        self.cost = cost
        self._project = project
        self._location = location
        self._sa_path = service_account_path

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

        # Retry-with-backoff on 429 (Vertex per-minute quota bursts)
        max_attempts = 5
        backoff = 8.0  # seconds — Vertex per-minute quota resets in ~60s
        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                r = client.models.generate_content(model=self._model_id, contents=parts)
                for cand in r.candidates:
                    for part in cand.content.parts:
                        inline = getattr(part, "inline_data", None)
                        if inline and inline.data:
                            return inline.data
                raise RuntimeError(
                    f"Google Vertex {self._model_id} returned no image. "
                    f"Finish reason: {r.candidates[0].finish_reason if r.candidates else 'no candidates'}"
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
                "[ERR] google_flash backend: no gemini_service_account.json found. "
                "Place it at the package root or pass service_account_path."
            )
        # Read project from the SA file if not given
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
        f"Known: fal_nb2, fal_nb_pro, google_flash"
    )


BACKEND_CHOICES = ("fal_nb2", "fal_nb_pro", "google_flash")
