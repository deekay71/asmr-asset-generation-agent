"""V6 compose() — assembles a structured prompt for one asset.

Replaces V3/V4/V5's free-form envelope wrapper with a slot-filled template
per step_type. Empty/missing slots are pruned automatically so the final
prompt is tight (~400-700 chars vs V5's ~3,500).

When `concept_spec` is missing, optionally calls Gemini Flash text to infer
slot values from gameplay context. Confidence score reflects how much of the
spec came from the user vs. inferred.

Usage:
    from prompt_agent import compose
    result = compose(level_dir, cfg, asset, asset_kind, prev_state_id)
    # result.text          — final prompt string
    # result.log           — dict of decisions made
    # result.confidence    — 0–1
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .memory import load_patterns, get_pattern, ensure_pattern_fields

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Reasonable defaults if a level's `concept_defaults` doesn't override
GLOBAL_DEFAULTS: dict[str, Any] = {
    "lighting": "soft warm top-left, gentle directional, no hard speculars",
    "camera": "strictly front-on, no tilt, no angle",
    "containment": "all effects stay strictly within the asset silhouette; never extend into the background",
    "task_type": "single-image I2I from the previous state",
}


@dataclass
class ComposedPrompt:
    """Output of compose()."""
    text: str
    log: dict = field(default_factory=dict)
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_template(step_type: str | None) -> str:
    """Load a template by step_type id. Falls back to _base.txt for unknown."""
    if step_type:
        p = TEMPLATES_DIR / f"{step_type}.txt"
        if p.exists():
            return p.read_text()
    return (TEMPLATES_DIR / "_base.txt").read_text()


def _strip_comments(template: str) -> str:
    """Drop lines starting with `#` (template comments)."""
    return "\n".join(line for line in template.splitlines() if not line.lstrip().startswith("#"))


def _render(template: str, slots: dict[str, str]) -> str:
    """Fill `{{slot}}` placeholders. Lines with empty/missing slots get pruned."""
    out_lines: list[str] = []
    for line in template.splitlines():
        # Substitute placeholders
        rendered = line
        slot_names_in_line = re.findall(r"\{\{(\w+)\}\}", line)
        any_missing = False
        for name in slot_names_in_line:
            value = slots.get(name, "")
            if not value:
                any_missing = True
            rendered = rendered.replace("{{" + name + "}}", value)
        # Drop lines where a slot was missing AND the line is now mostly empty
        if any_missing and slot_names_in_line:
            stripped = rendered.strip()
            # If the line is just a label like "NOTES: " with nothing after, drop it
            if stripped.endswith(":") or stripped.endswith(": "):
                continue
            # If the line content (excluding placeholder) is just whitespace + colon, drop
            non_placeholder = re.sub(r"\{\{\w+\}\}", "", line).strip(" \t")
            if non_placeholder.endswith(":") and not slots.get(slot_names_in_line[0]):
                continue
        out_lines.append(rendered)
    # Collapse triple+ blank lines to a single blank
    text = "\n".join(out_lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _block(items: list[str], prefix: str = "  - ") -> str:
    """Render a list of strings as a bullet block."""
    if not items:
        return ""
    return "\n".join(f"{prefix}{x}" for x in items)


def _shape_constraint_for(asset: dict) -> str:
    """For subpart_dusty, suggest a shape constraint hint based on filename."""
    fn = (asset.get("filename") or "").lower()
    if "filter" in fn:
        return "rectangular flat panel, ~4:5 aspect (slightly taller than wide), NO perspective distortion, NO warping"
    if "cover" in fn or "panel" in fn:
        return "rectangular flat panel, NO perspective, NO warping"
    if "shell" in fn or "housing" in fn:
        return "follow the source image's natural shape — no warping, no perspective drift"
    return "preserve the part's natural shape — no warping, no perspective drift"


# ---------------------------------------------------------------------------
# Slot inference (deterministic) + optional LLM assist
# ---------------------------------------------------------------------------

def _infer_slots(cfg: dict, asset: dict, asset_kind: str,
                 prev_state_id: str | None,
                 level_defaults: dict) -> tuple[dict[str, str], list[str]]:
    """Deterministically infer slot values from cfg + asset.
    Returns (slots, decisions_log)."""
    decisions: list[str] = []
    slots: dict[str, str] = {}

    spec = asset.get("spec", {}) or {}

    # Lighting
    slots["lighting"] = spec.get("lighting") or level_defaults.get("lighting") or GLOBAL_DEFAULTS["lighting"]
    if "lighting" not in spec:
        decisions.append(f"lighting ← {'level_defaults' if 'lighting' in level_defaults else 'GLOBAL_DEFAULTS'}")

    # Camera
    slots["camera"] = spec.get("camera") or level_defaults.get("camera") or GLOBAL_DEFAULTS["camera"]
    if "camera" not in spec:
        decisions.append(f"camera ← {'level_defaults' if 'camera' in level_defaults else 'GLOBAL_DEFAULTS'}")

    # Diff from prev
    if asset_kind in ("chain_state", "sprite", "subflow"):
        diff = spec.get("diff_from_prev") or asset.get("prompt") or asset.get("prompt_t2i") or ""
        if not diff and asset.get("prompt_i2i_nb2"):
            diff = asset["prompt_i2i_nb2"]
        slots["diff_from_prev"] = diff
        if diff and "diff_from_prev" not in spec:
            decisions.append("diff_from_prev ← legacy 'prompt' field (V2/V3 backwards compat)")
    elif asset_kind == "tool":
        slots["subject_description"] = asset.get("prompt_t2i", "")

    # Object count
    slots["object_count"] = spec.get("object_count", "")

    # Preserve from prev
    slots["preserve_from_prev"] = spec.get("preserve_from_prev", "")

    # Notes (free-form escape hatch)
    slots["notes"] = spec.get("notes", "")

    # Task type
    slots["task_type"] = level_defaults.get("task_type") or GLOBAL_DEFAULTS["task_type"]

    # Containment
    containment_value = level_defaults.get("containment") or GLOBAL_DEFAULTS["containment"]
    if cfg.get("containment_rule") is False:
        containment_value = ""  # explicitly disabled
        decisions.append("containment disabled by config")
    slots["containment"] = containment_value

    # Background directive
    bg_color = cfg.get("bg_color", "#808080")
    if asset_kind == "background":
        slots["bg_directive"] = "OPAQUE scene plate — do NOT use chroma key, this is a final scene plate"
        slots["surface_spec"] = spec.get("surface_spec", "")
    else:
        slots["bg_directive"] = f"flat solid {bg_color} chroma-key background"

    # Shape constraint (subpart_dusty)
    slots["shape_constraint"] = _shape_constraint_for(asset)

    # Best-practices block (from step_pattern + level color_constant)
    bp_lines = []
    if asset.get("step_type"):
        pat = get_pattern(asset["step_type"]) or {}
        ensure_pattern_fields(pat)
        bp_lines.extend(pat.get("best_practices", []))
    # If the level has a color_constant, add it as an identity-lock line in best practices
    if cfg.get("color_constant"):
        bp_lines.append(f"identity lock — {cfg['color_constant'][:200]}")
    slots["best_practices_block"] = _block(bp_lines)
    if bp_lines:
        decisions.append(f"best_practices ← {len(bp_lines)} rules from step_pattern + color_constant")

    # Forbid block
    forbid_lines = []
    if asset.get("step_type"):
        pat = get_pattern(asset["step_type"]) or {}
        forbid_lines.extend(pat.get("forbid", []))
    slots["forbid_block"] = _block(forbid_lines)
    if forbid_lines:
        decisions.append(f"forbid ← {len(forbid_lines)} anti-patterns from step_pattern")

    return slots, decisions


# ---------------------------------------------------------------------------
# Optional LLM assist for inferring `diff_from_prev` when concept_spec
# doesn't provide one and there's no legacy `prompt` field either.
# Costs ~$0.0005 per call. Only fires when truly missing.
# ---------------------------------------------------------------------------

def _llm_infer_diff(cfg: dict, asset: dict, prev_state_id: str | None) -> str | None:
    """Use Gemini Flash text to propose a diff_from_prev sentence when none
    is available. Returns None on any error (graceful fallback)."""
    try:
        import os
        from google import genai
        client = _llm_client()
        if client is None:
            return None
        level_name = cfg.get("name", "asset")
        step_type = asset.get("step_type", "generic")
        prompt = (
            f"You are inferring an I2I instruction for a 2D ASMR cleaning game asset.\n"
            f"Level: {level_name}. Asset: {asset.get('id', '?')}.\n"
            f"step_type: {step_type}.\n"
            f"Previous state id: {prev_state_id or 'anchor'}.\n"
            f"Color constant for the asset: {cfg.get('color_constant', '')[:300]}.\n\n"
            f"Write ONE concise paragraph (max 250 chars) describing what visual change "
            f"this asset should show compared to the previous state, given the step_type. "
            f"Be specific and concrete. Do NOT include lighting, camera, or background — "
            f"those are handled by separate slots."
        )
        r = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return (r.text or "").strip()
    except Exception:
        return None


_LLM_CLIENT_CACHE = {"client": None, "tried": False}


def _llm_client():
    """Lazy-init Gemini client for slot inference. Returns None if unavailable."""
    if _LLM_CLIENT_CACHE["tried"]:
        return _LLM_CLIENT_CACHE["client"]
    _LLM_CLIENT_CACHE["tried"] = True
    try:
        import os
        from google import genai
        # Use the same service account as i2i_backend if present
        sa_path = None
        here = Path(__file__).resolve().parent
        for c in (here.parent.parent / "gemini_service_account.json",
                  here.parent.parent.parent / "gemini_service_account.json"):
            if c.exists():
                sa_path = str(c)
                break
        if sa_path and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
        # Project ID
        proj = None
        if sa_path:
            try:
                proj = json.load(open(sa_path)).get("project_id")
            except Exception:
                pass
        if not proj:
            return None
        client = genai.Client(vertexai=True, project=proj, location="us-central1")
        _LLM_CLIENT_CACHE["client"] = client
        return client
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compose(level_dir: Path, cfg: dict, asset: dict, asset_kind: str,
            prev_state_id: str | None = None,
            regen_comment: str = "",
            allow_llm_assist: bool = True) -> ComposedPrompt:
    """Build the structured prompt for one asset.

    Args:
        level_dir: the level's directory (used for resolving any references)
        cfg: the items_config.json dict
        asset: the asset's own dict (a state / sprite / tool / subflow item)
        asset_kind: one of "chain_state", "sprite", "tool", "subflow", "background"
        prev_state_id: the previous chain state id, if applicable (for log only)
        regen_comment: user feedback from regen_queue.json, if any
        allow_llm_assist: if True, call Gemini Flash text to infer missing slots
    """
    level_defaults = cfg.get("concept_defaults", {})
    step_type = asset.get("step_type")

    template = _strip_comments(_load_template(step_type))
    slots, decisions = _infer_slots(cfg, asset, asset_kind, prev_state_id, level_defaults)

    # LLM-assist for diff_from_prev if missing and chain/sprite
    confidence = 1.0
    if allow_llm_assist and asset_kind in ("chain_state", "sprite", "subflow"):
        if not slots.get("diff_from_prev"):
            inferred = _llm_infer_diff(cfg, asset, prev_state_id)
            if inferred:
                slots["diff_from_prev"] = inferred
                decisions.append("diff_from_prev ← LLM-inferred (Gemini Flash text)")
                confidence = 0.6
            else:
                slots["diff_from_prev"] = "(no diff specified — model uses source image as-is)"
                confidence = 0.4

    body = _render(template, slots)

    # Append regen feedback as a final hard nudge
    if regen_comment:
        body += f"\n\nREGEN FEEDBACK (apply this on regen):\n{regen_comment.strip()}"
        decisions.append(f"regen_comment appended ({len(regen_comment)} chars)")

    log = {
        "asset_id": asset.get("id", "?"),
        "asset_kind": asset_kind,
        "step_type": step_type,
        "prev_state_id": prev_state_id,
        "char_count": len(body),
        "decisions": decisions,
        "confidence": confidence,
        "llm_assist_used": "LLM-inferred" in " ".join(decisions),
    }

    return ComposedPrompt(text=body, log=log, confidence=confidence)
