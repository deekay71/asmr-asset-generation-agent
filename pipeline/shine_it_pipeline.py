#!/usr/bin/env python3
"""
Shine It Items — Asset Production Pipeline

CLI orchestrator. Phase-based, resumable. Each phase has a hard approval gate
before progressing — no auto-cascade past Phase 1 until user signs off on
style/quality.

Phases:
  0    Project Init wizard — scan refs, confirm style, validate config (interactive)
  0.5  Concept Board — generate single grid-layout overview ($0.05) for stakeholder review
  1    Anchor generation (clean state) — FLUX Pro + Nano-Banana 2 in parallel + auto-select
  2    Chain plan validation + prompt linter + dependency map
  3   Backwards chain generation (state N-1 down to 0)
  3b  Trash overlay generation (independent transparent sprites)
  4   Tool sprite generation (T2I only, dedup against tools_manifest.json)
  5   Post-processing (rembg + alpha 128 + tight crop +2px)
  6   Review HTML (review_chain.html — feedback boxes, drag/drop ref, Send to Claude)
  7   Promote final → approved/

Usage:
  python shine_it_pipeline.py --level 5 --phase 0          # setup wizard
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
# Cost auto-detects from active model.
# Nano-Banana 2 Flash: $0.030/image · Nano-Banana Pro: $0.135/image
FLUX_PRO_COST = 0.050
NB2_EDIT_COST = 0.135 if "nano-banana-pro" in NANO_BANANA_2_EDIT_MODEL else 0.030

_MODEL_MAP = {
    "flash": ("fal-ai/nano-banana-2/edit",     0.030),
    "pro":   ("fal-ai/nano-banana-pro/edit",   0.135),
}


def _maybe_override_model(model_arg: str, yes: bool, phase: str, dry_run: bool):
    """Resolve --model choice → optionally override NANO_BANANA_2_EDIT_MODEL + NB2_EDIT_COST.

    Phases that don't use NB2 I2I (0, 0.5, 2, 5, 6, 7) skip this.
    --model ask + --yes  → silently use 'flash' (safe default).
    --model ask          → interactive picker.
    --model flash|pro    → use specified, print confirmation.
    """
    global NANO_BANANA_2_EDIT_MODEL, NB2_EDIT_COST

    # Phases that don't use NB2 I2I — no need to ask
    if phase in ("0", "0.5", "2", "5", "6", "7"):
        return

    chosen = model_arg
    if chosen == "ask":
        if yes:
            chosen = "flash"  # silent default for --yes
        else:
            print()
            print("─" * 70)
            print("  Chọn model I2I cho phase này:")
            print(f"    [1] Flash — {_MODEL_MAP['flash'][0]}")
            print(f"               ${_MODEL_MAP['flash'][1]:.3f}/img  ·  default sweet spot")
            print(f"    [2] Pro   — {_MODEL_MAP['pro'][0]}")
            print(f"               ${_MODEL_MAP['pro'][1]:.3f}/img  ·  4.5× đắt, chi tiết hơn ở scenes phức tạp")
            print("─" * 70)
            raw = input("  Default [1]: ").strip() or "1"
            chosen = "pro" if raw == "2" else "flash"

    model_id, cost = _MODEL_MAP[chosen]
    if model_id != NANO_BANANA_2_EDIT_MODEL:
        NANO_BANANA_2_EDIT_MODEL = model_id
        NB2_EDIT_COST = cost
        print(f"  → Model override: {chosen} ({model_id}, ${cost:.3f}/img)\n")
    elif chosen != "flash" or dry_run:
        # Print for explicit choices, not silent default
        print(f"  → Model: {chosen} ({model_id}, ${cost:.3f}/img)\n")


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
    from datetime import datetime, timezone
    log_path = level_dir / "cost_log.jsonl"
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    except KeyboardInterrupt:
        print("\n[ABORT] Cancelled by user.")
        return False
    return ans in ("y", "yes")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_level_config(level: int) -> tuple[dict, Path]:
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
# Phase 0 — Project Init wizard
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

def _scan_images(path: Path) -> list[Path]:
    """Return sorted list of image files in a directory (non-recursive)."""
    if not path.exists():
        return []
    return sorted(p for p in path.iterdir() if p.suffix.lower() in _IMAGE_EXTS)


def _prompt_choice(prompt: str, options: list[str], default: int = 0) -> int:
    """Print numbered options and return chosen index (0-based)."""
    print(f"\n{prompt}")
    for i, opt in enumerate(options):
        marker = " (default)" if i == default else ""
        print(f"  [{i+1}] {opt}{marker}")
    while True:
        raw = input("  > ").strip()
        if raw == "" :
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"  Vui lòng nhập số từ 1 đến {len(options)}")


def phase_0_init(level: int, dry_run: bool = False):
    """
    Interactive setup wizard — runs before Phase 1.

    1. Scans references/ and level dir for existing ref images
    2. Asks which style_reference to use
    3. Asks to confirm / modify style description
    4. Asks about any empty required fields
    5. Writes confirmed choices back to items_config.json (unless dry_run)
    """
    import shutil

    cfg, level_dir = load_level_config(level)
    cfg_path = level_dir / "items_config.json"
    refs_root = PRODUCER_ROOT / "references"

    print("=" * 70)
    print(f"  Phase 0 — Project Init (level {level}: {cfg.get('name','?')})")
    if dry_run:
        print(f"  [DRY-RUN] Sẽ hiện thay đổi nhưng KHÔNG ghi file.")
    print("=" * 70)

    # ── 1. Show current config summary ──────────────────────────────────────
    anchor = get_anchor_state(cfg)
    n_states   = len(cfg.get("states", []))
    n_subparts = len(cfg.get("subparts", []))
    n_tools    = len(cfg.get("tools_required", []))
    print(f"\n[CONFIG] items_config.json")
    print(f"  Title      : {cfg.get('title', cfg.get('name'))}")
    print(f"  States     : {n_states}  |  Subparts: {n_subparts}  |  Tools: {n_tools}")
    print(f"  Anchor     : {anchor['id']}")
    print(f"  Style ref  : {cfg.get('style_reference', '(not set)')}")

    # ── 2. Scan for reference images ────────────────────────────────────────
    print(f"\n[REFS] Quét thư mục references/...")
    global_refs  = _scan_images(refs_root)
    level_refs   = _scan_images(level_dir)
    # filter out pipeline artefacts from level dir
    level_refs = [p for p in level_refs
                  if p.suffix.lower() in _IMAGE_EXTS and "staging" not in p.parts
                  and "final" not in p.parts and "approved" not in p.parts]

    all_refs: list[Path] = []
    seen: set[str] = set()
    for p in global_refs + level_refs:
        if p.name not in seen:
            all_refs.append(p)
            seen.add(p.name)

    current_ref_rel  = cfg.get("style_reference", "")
    current_ref_path = (level_dir / current_ref_rel).resolve() if current_ref_rel else None

    if all_refs:
        print(f"  Tìm thấy {len(all_refs)} ảnh ref:")
        for i, p in enumerate(all_refs):
            rel = p.relative_to(PRODUCER_ROOT) if p.is_relative_to(PRODUCER_ROOT) else p
            is_current = (current_ref_path and p.resolve() == current_ref_path)
            flag = "  ← đang dùng" if is_current else ""
            print(f"    [{i+1}] {rel}{flag}")
    else:
        print("  Không tìm thấy ảnh ref nào.")

    # ── 3. Choose style_reference ────────────────────────────────────────────
    ref_options = [str(p.relative_to(PRODUCER_ROOT)
                       if p.is_relative_to(PRODUCER_ROOT) else p)
                   for p in all_refs]
    ref_options += ["Giữ nguyên cấu hình hiện tại", "Nhập path mới từ bên ngoài"]

    current_idx = len(ref_options) - 2  # default = "giữ nguyên"
    if all_refs and current_ref_path:
        for i, p in enumerate(all_refs):
            if p.resolve() == current_ref_path:
                current_idx = i
                break

    chosen_ref_idx = _prompt_choice(
        "Chọn style reference muốn dùng:", ref_options, default=current_idx
    )

    new_ref_path: Path | None = None
    if chosen_ref_idx < len(all_refs):
        new_ref_path = all_refs[chosen_ref_idx]
    elif ref_options[chosen_ref_idx] == "Nhập path mới từ bên ngoài":
        raw = input("  Nhập đường dẫn tuyệt đối đến ảnh: ").strip().strip('"')
        ext_path = Path(raw)
        if not ext_path.exists():
            print(f"[WARN] Không tìm thấy file: {ext_path}. Giữ nguyên ref cũ.")
        else:
            # Copy vào references/
            dest = refs_root / ext_path.name
            shutil.copy2(ext_path, dest)
            new_ref_path = dest
            print(f"  [COPY] {ext_path.name} → references/")

    # ── 4. Style description ─────────────────────────────────────────────────
    print(f"\n[STYLE] Mô tả style hiện tại:")
    print(f"  {cfg.get('style_description', '(chưa có)')}")
    change_style = input("\n  Thay đổi style description? (y/N): ").strip().lower()
    new_style: str | None = None
    if change_style == "y":
        print("  Nhập style mới (để trống để skip từng dòng, nhập 'END' để kết thúc):")
        lines = []
        while True:
            line = input("  > ")
            if line.strip().upper() == "END":
                break
            lines.append(line)
        if lines:
            new_style = " ".join(lines).strip()

    # ── 5. Confirm color_constant if empty ───────────────────────────────────
    if not cfg.get("color_constant", "").strip():
        print("\n[WARN] color_constant chưa được set.")
        cc = input("  Nhập color_constant (mô tả màu base của object): ").strip()
        if cc:
            cfg["color_constant"] = cc

    # ── 6. Check anchor prompt ───────────────────────────────────────────────
    anchor_flux = anchor.get("prompt_t2i_flux", "")
    anchor_nb2  = anchor.get("prompt_i2i_nb2",  "")
    if not anchor_flux.strip():
        print("\n[WARN] Anchor prompt_t2i_flux chưa có.")
        p = input("  Nhập anchor prompt (ngắn gọn): ").strip()
        if p:
            anchor["prompt_t2i_flux"] = p
    if not anchor_nb2.strip():
        anchor["prompt_i2i_nb2"] = anchor.get("prompt_t2i_flux", "")

    # ── 7. Summary & confirm ─────────────────────────────────────────────────
    final_ref_rel = current_ref_rel
    if new_ref_path is not None:
        try:
            final_ref_rel = "../../" + str(new_ref_path.relative_to(PRODUCER_ROOT))
        except ValueError:
            final_ref_rel = str(new_ref_path)

    print("\n" + "─" * 70)
    print("  TÓM TẮT CONFIG")
    print("─" * 70)
    print(f"  Level      : {level} ({cfg.get('name')})")
    print(f"  Style ref  : {final_ref_rel}")
    print(f"  Style desc : {new_style or cfg.get('style_description','(không đổi)')[:80]}...")
    print(f"  States     : {n_states}  |  Subparts: {n_subparts}")
    print(f"  Anchor     : {anchor['id']}")
    print("─" * 70)

    confirm = input("\n  Xác nhận và lưu config? (Y/n): ").strip().lower()
    if confirm == "n":
        print("[ABORT] Không lưu thay đổi.")
        return

    # ── 8. Write changes back ─────────────────────────────────────────────────
    changed = False
    if new_ref_path is not None and final_ref_rel != current_ref_rel:
        cfg["style_reference"] = final_ref_rel
        changed = True
    if new_style:
        cfg["style_description"] = new_style
        changed = True

    # Update anchor in states list
    for i, s in enumerate(cfg["states"]):
        if s["id"] == anchor["id"]:
            cfg["states"][i] = anchor
            break

    if dry_run:
        if changed or not anchor_flux.strip():
            print(f"\n[DRY-RUN] Sẽ cập nhật {cfg_path.name} (chưa ghi).")
        else:
            print("\n[DRY-RUN] Không có thay đổi.")
    elif changed or not anchor_flux.strip():
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        print(f"\n[SAVED] {cfg_path.name} đã cập nhật.")
    else:
        print("\n[OK] Không có thay đổi cần lưu.")

    print("\n[DONE] Phase 0 complete. Tiếp tục với: --phase 1")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Phase 0.5 — Concept Board (mega-prompt T2I overview)
# ---------------------------------------------------------------------------

def phase_0_5_concept(level: int, dry_run: bool = False, yes: bool = False):
    """
    Generate a single overview image showing all chain states + key subparts
    in a grid layout — for stakeholder review BEFORE committing to chain
    generation budget.

    Cost: ~$0.05 (1× FLUX Pro T2I) vs ~$1.31 for full chain.
    """
    cfg, level_dir = load_level_config(level)
    staging = level_dir / "staging"

    print("=" * 70)
    print(f"  Phase 0.5 — Concept Board (mega-prompt T2I)")
    print(f"  Output: {staging / 'concept_board.png'}")
    print(f"  Cost  : ${FLUX_PRO_COST:.3f} (1× FLUX Pro v1.1)")
    print("=" * 70)

    if dry_run:
        print("  [DRY-RUN] Would generate concept board.")
        return

    if not _confirm_cost(FLUX_PRO_COST, yes):
        print("[ABORT] User declined.")
        return

    # Build the mega-prompt from config
    states = [s for s in cfg["states"] if not s.get("is_anchor")]
    anchor = get_anchor_state(cfg)
    subparts = cfg.get("subparts", [])

    style_desc = cfg.get("style_description", "")
    color_const = cfg.get("color_constant", "")
    item_desc = cfg.get("title", cfg.get("name", "item"))

    # Compress each state into a short stage description
    stage_lines = []
    for i, s in enumerate(states):
        prompt = s.get("prompt", "")[:200]
        stage_lines.append(f"Stage {i+1} ({s['id']}): {prompt}")

    # Group subparts by part type
    part_groups: dict[str, list[str]] = {}
    for sp in subparts:
        # cover_dusty → cover
        key = sp["id"].rsplit("_", 1)[0]
        part_groups.setdefault(key, []).append(sp["id"])

    parts_list = ", ".join(part_groups.keys()) or "(no detached subparts)"

    mega_prompt = f"""Create one single image showing a full cleaning progression of the same {item_desc}, from extremely dirty to perfectly clean. Show all stages as a multi-step visual progression, arranged in a clean grid layout on a plain neutral gray background.

