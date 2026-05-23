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

# Portable package layout (rooted at the package, not the editing repo):
#   <root>/
#     .env                       (FAL_KEY=…)
#     pipeline/shine_it_pipeline.py
#     pipeline/fal_helper.py
#     projects/level_{NN}_*/items_config.json
#     projects/tools/tools_manifest.json
#     references/style_anchor_compact.png
PIPELINE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PIPELINE_DIR.parent / "projects"
PRODUCER_ROOT = PIPELINE_DIR.parent      # where .env lives
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
    clean_and_crop,
    FLUX_PRO_MODEL,
    NANO_BANANA_2_EDIT_MODEL,
    NANO_BANANA_T2I_MODEL,
)


# Per-call price for the model we use. Centralised here so cost telemetry stays
# accurate even if Fal pricing changes.
NB2_EDIT_COST = 0.030
FLUX_PRO_COST = 0.050


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

def phase_1_anchor(level: int, models: list[str]):
    """
    Generate clean plushie anchor via the selected models.
      flux  — FLUX Pro v1.1 (T2I, pure prompt)
      nb2   — Nano-Banana 2 Edit (I2I from style reference)

    Outputs land in {level_dir}/staging/anchor_{model}.png and a
    side-by-side style_comparison.html is built at the end.
    """
    cfg, level_dir = load_level_config(level)
    anchor = get_anchor_state(cfg)
    staging = level_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)

    # Resolve style reference
    style_ref_rel = cfg["style_reference"]
    style_ref = (level_dir / style_ref_rel).resolve()
    if not style_ref.exists():
        raise SystemExit(f"[ERR] style_reference not found: {style_ref}")

    print("=" * 70)
    print(f"  Phase 1 — Anchor generation (level {level})")
    print(f"  State : {anchor['id']}")
    print(f"  Models: {', '.join(models)}")
    print(f"  Output: {staging}")
    print("=" * 70)

    init_fal(str(PRODUCER_ROOT / ".env"))

    outputs = {}

    # --- FLUX Pro v1.1 (T2I) ---
    if "flux" in models:
        print("\n[1/2] FLUX Pro v1.1 — T2I ...")
        prompt = anchor["prompt_t2i_flux"]
        images = generate_image(
            prompt=prompt,
            model=FLUX_PRO_MODEL,
            image_size="square_hd",
            num_inference_steps=28,
            guidance_scale=3.5,
            num_images=1,
        )
        url = images[0]["url"]
        out_path = staging / "anchor_flux.png"
        download_image(url, out_path)
        outputs["flux"] = {
            "path": str(out_path.relative_to(level_dir)),
            "url": url,
            "model": FLUX_PRO_MODEL,
            "mode": "T2I",
            "prompt": prompt,
        }

    # --- Nano-Banana 2 Edit (I2I) ---
    if "nb2" in models:
        print("\n[2/2] Nano-Banana 2 Edit — I2I from style reference ...")
        ref_url = upload_file(str(style_ref))
        prompt = anchor["prompt_i2i_nb2"]
        out_url = nano_banana_edit(
            image_url=ref_url,
            prompt=prompt,
            model=NANO_BANANA_2_EDIT_MODEL,
            aspect_ratio="1:1",
            thinking_level="high",
        )
        out_path = staging / "anchor_nb2.png"
        download_image(out_url, out_path)
        outputs["nb2"] = {
            "path": str(out_path.relative_to(level_dir)),
            "url": out_url,
            "model": NANO_BANANA_2_EDIT_MODEL,
            "mode": "I2I",
            "style_reference": __import__("os").path.relpath(style_ref, level_dir),
            "prompt": prompt,
        }

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
        label = "FLUX Pro v1.1 (T2I)" if key == "flux" else "Nano-Banana 2 Edit (I2I)"
        prompt_html = info["prompt"].replace("<", "&lt;").replace(">", "&gt;")
        cards.append(f"""
        <div class="card">
          <div class="head">{label}</div>
          <img src="{info['path']}" alt="{key}">
          <details><summary>prompt</summary><pre>{prompt_html}</pre></details>
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
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  .card {{ background:#2a2a2a; border-radius:8px; padding:16px; }}
  .card .head {{ font-weight:600; margin-bottom:10px; }}
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
            elif kind not in ("chain", "subpart", "tool"):
                errs.append(f"{where}: unknown source kind {kind!r}")

    for cat_key in ("subparts", "overlay_effects", "style_variants",
                    "backgrounds", "trash_overlays"):
        for it in cfg.get(cat_key, []):
            if not isinstance(it, dict):
                continue  # legacy: lists of plain strings (e.g. L5 style_variants metadata) — skip
            check_sources(it, f"{cat_key}/{it.get('id', '?')}")
    for t in cfg.get("tools_required", []):
        if isinstance(t, dict):
            check_sources(t, f"tools_required/{t.get('id', '?')}")

    counts = {
        "states": len(cfg.get("states", [])),
        "trash": len(cfg.get("trash_overlays", [])),
        "subparts": len(cfg.get("subparts", [])),
        "overlay_effects": len(cfg.get("overlay_effects", [])),
        "style_variants": len(cfg.get("style_variants", [])),
        "backgrounds": len(cfg.get("backgrounds", [])),
        "tools_required": len(cfg.get("tools_required", [])),
    }

    if errs:
        print(f"[ERR] items_config.json has {len(errs)} issue(s):")
        for e in errs:
            print(f"   - {e}")
        raise SystemExit(1)

    summary = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
    print(f"[OK] items_config.json valid — {summary}")
    print(f"[OK] All sources resolve.")


# ---------------------------------------------------------------------------
# Phase 3 — Backwards chain generation (NB-2 Edit, I2I)
# ---------------------------------------------------------------------------

def phase_3_chain(level: int, state_filter: str | None = None,
                  dry_run: bool = False, yes: bool = False):
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

    estimate = len(targets) * NB2_EDIT_COST
    print("=" * 70)
    print(f"  Phase 3 — Backwards chain (level {level})")
    print(f"  Full order: {anchor_id} → " + " → ".join(order))
    if state_filter:
        print(f"  Filter   : {state_filter!r} → {targets}")
    print(f"  Will gen : {len(targets)} state(s)")
    print(f"  Cost est : {len(targets)} × ${NB2_EDIT_COST:.3f} = ${estimate:.2f}")
    if dry_run:
        print("  [DRY-RUN] No API calls will be made.")
        print("=" * 70)
        return
    print("=" * 70)

    if not _confirm_cost(estimate, yes):
        print("[ABORT] User declined.")
        return

    init_fal(str(PRODUCER_ROOT / ".env"))

    anchor_url = upload_file(str(anchor_path))
    style_prefix = cfg.get("style_lock_prefix", "")
    color_const = cfg.get("color_constant", "")

    for state_id in targets:
        s = states_by_id[state_id]
        src_id = s["source_state"]
        src_path = staging / f"{cfg['name']}_{src_id}.png"
        if not src_path.exists():
            raise SystemExit(f"[ERR] Source state not generated: {src_path}")
        out_path = staging / f"{cfg['name']}_{state_id}.png"

        print(f"\n[CHAIN] {src_id} → {state_id} (multi-anchor I2I)")
        src_url = upload_file(str(src_path))
        full_prompt = f"{style_prefix}\n\n{s['prompt']}\n\n{color_const}"
        try:
            out_url = nano_banana_edit(
                image_url=[anchor_url, src_url],
                prompt=full_prompt,
                model=NANO_BANANA_2_EDIT_MODEL,
                aspect_ratio="1:1",
                thinking_level="high",
            )
            download_image(out_url, out_path)
            _log_cost(level_dir, "3_chain", state_id, NANO_BANANA_2_EDIT_MODEL, NB2_EDIT_COST, True)
            print(f"[OK] {out_path.name}")
        except Exception as e:
            _log_cost(level_dir, "3_chain", state_id, NANO_BANANA_2_EDIT_MODEL, 0, False)
            print(f"[FAIL] {state_id}: {e}")
            raise

    print("\n[DONE] Chain generated.")


# ---------------------------------------------------------------------------
# Phase 3b — Trash overlay sprites (NB-2 T2I)
# ---------------------------------------------------------------------------

def phase_3b_trash(level: int, sprite_filter: str | None = None,
                   dry_run: bool = False, yes: bool = False):
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

    estimate = total * NB2_EDIT_COST
    print("=" * 70)
    print(f"  Phase 3b — Independent sprites ({total} total)")
    for label, items in filtered:
        if items:
            print(f"    {label}: {len(items)} — {[i['id'] for i in items]}")
    if sprite_filter:
        print(f"  Filter   : {sprite_filter!r}")
    print(f"  Cost est : {total} × ${NB2_EDIT_COST:.3f} = ${estimate:.2f}")
    if dry_run:
        print("  [DRY-RUN] No API calls will be made.")
        print("=" * 70)
        return
    print("=" * 70)

    if not _confirm_cost(estimate, yes):
        print("[ABORT] User declined.")
        return

    init_fal(str(PRODUCER_ROOT / ".env"))

    style_ref_rel = cfg["style_reference"]
    style_ref = (level_dir / style_ref_rel).resolve()
    ref_url = upload_file(str(style_ref))

    url_cache: dict[str, str] = {"style_ref": ref_url}

    def resolve_source_url(source: str) -> tuple[str, str]:
        """Resolve a single source spec to (url, label).
        Supports:
          "style_ref"        — explicit style reference (V2)
          "chain:STATE_ID"   — staging/{cfg.name}_{STATE_ID}.png
          "subpart:SUBPART_ID" — staging/{subpart.filename}
          "tool:TOOL_ID"     — tools/{tool.filename} (e.g. for tool variants)
        """
        if source == "style_ref":
            return ref_url, "style_ref"
        if source in url_cache:
            return url_cache[source], source
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
        else:
            raise ValueError(f"Unknown source kind: {kind}")
        if not path.exists():
            raise SystemExit(f"[ERR] source file not staged: {path}")
        url = upload_file(str(path))
        url_cache[source] = url
        return url, source

    for label, items in filtered:
        for s in items:
            sources = _normalise_sources(s)
            if sources:
                resolved = [resolve_source_url(src) for src in sources]
                urls = [u for u, _ in resolved]
                labels = ",".join(l for _, l in resolved)
                # Multi-anchor I2I → prompt is self-contained
                i2i_prompt = s["prompt_t2i"]
                image_arg = urls if len(urls) > 1 else urls[0]
            else:
                # Default: style ref subject-replace
                image_arg = ref_url
                labels = "style_ref"
                i2i_prompt = (
                    f"Match the EXACT same art style, render quality, lighting "
                    f"and finish as this reference image. Replace the subject of "
                    f"the reference entirely with: {s['prompt_t2i']}"
                )
            print(f"\n[{label.upper()}] {s['id']} (I2I from {labels})")
            try:
                url = nano_banana_edit(
                    image_url=image_arg,
                    prompt=i2i_prompt,
                    model=NANO_BANANA_2_EDIT_MODEL,
                    aspect_ratio="1:1",
                    thinking_level="high",
                )
                out_path = staging / s["filename"]
                download_image(url, out_path)
                _log_cost(level_dir, f"3b_{label}", s["id"], NANO_BANANA_2_EDIT_MODEL,
                          NB2_EDIT_COST, True)
                print(f"[OK] {out_path.name}")
            except Exception as e:
                _log_cost(level_dir, f"3b_{label}", s["id"], NANO_BANANA_2_EDIT_MODEL,
                          0, False)
                print(f"[FAIL] {s['id']}: {e}")
                raise

    print("\n[DONE] Independent sprites generated.")


# ---------------------------------------------------------------------------
# Phase 4 — Tool sprites (NB-2 T2I, dedup against manifest)
# ---------------------------------------------------------------------------

def phase_4_tools(level: int, tool_filter: str | None = None,
                  dry_run: bool = False, yes: bool = False, force: bool = False):
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

    estimate = len(to_gen) * NB2_EDIT_COST
    print("=" * 70)
    print(f"  Phase 4 — Tool sprites ({len(to_gen)} to gen, {len(skipped)} cached)")
    if skipped:
        print(f"  Cached: {', '.join(skipped)}")
    if to_gen:
        print(f"  To gen: {[t['id'] for t in to_gen]}")
    if tool_filter:
        print(f"  Filter: {tool_filter!r}")
    print(f"  Cost est : {len(to_gen)} × ${NB2_EDIT_COST:.3f} = ${estimate:.2f}")
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

    init_fal(str(PRODUCER_ROOT / ".env"))

    style_ref_rel = cfg["style_reference"]
    style_ref = (level_dir / style_ref_rel).resolve()
    ref_url = upload_file(str(style_ref))

    orientation_rule = cfg.get("tool_orientation_rule", "")
    staging = level_dir / "staging"
    url_cache: dict[str, str] = {"style_ref": ref_url}

    def resolve_tool_source_url(source: str) -> tuple[str, str]:
        """Supports: 'style_ref', 'chain:STATE_ID', 'tool:TOOL_ID'."""
        if source == "style_ref":
            return ref_url, "style_ref"
        if source in url_cache:
            return url_cache[source], source
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
        url = upload_file(str(path))
        url_cache[source] = url
        return url, source

    for t in to_gen:
        sources = _normalise_sources(t)
        if sources:
            resolved = [resolve_tool_source_url(src) for src in sources]
            urls = [u for u, _ in resolved]
            labels = ",".join(l for _, l in resolved)
            i2i_prompt = t["prompt_t2i"]
            image_arg = urls if len(urls) > 1 else urls[0]
        else:
            image_arg = ref_url
            labels = "style_ref"
            i2i_prompt = (
                f"Match the EXACT same art style, render quality, lighting and "
                f"finish as this reference image. Replace the subject of the "
                f"reference entirely with the tool described below.\n\n"
                f"SUBJECT: {t['prompt_t2i']}\n\n"
                f"ORIENTATION RULE (mandatory): {orientation_rule}"
            )
        print(f"\n[TOOL] {t['id']} (I2I from {labels})")
        try:
            url = nano_banana_edit(
                image_url=image_arg,
                prompt=i2i_prompt,
                model=NANO_BANANA_2_EDIT_MODEL,
                aspect_ratio="1:1",
                thinking_level="high",
            )
            out_path = tools_dir / t["filename"]
            download_image(url, out_path)
            manifest[t["id"]] = {
                "filename": t["filename"],
                "first_used_level": level,
                "prompt": t["prompt_t2i"],
                "method": f"i2i_from_{labels}",
            }
            manifest_path.write_text(json.dumps(manifest, indent=2))
            _log_cost(level_dir, "4_tools", t["id"], NANO_BANANA_2_EDIT_MODEL, NB2_EDIT_COST, True)
            print(f"[OK] {out_path.name}")
        except Exception as e:
            _log_cost(level_dir, "4_tools", t["id"], NANO_BANANA_2_EDIT_MODEL, 0, False)
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

    print("=" * 70)
    print(f"  Phase 5 — Post-processing (rembg + alpha 128 + tight crop)")
    print("=" * 70)

    # --- Chain states ---
    for s in cfg["states"]:
        src = staging / f"{cfg["name"]}_{s['id']}.png"
        if not src.exists():
            print(f"[SKIP] {src.name} missing")
            continue
        dst = final / f"{cfg["name"]}_{s['id']}.png"
        print(f"\n[POST] chain {s['id']}")
        # Hybrid is the safety net for plushie on grey #808080
        remove_bg_hybrid(str(src), str(dst))
        r = clean_and_crop(str(dst))
        print(f"[OK] {dst.name} {r['orig']} → {r['new']}")

    # --- Independent sprites: trash, subparts, overlay effects, style variants ---
    # Subparts use plain remove_bg_local (just rembg, no safety net) — the safety
    # nets in hybrid/smart_hybrid keep dust-coloured bg fringes because dust is
    # close in tone to the grey #808080 bg. Trusting rembg's U2Net boundary fully
    # produces the cleanest cutouts for the AC sub-parts.
    for cat_key in ("trash_overlays", "subparts", "overlay_effects", "style_variants"):
        for s in cfg.get(cat_key, []):
            src = staging / s["filename"]
            if not src.exists():
                print(f"[SKIP] {src.name} missing")
                continue
            dst = final / s["filename"]
            print(f"\n[POST] {cat_key[:-1] if cat_key.endswith('s') else cat_key} {s['id']}")
            if cat_key == "subparts":
                remove_bg_local(str(src), str(dst))
            else:
                remove_bg_hybrid(str(src), str(dst))
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
          <label class="approve-row"><input type="checkbox" class="approve-cb" data-id="{s['id']}" checked> Approve</label>
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
          <label class="approve-row"><input type="checkbox" class="approve-cb" data-id="{t['id']}" checked> Approve</label>
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
          <label class="approve-row"><input type="checkbox" class="approve-cb" data-id="{t['id']}" checked> Approve</label>
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
  .approve-row {{ display:flex; align-items:center; gap:6px; margin-top:8px; font-size:12px; color:#cfc; cursor:pointer; user-select:none; }}
  .card:has(.approve-cb:not(:checked)) {{ outline:2px solid #d44; opacity:0.6; }}
  .toolbar {{ position:sticky; top:0; background:#1e1e1e; padding:12px 0; margin-bottom:8px; z-index:10; border-bottom:1px solid #333; display:flex; gap:12px; align-items:center; }}
  .toolbar button {{ background:#3a7; color:#fff; border:0; padding:8px 14px; border-radius:4px; cursor:pointer; font-size:13px; }}
  .toolbar button:hover {{ background:#4b8; }}
  .toolbar .secondary {{ background:#555; }}
  .toolbar .secondary:hover {{ background:#666; }}
  #count {{ color:#aaa; font-size:12px; }}
</style></head>
<body>
  <h1>Shine It — Level {cfg['level']} ({cfg['name']}) Review</h1>
  <div class="sub">Tick assets to approve. Click "Save approvals" to download <code>approved_ids.json</code>. Drop it into this directory, then run <code>phase 7 --only-approved</code>.</div>
  <div class="toolbar">
    <button onclick="saveApprovals()">💾 Save approvals → approved_ids.json</button>
    <button class="secondary" onclick="setAll(true)">✓ Approve all</button>
    <button class="secondary" onclick="setAll(false)">✗ Unapprove all</button>
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
  function updateCount() {{
    const all = $$('.approve-cb');
    const on = [...all].filter(c => c.checked).length;
    document.getElementById('count').textContent = on + ' / ' + all.length + ' approved';
  }}
  function setAll(v) {{ $$('.approve-cb').forEach(c => c.checked = v); updateCount(); }}
  function saveApprovals() {{
    const approved = [...$$('.approve-cb')].filter(c => c.checked).map(c => c.dataset.id);
    const payload = {{ level: {cfg['level']}, name: "{cfg['name']}", approved: approved, saved_at: new Date().toISOString() }};
    const blob = new Blob([JSON.stringify(payload, null, 2)], {{type:'application/json'}});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'approved_ids.json';
    a.click();
    URL.revokeObjectURL(a.href);
  }}
  $$('.approve-cb').forEach(c => c.addEventListener('change', updateCount));
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
                   choices=["1", "2", "3", "3b", "4", "5", "6", "7"])
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
    args = p.parse_args()

    if args.phase == "1":
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        phase_1_anchor(args.level, models)
    elif args.phase == "2":
        phase_2_validate(args.level)
    elif args.phase == "3":
        phase_3_chain(args.level, state_filter=args.state,
                      dry_run=args.dry_run, yes=args.yes)
    elif args.phase == "3b":
        phase_3b_trash(args.level, sprite_filter=args.sprite,
                       dry_run=args.dry_run, yes=args.yes)
    elif args.phase == "4":
        phase_4_tools(args.level, tool_filter=args.tool,
                      dry_run=args.dry_run, yes=args.yes, force=args.force)
    elif args.phase == "5":
        phase_5_postprocess(args.level)
    elif args.phase == "6":
        phase_6_review(args.level)
    elif args.phase == "7":
        phase_7_promote(args.level, only_approved=args.only_approved,
                        dry_run=args.dry_run)


if __name__ == "__main__":
    main()
