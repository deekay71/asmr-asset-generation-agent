"""Project discovery and items_config loading."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Resolve repo paths once.
HERE = Path(__file__).resolve()
PIPELINE_DIR = HERE.parent.parent.parent              # .../pipeline
REPO_ROOT = PIPELINE_DIR.parent                        # repo root
PROJECTS_DIR = REPO_ROOT / "projects"


@dataclass
class LevelRef:
    level: int
    name: str          # "keyboard"
    dir_name: str      # "level_07_keyboard"
    path: Path         # absolute path to projects/level_07_keyboard

    @property
    def label(self) -> str:
        return f"L{self.level} — {self.name.title()}"


def scan_levels() -> list[LevelRef]:
    """Discover every projects/level_NN_* directory with an items_config.json."""
    if not PROJECTS_DIR.exists():
        return []
    out: list[LevelRef] = []
    for sub in sorted(PROJECTS_DIR.iterdir()):
        if not sub.is_dir():
            continue
        m = re.match(r"level_(\d+)_(.+)$", sub.name)
        if not m:
            continue
        cfg = sub / "items_config.json"
        if not cfg.exists():
            continue
        out.append(LevelRef(level=int(m.group(1)), name=m.group(2), dir_name=sub.name, path=sub))
    return out


def get_level(level: int) -> LevelRef | None:
    for lv in scan_levels():
        if lv.level == level:
            return lv
    return None


def load_config(level_dir: Path) -> dict:
    with open(level_dir / "items_config.json") as f:
        return json.load(f)


def list_assets(cfg: dict) -> list[dict]:
    """Flatten every renderable asset in the config with kind tags.
    Each entry: {id, kind, step_type, source_state, asset (raw dict)}"""
    out: list[dict] = []
    def _add(kind: str, items, default_step: str | None = None):
        for s in items or []:
            if not isinstance(s, dict):
                # Some configs list style_variants/tools as bare strings.
                out.append({"id": str(s), "kind": kind, "step_type": default_step, "asset": {"id": str(s)}})
                continue
            out.append({
                "id": s.get("id"),
                "kind": kind,
                "step_type": s.get("step_type", default_step),
                "source_state": s.get("source_state"),
                "asset": s,
            })

    _add("chain_state", cfg.get("states", []))
    _add("subflow", cfg.get("subflows", []))
    _add("subpart", cfg.get("subparts", []))
    _add("trash", cfg.get("trash_overlays", []), "trash_overlay")
    _add("overlay", cfg.get("overlay_effects", []))
    _add("style", cfg.get("style_variants", []))
    _add("background", cfg.get("backgrounds", []), "background_scene")
    _add("tool", cfg.get("tools_required", []), "tool_sprite")
    return out


def staging_pngs(level_dir: Path) -> list[Path]:
    d = level_dir / "staging"
    if not d.exists():
        return []
    return sorted(d.glob("*.png"))


def final_pngs(level_dir: Path) -> list[Path]:
    d = level_dir / "final"
    if not d.exists():
        return []
    return sorted(d.glob("*.png"))


def find_asset_image(level_dir: Path, asset_id: str, asset: dict | None = None) -> Path | None:
    """Best-effort image lookup. Tries final/, staging/, approved/.
    Matches: explicit asset.filename → `*{id}.png` → `*{id}*.png`."""
    if not asset_id:
        return None
    search_dirs = [level_dir / "final", level_dir / "staging", level_dir / "approved"]

    # 1. Explicit filename if the asset declares one.
    if asset and isinstance(asset, dict):
        fn = asset.get("filename")
        if fn:
            for d in search_dirs:
                p = d / fn
                if p.exists():
                    return p

    # 2. Match by stem ending in the id (e.g. keyboard_00_trashed.png for "00_trashed").
    for d in search_dirs:
        if not d.exists():
            continue
        suffix_hits = [p for p in d.glob("*.png") if p.stem.endswith(asset_id)]
        if suffix_hits:
            return sorted(suffix_hits, key=lambda p: len(p.name))[0]

    # 3. Fall back to substring match.
    for d in search_dirs:
        if not d.exists():
            continue
        sub_hits = list(d.glob(f"*{asset_id}*.png"))
        if sub_hits:
            return sorted(sub_hits, key=lambda p: len(p.name))[0]

    return None