OVERALL RULES:
- Use the EXACT same {item_desc} model in every stage.
- Keep the same front view, same proportions, same silhouette across all stages.
- {color_const}
- No people, no hands, no UI, no text, no labels.

MAIN STATES ({len(states)} stages):
{chr(10).join(stage_lines)}

DETACHED PARTS (if applicable): {parts_list} — show these in additional cells.

COMPOSITION: clean multi-panel grid, plain neutral gray background, even spacing, no text, no step numbers.

STYLE: {style_desc}
""".strip()

    print(f"\n[GEN] FLUX Pro · prompt = {len(mega_prompt)} chars")
    init_fal(str(PRODUCER_ROOT / ".env"))
    images = generate_image(
        prompt=mega_prompt,
        model=FLUX_PRO_MODEL,
        image_size="square_hd",
        num_inference_steps=28,
        guidance_scale=3.5,
        num_images=1,
    )
    out = staging / "concept_board.png"
    staging.mkdir(parents=True, exist_ok=True)
    download_image(images[0]["url"], out)
    _log_cost(level_dir, "0.5_concept", "concept_board", FLUX_PRO_MODEL, FLUX_PRO_COST, True)

    # Save prompt for reference
    (staging / "concept_board_prompt.txt").write_text(mega_prompt)

    print(f"\n[OK]  {out}")
    print(f"[PROMPT] {staging / 'concept_board_prompt.txt'}")
    print(f"\n  Mở xem: open {out}")
    print(f"  Approve → tiếp tục Phase 1 cho real production assets.")


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

    # ── Auto-anchor selection ──────────────────────────────────────────────
    _select_anchor(level_dir, staging, outputs, cfg, anchor)


def _select_anchor(level_dir: Path, staging: Path, outputs: dict,
                   cfg: dict, anchor: dict):
    """Interactive picker: ask user to choose between FLUX and NB2 candidate,
    copy chosen one to {name}_{anchor_id}.png so Phase 3 picks it up."""
    final_anchor = staging / f"{cfg['name']}_{anchor['id']}.png"

    print("\n" + "=" * 70)
    print("  Phase 1 complete. Đã sinh 2 anchor candidate.")
    print(f"  Comparison HTML: {(level_dir / 'style_comparison.html')}")
    print("=" * 70)

    if len(outputs) == 1:
        # Only one candidate generated — auto-select
        key = list(outputs.keys())[0]
        src = staging / Path(outputs[key]["path"]).name
        import shutil as _sh
        _sh.copy2(src, final_anchor)
        print(f"\n  [AUTO] Chỉ có 1 candidate → dùng {key} ({src.name}) làm anchor.")
        print(f"  → Set anchor: {final_anchor.name}")
        return

    # Two candidates — ask user
    print("\n  Chọn anchor để dùng cho chain generation:")
    print(f"    [1] flux — {outputs['flux']['path']}  (FLUX Pro v1.1, T2I)")
    print(f"    [2] nb2  — {outputs['nb2']['path']}   (Nano-Banana 2, I2I from style ref)")
    print(f"    [3] Skip — tự copy thủ công sau")
    while True:
        raw = input("  Default [1]: ").strip() or "1"
        if raw in ("1", "2", "3"):
            break
        print("  Nhập 1, 2, hoặc 3")

    if raw == "3":
        print(f"  → Skip. Khi sẵn sàng, chạy:")
        print(f"      cp {staging / 'anchor_flux.png'} {final_anchor}")
        return

    chosen_key = "flux" if raw == "1" else "nb2"
    src = staging / Path(outputs[chosen_key]["path"]).name
    import shutil as _sh
    _sh.copy2(src, final_anchor)
    print(f"\n  ✓ Anchor selected: {chosen_key} → {final_anchor.name}")
    print(f"  → Tiếp tục với: --phase 3")


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

    # Validate style_reference file exists
    style_ref_rel = cfg.get("style_reference", "")
    if style_ref_rel:
        style_ref_path = (level_dir / style_ref_rel).resolve()
        if not style_ref_path.exists():
            errs.append(f"style_reference not found: {style_ref_path}")
    else:
        errs.append("Missing required 'style_reference' field")

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

    # ── Prompt linter — soft warnings for likely-to-fail prompts ─────────────
    _lint_prompts(cfg)

    # ── State dependency map ─────────────────────────────────────────────────
    _print_state_dependency_map(cfg)


def _lint_prompts(cfg: dict):
    """Scan all state/subpart prompts for common failure patterns.
    Soft warnings only — never blocks the build."""
    warnings = []

    def _lint_one(item_kind: str, item_id: str, prompt: str):
        if not prompt or not prompt.strip():
            warnings.append(f"{item_kind}/{item_id}: empty prompt")
            return
        n = len(prompt)
        if n < 80:
            warnings.append(f"{item_kind}/{item_id}: very short prompt ({n} chars) — unlikely to express intent")

        # Detect prompts that mention dirty/foamy/staining without listing regions
        dirt_words = ("dust", "dirty", "stain", "foam", "grime", "rust", "filth")
        has_dirt = any(w in prompt.lower() for w in dirt_words)
        numbered = sum(prompt.count(f"({n})") for n in range(1, 8))
        if has_dirt and numbered == 0 and n > 200:
            warnings.append(
                f"{item_kind}/{item_id}: mentions dirt/foam but no numbered regions "
                f"(1)..(2).. — model may apply change to only one part. "
                f"Tip: liệt kê 3-5 vùng explicit (coil, walls, frame, etc.)"
            )

        # Detect transition-style prompts without "Only change" lock
        transition_words = ("now", "after", "next", "then")
        has_transition = any(f" {w} " in prompt.lower() for w in transition_words)
        if has_transition and "only change" not in prompt.lower() and "keep the" not in prompt.lower():
            warnings.append(
                f"{item_kind}/{item_id}: transition prompt missing 'Only change:' / "
                f"'Keep the EXACT same' lock — risk of identity drift"
            )

        # Detect contradictions
        if "NO " in prompt and prompt.count("NO ") >= 4:
            pass  # ok, lots of negatives is fine
        if "white" in prompt.lower() and "cream" in prompt.lower() and "not cream" not in prompt.lower():
            warnings.append(
                f"{item_kind}/{item_id}: mentions both 'white' and 'cream' — ambiguous tone, "
                f"add 'NOT cream' if pure white needed"
            )

    for s in cfg.get("states", []):
        if s.get("is_anchor"):
            _lint_one("state", s["id"], s.get("prompt_t2i_flux", ""))
        else:
            _lint_one("state", s["id"], s.get("prompt", ""))

    for sp in cfg.get("subparts", []):
        _lint_one("subpart", sp["id"], sp.get("prompt_t2i", ""))

    if warnings:
        print(f"\n[LINT] {len(warnings)} soft warning(s):")
        for w in warnings:
            print(f"   ⚠ {w}")
        print(f"   (Cảnh báo này không block build — chỉ là gợi ý cải thiện chất lượng.)")
    else:
        print(f"[LINT] All prompts pass quality heuristics ✓")


# ---------------------------------------------------------------------------
# Phase 3 — Backwards chain generation (NB-2 Edit, I2I)
# ---------------------------------------------------------------------------

def _backup_existing(out_path: Path):
    """Before overwriting an existing staging PNG, backup it to versions/.
    Keeps last 5 versions per state. Used by Phase 3 + 3b before regen."""
    if not out_path.exists():
        return
    versions_dir = out_path.parent / "versions"
    versions_dir.mkdir(exist_ok=True)
    # Find next version number for this state
    stem = out_path.stem
    existing = sorted(versions_dir.glob(f"{stem}_v*.png"))
    next_n = len(existing) + 1
    backup_path = versions_dir / f"{stem}_v{next_n:02d}.png"
    import shutil as _sh
    _sh.copy2(out_path, backup_path)
    # Trim to last 5 versions
    if len(existing) >= 5:
        for old in existing[:-4]:
            old.unlink()


def _pixel_diff_pct(path_a: Path, path_b: Path) -> float:
    """Return percentage of pixels that differ significantly between two images.
    Used to alert when a chain transition is too subtle (model likely failed)."""
    try:
        from PIL import Image, ImageChops
        a = Image.open(path_a).convert("RGB")
        b = Image.open(path_b).convert("RGB")
        if a.size != b.size:
            b = b.resize(a.size)
        diff = ImageChops.difference(a, b)
        bbox = diff.getbbox()
        if bbox is None:
            return 0.0
        # Compute mean diff intensity normalized to 0-100
        hist = diff.histogram()
        # bins 30+ on each R/G/B channel count as "significantly different"
        total = a.size[0] * a.size[1]
        significant = 0
        for ch in range(3):
            for i in range(30, 256):
                significant += hist[ch * 256 + i]
        return (significant / (total * 3)) * 100
    except Exception:
        return -1.0


def _print_state_dependency_map(cfg: dict):
    """Print a tree-like map of state→state dependencies (source_state graph)."""
    anchor_id = cfg["anchor_state"]
    print(f"\n  Chain dependency map (anchor: {anchor_id})")
    print(f"    {anchor_id} (anchor, T2I + I2I)")
    # Build children: state_id → list of states that have source_state == it
    children: dict[str, list[str]] = {}
    for s in cfg["states"]:
        src = s.get("source_state")
        if src:
            children.setdefault(src, []).append(s["id"])

    def _walk(node_id: str, depth: int):
        kids = children.get(node_id, [])
        for i, k in enumerate(sorted(kids)):
            is_last = (i == len(kids) - 1)
            connector = "└── " if is_last else "├── "
            indent = "    " * depth + connector
            tool = next((s.get("tool") for s in cfg["states"] if s["id"] == k), None)
            tool_note = f"  (uses {tool})" if tool else ""
            print(f"    {indent}{k}{tool_note}")
            _walk(k, depth + 1)

    _walk(anchor_id, 1)

    # Print subpart dependency map
    if cfg.get("subparts"):
        print(f"\n  Subpart dependency map ({len(cfg['subparts'])} sprites):")
        # Group by source type
        for sp in cfg.get("subparts", []):
            src = sp.get("source", "")
            print(f"    {sp['id']:30} ← {src}")


def _detect_complex_states(states_by_id: dict, targets: list[str]) -> set[str]:
    """Heuristic-based detection of risky/complex states for smart mode.
    A state is "complex" if its prompt has many regions or unusual elements."""
    complex_states: set[str] = set()
    for sid in targets:
        prompt = states_by_id[sid].get("prompt", "")
        # Signal 1: long prompt (lots of detail = lots of failure surface)
        if len(prompt) > 800:
            complex_states.add(sid)
            continue
        # Signal 2: explicit numbered regions like (1), (2), (3)
        numbered_regions = sum(prompt.count(f"({n})") for n in range(1, 8))
        if numbered_regions >= 3:
            complex_states.add(sid)
            continue
        # Signal 3: contains "OVERFLOW", "REVEAL", "BURIED" — words signalling drama
        dramatic = ["OVERFLOW", "REVEAL", "BURIED", "COBWEBS",
                    "DOUBLE", "PROTECTED", "HORRIFICALLY"]
        if any(w in prompt for w in dramatic):
            complex_states.add(sid)
    return complex_states


def _interactive_review(out_path: Path, state_id: str, is_last: bool) -> str:
    """After generating one state, ask user what to do.

    Returns:
        'continue' — proceed to next state
        'regen'    — re-run this state (caller re-runs the loop iteration)
        'abort'    — exit Phase 3
    """
    print(f"\n  📸 Generated: {out_path}")
    print(f"     Mở xem:   open {out_path}")
    while True:
        prompt_line = "  Approve? [Y=continue / r=regen this / q=quit"
        if not is_last:
            prompt_line += " / s=skip rest"
        prompt_line += "]: "
        choice = input(prompt_line).strip().lower() or "y"
        if choice in ("y", "yes", ""):  return "continue"
        if choice == "r":               return "regen"
        if choice == "q":               return "abort"
        if choice == "s" and not is_last: return "abort"


def _ask_mode(targets: list[str], complex_set: set[str]) -> str:
    """Ask user which mode to run Phase 3 in."""
    print()
    print("─" * 70)
    print("  Chọn mode chạy:")
    print(f"    [1] Batch     — gen hết {len(targets)} states, review cuối ({len(targets)} × $0.030)")
    print(f"    [2] Waterfall — approve từng state ({len(targets)} pauses)")
    print(f"    [3] Smart     — pause tại {len(complex_set)} complex state(s): "
          f"{sorted(complex_set) if complex_set else '(không có)'}")
    print("─" * 70)
    raw = input("  Default [3]: ").strip() or "3"
    return {"1": "batch", "2": "waterfall", "3": "smart"}.get(raw, "smart")


def phase_3_chain(level: int, state_filter: str | None = None,
                  dry_run: bool = False, yes: bool = False,
                  skip_on_error: bool = False, mode: str = "ask"):
    """
    Generate chain states from anchor backward to dirtiest state.

    mode:
        'batch'     — gen all, no pauses (fastest, default for --yes)
        'waterfall' — pause after each state for user approval
        'smart'     — pause only at heuristically-detected complex states
        'ask'       — prompt user at start to choose
    """
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

    # ── Detect complex states for smart mode ─────────────────────────────────
    complex_set = _detect_complex_states(states_by_id, targets)

    estimate = len(targets) * NB2_EDIT_COST
    print("=" * 70)
    print(f"  Phase 3 — Backwards chain (level {level})")
    print(f"  Full order: {anchor_id} → " + " → ".join(order))
    if state_filter:
        print(f"  Filter   : {state_filter!r} → {targets}")
    print(f"  Will gen : {len(targets)} state(s)")
    print(f"  Cost est : {len(targets)} × ${NB2_EDIT_COST:.3f} = ${estimate:.2f}")
    if skip_on_error:
        print("  [SKIP-ON-ERROR] Failures will be logged and skipped.")
    if dry_run:
        print("  [DRY-RUN] No API calls will be made.")
        if complex_set:
            print(f"  Smart mode would pause at: {sorted(complex_set)}")
        print("=" * 70)
        return
    print("=" * 70)

    # ── Resolve mode ─────────────────────────────────────────────────────────
    if mode == "ask":
        if yes:
            mode = "batch"  # --yes implies non-interactive
        else:
            mode = _ask_mode(targets, complex_set)
    print(f"\n  Mode: {mode}" + (f"  (pause at: {sorted(complex_set)})" if mode == "smart" else ""))

    if not _confirm_cost(estimate, yes):
        print("[ABORT] User declined.")
        return

    init_fal(str(PRODUCER_ROOT / ".env"))

    anchor_url = upload_file(str(anchor_path))
    style_prefix = cfg.get("style_lock_prefix", "")
    color_const = cfg.get("color_constant", "")

    def _generate_one(state_id: str) -> bool:
        """Generate a single state.  Returns True on success."""
        s = states_by_id[state_id]
        src_id = s["source_state"]
        src_path = staging / f"{cfg['name']}_{src_id}.png"
        if not src_path.exists():
            raise SystemExit(f"[ERR] Source state not generated: {src_path}")
        out_path = staging / f"{cfg['name']}_{state_id}.png"

        print(f"\n[CHAIN] {src_id} → {state_id} (multi-anchor I2I)")
        src_url = upload_file(str(src_path))
        full_prompt = f"{style_prefix}\n\n{s['prompt']}\n\n{color_const}"
        # Backup existing version before overwrite
        _backup_existing(out_path)
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
            # Visual diff alert
            diff_pct = _pixel_diff_pct(src_path, out_path)
            if 0 <= diff_pct < 5:
                print(f"[OK] {out_path.name}  ⚠ Diff vs source = {diff_pct:.1f}% (rất nhỏ — model có thể không apply change)")
            elif diff_pct >= 0:
                print(f"[OK] {out_path.name}  (diff vs source = {diff_pct:.1f}%)")
            else:
                print(f"[OK] {out_path.name}")
            return True
        except Exception as e:
            _log_cost(level_dir, "3_chain", state_id, NANO_BANANA_2_EDIT_MODEL, 0, False)
            print(f"[FAIL] {state_id}: {e}")
            if skip_on_error:
                print(f"[SKIP] Continuing to next state (--skip-on-error).")
                return True
            raise

    # ── Main generation loop with optional pauses ───────────────────────────
    i = 0
    while i < len(targets):
        state_id = targets[i]
        if not _generate_one(state_id):
            i += 1
            continue

        # Decide whether to pause
        should_pause = (
            mode == "waterfall"
            or (mode == "smart" and state_id in complex_set)
        )
        if should_pause:
            is_last = (i == len(targets) - 1)
            decision = _interactive_review(
                staging / f"{cfg['name']}_{state_id}.png",
                state_id, is_last,
            )
            if decision == "regen":
                print(f"  → Regen {state_id} (đảm bảo đã sửa prompt trong items_config.json nếu cần).")
                # Reload config in case user edited it
                cfg.clear(); cfg.update(load_level_config(level)[0])
                states_by_id.clear(); states_by_id.update({s["id"]: s for s in cfg["states"]})
                continue  # don't advance i
            if decision == "abort":
                print(f"  → Dừng tại {state_id}. Các state còn lại: {targets[i+1:]}")
                return
        i += 1

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

def phase_5_postprocess(level: int, dry_run: bool = False):
    cfg, level_dir = load_level_config(level)
    staging = level_dir / "staging"
    final = level_dir / "final"
    if not dry_run:
        final.mkdir(parents=True, exist_ok=True)
    tools_dir = PROJECT_DIR / "tools"

    print("=" * 70)
    print(f"  Phase 5 — Post-processing (rembg + alpha 128 + tight crop)")
    if dry_run:
        print(f"  [DRY-RUN] Sẽ list files cần process, không thực sự rembg.")
    print("=" * 70)

    if dry_run:
        level_name = cfg["name"]
        n = 0
        for s in cfg["states"]:
            src = staging / f"{level_name}_{s['id']}.png"
            if src.exists():
                print(f"  [PLAN] chain {s['id']:35} → final/{src.name}")
                n += 1
        for cat_key in ("trash_overlays", "subparts", "overlay_effects", "style_variants"):
            for s in cfg.get(cat_key, []):
                if isinstance(s, dict):
                    src = staging / s["filename"]
                    if src.exists():
                        print(f"  [PLAN] {cat_key[:8]:9}{s['id']:35} → final/{src.name}")
                        n += 1
        for s in cfg.get("backgrounds", []):
            src = staging / s["filename"]
            if src.exists():
                print(f"  [PLAN] bg       {s['id']:35} → final/{src.name}")
                n += 1
        print(f"\n[DRY-RUN] Sẽ process {n} files. Bỏ --dry-run để chạy thật.")
        return

    # --- Chain states ---
    level_name = cfg["name"]
    for s in cfg["states"]:
        src = staging / f"{level_name}_{s['id']}.png"
        if not src.exists():
            print(f"[SKIP] {src.name} missing")
            continue
        dst = final / f"{level_name}_{s['id']}.png"
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

def phase_6_review(level: int, serve: bool = False):
    """
    Generate an enhanced interactive review page (review_chain.html) and a
    companion feedback_server.py.  Open the HTML in a browser, fill feedback
    text boxes, drag reference images onto cards, flag items for regen, then
    click 'Gửi cho Claude' to POST to the local feedback server — or use
    'Copy feedback' as a clipboard fallback.

    If serve=True, auto-starts the feedback server (foreground, Ctrl+C to stop)
    and opens the HTML in the default browser.
    """
    import os as _os
    cfg, level_dir = load_level_config(level)
    final       = level_dir / "final"
    tools_final = PROJECT_DIR / "tools" / "final"

    # ── Build JS asset-data arrays ──────────────────────────────────────────
    def _rel(p: Path) -> str:
        try:
            return "final/" + p.name
        except Exception:
            return str(p)

    def _tool_rel(p: Path) -> str:
        try:
            return _os.path.relpath(p, level_dir).replace("\\", "/")
        except Exception:
            return str(p)

    def _js_items(items: list[dict], *, is_tool: bool = False) -> str:
        rows = []
        for it in items:
            fname = it.get("filename") or f"{cfg['name']}_{it['id']}.png"
            fp = (tools_final if is_tool else final) / fname
            if not fp.exists():
                continue
            frel = _tool_rel(fp) if is_tool else _rel(fp)
            label = it.get("label") or it["id"]
            note  = it.get("note") or it.get("tool_note") or ""
            tag   = it.get("tag") or ("anchor" if it.get("is_anchor") else "")
            tc    = it.get("tagColor") or ""
            rows.append(
                "{" +
                f'id:{json.dumps(it["id"])},'
                f'file:{json.dumps(frel)},'
                f'label:{json.dumps(label)},'
                f'note:{json.dumps(note)},'
                f'tag:{json.dumps(tag)},'
                f'tagColor:{json.dumps(tc)}'
                + "}"
            )
        return "[" + ",\n".join(rows) + "]"

    chain_items = []
    for s in cfg["states"]:
        it = dict(s)
        it["filename"] = f"{cfg['name']}_{s['id']}.png"
        it["label"] = s["id"]
        chain_items.append(it)

    subpart_items = cfg.get("subparts", [])
    style_items   = cfg.get("style_variants", [])
    bg_items      = cfg.get("backgrounds", [])
    tool_items    = cfg.get("tools_required", [])

    js_chain    = _js_items(chain_items)
    js_subparts = _js_items(subpart_items)
    js_styles   = _js_items(style_items)
    js_bgs      = _js_items(bg_items)
    js_tools    = _js_items(tool_items, is_tool=True)

    level_n = cfg["level"]
    name    = cfg["name"]

    # ── Generate feedback_server.py alongside the HTML ──────────────────────
    server_src = '''\
#!/usr/bin/env python3
"""Feedback server for the Shine It review page.  Run: python3 feedback_server.py"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, base64
from pathlib import Path
from datetime import datetime

