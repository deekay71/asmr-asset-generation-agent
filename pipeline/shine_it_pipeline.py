#!/usr/bin/env python3
"""
Shine It Items — Asset Production Pipeline

CLI orchestrator. Phase-based, resumable. Each phase has a hard approval gate
before progressing — no auto-cascade past Phase 1 until user signs off on
style/quality.

Phases:
  1   Anchor generation (clean state) — FLUX Pro + Nano-Banana 2 in parallel
  2   Chain plan validation (items_config.json approval gate)
  3   Backwards chain generation (state N-1 down to 0)
  3b  Trash overlay generation (independent transparent sprites)
  4   Tool sprite generation (T2I only, dedup against tools_manifest.json)
  5   Post-processing (rembg + alpha 128 + tight crop +2px)
  6   Review HTML (review_chain.html, per-state approve/regen)
  7   Promote staging → approved/

Usage:
  python shine_it_pipeline.py --level 5 --phase 1
  python shine_it_pipeline.py --level 5 --phase 1 --models flux,nb2   # default
  python shine_it_pipeline.py --level 5 --phase 1 --models flux       # FLUX only
"""

import argparse
import json
import sys
from pathlib import Path

# Portable zip layout:
#   <root>/
#     .env                        (FAL_KEY)
#     gemini_service_account.json (for Vertex backend, optional)
#     pipeline/shine_it_pipeline.py
#     pipeline/fal_helper.py
#     pipeline/i2i_backend.py
#     pipeline/prompt_agent/
#     pipeline/step_patterns.json
#     projects/level_{NN}_*/items_config.json
#     projects/tools/
#     references/
PIPELINE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PIPELINE_DIR.parent / "projects"
PRODUCER_ROOT = PIPELINE_DIR.parent
sys.path.insert(0, str(PIPELINE_DIR))

from fal_helper import (  # noqa: E402
    init_fal,
    generate_image,
    nano_banana_edit,
    nano_banana_generate,
    upload_file,
    download_image,
    remove_bg_local,
    remove_bg_hybrid,
    remove_bg_smart_hybrid,
    remove_bg_hsv_chroma,
    clean_and_crop,
    FLUX_PRO_MODEL,
    NANO_BANANA_2_EDIT_MODEL,
    NANO_BANANA_T2I_MODEL,
)

# V5 — backend abstraction
try:
    from i2i_backend import get_backend as _get_i2i_backend, BACKEND_CHOICES
except ImportError:
    _get_i2i_backend = None
    BACKEND_CHOICES = ("fal_nb2", "fal_nb_pro", "google_flash")

# V6 — prompt agent (optional; falls back to V5 envelope if unavailable)
try:
    from prompt_agent import compose as _compose, learn as _learn
    _AGENT_AVAILABLE = True
except ImportError:
    _compose = None
    _learn = None
    _AGENT_AVAILABLE = False


def _agent_banner_html(level_dir) -> str:
    """V6 — show a banner if step_patterns.json has been updated since the last
    review HTML build. Reads `agent_banner_seen.json` to track last-viewed state."""
    if not _AGENT_AVAILABLE:
        return ""
    try:
        from prompt_agent.memory import PATTERNS_PATH
    except ImportError:
        return ""
    if not PATTERNS_PATH.exists():
        return ""

    seen_path = level_dir / "agent_banner_seen.json"
    last_seen_ts = ""
    if seen_path.exists():
        try:
            last_seen_ts = json.loads(seen_path.read_text()).get("last_seen_ts", "")
        except Exception:
            pass

    try:
        doc = json.loads(PATTERNS_PATH.read_text())
    except Exception:
        return ""

    history = doc.get("history", [])
    recent = [h for h in history if h.get("ts", "") > last_seen_ts]
    if not recent:
        return ""

    # Count promoted-rule events
    promoted_count = sum(1 for h in recent if h.get("polarity") and "promoted" in str(h.get("outcome", "")).lower())
    # Always show at least the count of feedback entries learned
    n_recent = len(recent)

    # Update seen marker
    if doc.get("history"):
        latest_ts = max(h.get("ts", "") for h in history)
        seen_path.write_text(json.dumps({"last_seen_ts": latest_ts}, indent=2))

    return (
        f'<div style="margin-top:12px;padding:10px 14px;background:#2a3f2a;'
        f'border-radius:6px;border-left:3px solid #6f6;color:#cfc;">'
        f'<strong>🧠 Agent learned {n_recent} new rule(s) since your last review.</strong> '
        f'See <code>step_patterns.json</code> → <code>history</code> for details.'
        f'</div>'
    )

# V5 default backend — Vertex Flash (gemini-2.5-flash-image direct).
# Better instruction-following + ~2.5× faster than Fal Pro, ~1.3× cost of NB-2.
DEFAULT_BACKEND_NAME = "google_flash"

_BACKEND_CACHE: dict = {}

def _get_backend(name: str | None = None):
    """Return an I2IBackend instance. Cached per name across the run.
    Falls back to legacy direct nano_banana_edit if i2i_backend module
    isn't importable (shouldn't happen in V5 layouts)."""
    if _get_i2i_backend is None:
        raise RuntimeError("i2i_backend module not available")
    nm = name or DEFAULT_BACKEND_NAME
    if nm not in _BACKEND_CACHE:
        _BACKEND_CACHE[nm] = _get_i2i_backend(nm)
    return _BACKEND_CACHE[nm]


def _select_bg_remover(bg_color_hex: str):
    """Return the right bg-removal function for the level's bg colour.
    Green (#00FF00 or any #00xxxx-ish) → HSV chroma key (handles green-screen)
    Grey (#808080-ish) → rembg local (default — handles most subjects on grey)
    """
    if not bg_color_hex:
        return remove_bg_local
    hx = bg_color_hex.lstrip("#").lower()
    if len(hx) == 6:
        r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
        if g > r + 40 and g > b + 40:  # green-dominant
            return remove_bg_hsv_chroma
    return remove_bg_local


# Per-call price for the model we use. Centralised here so cost telemetry stays
# accurate even if Fal pricing changes.
NB2_EDIT_COST = 0.030
FLUX_PRO_COST = 0.050

PIPELINE_VERSION = "3.0"

# ---------------------------------------------------------------------------
# V3 — Style mode presets
#
# Distilled from the artist-team prompt (Level 6 reference, May 2026). Each
# preset expands into a paragraph that goes in the STYLE block of the prompt
# envelope. Configs opt-in via `style_mode: "<key>"`.
# ---------------------------------------------------------------------------
STYLE_MODE_PRESETS = {
    "2d_game_asset_flat": (
        "Realistic 2D game asset, highly readable, polished, satisfying ASMR "
        "cleaning visuals. Detailed dust, stains, foam, and cleaned result. "
        "Soft warm top-left lighting with gentle directional shading. "
        "NOT painterly, NOT photobashed. Clean asset-sheet presentation."
    ),
    "3d_prop_render": (
        "Semi-realistic stylized 3D mobile-game prop render. Soft warm top-left "
        "lighting with smooth gradients and gentle highlight rim. Soft drop "
        "shadow under the object. Premium product-photography feel. Matte "
        "finish — NO glossy plastic highlights. NOT photoreal, NOT cartoon flat."
    ),
}

# Default ASMR framing if config doesn't override
DEFAULT_ASMR_FRAMING = (
    "This is for a 2D ASMR cleaning game. The player wants a satisfying "
    "disgust→clean transition. Dirty states should feel disgusting, deeply "
    "embedded, neglected, ugly. Clean states should feel fresh, bright, "
    "satisfying, and like new. No people, no hands, no UI, no text, no "
    "labels, no room background visible in the asset."
)

# V4 default containment rule — auto-injected unless the config explicitly
# disables it via `containment_rule: false`. Reinforces that effects stay
# within the asset silhouette and don't bleed into the background.
DEFAULT_CONTAINMENT_RULE = (
    "CONTAINMENT: All effects added to the asset (foam, water, dust, dirt, "
    "stains, mortar, debris, lather, suds) MUST stay strictly within the "
    "asset's silhouette. NEVER extend past the object's outline into the "
    "background. The flat chroma-key background must remain perfectly clean "
    "of any spillover, drips, or mist. If a tool would naturally spray water "
    "past the object, do NOT depict the spray — just the wet result on the "
    "object itself."
)

# V4 — step pattern library (loaded once at startup, append-only)
# Search in: pipeline-script dir, parent dir, grandparent dir
_HERE = Path(__file__).resolve().parent
_STEP_PATTERNS_CANDIDATES = [
    _HERE / "step_patterns.json",
    _HERE.parent / "step_patterns.json",
    _HERE.parent.parent / "step_patterns.json",
]
STEP_PATTERNS = {}
for _candidate in _STEP_PATTERNS_CANDIDATES:
    if _candidate.exists():
        try:
            with open(_candidate) as _f:
                STEP_PATTERNS = json.load(_f).get("patterns", {})
            break
        except (json.JSONDecodeError, OSError) as _e:
            print(f"[WARN] Could not parse {_candidate}: {_e}")
if not STEP_PATTERNS:
    print(f"[NOTE] No step_patterns.json found. `step_type:` fields will be ignored.")


