"""Postprocess approved generations:

  raw PNG  →  rembg background removal  →  tight crop on alpha
                                       →  split into per-component PNGs
                                          (so multi-item compositions become
                                           individual sprites)

Outputs land in `<step>/approved/`. Returns paths so the UI can display them.
"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image
from scipy import ndimage

from .projects import PIPELINE_DIR

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))


# Tunables — the defaults work for ASMR cleaning game assets on solid backgrounds.
ALPHA_THRESHOLD = 16          # pixels with alpha ≤ this count as transparent
ALPHA_BINARIZE = 128          # rembg soft alpha → binarized at this cutoff
MIN_COMPONENT_AREA_FRAC = 0.003   # 0.3% of total pixels — anything smaller is noise
CROP_PADDING = 2              # px around the bounding box


def _binarize_alpha(path: Path, threshold: int = ALPHA_BINARIZE) -> None:
    """Harden any soft alpha mask: alpha < threshold → 0, else → 255.
    Also zeroes RGB on transparent pixels so background colour stops bleeding
    through anti-alias edges."""
    img = Image.open(path).convert("RGBA")
    arr = np.array(img)
    mask = arr[..., 3] >= threshold
    arr[..., 3] = np.where(mask, 255, 0).astype(np.uint8)
    arr[~mask, :3] = 0
    Image.fromarray(arr).save(path)


def remove_background(src: Path, dst: Path) -> None:
    """Background removal via FAL BiRefNet (cloud, high-quality).

    Falls back to local rembg if FAL is unreachable / FAL_KEY missing — so a
    network blip doesn't break the Approve flow.
    """
    from fal_helper import remove_bg_fal_birefnet, init_fal  # noqa: WPS433
    import os

    # Make sure FAL_KEY is in the environment.
    if not os.environ.get("FAL_KEY"):
        for candidate in (
            PIPELINE_DIR.parent / ".env",
            PIPELINE_DIR / ".env",
        ):
            if candidate.exists():
                init_fal(str(candidate))
                break

    try:
        remove_bg_fal_birefnet(str(src), str(dst))
    except Exception as e:
        print(f"[postprocess] FAL BiRefNet failed ({e}); falling back to rembg")
        from rembg import remove
        out = remove(src.read_bytes())
        dst.write_bytes(out)
    _binarize_alpha(dst)


def tight_crop_path(path: Path, padding: int = CROP_PADDING) -> None:
    """In-place tight crop on non-zero alpha."""
    img = Image.open(path).convert("RGBA")
    arr = np.array(img)
    alpha = arr[..., 3]
    ys, xs = np.where(alpha > ALPHA_THRESHOLD)
    if ys.size == 0:
        return
    y0 = max(0, int(ys.min()) - padding)
    y1 = min(img.height, int(ys.max()) + padding + 1)
    x0 = max(0, int(xs.min()) - padding)
    x1 = min(img.width, int(xs.max()) + padding + 1)
    img.crop((x0, y0, x1, y1)).save(path)


def split_components(rgba_path: Path, out_dir: Path,
                     min_area_frac: float = MIN_COMPONENT_AREA_FRAC,
                     padding: int = CROP_PADDING) -> list[Path]:
    """Split a transparent PNG into one cropped PNG per connected alpha blob.

    Returns the list of saved part paths (sorted top-left → bottom-right).
    Returns an empty list when only one (or zero) significant component exists.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(rgba_path).convert("RGBA")
    arr = np.array(img)
    alpha = arr[..., 3]
    mask = alpha > ALPHA_THRESHOLD
    if not mask.any():
        return []

    # 8-connectivity captures diagonally-touching pixels — better for fuzzy edges.
    structure = np.ones((3, 3), dtype=bool)
    labels, n = ndimage.label(mask, structure=structure)
    if n < 2:
        return []  # only one item — nothing to split

    total_area = mask.size
    min_area = max(64, int(total_area * min_area_frac))

    # Collect bounding boxes for kept components.
    boxes: list[tuple[int, int, int, int, int]] = []  # (label, y0, x0, y1, x1)
    for lid in range(1, n + 1):
        comp = labels == lid
        area = int(comp.sum())
        if area < min_area:
            continue
        ys, xs = np.where(comp)
        boxes.append((lid, int(ys.min()), int(xs.min()), int(ys.max()), int(xs.max())))

    if len(boxes) < 2:
        return []  # only one survived size filter — treat as single item

    # Sort top-to-bottom, then left-to-right.
    boxes.sort(key=lambda b: (b[1] // 32, b[2]))

    saved: list[Path] = []
    for i, (lid, y0, x0, y1, x1) in enumerate(boxes, start=1):
        comp = labels == lid
        part = arr.copy()
        part_alpha = part[..., 3].copy()
        part_alpha[~comp] = 0
        part[..., 3] = part_alpha
        py0 = max(0, y0 - padding)
        py1 = min(img.height, y1 + padding + 1)
        px0 = max(0, x0 - padding)
        px1 = min(img.width, x1 + padding + 1)
        crop = Image.fromarray(part).crop((px0, py0, px1, py1))
        p = out_dir / f"{rgba_path.stem}_part_{i:02d}.png"
        crop.save(p)
        saved.append(p)
    return saved


def process(src: Path, out_dir: Path) -> dict:
    """Full pipeline. Returns {'merged': Path, 'parts': [Path, ...]}.

    `merged` is the single cleaned + cropped PNG.
    `parts` is empty when the image only contains one component.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    merged = out_dir / src.name
    remove_background(src, merged)
    tight_crop_path(merged)
    parts = split_components(merged, out_dir)
    return {"merged": merged, "parts": parts}