PORT = 7771
HERE  = Path(__file__).parent
FEEDBACK_FILE = HERE / "feedback.json"
REFS_DIR      = HERE / "feedback_refs"


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path == "/ping":
            self.send_response(200); self._cors()
            self.send_header("Content-Type","application/json"); self.end_headers()
            self.wfile.write(b\'{"status":"ok"}\')
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
        REFS_DIR.mkdir(exist_ok=True)
        for item in data.get("items",[]):
            b64 = item.pop("ref_image_b64", None)
            if b64:
                ext = item.pop("ref_image_ext","png")
                p = REFS_DIR / f"ref_{item[\'id\']}.{ext}"
                p.write_bytes(base64.b64decode(b64.split(",")[-1]))
                item["ref_image_path"] = str(p)
        data["saved_at"] = datetime.now().isoformat()
        FEEDBACK_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        n = sum(1 for i in data.get("items",[]) if i.get("text","").strip())
        r = sum(1 for i in data.get("items",[]) if i.get("regen"))
        print(f"[FEEDBACK] {n} comments · {r} regen  →  {FEEDBACK_FILE}")
        self.send_response(200); self._cors()
        self.send_header("Content-Type","application/json"); self.end_headers()
        self.wfile.write(json.dumps({"ok":True,"n_feedback":n,"n_regen":r}).encode())

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.send_header("Access-Control-Allow-Methods","POST,GET,OPTIONS")

    def log_message(self,*a): pass


if __name__ == "__main__":
    s = HTTPServer(("localhost", PORT), Handler)
    print(f"Feedback server → http://localhost:{PORT}  (Ctrl+C để dừng)")
    try: s.serve_forever()
    except KeyboardInterrupt: print("\\nStopped.")
'''
    (level_dir / "feedback_server.py").write_text(server_src)

    # ── HTML template ────────────────────────────────────────────────────────
    html = f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<title>Shine It — Level {level_n} {name} · Review</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#111;--bg2:#1a1a1a;--bg3:#222;
  --border:#2c2c2c;--border-h:#444;
  --text:#ddd;--dim:#777;--faint:#3a3a3a;
  --accent:#3b82f6;--accent-h:#60a5fa;
  --green:#22c55e;--orange:#f97316;--red:#ef4444;
}}
body{{font-family:-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--text);}}

/* header */
.hdr{{position:sticky;top:0;z-index:100;background:rgba(17,17,17,.96);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--border);
  padding:10px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}}
.hdr h1{{font-size:15px;font-weight:700;color:#fff;white-space:nowrap;}}
.hdr .sub{{font-size:11px;color:var(--dim);}}
.hdr .sp{{flex:1;}}
.dot{{width:8px;height:8px;border-radius:50%;background:var(--faint);transition:.3s;}}
.dot.ok{{background:var(--green);box-shadow:0 0 6px var(--green);}}
.dot.err{{background:var(--red);}}
.srv-lbl{{font-size:11px;color:var(--dim);}}
.regen-badge{{background:var(--orange);color:#fff;padding:2px 8px;border-radius:10px;
  font-size:11px;font-weight:700;display:none;}}
.regen-badge.show{{display:inline-block;}}

/* buttons */
.btn{{display:inline-flex;align-items:center;gap:5px;padding:7px 13px;
  border-radius:6px;border:none;font-size:12px;font-weight:600;cursor:pointer;transition:.15s;}}
.btn-p{{background:var(--accent);color:#fff;}}
.btn-p:hover{{background:var(--accent-h);}}
.btn-c{{background:var(--bg3);color:var(--text);border:1px solid var(--border);}}
.btn-c:hover{{border-color:var(--accent);color:var(--accent);}}

/* main */
.main{{padding:20px;max-width:1600px;margin:0 auto;}}

/* overall feedback */
.overall-box{{background:var(--bg2);border:1px solid var(--border);border-radius:10px;
  padding:14px 16px;margin-bottom:28px;}}
.overall-box label{{font-size:12px;font-weight:700;color:var(--dim);
  text-transform:uppercase;letter-spacing:.06em;display:block;margin-bottom:8px;}}
.overall-box textarea{{width:100%;min-height:64px;resize:vertical;
  background:var(--bg3);border:1px solid var(--border);border-radius:6px;
  color:var(--text);font-size:12px;font-family:inherit;padding:8px 10px;
  outline:none;line-height:1.5;}}
.overall-box textarea:focus{{border-color:var(--accent);}}
.overall-box textarea::placeholder{{color:#3a3a3a;}}

/* section */
.sec{{margin-bottom:40px;}}
.sec-title{{display:flex;align-items:center;gap:10px;font-size:11px;font-weight:700;
  text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin-bottom:12px;}}
.sec-title::after{{content:'';flex:1;height:1px;background:var(--border);}}

/* grids */
.g7{{display:grid;grid-template-columns:repeat(7,1fr);gap:10px;}}
.g5{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;}}
.g3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;}}
.g2{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;}}
.g-tools{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;}}

.sub-grp{{margin-bottom:20px;}}
.sub-grp-lbl{{font-size:11px;color:var(--dim);font-weight:600;
  padding-left:2px;margin-bottom:7px;}}

/* state labels */
.slabels{{display:grid;gap:10px;margin-bottom:6px;}}
.slabels span{{font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.05em;text-align:center;}}

/* card */
.card{{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  overflow:hidden;display:flex;flex-direction:column;transition:border-color .15s;}}
.card:hover{{border-color:var(--border-h);}}
.card.flagged{{border-color:var(--orange)!important;box-shadow:0 0 0 1px var(--orange);}}
.card.noted{{border-color:var(--accent);}}

/* img zone */
.iz{{position:relative;
  background:repeating-conic-gradient(#1f1f1f 0% 25%,#181818 0% 50%) 50%/14px 14px;
  aspect-ratio:1/1;overflow:hidden;}}
.iz.tool-bg{{background:#efefef;}}
/* CRITICAL: main img shouldn't intercept drag events */
.iz > img{{width:100%;height:100%;object-fit:contain;display:block;pointer-events:none;}}
.drop-ov{{position:absolute;inset:0;background:rgba(59,130,246,.18);
  border:2px dashed var(--accent);display:none;align-items:center;
  justify-content:center;font-size:11px;font-weight:600;color:var(--accent);
  flex-direction:column;gap:4px;pointer-events:none;z-index:10;}}
.card.drag-over .drop-ov{{display:flex;}}
.card.drag-over{{border-color:var(--accent);}}
.ref-thumb{{position:absolute;bottom:5px;right:5px;background:var(--bg);
  border:1px solid var(--border);border-radius:5px;padding:2px;display:none;cursor:pointer;}}
.ref-thumb img{{width:40px;height:40px;object-fit:cover;border-radius:3px;display:block;}}
.ref-thumb .rm{{position:absolute;top:-5px;right:-5px;width:14px;height:14px;
  border-radius:50%;background:var(--red);color:#fff;border:none;cursor:pointer;
  font-size:9px;line-height:14px;text-align:center;padding:0;}}

/* card body */
.cb{{padding:7px;display:flex;flex-direction:column;gap:5px;flex:1;}}
.ch{{display:flex;align-items:center;gap:5px;}}
.aid{{font-size:11px;font-weight:600;color:var(--text);flex:1;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.atag{{font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700;}}
.fa{{width:100%;min-height:46px;resize:vertical;background:var(--bg3);
  border:1px solid var(--border);border-radius:5px;color:var(--text);
  font-size:11px;font-family:inherit;padding:5px 7px;outline:none;line-height:1.4;}}
.fa:focus{{border-color:var(--accent);}}
.fa::placeholder{{color:#333;}}
/* Per-card "send this now" button */
.bsend{{display:flex;align-items:center;justify-content:center;gap:5px;
  font-size:11px;font-weight:700;cursor:pointer;padding:6px 10px;
  border-radius:5px;background:#2a1810;border:1px solid #4a2a10;
  color:var(--orange);transition:.15s;user-select:none;width:100%;}}
.bsend:hover{{background:var(--orange);border-color:var(--orange);color:#fff;
  transform:translateY(-1px);}}
.bsend.sent{{background:#1a3a1a;border-color:var(--green);color:var(--green);}}
.bsend.sent::before{{content:'✓ ';}}

/* toast */
#toast{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
  background:#1e2a1e;border:1px solid var(--green);color:var(--green);
  padding:9px 18px;border-radius:7px;font-size:12px;font-weight:600;
  opacity:0;transition:opacity .3s;pointer-events:none;z-index:999;white-space:nowrap;}}
#toast.err{{background:#2a1e1e;border-color:var(--red);color:var(--red);}}
#toast.show{{opacity:1;}}

/* bg preview */
.bgprev{{border-radius:8px;overflow:hidden;border:1px solid var(--border);}}
.bgprev img{{width:100%;display:block;max-height:200px;object-fit:cover;}}
</style>
</head>
<body>
<div class="hdr">
  <h1>Level {level_n} — {name}</h1>
  <span class="sub">Phase 6 Review · final/ · {level_n:02d}</span>
  <div class="sp"></div>
  <div class="dot" id="dot"></div>
  <span class="srv-lbl" id="srvlbl">Connecting...</span>
  <span class="regen-badge" id="rbadge">0 feedback</span>
  <button class="btn btn-c" onclick="doCopy()">📋 Copy</button>
  <button class="btn btn-p" onclick="doSend()">🚀 Gửi cho Claude</button>
</div>

<div class="main">
  <!-- Overall feedback -->
  <div class="overall-box">
    <label>💬 Feedback tổng thể</label>
    <textarea id="overall-fb"
      placeholder="Nhận xét chung về toàn bộ batch này... (vd: style không nhất quán, foam quá trắng, shadow thiếu chiều sâu)"></textarea>
  </div>

  <!-- Chain -->
  <div class="sec">
    <div class="sec-title">Main chain ({len(chain_items)} states)</div>
    <div class="g7" id="chain-g"></div>
  </div>

  <!-- Subparts -->
  <div class="sec">
    <div class="sec-title">Subparts</div>
    <div id="subpart-g"></div>
  </div>

  <!-- Styles -->
  <div class="sec">
    <div class="sec-title">Style variants</div>
    <div class="g3" id="style-g"></div>
  </div>

  <!-- Backgrounds -->
  <div class="sec">
    <div class="sec-title">Backgrounds</div>
    <div class="g3" id="bg-g"></div>
  </div>

  <!-- Tools -->
  <div class="sec">
    <div class="sec-title">Tools</div>
    <div class="g-tools" id="tools-g"></div>
  </div>
</div>

<div id="toast"></div>

<script>
const CHAIN    = {js_chain};
const SUBPARTS = {js_subparts};
const STYLES   = {js_styles};
const BGS      = {js_bgs};
const TOOLS    = {js_tools};

// Group subparts by prefix (cover_, shell_, filter_1_, ...)
function groupSubparts(items) {{
  const groups = {{}};
  for (const it of items) {{
    const key = it.id.replace(/_(?:dusty|stained|foamed|scrubbed|clean)$/, '');
    if (!groups[key]) groups[key] = [];
    groups[key].push(it);
  }}
  return groups;
}}

// ── State ──
const ST = {{}};
function gs(id) {{
  if (!ST[id]) ST[id] = {{text:'', regen:false, ref_b64:null, ref_ext:null}};
  return ST[id];
}}

// ── Card ──
function makeCard(it, isTool) {{
  const s = gs(it.id);
  const card = document.createElement('div');
  card.className = 'card';
  card.dataset.id = it.id;

  // img zone
  const iz = document.createElement('div');
  iz.className = 'iz' + (isTool ? ' tool-bg' : '');
  const img = document.createElement('img');
  img.src = it.file; img.loading = 'lazy'; img.alt = it.label;
  iz.appendChild(img);

  // ref thumb (preview of dropped ref image)
  const thumb = document.createElement('div');
  thumb.className = 'ref-thumb';
  thumb.innerHTML = '<img alt="ref"><button class="rm" title="Xóa">✕</button>';
  thumb.querySelector('.rm').addEventListener('click', e => {{
    e.stopPropagation();
    gs(it.id).ref_b64 = null; gs(it.id).ref_ext = null;
    thumb.style.display = 'none'; thumb.querySelector('img').src = '';
  }});
  iz.appendChild(thumb);

  // drop overlay (visual cue when dragging) — added to CARD, not iz
  const dov = document.createElement('div');
  dov.className = 'drop-ov';
  dov.innerHTML = '<span style="font-size:24px;">📎</span><span>Thả ảnh ref vào đây</span>';

  // body
  const cb = document.createElement('div'); cb.className = 'cb';
  const ch = document.createElement('div'); ch.className = 'ch';
  const aid = document.createElement('span');
  aid.className = 'aid'; aid.title = it.id;
  aid.textContent = it.label || it.id;
  ch.appendChild(aid);
  if (it.tag) {{
    const t = document.createElement('span');
    t.className = 'atag';
    t.style.background = (it.tagColor||'#555')+'22';
    t.style.color = it.tagColor||'#aaa';
    t.textContent = it.tag; ch.appendChild(t);
  }}

  const ta = document.createElement('textarea');
  ta.className = 'fa';
  ta.placeholder = 'Feedback (vd: bụi dày hơn, foam đặc hơn)...';
  ta.value = s.text;
  ta.addEventListener('input', () => {{
    gs(it.id).text = ta.value;
    card.classList.toggle('noted', !!ta.value.trim());
    updateBadge();
  }});

  // "🔄 Gửi & regen ngay" — immediate single-item send
  const btn = document.createElement('button');
  btn.className = 'bsend';
  btn.textContent = '🔄 Gửi & regen ngay';
  btn.addEventListener('click', () => sendSingle(it.id, btn));

  cb.append(ch, ta, btn);
  card.append(iz, cb, dov);

  // Setup drop on entire CARD (so dropping anywhere — img/textarea/btn — works)
  setupDrop(card, it.id, thumb);
  return card;
}}

// ── Drag & Drop ──
// Use a counter to handle dragenter/dragleave correctly when crossing child elements
function setupDrop(zone, id, thumb) {{
  let depth = 0;
  zone.addEventListener('dragenter', e => {{
    e.preventDefault();
    depth++;
    zone.classList.add('drag-over');
  }});
  zone.addEventListener('dragover', e => {{
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }});
  zone.addEventListener('dragleave', e => {{
    depth--;
    if (depth <= 0) {{ depth = 0; zone.classList.remove('drag-over'); }}
  }});
  zone.addEventListener('drop', e => {{
    e.preventDefault();
    e.stopPropagation();
    depth = 0;
    zone.classList.remove('drag-over');
    const f = e.dataTransfer.files[0];
    if (!f) {{ toast('Không có file', true); return; }}
    if (!f.type.startsWith('image/')) {{ toast('Cần file ảnh (PNG/JPG)', true); return; }}
    const r = new FileReader();
    r.onload = ev => {{
      gs(id).ref_b64 = ev.target.result;
      gs(id).ref_ext = (f.name.split('.').pop()||'png').toLowerCase();
      thumb.querySelector('img').src = ev.target.result;
      thumb.style.display = 'block';
      zone.classList.add('noted');
      toast('✓ Đã gắn ref vào ' + id);
      updateBadge();
    }};
    r.onerror = () => toast('Lỗi đọc file', true);
    r.readAsDataURL(f);
  }});
}}

// Prevent the browser from navigating away when dropping outside cards
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => e.preventDefault());

// ── Send single item NOW ──
async function sendSingle(id, btn) {{
  const s = gs(id);
  s.regen = true;  // implicit regen
  const overall = document.getElementById('overall-fb').value.trim();
  const payload = {{
    level: {level_n}, name: '{name}',
    overall_feedback: overall,
    items: [{{
      id, text: s.text.trim(), regen: true,
      ref_image_b64: s.ref_b64 || null,
      ref_image_ext: s.ref_ext || null,
    }}],
    single_item: true,
  }};
  try {{
    const res = await fetch('http://localhost:7771/feedback', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(payload),
    }});
    if (!res.ok) throw new Error();
    btn.classList.add('sent');
    btn.textContent = 'Đã gửi — gõ "check feedback" vào Claude';
    toast('✓ Đã gửi ' + id + ' — chuyển sang Claude Code chat, gõ "check"');
  }} catch {{
    toast('Server offline — paste prompt thủ công vào Claude', true);
  }}
}}

// ── Render ──
function render() {{
  // Chain
  const cg = document.getElementById('chain-g');
  CHAIN.forEach(a => cg.appendChild(makeCard(a, false)));

  // Subparts grouped
  const sg = document.getElementById('subpart-g');
  const groups = groupSubparts(SUBPARTS);
  Object.entries(groups).forEach(([key, items]) => {{
    const grp = document.createElement('div'); grp.className = 'sub-grp';
    const lbl = document.createElement('div'); lbl.className = 'sub-grp-lbl';
    lbl.textContent = key;
    const g = document.createElement('div');
    g.className = 'g' + Math.min(items.length, 5);
    items.forEach(a => g.appendChild(makeCard(a, false)));
    grp.append(lbl, g); sg.appendChild(grp);
  }});

  // Styles
  const stg = document.getElementById('style-g');
  STYLES.forEach(a => stg.appendChild(makeCard(a, false)));

  // BGS
  const bg = document.getElementById('bg-g');
  BGS.forEach(a => bg.appendChild(makeCard(a, false)));

  // Tools
  const tg = document.getElementById('tools-g');
  TOOLS.forEach(a => tg.appendChild(makeCard(a, true)));
}}

// ── Badge ──
function updateBadge() {{
  const n = Object.values(ST).filter(s => s.text.trim()||s.regen||s.ref_b64).length;
  const b = document.getElementById('rbadge');
  b.textContent = n + ' feedback';
  b.classList.toggle('show', n > 0);
}}

// ── Collect ──
function collect() {{
  const overall = document.getElementById('overall-fb').value.trim();
  const items = [];
  for (const [id, s] of Object.entries(ST)) {{
    if (s.text.trim() || s.regen || s.ref_b64)
      items.push({{id, text:s.text.trim(), regen:s.regen,
                   ref_image_b64:s.ref_b64||null, ref_image_ext:s.ref_ext||null}});
  }}
  return {{level:{level_n}, name:'{name}', overall_feedback:overall, items}};
}}

// ── Copy ──
function doCopy() {{
  const d = collect();
  if (!d.items.length && !d.overall_feedback) {{ toast('Chưa có feedback!', true); return; }}
  let lines = ['## Feedback — Level {level_n} {name}\\n'];
  if (d.overall_feedback) lines.push('**Tổng thể:** ' + d.overall_feedback + '\\n');
  d.items.forEach(i => {{
    lines.push('### ' + i.id);
    if (i.regen) lines.push('**→ Cần regenerate**');
    if (i.text)  lines.push(i.text);
    if (i.ref_image_b64) lines.push('_(kèm ảnh ref)_');
    lines.push('');
  }});
  navigator.clipboard.writeText(lines.join('\\n'))
    .then(() => toast('✓ Copied ' + d.items.length + ' items'))
    .catch(() => toast('Copy thất bại', true));
}}

// ── Send ──
async function doSend() {{
  const d = collect();
  if (!d.items.length && !d.overall_feedback) {{ toast('Chưa có feedback!', true); return; }}
  try {{
    const res = await fetch('http://localhost:7771/feedback', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(d)
    }});
    if (!res.ok) throw new Error();
    const j = await res.json();
    toast('✓ Đã gửi ' + j.n_feedback + ' feedback cho Claude');
  }} catch {{
    doCopy();
    toast('Server offline — đã copy thay thế', true);
  }}
}}

// ── Server ping ──
async function ping() {{
  const dot = document.getElementById('dot');
  const lbl = document.getElementById('srvlbl');
  try {{
    const r = await fetch('http://localhost:7771/ping', {{signal:AbortSignal.timeout(1500)}});
    dot.className = 'dot ok'; lbl.textContent = 'Claude server online';
  }} catch {{
    dot.className = 'dot err'; lbl.textContent = 'Server offline';
  }}
}}

// ── Toast ──
let _tt;
function toast(msg, err) {{
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'toast show' + (err ? ' err' : '');
  clearTimeout(_tt); _tt = setTimeout(() => t.classList.remove('show'), 3000);
}}

render();
ping();
setInterval(ping, 8000);
</script>
</body></html>"""

    out = level_dir / "review_chain.html"
    out.write_text(html)
    server_path = level_dir / "feedback_server.py"
    print(f"[HTML]   {out}")
    print(f"[SERVER] {server_path}")

    if not serve:
        print(f"\n  Mở HTML: open {out}")
        print(f"  Chạy server: python3 {server_path}")
        print(f"  Hoặc chạy 1 lệnh: --phase 6 --serve")
        print(f"\n[DONE] Phase 6 complete.")
        return

    # ── Auto-launch mode ────────────────────────────────────────────────────
    import subprocess, webbrowser, time, sys as _sys

    # Open browser first (non-blocking)
    print(f"\n[OPEN] {out}")
    if _sys.platform == "darwin":
        subprocess.Popen(["open", str(out)])
    elif _sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", str(out)])
    elif _sys.platform == "win32":
        _os.startfile(str(out))  # type: ignore
    else:
        webbrowser.open(out.as_uri())

    print(f"[SERVE] Starting feedback server... (Ctrl+C để dừng)")
    print("─" * 70)
    # Run the server in foreground so user can Ctrl+C to stop cleanly
    try:
        subprocess.run([_sys.executable, str(server_path)], cwd=str(level_dir))
    except KeyboardInterrupt:
        print("\n[STOP] Server dừng.")


def phase_6_review_old_chain_items(level: int):
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
                   choices=["0", "0.5", "1", "2", "3", "3b", "4", "5", "6", "7"])
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
    p.add_argument("--skip-on-error", action="store_true",
                   help="Phase 3: log failures and continue instead of aborting the chain")
    p.add_argument("--serve", action="store_true",
                   help="Phase 6: auto-open HTML in browser + start feedback server")
    p.add_argument("--mode", default="ask",
                   choices=["ask", "batch", "waterfall", "smart"],
                   help="Phase 3: how to gate state generation (default: ask)")
    p.add_argument("--model", default="ask",
                   choices=["ask", "flash", "pro"],
                   help="I2I model for Phase 1/3/3b/4: flash ($0.030/img) or pro ($0.135). "
                        "Default 'ask' = prompt at runtime. --yes implies 'flash'.")
    args = p.parse_args()

    # Resolve model BEFORE phase dispatch so cost estimates are accurate
    _maybe_override_model(args.model, args.yes, args.phase, args.dry_run)

    if args.phase == "0":
        phase_0_init(args.level, dry_run=args.dry_run)
    elif args.phase == "0.5":
        phase_0_5_concept(args.level, dry_run=args.dry_run, yes=args.yes)
    elif args.phase == "1":
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        phase_1_anchor(args.level, models)
    elif args.phase == "2":
        phase_2_validate(args.level)
    elif args.phase == "3":
        phase_3_chain(args.level, state_filter=args.state,
                      dry_run=args.dry_run, yes=args.yes,
                      skip_on_error=args.skip_on_error, mode=args.mode)
    elif args.phase == "3b":
        phase_3b_trash(args.level, sprite_filter=args.sprite,
                       dry_run=args.dry_run, yes=args.yes)
    elif args.phase == "4":
        phase_4_tools(args.level, tool_filter=args.tool,
                      dry_run=args.dry_run, yes=args.yes, force=args.force)
    elif args.phase == "5":
        phase_5_postprocess(args.level, dry_run=args.dry_run)
    elif args.phase == "6":
        phase_6_review(args.level, serve=args.serve)
    elif args.phase == "7":
        phase_7_promote(args.level, only_approved=args.only_approved,
                        dry_run=args.dry_run)


if __name__ == "__main__":
    main()