# ---------------------------------------------------------------------------
# V2 helpers — id filtering, cost logging, multi-source resolution
# ---------------------------------------------------------------------------

def _match_id(item_id: str, pattern: str | None) -> bool:
    """Return True if item_id matches pattern. Supports `*` wildcards and exact match.
    pattern None / empty → always True (no filter active).
    """
    if not pattern:
        return True
    import fnmatch
    return fnmatch.fnmatchcase(item_id, pattern)


def _filter_items(items: list, attr: str, pattern: str | None) -> list:
    """Filter a list of dicts by an attribute against a glob pattern."""
    if not pattern:
        return items
    return [i for i in items if _match_id(i.get(attr, ""), pattern)]


def _log_cost(level_dir: Path, phase: str, asset_id: str, model: str,
              cost: float, success: bool = True) -> None:
    """Append one line to cost_log.jsonl in the level directory."""
    import time as _time
    log_path = level_dir / "cost_log.jsonl"
    entry = {
        "ts": _time.strftime("%Y-%m-%dT%H:%M:%S"),
        "phase": phase,
        "asset_id": asset_id,
        "model": model,
        "cost": round(cost, 4),
        "success": success,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _normalise_sources(item: dict) -> list[str]:
    """Return ordered list of source specs for an item.
    Supports both:
      "source": "chain:00"            (legacy, single string)
      "sources": ["style_ref", ...]   (V2, ordered list)
    Empty list = use default style ref.
    """
    if "sources" in item and item["sources"]:
        srcs = item["sources"]
        return list(srcs) if isinstance(srcs, list) else [srcs]
    if item.get("source"):
        return [item["source"]]
    return []


def _confirm_cost(estimate: float, yes: bool) -> bool:
    """Interactive gate before spending. --yes bypasses."""
    if yes or estimate == 0:
        return True
    try:
        ans = input(f"\n  Estimated cost: ${estimate:.2f}. Continue? [y/N] ").strip().lower()
    except EOFError:
        print("[ERR] No stdin available for confirmation. Pass --yes to override.")
        return False
    return ans in ("y", "yes")


def _build_prompt_envelope(cfg: dict) -> str:
    """V3+V4 — build the consistent prompt header that wraps every
    chain/sprite/tool instruction. Returns a multi-paragraph string with
    CONSISTENCY RULES, GAME CONTEXT, STYLE, and CONTAINMENT blocks. Falls
    back gracefully on V2 configs that don't declare the new fields.
    """
    parts = []
    is_v3_plus = cfg.get("schema_version", "").startswith(("3", "4"))

    contract = cfg.get("chain_consistency_contract")
    if contract:
        parts.append(f"CONSISTENCY RULES:\n{contract}")

    framing = cfg.get("asmr_framing", DEFAULT_ASMR_FRAMING) if is_v3_plus else cfg.get("asmr_framing")
    if framing:
        parts.append(f"GAME CONTEXT:\n{framing}")

    style_mode = cfg.get("style_mode")
    if style_mode and style_mode in STYLE_MODE_PRESETS:
        parts.append(f"STYLE:\n{STYLE_MODE_PRESETS[style_mode]}")
    elif style_mode:
        print(f"[WARN] Unknown style_mode {style_mode!r}. Known: {list(STYLE_MODE_PRESETS)}")
        if cfg.get("style_description"):
            parts.append(f"STYLE:\n{cfg['style_description']}")
    elif cfg.get("style_description"):
        parts.append(f"STYLE:\n{cfg['style_description']}")

    # V4 — containment rule. Auto-inject default on V3+ unless explicitly
    # set to false. V2 configs only get it if explicitly opted in.
    containment = cfg.get("containment_rule")
    if containment is False:
        pass  # explicitly disabled
    elif containment is True or (containment is None and is_v3_plus):
        parts.append(DEFAULT_CONTAINMENT_RULE)
    elif isinstance(containment, str):
        parts.append(f"CONTAINMENT:\n{containment}")

    return "\n\n".join(parts)


def _build_step_pattern_block(asset: dict) -> str:
    """V4 — if the asset declares a `step_type`, look it up in the global
    STEP_PATTERNS library and emit a STEP-TYPE PATTERN block. Returns "" if
    no step_type set or unknown.
    """
    step_type = asset.get("step_type")
    if not step_type:
        return ""
    pat = STEP_PATTERNS.get(step_type)
    if not pat:
        print(f"[WARN] Unknown step_type {step_type!r}. Known: {list(STEP_PATTERNS)}")
        return ""
    lines = [f"STEP-TYPE PATTERN: {step_type}"]
    if pat.get("description"):
        lines.append(f"  Purpose: {pat['description']}")
    if pat.get("required_qualities"):
        lines.append("  Required qualities:")
        lines.extend([f"    • {q}" for q in pat["required_qualities"]])
    if pat.get("best_practices"):
        lines.append("  Best practices (locked in from prior feedback):")
        lines.extend([f"    • {p}" for p in pat["best_practices"]])
    if pat.get("common_pitfalls"):
        lines.append("  AVOID:")
        lines.extend([f"    • {p}" for p in pat["common_pitfalls"]])
    return "\n".join(lines)


def _use_v6_agent(cfg: dict, asset: dict) -> bool:
    """V6 routing: use the prompt agent if available AND the asset has either
    a `step_type` or a `spec` block. Falls back to V5 envelope otherwise.
    Disable globally with `pipeline_version: 5` in cfg."""
    if not _AGENT_AVAILABLE or _compose is None:
        return False
    if cfg.get("pipeline_version") == 5:
        return False
    return bool(asset.get("step_type") or asset.get("spec"))


def _log_agent_decision(level_dir, asset_id: str, log: dict):
    """Append agent decision log to agent_log.jsonl for traceability."""
    import time as _t
    p = level_dir / "agent_log.jsonl"
    entry = {"ts": _t.strftime("%Y-%m-%dT%H:%M:%S"), "asset_id": asset_id, **log}
    with open(p, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _build_chain_prompt(cfg: dict, state: dict, regen_comment: str = "",
                        level_dir=None) -> str:
    """Assemble the full chain-state prompt.
    V6: delegates to prompt_agent.compose() if step_type or spec is set.
    V5 fallback: envelope + step-pattern block + task + color_const + regen."""
    if level_dir is not None and _use_v6_agent(cfg, state):
        result = _compose(
            level_dir, cfg, state,
            asset_kind="chain_state",
            prev_state_id=state.get("source_state"),
            regen_comment=regen_comment,
        )
        _log_agent_decision(level_dir, state.get("id", "?"), result.log)
        return result.text

    # V5 envelope (backwards-compat for V2/V3/V4 configs without step_type/spec)
    envelope = _build_prompt_envelope(cfg)
    pattern_block = _build_step_pattern_block(state)
    color_const = cfg.get("color_constant", "")
    parts = [envelope] if envelope else []
    if pattern_block:
        parts.append(pattern_block)
    parts.append(f"TASK (single-image I2I from the previous state):\n{state['prompt']}")
    if color_const:
        parts.append(f"IDENTITY LOCK:\n{color_const}")
    if regen_comment:
        parts.append(f"REGEN FEEDBACK (apply this to the regenerated asset):\n{regen_comment}")
    return "\n\n".join(parts)


def _build_sprite_prompt(cfg: dict, sprite: dict, has_source: bool,
                          regen_comment: str = "", level_dir=None) -> str:
    """V6: delegates to compose() when step_type or spec set. V5 fallback otherwise."""
    if level_dir is not None and _use_v6_agent(cfg, sprite):
        # Infer asset_kind from sprite filename hint
        fn = sprite.get("filename", "")
        if "subflow" in fn or sprite.get("id", "").startswith("subflow"):
            kind = "subflow"
        elif "background" in fn:
            kind = "background"
        else:
            kind = "sprite"
        result = _compose(
            level_dir, cfg, sprite,
            asset_kind=kind,
            regen_comment=regen_comment,
        )
        _log_agent_decision(level_dir, sprite.get("id", "?"), result.log)
        return result.text

    # V5 envelope fallback
    envelope = _build_prompt_envelope(cfg)
    pattern_block = _build_step_pattern_block(sprite)
    if has_source:
        body = f"TASK (I2I — transform the reference image as follows):\n{sprite['prompt_t2i']}"
    else:
        body = (
            "TASK (I2I from style reference):\nMatch the reference image's art "
            "style exactly. Replace the subject of the reference entirely with:\n"
            f"{sprite['prompt_t2i']}"
        )
    parts = [envelope] if envelope else []
    if pattern_block:
        parts.append(pattern_block)
    parts.append(body)
    if regen_comment:
        parts.append(f"REGEN FEEDBACK (apply this to the regenerated asset):\n{regen_comment}")
    return "\n\n".join(parts)


def _build_tool_prompt(cfg: dict, tool: dict, has_source: bool,
                       regen_comment: str = "", level_dir=None) -> str:
    """V6: delegates to compose() when step_type or spec set. V5 fallback otherwise."""
    if level_dir is not None and _use_v6_agent(cfg, tool):
        result = _compose(
            level_dir, cfg, tool,
            asset_kind="tool",
            regen_comment=regen_comment,
        )
        _log_agent_decision(level_dir, tool.get("id", "?"), result.log)
        return result.text
    # Legacy V5 path follows below
    envelope = _build_prompt_envelope(cfg)
    # Tools default to `step_type: tool_sprite` if not set explicitly
    if "step_type" not in tool:
        tool = {**tool, "step_type": "tool_sprite"}
    pattern_block = _build_step_pattern_block(tool)
    orientation = cfg.get("tool_orientation_rule", "")
    if has_source:
        body = f"TASK (I2I — transform the reference image as follows):\n{tool['prompt_t2i']}"
    else:
        body = (
            "TASK (I2I from style reference):\nMatch the reference image's art "
            "style exactly. Replace the subject of the reference entirely with "
            f"the tool described below:\n\nSUBJECT: {tool['prompt_t2i']}"
        )
    parts = [envelope] if envelope else []
    if pattern_block:
        parts.append(pattern_block)
    parts.append(body)
    if orientation:
        parts.append(f"ORIENTATION RULE (mandatory):\n{orientation}")
    if regen_comment:
        parts.append(f"REGEN FEEDBACK (apply this to the regenerated asset):\n{regen_comment}")
    return "\n\n".join(parts)


def _load_regen_queue(level_dir) -> dict:
    """V4 — load per-asset regen comments from regen_queue.json (written by
    the review HTML). Returns {asset_id: comment_string}. Empty dict if
    file doesn't exist.
    """
    p = level_dir / "regen_queue.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return {entry["id"]: entry.get("comment", "") for entry in data.get("regen", [])}
    except (json.JSONDecodeError, KeyError):
        print(f"[WARN] {p} exists but couldn't be parsed. Ignoring.")
        return {}


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_level_config(level: int) -> tuple[dict, Path]:
    level_dir = PROJECT_DIR / f"level_{level:02d}_plushie"
    if not level_dir.exists():
        # Fallback: scan for any level_{NN}_* directory
        matches = list(PROJECT_DIR.glob(f"level_{level:02d}_*"))
        if not matches:
            raise SystemExit(f"[ERR] No level_{level:02d}_* dir under {PROJECT_DIR}")
        level_dir = matches[0]

    cfg_path = level_dir / "items_config.json"
    if not cfg_path.exists():
        raise SystemExit(f"[ERR] Missing items_config.json at {cfg_path}")

    with open(cfg_path) as f:
        cfg = json.load(f)
    return cfg, level_dir


def get_anchor_state(cfg: dict) -> dict:
    anchor_id = cfg["anchor_state"]
    for s in cfg["states"]:
        if s["id"] == anchor_id:
            return s
    raise SystemExit(f"[ERR] Anchor state '{anchor_id}' not found in states[]")


# ---------------------------------------------------------------------------
# Phase 1 — Anchor generation
# ---------------------------------------------------------------------------

ANCHOR_VARIANT_SUFFIXES = [
    # V1 — matte / clean / neutral
    "STYLE / FINISH for THIS variant: clean MATTE finish with soft diffuse lighting. "
    "Neutral colour temperature. No glossy highlights. Crisp readable silhouette.",
    # V2 — slightly polished / product-photo
    "STYLE / FINISH for THIS variant: subtle satin sheen with gentle product-photo "
    "highlights along the upper edges. Slightly higher contrast than v1. Soft warm "
    "top-left lighting with a clean rim highlight.",
    # V3 — warm golden / atmospheric
    "STYLE / FINISH for THIS variant: warmer golden-hour colour temperature, slight "
    "warm tint across the whole asset, soft enveloping lighting. Mood is cosy / "
    "lived-in / inviting. Matte finish with gentle warm reflections.",
]


def phase_1_anchor(level: int, models: list[str], num_variants: int = 3):
    """V5+ Phase 1 — generate `num_variants` anchor options (default 3) with
    slight style/finish variations so the user can pick the best one.

    Outputs:
      staging/anchor_v{N}.png        — for the I2I backend (default google_flash)
      staging/anchor_flux.png        — single FLUX Pro shot, optional (if "flux" in models)
      style_comparison.html          — N-up picker UI
    """
    cfg, level_dir = load_level_config(level)
    anchor = get_anchor_state(cfg)
    staging = level_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)

    style_ref_rel = cfg["style_reference"]
    style_ref = (level_dir / style_ref_rel).resolve()
    if not style_ref.exists():
        raise SystemExit(f"[ERR] style_reference not found: {style_ref}")

    backend = _get_backend()  # default (currently google_flash)
    n_variants = max(1, min(num_variants, len(ANCHOR_VARIANT_SUFFIXES)))

    estimate = n_variants * backend.cost
    if "flux" in models:
        estimate += FLUX_PRO_COST  # legacy FLUX comparison

    print("=" * 70)
    print(f"  Phase 1 — Anchor generation (level {level})")
    print(f"  State    : {anchor['id']}")
    print(f"  Variants : {n_variants}  (slight style/finish differences)")
    print(f"  Backend  : {backend.name}  (~${backend.cost:.3f}/call)")
    print(f"  Models   : {', '.join(models)}")
    print(f"  Cost est : ~${estimate:.2f}")
    print(f"  Output   : {staging}")
    print("=" * 70)

    outputs = {}

    # --- Optional FLUX Pro v1.1 (T2I) one-off ---
    if "flux" in models:
        init_fal(str(PRODUCER_ROOT / ".env"))
        print("\n[FLUX] Pro v1.1 — T2I one-shot ...")
        prompt = anchor.get("prompt_t2i_flux", "")
        if prompt:
            images = generate_image(
                prompt=prompt, model=FLUX_PRO_MODEL, image_size="square_hd",
                num_inference_steps=28, guidance_scale=3.5, num_images=1,
            )
            url = images[0]["url"]
            out_path = staging / "anchor_flux.png"
            download_image(url, out_path)
            outputs["flux"] = {
                "path": str(out_path.relative_to(level_dir)),
                "url": url, "model": FLUX_PRO_MODEL, "mode": "T2I", "prompt": prompt,
            }
        else:
            print("  [SKIP] No prompt_t2i_flux on anchor state.")

    # --- N variants via the active I2I backend (default google_flash) ---
    if "nb2" in models:
        base_prompt = anchor.get("prompt_i2i_nb2") or anchor.get("prompt_t2i_flux", "")
        if not base_prompt:
            raise SystemExit("[ERR] Anchor state has no prompt_i2i_nb2 or prompt_t2i_flux.")

        if backend.name.startswith("fal_"):
            init_fal(str(PRODUCER_ROOT / ".env"))

        for i in range(n_variants):
            suffix = ANCHOR_VARIANT_SUFFIXES[i]
            variant_prompt = f"{base_prompt}\n\n{suffix}"
            print(f"\n[VARIANT v{i+1}/{n_variants}] {backend.name} ...")
            data = backend.edit(style_ref, variant_prompt)
            out_path = staging / f"anchor_v{i+1}.png"
            out_path.write_bytes(data)
            _log_cost(level_dir, "1_anchor", f"anchor_v{i+1}", backend.name,
                      backend.cost, True)
            outputs[f"v{i+1}"] = {
                "path": str(out_path.relative_to(level_dir)),
                "backend": backend.name,
                "mode": "I2I",
                "style_reference": __import__("os").path.relpath(style_ref, level_dir),
                "variant_suffix": suffix,
                "prompt": variant_prompt,
            }
            print(f"  ✓ {out_path.name}  ({len(data)//1024} KB)")

    # Save run metadata
    meta_path = staging / "anchor_run.json"
    with open(meta_path, "w") as f:
        json.dump({"phase": 1, "level": level, "anchor": anchor["id"],
                   "outputs": outputs}, f, indent=2)
    print(f"\n[META] {meta_path.relative_to(PROJECT_DIR)}")

    # Build comparison HTML
    build_style_comparison_html(level_dir, outputs, anchor, cfg)

    print("\n" + "=" * 70)
    print("  Phase 1 complete. Open the comparison HTML to approve a winner")
    print(f"  before proceeding to Phase 2/3.")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Style comparison HTML (Phase 1 gate)
# ---------------------------------------------------------------------------

def build_style_comparison_html(level_dir: Path, outputs: dict,
                                anchor: dict, cfg: dict):
    style_ref_rel = cfg["style_reference"]
    cards = []
    for key, info in outputs.items():
        if key == "flux":
            label = "FLUX Pro v1.1 (T2I — legacy comparison)"
        elif key.startswith("v"):
            label = f"Variant {key.upper()}  ·  {info.get('backend', '?')}"
        else:
            label = key
        # Variant suffix (V5+) tells the human what subtle finish change defined this variant
        variant_blurb = info.get("variant_suffix", "")
        prompt_html = info["prompt"].replace("<", "&lt;").replace(">", "&gt;")
        cards.append(f"""
        <div class="card">
          <div class="head">{label}</div>
          <img src="{info['path']}" alt="{key}">
          {f'<div class="meta">{variant_blurb}</div>' if variant_blurb else ''}
          <details><summary>full prompt</summary><pre>{prompt_html}</pre></details>
          <div class="actions">
            <button class="approve" onclick="pick('{key}')">Approve this</button>
          </div>
        </div>""")

    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Shine It — Style Comparison (Level {cfg['level']} {cfg['name']})</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background:#222; color:#eee; margin:0; padding:24px; }}
  h1 {{ margin:0 0 6px 0; }}
  .sub {{ color:#aaa; margin-bottom:18px; }}
  .ref {{ display:flex; gap:16px; align-items:center; background:#2a2a2a; padding:12px; border-radius:8px; margin-bottom:24px; }}
  .ref img {{ max-height:160px; border-radius:4px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:24px; }}
  .card {{ background:#2a2a2a; border-radius:8px; padding:16px; }}
  .card .head {{ font-weight:600; margin-bottom:10px; }}
  .card .meta {{ font-size:12px; color:#bbb; margin:8px 0; font-style:italic; line-height:1.4; }}
  .card img {{ width:100%;
    background:
      repeating-conic-gradient(#444 0% 25%, #555 0% 50%) 50% / 32px 32px;
    border-radius:4px; }}
  details {{ margin-top:10px; color:#bbb; }}
  pre {{ white-space:pre-wrap; font-size:12px; background:#1a1a1a; padding:10px; border-radius:4px; }}
  .actions {{ margin-top:10px; }}
  .approve {{ background:#3a7; color:#fff; border:0; padding:8px 14px; border-radius:4px; cursor:pointer; font-size:14px; }}
  .approve:hover {{ background:#4b8; }}
  #picked {{ margin-top:18px; padding:12px; background:#1a3a1a; border-radius:6px; display:none; }}
</style>
</head>
<body>
  <h1>Shine It — Style Comparison</h1>
  <div class="sub">Level {cfg['level']} · {cfg['name']} · anchor state <code>{anchor['id']}</code></div>
  <div class="ref">
    <div><strong>Style reference:</strong></div>
    <img src="{style_ref_rel}" alt="style ref">
    <div style="font-size:12px;color:#aaa;">{style_ref_rel}</div>
  </div>
  <div class="grid">
    {''.join(cards)}
  </div>
  <div id="picked"></div>
<script>
  function pick(k) {{
    const el = document.getElementById('picked');
    el.style.display = 'block';
    el.innerHTML = '<strong>Approved:</strong> ' + k + ' — relay this back to Claude to continue with Phase 2.';
  }}
</script>
</body></html>
"""
    out_path = level_dir / "style_comparison.html"
    out_path.write_text(html)
    print(f"[HTML] {out_path}")


# ---------------------------------------------------------------------------
# Phase stubs (future)
# ---------------------------------------------------------------------------

def phase_2_validate(level: int):
    """Validate items_config.json:
    - Required: states (with at least one anchor), tools_required
    - Optional: trash_overlays, subparts, overlay_effects, style_variants, backgrounds
    - All sources (string or list) must resolve to a declared id or 'style_ref'
    """
    cfg, level_dir = load_level_config(level)
    errs = []

    if not cfg.get("states"):
        errs.append("Missing required 'states' array")
    anchor_id = cfg.get("anchor_state")
    state_ids = {s["id"] for s in cfg.get("states", [])}
    if anchor_id and anchor_id not in state_ids:
        errs.append(f"anchor_state {anchor_id!r} not declared in states[]")

    # Gather declared ids per kind for source resolution
    declared_subparts = {s["id"] for s in cfg.get("subparts", [])}
    declared_tools = {t["id"] for t in cfg.get("tools_required", [])}

    declared_subflows = {sf.get("id") for sf in cfg.get("subflows", []) if isinstance(sf, dict)}

    def check_sources(item, where):
        srcs = _normalise_sources(item)
        for src in srcs:
            if src == "style_ref":
                continue
            if ":" not in src:
                errs.append(f"{where}: malformed source {src!r}")
                continue
            kind, _id = src.split(":", 1)
            if kind == "chain" and _id not in state_ids:
                errs.append(f"{where}: source 'chain:{_id}' references unknown state")
            elif kind == "subpart" and _id not in declared_subparts:
                errs.append(f"{where}: source 'subpart:{_id}' references unknown subpart")
            elif kind == "tool" and _id not in declared_tools:
                errs.append(f"{where}: source 'tool:{_id}' references unknown tool")
            elif kind == "subflow" and _id not in declared_subflows:
                errs.append(f"{where}: source 'subflow:{_id}' references unknown subflow")
            elif kind not in ("chain", "subpart", "tool", "subflow"):
                errs.append(f"{where}: unknown source kind {kind!r}")

    # V4 — validate step_type references against the loaded library
    def check_step_type(item, where):
        st = item.get("step_type")
        if st and STEP_PATTERNS and st not in STEP_PATTERNS:
            errs.append(
                f"{where}: step_type {st!r} not in step_patterns.json. "
                f"Known: {sorted(STEP_PATTERNS)}"
            )

    for cat_key in ("subparts", "overlay_effects", "style_variants",
                    "backgrounds", "trash_overlays"):
        for it in cfg.get(cat_key, []):
            if not isinstance(it, dict):
                continue
            check_sources(it, f"{cat_key}/{it.get('id', '?')}")
            check_step_type(it, f"{cat_key}/{it.get('id', '?')}")
    for t in cfg.get("tools_required", []):
        if isinstance(t, dict):
            check_sources(t, f"tools_required/{t.get('id', '?')}")
            check_step_type(t, f"tools_required/{t.get('id', '?')}")
    for st in cfg.get("states", []):
        check_step_type(st, f"states/{st.get('id', '?')}")
    # V4 — validate subflow definitions
    for sf in cfg.get("subflows", []):
        if not isinstance(sf, dict):
            errs.append(f"subflows: entries must be objects")
            continue
        if not sf.get("id"):
            errs.append(f"subflow missing id")
        if not sf.get("filename"):
            errs.append(f"subflow {sf.get('id','?')!r}: missing filename")
        if not sf.get("from_chain_state"):
            errs.append(f"subflow {sf.get('id','?')!r}: missing from_chain_state")
        elif sf["from_chain_state"] not in state_ids and sf["from_chain_state"] != "style_ref":
            errs.append(f"subflow {sf.get('id','?')!r}: from_chain_state {sf['from_chain_state']!r} not in states (use 'style_ref' to generate from scratch)")

    # V3 field validation
    schema_v = cfg.get("schema_version", "")
    style_mode = cfg.get("style_mode")
    if style_mode and style_mode not in STYLE_MODE_PRESETS:
        errs.append(
            f"unknown style_mode {style_mode!r}. Known presets: "
            f"{sorted(STYLE_MODE_PRESETS)}"
        )

    counts = {
        "states": len(cfg.get("states", [])),
        "trash": len(cfg.get("trash_overlays", [])),
        "subparts": len(cfg.get("subparts", [])),
        "overlay_effects": len(cfg.get("overlay_effects", [])),
        "style_variants": len(cfg.get("style_variants", [])),
        "backgrounds": len(cfg.get("backgrounds", [])),
        "tools_required": len(cfg.get("tools_required", [])),
        "subflows": len(cfg.get("subflows", [])),  # V4
    }

    if errs:
        print(f"[ERR] items_config.json has {len(errs)} issue(s):")
        for e in errs:
            print(f"   - {e}")
        raise SystemExit(1)

    summary = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
    print(f"[OK] items_config.json valid — schema_version={schema_v or 'unset (V2)'}  {summary}")
    print(f"[OK] All sources resolve.")
    print(f"[OK] Default backend: {DEFAULT_BACKEND_NAME}  (override with --backend)")

    # V3 envelope preview — show the prompt prefix the pipeline will inject
    envelope = _build_prompt_envelope(cfg)
    if envelope:
        n_lines = envelope.count("\n") + 1
        print(f"[OK] Prompt envelope active ({n_lines} lines): "
              f"{'contract' if cfg.get('chain_consistency_contract') else ''}"
              f"{', framing' if cfg.get('asmr_framing') else ''}"
              f"{', style_mode=' + style_mode if style_mode else ''}".lstrip(", "))
    else:
        print("[NOTE] No V3 prompt envelope declared (legacy V2 behaviour).")


# ---------------------------------------------------------------------------
# Phase 3 — Backwards chain generation (NB-2 Edit, I2I)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Phase 1b — Sub-flow composite anchor generation (V4)
#
# When a level has a sub-flow (e.g. AC opened, parts arranged on a surface),
# we generate ONE composite-anchor image showing the parent + all detached
# parts in a fixed arrangement. Sub-part state chains then I2I from this
# composite via `source: "subflow:ID"`. This locks the arrangement once and
# inherits it through every sub-part state.
# ---------------------------------------------------------------------------

def phase_1b_subflow_anchors(level: int, subflow_filter: str | None = None,
                             dry_run: bool = False, yes: bool = False,
                             backend_name: str | None = None):
    cfg, level_dir = load_level_config(level)
    staging = level_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)

    subflows = cfg.get("subflows", [])
    if subflow_filter:
        subflows = [sf for sf in subflows if _match_id(sf.get("id", ""), subflow_filter)]
    if not subflows:
        if subflow_filter:
            raise SystemExit(f"[ERR] No subflow matches {subflow_filter!r}.")
        print("[SKIP] No subflows declared for this level.")
        return

    backend = _get_backend(backend_name)
    per_call_cost = backend.cost
    estimate = len(subflows) * per_call_cost
    print("=" * 70)
    print(f"  Phase 1b — Sub-flow composite anchors ({len(subflows)})")
    print(f"  Backend  : {backend.name}  (~${per_call_cost:.3f}/call)")
    for sf in subflows:
        print(f"    {sf['id']:30s} ← I2I from chain:{sf['from_chain_state']}")
    print(f"  Cost est : {len(subflows)} × ${per_call_cost:.3f} = ${estimate:.2f}")
    if dry_run:
        print("  [DRY-RUN] No API calls will be made.")
        print("=" * 70)
        return
    print("=" * 70)

    if not _confirm_cost(estimate, yes):
        print("[ABORT] User declined.")
        return

    if backend.name.startswith("fal_"):
        init_fal(str(PRODUCER_ROOT / ".env"))
    envelope = _build_prompt_envelope(cfg)

    style_ref_rel = cfg["style_reference"]
    style_ref = (level_dir / style_ref_rel).resolve()

    for sf in subflows:
        src_state = sf["from_chain_state"]
        # V5+ — `from_chain_state: "style_ref"` means "generate from scratch via
        # the canonical style reference" (escape hatch for sub-flows that don't
        # have a viable chain-state source).
        if src_state == "style_ref":
            src_path = style_ref
        else:
            src_path = staging / f"{cfg['name']}_{src_state}.png"
        if not src_path.exists():
            raise SystemExit(
                f"[ERR] Subflow {sf['id']!r}: source not found at {src_path}"
            )
        body = (
            "TASK (I2I — generate composite sub-flow anchor):\n"
            f"{sf['prompt']}\n\n"
            "All parts must be arranged in a FIXED layout that will be preserved "
            "across every downstream sub-part state."
        )
        full_prompt = "\n\n".join([envelope, body]) if envelope else body
        try:
            data = backend.edit(src_path, full_prompt)
            out_path = staging / sf["filename"]
            out_path.write_bytes(data)
            _log_cost(level_dir, "1b_subflow", sf["id"], backend.name,
                      per_call_cost, True)
            print(f"[OK] {out_path.name}  ({len(data)//1024} KB)")
        except Exception as e:
            _log_cost(level_dir, "1b_subflow", sf["id"], backend.name, 0, False)
            print(f"[FAIL] {sf['id']}: {e}")
            raise

    print("\n[DONE] Subflow composites generated.")


def phase_3_chain(level: int, state_filter: str | None = None,
                  dry_run: bool = False, yes: bool = False,
                  backend_name: str | None = None):
    cfg, level_dir = load_level_config(level)
    staging = level_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)

    states_by_id = {s["id"]: s for s in cfg["states"]}
    anchor_id = cfg["anchor_state"]

    # Walk backwards from anchor following source_state pointers
    order = []
    visited = {anchor_id}
    current_seeds = [anchor_id]
    while current_seeds:
        next_seeds = []
        for sid in current_seeds:
            for s in cfg["states"]:
                if s.get("source_state") == sid and s["id"] not in visited:
                    order.append(s["id"])
                    visited.add(s["id"])
                    next_seeds.append(s["id"])
        current_seeds = next_seeds

    if not order:
        raise SystemExit("[ERR] No chain to generate (anchor only).")

    # Apply --state filter
    targets = [sid for sid in order if _match_id(sid, state_filter)]
    if state_filter and not targets:
        raise SystemExit(f"[ERR] No states match filter: {state_filter!r}. Available: {order}")

    anchor_path = staging / f"{cfg['name']}_{anchor_id}.png"
    if not anchor_path.exists():
        raise SystemExit(f"[ERR] Anchor not staged: {anchor_path}")

    backend = _get_backend(backend_name)
    per_call_cost = backend.cost
    estimate = len(targets) * per_call_cost
    print("=" * 70)
    print(f"  Phase 3 — Backwards chain (level {level})")
    print(f"  Backend  : {backend.name}  (~${per_call_cost:.3f}/call)")
    print(f"  Full order: {anchor_id} → " + " → ".join(order))
    if state_filter:
        print(f"  Filter   : {state_filter!r} → {targets}")
    print(f"  Will gen : {len(targets)} state(s)")
    print(f"  Cost est : {len(targets)} × ${per_call_cost:.3f} = ${estimate:.2f}")
    if dry_run:
        print("  [DRY-RUN] No API calls will be made.")
        print("=" * 70)
        return
    print("=" * 70)

    if not _confirm_cost(estimate, yes):
        print("[ABORT] User declined.")
        return

    # Init Fal only if the chosen backend is Fal-based — Google needs no init
    if backend.name.startswith("fal_"):
        init_fal(str(PRODUCER_ROOT / ".env"))

    # V2.1 change: single-image I2I from the previous (cleaner) state — NO
    # anchor lock. The anchor was preventing per-step changes (e.g. hole
    # placements) from carrying over because the model would snap back to the
    # anchor silhouette.
    # V3 change: prompt envelope (consistency contract + ASMR framing + style)
    # is auto-prepended for all chain states via _build_chain_prompt.

    regen_q = _load_regen_queue(level_dir)
    if regen_q:
        print(f"[REGEN-QUEUE] {len(regen_q)} comment(s) loaded from regen_queue.json")

    for state_id in targets:
        s = states_by_id[state_id]
        src_id = s["source_state"]
        src_path = staging / f"{cfg['name']}_{src_id}.png"
        if not src_path.exists():
            raise SystemExit(f"[ERR] Source state not generated: {src_path}")
        out_path = staging / f"{cfg['name']}_{state_id}.png"

        comment = regen_q.get(state_id, "")
        # V6: extra reference images via spec.reference_images (list of paths
        # relative to level_dir). Passed alongside src_path as multi-source.
        extra_refs = []
        for rel in ((s.get("spec") or {}).get("reference_images") or []):
            p = (level_dir / rel).resolve()
            if p.exists():
                extra_refs.append(p)
            else:
                print(f"  [WARN] reference_image not found: {p}")
        # Order: extra refs FIRST (style targets), src_path LAST (the canvas).
        # Gemini tends to weight the last image as the primary edit subject.
        image_arg = [*extra_refs, src_path] if extra_refs else src_path
        print(f"\n[CHAIN] {src_id} → {state_id} via {backend.name}"
              f"{' [+regen comment]' if comment else ''}"
              f"{f' [+{len(extra_refs)} ref(s)]' if extra_refs else ''}")
        full_prompt = _build_chain_prompt(cfg, s, regen_comment=comment, level_dir=level_dir)
        try:
            data = backend.edit(image_arg, full_prompt)
            out_path.write_bytes(data)
            _log_cost(level_dir, "3_chain", state_id, backend.name, per_call_cost, True)
            print(f"[OK] {out_path.name}  ({len(data)//1024} KB)")
        except Exception as e:
            _log_cost(level_dir, "3_chain", state_id, backend.name, 0, False)
            print(f"[FAIL] {state_id}: {e}")
            raise

    print("\n[DONE] Chain generated.")


# ---------------------------------------------------------------------------
# Phase 3b — Trash overlay sprites (NB-2 T2I)
# ---------------------------------------------------------------------------

def phase_3b_trash(level: int, sprite_filter: str | None = None,
                   dry_run: bool = False, yes: bool = False,
                   backend_name: str | None = None):
    cfg, level_dir = load_level_config(level)
    staging = level_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)

    sprite_categories = [
        ("trash", cfg.get("trash_overlays", [])),
        ("subpart", cfg.get("subparts", [])),
        ("overlay", cfg.get("overlay_effects", [])),
        ("style_variant", cfg.get("style_variants", [])),
        ("background", cfg.get("backgrounds", [])),
    ]
    # Apply --sprite filter to each category
    filtered = [(lab, _filter_items(items, "id", sprite_filter))
                for lab, items in sprite_categories]
    total = sum(len(items) for _, items in filtered)

    if total == 0:
        if sprite_filter:
            available = [i["id"] for _, items in sprite_categories for i in items]
            raise SystemExit(f"[ERR] No sprites match {sprite_filter!r}. Available: {available}")
        print("[SKIP] No independent sprites declared for this level.")
        return

    backend = _get_backend(backend_name)
    per_call_cost = backend.cost
    estimate = total * per_call_cost
    print("=" * 70)
    print(f"  Phase 3b — Independent sprites ({total} total)")
    print(f"  Backend  : {backend.name}  (~${per_call_cost:.3f}/call)")
    for label, items in filtered:
        if items:
            print(f"    {label}: {len(items)} — {[i['id'] for i in items]}")
    if sprite_filter:
        print(f"  Filter   : {sprite_filter!r}")
    print(f"  Cost est : {total} × ${per_call_cost:.3f} = ${estimate:.2f}")
    if dry_run:
        print("  [DRY-RUN] No API calls will be made.")
        print("=" * 70)
        return
    print("=" * 70)

    if not _confirm_cost(estimate, yes):
        print("[ABORT] User declined.")
        return

    if backend.name.startswith("fal_"):
        init_fal(str(PRODUCER_ROOT / ".env"))

    style_ref_rel = cfg["style_reference"]
    style_ref = (level_dir / style_ref_rel).resolve()

    # V5 — source resolver returns LOCAL PATHS (backend handles upload itself)
    path_cache: dict[str, Path] = {"style_ref": style_ref}

    def resolve_source_path(source: str) -> tuple[Path, str]:
        """Resolve a single source spec to (local Path, label).
        Supports: style_ref, chain:STATE_ID, subpart:SUBPART_ID,
                  tool:TOOL_ID, subflow:SUBFLOW_ID
        """
        if source == "style_ref":
            return style_ref, "style_ref"
        if source in path_cache:
            return path_cache[source], source
        kind, _id = source.split(":", 1)
        if kind == "chain":
            path = staging / f"{cfg['name']}_{_id}.png"
        elif kind == "subpart":
            path = None
            for sp in cfg.get("subparts", []):
                if sp["id"] == _id:
                    path = staging / sp["filename"]
                    break
            if path is None:
                raise SystemExit(f"[ERR] subpart not found in config: {_id}")
        elif kind == "tool":
            tools_dir = PROJECT_DIR / "tools"
            path = None
            for t in cfg.get("tools_required", []):
                if t["id"] == _id:
                    path = tools_dir / t["filename"]
                    break
            if path is None:
                raise SystemExit(f"[ERR] tool not found in config: {_id}")
        elif kind == "subflow":
            path = None
            for sf in cfg.get("subflows", []):
                if sf["id"] == _id:
                    path = staging / sf["filename"]
                    break
            if path is None:
                raise SystemExit(f"[ERR] subflow not found in config: {_id}")
        else:
            raise ValueError(f"Unknown source kind: {kind}")
        if not path.exists():
            raise SystemExit(f"[ERR] source file not staged: {path}")
        path_cache[source] = path
        return path, source

    regen_q = _load_regen_queue(level_dir)
    if regen_q:
        print(f"[REGEN-QUEUE] {len(regen_q)} comment(s) loaded from regen_queue.json")

    for label, items in filtered:
        for s in items:
            sources = _normalise_sources(s)
            has_source = bool(sources)
            if has_source:
                resolved = [resolve_source_path(src) for src in sources]
                paths = [p for p, _ in resolved]
                labels = ",".join(l for _, l in resolved)
                image_arg = paths if len(paths) > 1 else paths[0]
            else:
                image_arg = style_ref
                labels = "style_ref"
            comment = regen_q.get(s["id"], "")
            i2i_prompt = _build_sprite_prompt(cfg, s, has_source, regen_comment=comment, level_dir=level_dir)
            print(f"\n[{label.upper()}] {s['id']} via {backend.name} (from {labels})"
                  f"{' [+regen comment]' if comment else ''}")
            try:
                data = backend.edit(image_arg, i2i_prompt)
                out_path = staging / s["filename"]
                out_path.write_bytes(data)
                _log_cost(level_dir, f"3b_{label}", s["id"], backend.name,
                          per_call_cost, True)
                print(f"[OK] {out_path.name}  ({len(data)//1024} KB)")
            except Exception as e:
                _log_cost(level_dir, f"3b_{label}", s["id"], backend.name, 0, False)
                print(f"[FAIL] {s['id']}: {e}")
                raise

    print("\n[DONE] Independent sprites generated.")


# ---------------------------------------------------------------------------
# Phase 4 — Tool sprites (NB-2 T2I, dedup against manifest)
# ---------------------------------------------------------------------------

def phase_4_tools(level: int, tool_filter: str | None = None,
                  dry_run: bool = False, yes: bool = False, force: bool = False,
                  backend_name: str | None = None):
    cfg, level_dir = load_level_config(level)
    tools_dir = PROJECT_DIR / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = tools_dir / "tools_manifest.json"
    manifest = json.loads(manifest_path.read_text() or "{}")

    needed = cfg["tools_required"]
    # Apply --tool filter (overrides cache check so user can force regen by ID)
    if tool_filter:
        needed = _filter_items(needed, "id", tool_filter)
        if not needed:
            raise SystemExit(f"[ERR] No tools match {tool_filter!r}.")
        # With explicit filter, regen even if cached (user clearly wants that one)
        to_gen = [t for t in needed if not t.get("cached") or force]
        skipped = [t["id"] for t in needed if t.get("cached") and not force]
    else:
        to_gen = [t for t in needed if (force or t["id"] not in manifest) and not t.get("cached")]
        skipped = [t["id"] for t in needed if (t["id"] in manifest and not force) or t.get("cached")]

    backend = _get_backend(backend_name)
    per_call_cost = backend.cost
    estimate = len(to_gen) * per_call_cost
    print("=" * 70)
    print(f"  Phase 4 — Tool sprites ({len(to_gen)} to gen, {len(skipped)} cached)")
    print(f"  Backend  : {backend.name}  (~${per_call_cost:.3f}/call)")
    if skipped:
        print(f"  Cached: {', '.join(skipped)}")
    if to_gen:
        print(f"  To gen: {[t['id'] for t in to_gen]}")
    if tool_filter:
        print(f"  Filter: {tool_filter!r}")
    print(f"  Cost est : {len(to_gen)} × ${per_call_cost:.3f} = ${estimate:.2f}")
    if dry_run:
        print("  [DRY-RUN] No API calls will be made.")
        print("=" * 70)
        return
    print("=" * 70)

    if not to_gen:
        print("[DONE] All tools already in manifest. Nothing to generate.")
        return

    if not _confirm_cost(estimate, yes):
        print("[ABORT] User declined.")
        return

    if backend.name.startswith("fal_"):
        init_fal(str(PRODUCER_ROOT / ".env"))

    style_ref_rel = cfg["style_reference"]
    style_ref = (level_dir / style_ref_rel).resolve()

    orientation_rule = cfg.get("tool_orientation_rule", "")
    staging = level_dir / "staging"
    path_cache: dict[str, Path] = {"style_ref": style_ref}

    def resolve_tool_source_path(source: str) -> tuple[Path, str]:
        """Supports: 'style_ref', 'chain:STATE_ID', 'tool:TOOL_ID'."""
        if source == "style_ref":
            return style_ref, "style_ref"
        if source in path_cache:
            return path_cache[source], source
        kind, _id = source.split(":", 1)
        if kind == "chain":
            path = staging / f"{cfg['name']}_{_id}.png"
        elif kind == "tool":
            path = None
            for tt in cfg.get("tools_required", []):
                if tt["id"] == _id:
                    path = tools_dir / tt["filename"]
                    break
            if path is None:
                raise SystemExit(f"[ERR] tool not found in config: {_id}")
        else:
            raise ValueError(f"Unsupported tool source kind: {kind}")
        if not path.exists():
            raise SystemExit(f"[ERR] tool source not staged: {path}")
        path_cache[source] = path
        return path, source

    regen_q = _load_regen_queue(level_dir)
    if regen_q:
        print(f"[REGEN-QUEUE] {len(regen_q)} comment(s) loaded from regen_queue.json")

    for t in to_gen:
        sources = _normalise_sources(t)
        has_source = bool(sources)
        if has_source:
            resolved = [resolve_tool_source_path(src) for src in sources]
            paths = [p for p, _ in resolved]
            labels = ",".join(l for _, l in resolved)
            image_arg = paths if len(paths) > 1 else paths[0]
        else:
            image_arg = style_ref
            labels = "style_ref"
        comment = regen_q.get(t["id"], "")
        i2i_prompt = _build_tool_prompt(cfg, t, has_source, regen_comment=comment, level_dir=level_dir)
        print(f"\n[TOOL] {t['id']} via {backend.name} (from {labels})"
              f"{' [+regen comment]' if comment else ''}")
        try:
            data = backend.edit(image_arg, i2i_prompt)
            out_path = tools_dir / t["filename"]
            out_path.write_bytes(data)
            manifest[t["id"]] = {
                "filename": t["filename"],
                "first_used_level": level,
                "prompt": t["prompt_t2i"],
                "method": f"i2i_from_{labels}_via_{backend.name}",
            }
            manifest_path.write_text(json.dumps(manifest, indent=2))
            _log_cost(level_dir, "4_tools", t["id"], backend.name, per_call_cost, True)
            print(f"[OK] {out_path.name}  ({len(data)//1024} KB)")
        except Exception as e:
            _log_cost(level_dir, "4_tools", t["id"], backend.name, 0, False)
            print(f"[FAIL] {t['id']}: {e}")
            raise

    print("\n[DONE] Tool sprites generated, manifest updated.")


# ---------------------------------------------------------------------------
# Phase 5 — Post-processing (rembg + alpha 128 + tight crop)
# ---------------------------------------------------------------------------

def phase_5_postprocess(level: int):
    cfg, level_dir = load_level_config(level)
    staging = level_dir / "staging"
    final = level_dir / "final"
    final.mkdir(parents=True, exist_ok=True)
    tools_dir = PROJECT_DIR / "tools"

    # Pick bg-removal algorithm based on the level's declared bg colour.
    # Green → HSV chroma key. Grey → rembg local. Default fallback: rembg local.
    bg_color = cfg.get("bg_color", "#808080")
    chain_remover = _select_bg_remover(bg_color)
    sprite_remover = chain_remover  # subparts/trash share the chain bg
    print("=" * 70)
    print(f"  Phase 5 — Post-processing (alpha 128 + tight crop)")
    print(f"  bg_color: {bg_color}  →  remover: {chain_remover.__name__}")
    print("=" * 70)

    # --- Chain states ---
    for s in cfg["states"]:
        src = staging / f"{cfg['name']}_{s['id']}.png"
        if not src.exists():
            print(f"[SKIP] {src.name} missing")
            continue
        dst = final / f"{cfg['name']}_{s['id']}.png"
        print(f"\n[POST] chain {s['id']}")
        chain_remover(str(src), str(dst))
        r = clean_and_crop(str(dst))
        print(f"[OK] {dst.name} {r['orig']} → {r['new']}")

    # --- Independent sprites: trash, subparts, overlay effects, style variants ---
    for cat_key in ("trash_overlays", "subparts", "overlay_effects", "style_variants"):
        for s in cfg.get(cat_key, []):
            src = staging / s["filename"]
            if not src.exists():
                print(f"[SKIP] {src.name} missing")
                continue
            dst = final / s["filename"]
            print(f"\n[POST] {cat_key[:-1] if cat_key.endswith('s') else cat_key} {s['id']}")
            sprite_remover(str(src), str(dst))
            r = clean_and_crop(str(dst))
            print(f"[OK] {dst.name} {r['orig']} → {r['new']}")

    # --- Backgrounds: opaque scene plates, NO rembg ---
    import shutil
    for s in cfg.get("backgrounds", []):
        src = staging / s["filename"]
        if not src.exists():
            print(f"[SKIP] {src.name} missing")
            continue
        dst = final / s["filename"]
        print(f"\n[POST] background {s['id']} (no rembg, opaque scene plate)")
        shutil.copy2(src, dst)
        print(f"[OK] {dst.name}")

    # --- Tools (in tools_dir, postprocess in place; tools share across levels) ---
    tools_final = tools_dir / "final"
    tools_final.mkdir(parents=True, exist_ok=True)
    for t in cfg["tools_required"]:
        if t.get("cached"):
            print(f"[CACHED] tool {t['id']} (declared cached)")
            continue
        src = tools_dir / t["filename"]
        if not src.exists():
            print(f"[SKIP] tool {src.name} missing")
            continue
        dst = tools_final / t["filename"]
        if dst.exists():
            print(f"[CACHED] tool {t['id']} already processed")
            continue
        print(f"\n[POST] tool {t['id']}")
        remove_bg_local(str(src), str(dst))
        r = clean_and_crop(str(dst))
        print(f"[OK] {dst.name} {r['orig']} → {r['new']}")

    print("\n[DONE] Post-processing complete.")


# ---------------------------------------------------------------------------
# Phase 6 — Review HTML (per-asset approve/regen gate)
# ---------------------------------------------------------------------------

def phase_6_review(level: int):
    cfg, level_dir = load_level_config(level)
    final = level_dir / "final"
    tools_final = PROJECT_DIR / "tools" / "final"

    def _verdict_controls(asset_id: str) -> str:
        """V4 — Approve / Reject / Regen-with-comment three-state radio + comment input."""
        return f"""
          <div class="verdict-row" data-id="{asset_id}">
            <label><input type="radio" name="v_{asset_id}" value="approve" data-id="{asset_id}" checked> ✓ Approve</label>
            <label><input type="radio" name="v_{asset_id}" value="reject" data-id="{asset_id}"> ✗ Reject</label>
            <label><input type="radio" name="v_{asset_id}" value="regen" data-id="{asset_id}"> 🔄 Regen</label>
            <input type="text" class="regen-comment" data-id="{asset_id}" placeholder="comment for regen…" disabled>
          </div>"""

    chain_cards = []
    for s in cfg["states"]:
        f = final / f"{cfg['name']}_{s['id']}.png"
        if not f.exists():
            continue
        tool_txt = f"tool: <code>{s['tool']}</code>" if s.get("tool") else "<em>final, no tool</em>"
        chain_cards.append(f"""
        <div class="card" data-id="{s['id']}">
          <div class="head">{s['id']}</div>
          <img src="final/{f.name}" alt="{s['id']}">
          <div class="meta">{tool_txt}</div>
          {_verdict_controls(s['id'])}
        </div>""")

    def _sprite_cards(items):
        cards = []
        for t in items:
            f = final / t["filename"]
            if not f.exists():
                continue
            cards.append(f"""
        <div class="card" data-id="{t['id']}">
          <div class="head">{t['id']}</div>
          <img src="final/{f.name}" alt="{t['id']}">
          {_verdict_controls(t['id'])}
        </div>""")
        return cards

    trash_cards = _sprite_cards(cfg.get("trash_overlays", []))
    subpart_cards = _sprite_cards(cfg.get("subparts", []))
    overlay_cards = _sprite_cards(cfg.get("overlay_effects", []))
    style_cards = _sprite_cards(cfg.get("style_variants", []))
    bg_cards = _sprite_cards(cfg.get("backgrounds", []))

    tool_cards = []
    for t in cfg["tools_required"]:
        f = tools_final / t["filename"]
        if not f.exists():
            continue
        rel = __import__("os").path.relpath(f, level_dir)
        tool_cards.append(f"""
        <div class="card" data-id="{t['id']}">
          <div class="head">{t['id']}</div>
          <img src="{rel}" alt="{t['id']}">
          {_verdict_controls(t['id'])}
        </div>""")

    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Shine It — Review Level {cfg['level']} ({cfg['name']})</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background:#1e1e1e; color:#eee; margin:0; padding:24px; }}
  h1 {{ margin:0 0 6px 0; }} h2 {{ margin-top:32px; }}
  .sub {{ color:#999; margin-bottom:12px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(220px,1fr)); gap:16px; }}
  .card {{ background:#2a2a2a; border-radius:8px; padding:12px; }}
  .card .head {{ font-weight:600; margin-bottom:8px; font-size:13px; }}
  .card img {{ width:100%;
    background: repeating-conic-gradient(#444 0% 25%, #555 0% 50%) 50% / 24px 24px;
    border-radius:4px; cursor:zoom-in; }}
  .card img:hover {{ outline:2px solid #4a8; }}
  .meta {{ font-size:12px; color:#aaa; margin-top:8px; }}
  .verdict-row {{ display:flex; flex-direction:column; gap:4px; margin-top:8px; font-size:12px; user-select:none; }}
  .verdict-row label {{ display:flex; align-items:center; gap:6px; cursor:pointer; }}
  .verdict-row input[type=text] {{
    width:100%; padding:5px 6px; background:#111; color:#eee; border:1px solid #444;
    border-radius:3px; font-size:11px; font-family: inherit;
  }}
  .verdict-row input[type=text]:disabled {{ opacity:0.4; cursor:not-allowed; }}
  .card.v-approve {{ outline:2px solid #3a7; }}
  .card.v-reject {{ outline:2px solid #d44; opacity:0.55; }}
  .card.v-regen {{ outline:2px solid #d90; }}
  .toolbar {{ position:sticky; top:0; background:#1e1e1e; padding:12px 0; margin-bottom:8px; z-index:10; border-bottom:1px solid #333; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
  .toolbar button {{ background:#3a7; color:#fff; border:0; padding:8px 14px; border-radius:4px; cursor:pointer; font-size:13px; }}
  .toolbar button:hover {{ background:#4b8; }}
  .toolbar .secondary {{ background:#555; }}
  .toolbar .secondary:hover {{ background:#666; }}
  .toolbar .danger {{ background:#a44; }}
  .toolbar .warning {{ background:#a73; }}
  #count {{ color:#aaa; font-size:12px; }}
</style></head>
<body>
  <h1>Shine It — Level {cfg['level']} ({cfg['name']}) Review</h1>
  <div class="sub">
    Mark each asset as <strong>Approve</strong> (✓), <strong>Reject</strong> (✗), or <strong>Regen</strong> (🔄, with optional comment).
    Click <strong>Save verdicts</strong> to download two files: <code>approved_ids.json</code> (used by Phase 7 <code>--only-approved</code>)
    and <code>regen_queue.json</code> (read by Phases 3/3b/4 to apply comments to the prompts on next regen).
    Drop both files in this directory before re-running the pipeline.
    {_agent_banner_html(level_dir)}
  </div>
  <div class="toolbar">
    <button onclick="saveAll()">💾 Save verdicts → approved_ids.json + regen_queue.json</button>
    <button class="secondary" onclick="setAll('approve')">✓ All approve</button>
    <button class="warning" onclick="setAll('regen')">🔄 All regen</button>
    <button class="danger" onclick="setAll('reject')">✗ All reject</button>
    <span id="count"></span>
  </div>

  <h2>Chain ({len(chain_cards)} states)</h2>
  <div class="grid">{''.join(chain_cards)}</div>

  <h2>Trash overlays ({len(trash_cards)})</h2>
  <div class="grid">{''.join(trash_cards)}</div>

  <h2>Sub-parts ({len(subpart_cards)})</h2>
  <div class="grid">{''.join(subpart_cards)}</div>

  <h2>Overlay effects ({len(overlay_cards)})</h2>
  <div class="grid">{''.join(overlay_cards)}</div>

  <h2>Style variants ({len(style_cards)})</h2>
  <div class="grid">{''.join(style_cards)}</div>

  <h2>Backgrounds ({len(bg_cards)}) — opaque scene plates</h2>
  <div class="grid">{''.join(bg_cards)}</div>

  <h2>Tools ({len(tool_cards)})</h2>
  <div class="grid">{''.join(tool_cards)}</div>

<script>
  const $$ = sel => document.querySelectorAll(sel);

  function applyCardClass(id, verdict) {{
    const card = document.querySelector('.card[data-id="' + CSS.escape(id) + '"]');
    if (!card) return;
    card.classList.remove('v-approve', 'v-reject', 'v-regen');
    card.classList.add('v-' + verdict);
    const commentInput = card.querySelector('.regen-comment');
    if (commentInput) commentInput.disabled = (verdict !== 'regen');
  }}

  function currentVerdict(id) {{
    const checked = document.querySelector('input[name="v_' + CSS.escape(id) + '"]:checked');
    return checked ? checked.value : 'approve';
  }}

  function updateCount() {{
    const ids = [...new Set([...$$('.card')].map(c => c.dataset.id))];
    let a=0, r=0, g=0;
    ids.forEach(id => {{
      const v = currentVerdict(id);
      if (v === 'approve') a++;
      else if (v === 'reject') r++;
      else if (v === 'regen') g++;
    }});
    document.getElementById('count').textContent =
      `${{a}} approve · ${{g}} regen · ${{r}} reject · ${{ids.length}} total`;
  }}

  function setAll(verdict) {{
    [...new Set([...$$('.card')].map(c => c.dataset.id))].forEach(id => {{
      const radio = document.querySelector('input[name="v_' + CSS.escape(id) + '"][value="' + verdict + '"]');
      if (radio) {{ radio.checked = true; applyCardClass(id, verdict); }}
    }});
    updateCount();
  }}

  function download(name, payload) {{
    const blob = new Blob([JSON.stringify(payload, null, 2)], {{type:'application/json'}});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
  }}

  function saveAll() {{
    const ids = [...new Set([...$$('.card')].map(c => c.dataset.id))];
    const approved = [];
    const regen = [];
    const rejected = [];
    ids.forEach(id => {{
      const v = currentVerdict(id);
      if (v === 'approve') approved.push(id);
      else if (v === 'reject') rejected.push(id);
      else if (v === 'regen') {{
        const card = document.querySelector('.card[data-id="' + CSS.escape(id) + '"]');
        const comment = card?.querySelector('.regen-comment')?.value || '';
        regen.push({{ id: id, comment: comment.trim() }});
      }}
    }});
    const meta = {{ level: {cfg['level']}, name: "{cfg['name']}", saved_at: new Date().toISOString() }};
    download('approved_ids.json', {{ ...meta, approved: approved }});
    download('regen_queue.json', {{ ...meta, regen: regen, rejected: rejected }});
  }}

  // Wire up radio change handlers
  $$('input[type=radio][name^=v_]').forEach(r => {{
    r.addEventListener('change', e => {{
      applyCardClass(e.target.dataset.id, e.target.value);
      updateCount();
    }});
  }});

  // Initial state — every card defaults to approve, paint the outline
  [...new Set([...$$('.card')].map(c => c.dataset.id))].forEach(id => applyCardClass(id, 'approve'));
  updateCount();
</script>
</body></html>
"""
    out = level_dir / "review_chain.html"
    out.write_text(html)
    print(f"[HTML] {out}")


# ---------------------------------------------------------------------------
# Phase 7 — Promote final → approved
# ---------------------------------------------------------------------------

def phase_7_promote(level: int, only_approved: bool = False, dry_run: bool = False):
    """Promote files from final/ → approved/.

    Default: copy every PNG in final/.
    --only-approved: read approved_ids.json (written by review HTML) and only
    promote files whose id is in the approved list.
    """
    import shutil
    cfg, level_dir = load_level_config(level)
    final = level_dir / "final"
    approved_dir = level_dir / "approved"
    approved_dir.mkdir(parents=True, exist_ok=True)

    if only_approved:
        approval_path = level_dir / "approved_ids.json"
        if not approval_path.exists():
            raise SystemExit(
                f"[ERR] --only-approved set but {approval_path.name} missing.\n"
                f"Open review_chain.html, mark assets as approved, then click "
                f"'Save approvals' before running Phase 7 --only-approved."
            )
        approved_ids = set(json.loads(approval_path.read_text()).get("approved", []))
        # Map asset id → filename via the config
        id_to_file: dict[str, str] = {}
        for s in cfg.get("states", []):
            id_to_file[s["id"]] = f"{cfg['name']}_{s['id']}.png"
        for cat_key in ("trash_overlays", "subparts", "overlay_effects",
                        "style_variants", "backgrounds"):
            for it in cfg.get(cat_key, []):
                id_to_file[it["id"]] = it["filename"]
        targets = [id_to_file[aid] for aid in approved_ids if aid in id_to_file]
        missing_ids = [aid for aid in approved_ids if aid not in id_to_file]
        if missing_ids:
            print(f"[WARN] {len(missing_ids)} approved ID(s) not found in config: {missing_ids}")
        files = [final / fn for fn in targets if (final / fn).exists()]
        skipped = [fn for fn in targets if not (final / fn).exists()]
        if skipped:
            print(f"[WARN] {len(skipped)} approved file(s) not in final/: {skipped}")
        print(f"  Promoting {len(files)} approved file(s) of {len(approved_ids)} approved IDs.")
    else:
        files = sorted(final.glob("*.png"))
        print(f"  Promoting all {len(files)} files in final/.")

    if dry_run:
        for f in files:
            print(f"  [DRY-RUN] would copy {f.name}")
        return

    n = 0
    for f in files:
        shutil.copy2(f, approved_dir / f.name)
        n += 1
    print(f"[OK] Promoted {n} files → {approved_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Shine It Items pipeline — V2",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--level", type=int, required=True)
    p.add_argument("--phase", required=True,
                   choices=["1", "1b", "2", "3", "3b", "4", "5", "6", "7"])
    p.add_argument("--subflow", default=None,
                   help="Phase 1b: glob filter for subflow id")
    p.add_argument("--models", default="flux,nb2",
                   help="Phase 1: comma list of {flux, nb2}")
    p.add_argument("--state", default=None,
                   help="Phase 3: glob filter for state id (e.g. 04_coil_foamed or 'cover_*')")
    p.add_argument("--sprite", default=None,
                   help="Phase 3b: glob filter for sprite id (subpart/trash/overlay/style/bg)")
    p.add_argument("--tool", default=None,
                   help="Phase 4: glob filter for tool id")
    p.add_argument("--force", action="store_true",
                   help="Phase 4: regen tool even if cached in manifest")
    p.add_argument("--dry-run", action="store_true",
                   help="Print plan + cost estimate without spending. Free.")
    p.add_argument("--yes", action="store_true",
                   help="Skip the interactive cost-confirmation prompt.")
    p.add_argument("--only-approved", action="store_true",
                   help="Phase 7: only promote files whose ids are in approved_ids.json")
    p.add_argument("--backend", default=None, choices=list(BACKEND_CHOICES),
                   help=f"I2I backend (default: {DEFAULT_BACKEND_NAME}). "
                        f"Choices: {', '.join(BACKEND_CHOICES)}")
    p.add_argument("--learn", action="store_true",
                   help="V6: process regen_queue.json into permanent step_pattern rules. "
                        "Run after a review session to teach the agent.")
    args = p.parse_args()

    if args.learn:
        if _learn is None:
            raise SystemExit("[ERR] prompt_agent unavailable — V6 features disabled.")
        _, level_dir = load_level_config(args.level)
        summary = _learn(level_dir)
        print("=" * 70)
        print(f"  V6 Learn — Level {args.level}")
        print(f"  Entries processed   : {summary.entries_processed}")
        print(f"  Candidates added    : {summary.candidates_added}")
        print(f"  Candidates +1       : {summary.candidates_incremented}")
        print(f"  Rules PROMOTED      : {len(summary.rules_promoted)}")
        for r in summary.rules_promoted:
            print(f"    • [{r['step_type']}.{r['polarity']}] {r['clause']}")
        if summary.skipped:
            print(f"  Skipped             : {len(summary.skipped)}")
            for s in summary.skipped[:5]:
                print(f"    - {s}")
        print("=" * 70)
        return

    if args.phase == "1":
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        phase_1_anchor(args.level, models)
    elif args.phase == "1b":
        phase_1b_subflow_anchors(args.level, subflow_filter=args.subflow,
                                  dry_run=args.dry_run, yes=args.yes,
                                  backend_name=args.backend)
    elif args.phase == "2":
        phase_2_validate(args.level)
    elif args.phase == "3":
        phase_3_chain(args.level, state_filter=args.state,
                      dry_run=args.dry_run, yes=args.yes,
                      backend_name=args.backend)
    elif args.phase == "3b":
        phase_3b_trash(args.level, sprite_filter=args.sprite,
                       dry_run=args.dry_run, yes=args.yes,
                       backend_name=args.backend)
    elif args.phase == "4":
        phase_4_tools(args.level, tool_filter=args.tool,
                      dry_run=args.dry_run, yes=args.yes, force=args.force,
                      backend_name=args.backend)
    elif args.phase == "5":
        phase_5_postprocess(args.level)
    elif args.phase == "6":
        phase_6_review(args.level)
    elif args.phase == "7":
        phase_7_promote(args.level, only_approved=args.only_approved,
                        dry_run=args.dry_run)


if __name__ == "__main__":
    main()
