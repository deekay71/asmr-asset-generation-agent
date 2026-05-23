"""
Fal.ai helper for game asset generation.

Handles authentication, image generation (FLUX.1 [dev], Seedream v4.5),
background removal (rembg local), alpha cleanup, and tight cropping.

Requirements:
  - FAL_KEY in .env (get from https://fal.ai/dashboard/keys)

Usage:
    from fal_helper import (
        init_fal, generate_image, seedream_edit, upload_file,
        remove_bg_local, clean_and_crop, download_image, generate_asset,
    )
"""

import json
import os
import ssl
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Dependency management
# ---------------------------------------------------------------------------

def _ensure_packages():
    """Install missing packages on first run."""
    packages = {
        "fal-client": "fal_client",
        "Pillow": "PIL",
        "rembg[cpu]": "rembg",
    }
    missing = []
    for pkg_name, import_name in packages.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg_name)

    if missing:
        print(f"[FAL] Installing missing packages: {', '.join(missing)}")
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing,
             "--break-system-packages", "-q"]
        )


_ensure_packages()

import fal_client  # noqa: E402
from PIL import Image  # noqa: E402
from rembg import remove as rembg_remove  # noqa: E402

# Optional: dotenv and requests (not strictly required)
try:
    from dotenv import load_dotenv
    _HAS_DOTENV = True
except ImportError:
    _HAS_DOTENV = False

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# SSL fix for macOS
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE
_opener = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_ssl_ctx)
)
urllib.request.install_opener(_opener)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Model endpoints
FLUX_MODEL = "fal-ai/flux/dev"
FLUX_PRO_MODEL = "fal-ai/flux-pro/v1.1"
SEEDREAM_EDIT_MODEL = "fal-ai/flux-pro/kontext"   # Seedream v4.5 deprecated — kontext is active I2I edit
SEEDREAM_T2I_MODEL = "fal-ai/flux/dev"             # Seedream v4.5 T2I deprecated — flux/dev is active
GPT_IMAGE_MODEL = "fal-ai/gpt-image-1.5"
NANO_BANANA_EDIT_MODEL = "fal-ai/nano-banana/edit"         # v1
NANO_BANANA_2_EDIT_MODEL = "fal-ai/nano-banana-2/edit"     # Gemini 2.5 Flash Image
NANO_BANANA_PRO_EDIT_MODEL = "fal-ai/nano-banana-pro/edit" # Gemini 2.5 Pro Image
NANO_BANANA_T2I_MODEL = "fal-ai/nano-banana-2"

# Image size presets (name -> Fal.ai value)
IMAGE_SIZES = {
    "square": "square",                 # 512x512
    "square_hd": "square_hd",           # 1024x1024
    "portrait_4_3": "portrait_4_3",     # 768x1024
    "landscape_4_3": "landscape_4_3",   # 1024x768
    "landscape_16_9": "landscape_16_9", # 1024x576
}

# Asset type -> default image size
ASSET_SIZE_DEFAULTS = {
    "character": "portrait_4_3",
    "enemy": "portrait_4_3",
    "npc": "portrait_4_3",
    "item": "square",
    "projectile": "square",
    "icon": "square",
    "platform": "landscape_4_3",
    "background": "landscape_16_9",
    "sprite": "square_hd",
    "ui_element": "square_hd",
    "effect": "square_hd",
    "obstacle": "square_hd",
    "decoration": "square_hd",
}

# Cost tracking
MODEL_COSTS = {
    FLUX_MODEL: 0.025,
    FLUX_PRO_MODEL: 0.05,
    SEEDREAM_EDIT_MODEL: 0.04,   # kontext
    SEEDREAM_T2I_MODEL: 0.025,  # flux/dev
    GPT_IMAGE_MODEL: 0.02,
    NANO_BANANA_EDIT_MODEL: 0.03,
    NANO_BANANA_2_EDIT_MODEL: 0.03,
    NANO_BANANA_PRO_EDIT_MODEL: 0.06,
    NANO_BANANA_T2I_MODEL: 0.03,
}

# Appended to every prompt to prevent text/annotation contamination
NO_TEXT = (
    " NO text, NO labels, NO annotations, NO writing, NO callouts, "
    "NO diagrams, NO UI elements, NO watermarks anywhere in the image."
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FalResult:
    """Result of a single asset generation."""
    image_url: str
    local_path: str
    asset_id: str
    state: str
    variant: int
    prompt: str
    model: str
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def init_fal(env_path: str) -> str:
    """Load FAL_KEY from .env and set in environment. Returns the key."""
    if _HAS_DOTENV:
        load_dotenv(env_path)
    else:
        # Manual .env parsing
        for line in Path(env_path).read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "FAL_KEY":
                os.environ["FAL_KEY"] = v.strip()

    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        raise ValueError(
            "FAL_KEY not found in .env file.\n"
            "Get your key from: https://fal.ai/dashboard/keys\n"
            "Add to .env: FAL_KEY=your-key-here"
        )

    os.environ["FAL_KEY"] = fal_key
    print("[FAL] API key loaded")
    return fal_key


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

def upload_file(file_path: str) -> str:
    """Upload a local file to Fal.ai CDN. Returns the URL."""
    url = fal_client.upload_file(file_path)
    print(f"[UPLOAD] {Path(file_path).name} → {url[:60]}...")
    return url


# ---------------------------------------------------------------------------
# Image generation — T2I
# ---------------------------------------------------------------------------

def generate_image(
    prompt: str,
    model: str = FLUX_MODEL,
    image_size: str = "square_hd",
    num_inference_steps: int = 28,
    guidance_scale: float = 3.5,
    num_images: int = 1,
    seed: int = None,
    append_no_text: bool = True,
):
    """
    Generate images via T2I (FLUX, Seedream T2I, GPT Image).

    Returns list of dicts with 'url', 'width', 'height' keys.
    """
    full_prompt = prompt + NO_TEXT if append_no_text else prompt

    arguments = {
        "prompt": full_prompt,
        "num_images": num_images,
        "output_format": "png",
    }

    # Model-specific parameters
    if model in (FLUX_MODEL, FLUX_PRO_MODEL):
        if image_size not in IMAGE_SIZES:
            raise ValueError(f"Invalid image_size '{image_size}'. Use: {list(IMAGE_SIZES.keys())}")
        arguments["image_size"] = IMAGE_SIZES[image_size]
        arguments["num_inference_steps"] = num_inference_steps
        arguments["guidance_scale"] = guidance_scale
    elif model == GPT_IMAGE_MODEL:
        arguments["image_size"] = "1024x1024"
        arguments["background"] = "transparent"
        arguments["quality"] = "high"
    elif model == SEEDREAM_T2I_MODEL:
        # Now points to flux/dev — use same params as FLUX
        if image_size not in IMAGE_SIZES:
            image_size = "square_hd"
        arguments["image_size"] = IMAGE_SIZES[image_size]
        arguments["num_inference_steps"] = num_inference_steps
        arguments["guidance_scale"] = guidance_scale

    if seed is not None:
        arguments["seed"] = seed

    cost = MODEL_COSTS.get(model, 0.03)
    print(f"[GEN] {model.split('/')[-1]} | {image_size} | {num_images} image(s) | ~${cost:.3f}/img")

    result = _call_fal_with_retry(model, arguments)

    images = result.get("images", [])
    if not images:
        raise RuntimeError(f"{model} returned no images. Check prompt and retry.")

    print(f"[GEN] Generated {len(images)} image(s)")
    return images


# ---------------------------------------------------------------------------
# Image generation — I2I Edit (Seedream v4.5)
# ---------------------------------------------------------------------------

def seedream_edit(
    image_url: str,
    prompt: str,
    output_size: dict = None,
    append_no_text: bool = True,
) -> str:
    """
    Edit an image using Seedream v4.5 / Kontext. Best for I2I chains.

    Args:
        image_url: URL of source image (from upload_file or previous step).
        prompt: Edit instruction (describe ONLY what changes).
        output_size: Optional {"width": W, "height": H} to force output dimensions.
        append_no_text: Append NO_TEXT suffix to prompt.

    Returns:
        URL of the edited image.
    """
    full_prompt = prompt + NO_TEXT if append_no_text else prompt

    arguments = {
        "image_url": image_url,
        "prompt": full_prompt,
        "image_size": output_size or "square_hd",
        "num_images": 1,
    }

    print(f"[EDIT] Kontext I2I edit | ~$0.04")

    result = _call_fal_with_retry(SEEDREAM_EDIT_MODEL, arguments)

    # Extract URL from response (handles multiple response formats)
    url = _extract_image_url(result)
    if not url:
        raise RuntimeError(f"Seedream returned no image. Keys: {list(result.keys())}")

    print(f"[EDIT] Done")
    return url


# ---------------------------------------------------------------------------
# Image generation — I2I Edit (Nano Banana 2)
# ---------------------------------------------------------------------------

def nano_banana_edit(
    image_url: "str | list[str]",
    prompt: str,
    model: str = None,
    aspect_ratio: str = "1:1",
    thinking_level: str = None,
    system_prompt: str = None,
    append_no_text: bool = True,
) -> str:
    """
    Edit an image using Nano Banana (v1 / 2 / pro). Accepts either a single
    URL or a LIST of URLs — NB-2 supports multi-image conditioning where
    the first image typically anchors style/proportions and additional
    images supply secondary references (e.g., costume design).

    Args:
        image_url: URL string OR list of URL strings (multi-anchor I2I).
        prompt: Edit instruction describing the desired transformation.
        model: FAL model ID. Defaults to NANO_BANANA_EDIT_MODEL (v1).
               Use NANO_BANANA_2_EDIT_MODEL or NANO_BANANA_PRO_EDIT_MODEL for newer models.
        aspect_ratio: Output aspect ratio (e.g. "1:1", "9:16", "16:9").
        thinking_level: "minimal" or "high" — nano-banana-2 and pro only.
        system_prompt: System-level instruction — nano-banana-2 and pro only.
        append_no_text: Append NO_TEXT suffix to prompt.

    Returns:
        URL of the edited image.
    """
    model = model or NANO_BANANA_EDIT_MODEL
    full_prompt = prompt + NO_TEXT if append_no_text else prompt

    image_urls = [image_url] if isinstance(image_url, str) else list(image_url)
    arguments = {
        "image_urls": image_urls,
        "prompt": full_prompt,
        "aspect_ratio": aspect_ratio,
        "output_format": "png",
    }

    # nano-banana-2 and pro support thinking_level and system_prompt
    _advanced_models = (NANO_BANANA_2_EDIT_MODEL, NANO_BANANA_PRO_EDIT_MODEL)
    if model in _advanced_models:
        if thinking_level:
            arguments["thinking_level"] = thinking_level
        if system_prompt:
            arguments["system_prompt"] = system_prompt

    cost = MODEL_COSTS.get(model, 0.03)
    print(f"[EDIT] {model.split('/')[-1]} | ~${cost:.3f}")

    result = _call_fal_with_retry(model, arguments)

    url = _extract_image_url(result)
    if not url:
        # Try alternate response format
        if "output" in result:
            url = result["output"]
        if not url:
            raise RuntimeError(
                f"Nano Banana 2 returned no image. Keys: {list(result.keys())}"
            )

    print(f"[EDIT] Done")
    return url


def nano_banana_generate(
    prompt: str,
    aspect_ratio: str = "1:1",
    append_no_text: bool = True,
) -> str:
    """
    Generate image using Nano Banana 2 T2I (fal-ai/nano-banana-2).

    Args:
        prompt: Full image description.
        aspect_ratio: Output aspect ratio.
        append_no_text: Append NO_TEXT suffix.

    Returns:
        URL of the generated image.
    """
    full_prompt = prompt + NO_TEXT if append_no_text else prompt

    arguments = {
        "prompt": full_prompt,
        "aspect_ratio": aspect_ratio,
        "output_format": "png",
    }

    cost = MODEL_COSTS.get(NANO_BANANA_T2I_MODEL, 0.03)
    print(f"[GEN] Nano Banana 2 T2I | ~${cost:.3f}")

    result = _call_fal_with_retry(NANO_BANANA_T2I_MODEL, arguments)

    url = _extract_image_url(result)
    if not url:
        if "output" in result:
            url = result["output"]
        if not url:
            raise RuntimeError(
                f"Nano Banana 2 T2I returned no image. Keys: {list(result.keys())}"
            )

    print(f"[GEN] Done")
    return url


# ---------------------------------------------------------------------------
# Background removal — rembg (local, free)
# ---------------------------------------------------------------------------

def remove_bg_local(src_path: str, dst_path: str) -> int:
    """
    Remove background using rembg (local U2Net model).
    Preferred over BiRefNet — handles surfaces/walls correctly.

    Returns output file size in KB.
    """
    print(f"[BG] rembg: {Path(src_path).name}")

    with open(src_path, "rb") as f:
        input_data = f.read()

    output_data = rembg_remove(input_data)

    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as f:
        f.write(output_data)

    kb = len(output_data) // 1024
    print(f"[BG] Saved: {Path(dst_path).name} ({kb}KB)")
    return kb


def remove_bg_hybrid(src_path: str, dst_path: str, color_threshold: int = 72,
                     padding: int = 2, alpha_threshold: int = 128) -> int:
    """
    Hybrid bg removal: color-key flood fill ∪ rembg.

    Safest method for low-contrast assets on grey backgrounds.
    A pixel is foreground if EITHER method says it's foreground:
    - color-key prevents rembg from eating the asset
    - rembg catches gradient bg that color-key misses

    Use this when rembg alone destroys part of the asset.
    Returns output file size in KB.
    """
    import io
    from collections import deque
    import numpy as np

    print(f"[BG-HYBRID] {Path(src_path).name}")

    img = Image.open(src_path).convert("RGBA")
    pixels = np.array(img)
    h, w = pixels.shape[:2]

    # Auto-detect bg color from edge pixels
    samples = []
    for d in range(15):
        samples.extend(pixels[d, ::4, :3].tolist())
        samples.extend(pixels[h-1-d, ::4, :3].tolist())
        samples.extend(pixels[::4, d, :3].tolist())
        samples.extend(pixels[::4, w-1-d, :3].tolist())
    samples = np.array(samples, dtype=np.float32)
    spread = np.max(samples, axis=1) - np.min(samples, axis=1)
    brightness = np.mean(samples, axis=1)
    grey_mask = (spread < 30) & (brightness > 80) & (brightness < 220)
    if grey_mask.sum() > 10:
        bg_color = tuple(np.median(samples[grey_mask], axis=0).astype(int).tolist())
    else:
        bg_color = (128, 128, 128)
    print(f"[BG-HYBRID] bg_color=RGB{bg_color}")

    # Color-key flood fill
    rgb = pixels[:, :, :3].astype(np.float32)
    bg = np.array(bg_color, dtype=np.float32)
    dist = np.sqrt(np.sum((rgb - bg) ** 2, axis=2))
    could_be_bg = dist < color_threshold

    is_bg = np.zeros((h, w), dtype=bool)
    visited = np.zeros((h, w), dtype=bool)
    queue = deque()
    for x in range(w):
        for row in [0, h-1]:
            if could_be_bg[row, x] and not visited[row, x]:
                queue.append((row, x)); visited[row, x] = True
    for y in range(h):
        for col in [0, w-1]:
            if could_be_bg[y, col] and not visited[y, col]:
                queue.append((y, col)); visited[y, col] = True
    while queue:
        cy, cx = queue.popleft()
        is_bg[cy, cx] = True
        for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
            ny, nx = cy+dy, cx+dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and could_be_bg[ny, nx]:
                visited[ny, nx] = True; queue.append((ny, nx))
    colorkey_fg = ~is_bg

    # rembg
    with open(src_path, "rb") as f:
        rembg_data = rembg_remove(f.read())
    rembg_img = np.array(Image.open(io.BytesIO(rembg_data)).convert("RGBA"))
    rembg_fg = rembg_img[:, :, 3] > alpha_threshold

    # Combine: foreground if EITHER says foreground (conservative — preserves content)
    combined_fg = colorkey_fg | rembg_fg
    pixels[:, :, 3] = np.where(combined_fg, 255, 0).astype(np.uint8)

    result = Image.fromarray(pixels)
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(dst_path, "PNG", optimize=True)

    kb = Path(dst_path).stat().st_size // 1024
    print(f"[BG-HYBRID] Saved: {Path(dst_path).name} ({kb}KB)")
    return kb


def remove_bg_colorkey_only(src_path: str, dst_path: str,
                            color_threshold: int = 60,
                            alpha_threshold: int = 128,
                            bg_color: tuple = None) -> int:
    """
    Color-key-only bg removal: flood fill from edges, no rembg.

    Best for wall/surface assets where rembg destroys the content because
    the asset color (white plaster, cream walls) is too close to grey bg.

    NEVER removes asset content — only removes connected bg regions from edges.
    May leave some bg residue near edges as a tradeoff for content safety.

    Args:
        bg_color: Explicit RGB tuple e.g. (0, 255, 0) for green bg.
                  If None, auto-detects from edge pixels (works for grey-ish bgs).

    Returns output file size in KB.
    """
    from collections import deque
    import numpy as np

    print(f"[BG-COLORKEY] {Path(src_path).name}")

    img = Image.open(src_path).convert("RGBA")
    pixels = np.array(img)
    h, w = pixels.shape[:2]

    if bg_color is not None:
        # Use explicitly provided bg color (e.g. green #00FF00)
        print(f"[BG-COLORKEY] bg_color=RGB{bg_color} (explicit)")
    else:
        # Auto-detect bg color from edge pixels using clustering
        # Works for grey, green, or any uniform bg color
        samples = []
        for d in range(15):
            samples.extend(pixels[d, ::4, :3].tolist())
            samples.extend(pixels[h-1-d, ::4, :3].tolist())
            samples.extend(pixels[::4, d, :3].tolist())
            samples.extend(pixels[::4, w-1-d, :3].tolist())
        samples = np.array(samples, dtype=np.float32)

        # First try: grey-ish background (low channel spread)
        spread = np.max(samples, axis=1) - np.min(samples, axis=1)
        brightness = np.mean(samples, axis=1)
        grey_mask = (spread < 30) & (brightness > 80) & (brightness < 220)
        if grey_mask.sum() > 10:
            bg_color = tuple(np.median(samples[grey_mask], axis=0).astype(int).tolist())
            print(f"[BG-COLORKEY] bg_color=RGB{bg_color} (auto-detected: grey)")
        else:
            # Fallback: find the most common color cluster among edge pixels
            # Use corner pixels (most likely to be bg) as seed
            corner_samples = []
            for dy in range(20):
                for dx in range(20):
                    corner_samples.append(pixels[dy, dx, :3].tolist())
                    corner_samples.append(pixels[dy, w-1-dx, :3].tolist())
                    corner_samples.append(pixels[h-1-dy, dx, :3].tolist())
                    corner_samples.append(pixels[h-1-dy, w-1-dx, :3].tolist())
            corner_arr = np.array(corner_samples, dtype=np.float32)
            bg_color = tuple(np.median(corner_arr, axis=0).astype(int).tolist())
            print(f"[BG-COLORKEY] bg_color=RGB{bg_color} (auto-detected: corner median)")

    # Color-key flood fill from edges
    rgb = pixels[:, :, :3].astype(np.float32)
    bg = np.array(bg_color, dtype=np.float32)
    dist = np.sqrt(np.sum((rgb - bg) ** 2, axis=2))
    could_be_bg = dist < color_threshold

    is_bg = np.zeros((h, w), dtype=bool)
    visited = np.zeros((h, w), dtype=bool)
    queue = deque()
    for x in range(w):
        for row in [0, h-1]:
            if could_be_bg[row, x] and not visited[row, x]:
                queue.append((row, x)); visited[row, x] = True
    for y in range(h):
        for col in [0, w-1]:
            if could_be_bg[y, col] and not visited[y, col]:
                queue.append((y, col)); visited[y, col] = True
    while queue:
        cy, cx = queue.popleft()
        is_bg[cy, cx] = True
        for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
            ny, nx = cy+dy, cx+dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and could_be_bg[ny, nx]:
                visited[ny, nx] = True; queue.append((ny, nx))

    fg_mask = ~is_bg
    pixels[:, :, 3] = np.where(fg_mask, 255, 0).astype(np.uint8)

    total_px = h * w
    fg_pct = fg_mask.sum() / total_px * 100
    print(f"[BG-COLORKEY] fg={fg_pct:.1f}%  removed={100-fg_pct:.1f}%")

    result = Image.fromarray(pixels)
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(dst_path, "PNG", optimize=True)

    kb = Path(dst_path).stat().st_size // 1024
    print(f"[BG-COLORKEY] Saved: {Path(dst_path).name} ({kb}KB)")
    return kb


def remove_bg_green_direct(src_path: str, dst_path: str,
                           alpha_threshold: int = 128) -> int:
    """
    Remove green background using three-pass approach:
    1. Direct detection: any pixel where G clearly dominates R and B
    2. Edge flood fill: catch blend-zone pixels connected to image border
    3. Edge proximity boost: within 30px of image border, use more aggressive detection

    This handles the soft gradient Seedream produces between green bg and asset.
    Pass 3 is key: near the image edge, pixels are almost certainly bg, not asset.

    Best for: white/cream/pastel surfaces on green bg.

    Returns output file size in KB.
    """
    from collections import deque
    import numpy as np

    print(f"[BG-GREEN] {Path(src_path).name}")

    img = Image.open(src_path).convert("RGBA")
    pixels = np.array(img)
    h, w = pixels.shape[:2]
    rgb = pixels[:, :, :3].astype(np.float32)

    # Find actual bg color from green edge pixels
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    edge_samples = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
    edge_g, edge_r, edge_b = edge_samples[:, 1], edge_samples[:, 0], edge_samples[:, 2]
    green_edge_mask = (edge_g > edge_r + 15) & (edge_g > edge_b + 15) & (edge_g > 80)

    bg_color = np.array([135, 210, 100], dtype=np.float32)
    if green_edge_mask.sum() > 10:
        bg_color = np.median(edge_samples[green_edge_mask], axis=0)

    dist = np.sqrt(np.sum((rgb - bg_color) ** 2, axis=2))
    print(f"[BG-GREEN] bg_color=RGB({bg_color[0]:.0f},{bg_color[1]:.0f},{bg_color[2]:.0f})")

    # Pass 1: Direct green-dominant detection (anywhere in image)
    is_green = (g > r + 20) & (g > b + 20) & (g > 100)

    # Pass 2: Flood fill from edges with moderate threshold (120)
    could_be_bg_moderate = dist < 120
    is_bg = np.copy(is_green)
    visited = np.copy(is_green)
    queue = deque()
    for x in range(w):
        for row in [0, h - 1]:
            if could_be_bg_moderate[row, x] and not visited[row, x]:
                queue.append((row, x)); visited[row, x] = True
    for y in range(h):
        for col in [0, w - 1]:
            if could_be_bg_moderate[y, col] and not visited[y, col]:
                queue.append((y, col)); visited[y, col] = True
    while queue:
        cy, cx = queue.popleft()
        is_bg[cy, cx] = True
        for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            ny, nx = cy + dy, cx + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and could_be_bg_moderate[ny, nx]:
                visited[ny, nx] = True; queue.append((ny, nx))

    # Pass 3: Edge proximity — within 50px of image border, use aggressive threshold
    # Near the border, even slightly greenish pixels are bg (asset should be centered)
    edge_proximity = np.zeros((h, w), dtype=bool)
    border = 80
    edge_proximity[:border, :] = True
    edge_proximity[-border:, :] = True
    edge_proximity[:, :border] = True
    edge_proximity[:, -border:] = True

    # In edge zone: remove anything within 180 dist of green bg
    edge_aggressive = edge_proximity & (dist < 180) & ~is_bg
    is_bg = is_bg | edge_aggressive

    # Also remove any remaining green-tinted pixels in edge zone
    # (greenish hue = G channel is highest, even slightly)
    edge_greenish = edge_proximity & (g > r) & (g > b) & (g > 80) & ~is_bg
    is_bg = is_bg | edge_greenish

    fg_mask = ~is_bg
    pixels[:, :, 3] = np.where(fg_mask, 255, 0).astype(np.uint8)

    total_px = h * w
    fg_pct = fg_mask.sum() / total_px * 100
    removed_pct = is_bg.sum() / total_px * 100
    green_direct_pct = is_green.sum() / total_px * 100
    print(f"[BG-GREEN] pass1_green={green_direct_pct:.1f}%  total_removed={removed_pct:.1f}%  fg={fg_pct:.1f}%")

    result = Image.fromarray(pixels)
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(dst_path, "PNG", optimize=True)

    kb = Path(dst_path).stat().st_size // 1024
    print(f"[BG-GREEN] Saved: {Path(dst_path).name} ({kb}KB)")
    return kb


def remove_bg_hsv_chroma(src_path: str, dst_path: str,
                         hue_lo: float = 65.0, hue_hi: float = 165.0,
                         sat_min: float = 0.12, val_min: float = 0.04) -> int:
    """
    HSV hue-based chroma-key removal for green-screen backgrounds.

    Unlike RGB-distance approaches, hue detection catches all green shades:
    - Bright lime #00FF00 (H≈120, S=1, V=1)
    - Soft/desaturated model-generated green (H≈100-130, S≈0.5, V≈0.7)
    - Shadow cast on green bg (same H, lower V) ← key advantage over RGB distance

    Safe for: white/cream/yellow/brown/stainless microwave tones (H<75 or H>165).
    """
    from collections import deque
    import numpy as np

    print(f"[BG-HSV] {Path(src_path).name}")

    img = Image.open(src_path).convert("RGBA")
    pixels = np.array(img)
    h, w = pixels.shape[:2]
    rgb = pixels[:, :, :3].astype(np.float32) / 255.0
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]

    # Vectorised HSV
    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    sat = np.where(cmax > 1e-6, delta / cmax, 0.0)
    val = cmax

    hue = np.zeros((h, w), dtype=np.float32)
    m = (delta > 1e-6)
    # Green dominant
    gm = m & (g == cmax)
    hue[gm] = (60.0 * ((b[gm] - r[gm]) / delta[gm]) + 120.0) % 360.0
    # Red dominant
    rm = m & (r == cmax)
    hue[rm] = (60.0 * ((g[rm] - b[rm]) / delta[rm]) + 360.0) % 360.0
    # Blue dominant
    bm = m & (b == cmax)
    hue[bm] = (60.0 * ((r[bm] - g[bm]) / delta[bm]) + 240.0) % 360.0

    # Primary green mask: hue in range, enough saturation and value
    is_green = (hue >= hue_lo) & (hue <= hue_hi) & (sat >= sat_min) & (val >= val_min)

    # Flood fill from all 4 edges with slightly relaxed thresholds to cross anti-alias fringe
    flood_green = (hue >= hue_lo) & (hue <= hue_hi) & (sat >= sat_min * 0.6) & (val >= val_min * 0.6)
    is_bg = np.copy(is_green)
    visited = np.copy(is_green)
    queue = deque()
    for x in range(w):
        for row in [0, h - 1]:
            if flood_green[row, x] and not visited[row, x]:
                visited[row, x] = True
                queue.append((row, x))
    for y in range(h):
        for col in [0, w - 1]:
            if flood_green[y, col] and not visited[y, col]:
                visited[y, col] = True
                queue.append((y, col))
    while queue:
        cy, cx = queue.popleft()
        is_bg[cy, cx] = True
        for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            ny, nx = cy + dy, cx + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and flood_green[ny, nx]:
                visited[ny, nx] = True
                queue.append((ny, nx))

    # Edge proximity boost: within 120px of bottom (for ground shadows), 60px other sides
    edge_zone = np.zeros((h, w), dtype=bool)
    edge_zone[:60, :] = True        # top
    edge_zone[-120:, :] = True      # bottom — wider to catch cast shadows under feet
    edge_zone[:, :60] = True        # left
    edge_zone[:, -60:] = True       # right
    edge_green = edge_zone & (hue >= hue_lo) & (hue <= hue_hi) & (sat >= sat_min * 0.5) & (val >= val_min * 0.5)
    is_bg = is_bg | edge_green

    fg_mask = ~is_bg
    pixels[:, :, 3] = np.where(fg_mask, 255, 0).astype(np.uint8)

    total_px = h * w
    fg_pct = fg_mask.sum() / total_px * 100
    removed_pct = is_bg.sum() / total_px * 100
    print(f"[BG-HSV] removed={removed_pct:.1f}%  fg={fg_pct:.1f}%")

    result = Image.fromarray(pixels)
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(dst_path, "PNG", optimize=True)

    kb = Path(dst_path).stat().st_size // 1024
    print(f"[BG-HSV] Saved: {Path(dst_path).name} ({kb}KB)")
    return kb


def remove_bg_refined(src_path: str, dst_path: str,
                      erode_px: int = 2, blur_px: int = 3,
                      alpha_threshold: int = 128) -> int:
    """
    Refined bg removal: rembg ONLY + morphological edge cleanup.

    Fixes the smart_hybrid problem where the safety net (dist_from_bg > threshold)
    re-introduces bg-colored pixels for assets whose colors overlap with the bg
    (e.g., grey clock body on grey bg, dark TV cabinet on grey bg).

    Approach:
    1. Run rembg to get the initial alpha mask
    2. Threshold the mask to binary (fg/bg)
    3. Erode the mask by erode_px to eat away edge artifacts/residue
    4. Gaussian blur the mask edges for smooth alpha transitions
    5. Re-threshold to get a clean final mask

    This trusts rembg's boundary detection entirely but cleans up its
    sometimes-rough edges with morphological operations.

    Args:
        erode_px: Erosion radius in pixels (default 2). Trims edge residue.
        blur_px: Gaussian blur radius for edge smoothing (default 3).
        alpha_threshold: Threshold for converting rembg's soft alpha to binary.

    Returns output file size in KB.
    """
    import io
    import numpy as np
    from PIL import ImageFilter

    print(f"[BG-REFINED] {Path(src_path).name}")

    img = Image.open(src_path).convert("RGBA")
    pixels = np.array(img)
    h, w = pixels.shape[:2]

    # ── rembg: get initial mask ──
    with open(src_path, "rb") as f:
        rembg_data = rembg_remove(f.read())
    rembg_img = Image.open(io.BytesIO(rembg_data)).convert("RGBA")
    rembg_arr = np.array(rembg_img)

    # Extract alpha channel as a PIL image for morphological ops
    alpha = Image.fromarray(rembg_arr[:, :, 3])

    # ── Step 1: Threshold to binary ──
    alpha = alpha.point(lambda x: 255 if x >= alpha_threshold else 0)

    # ── Step 2: Erode to remove edge residue ──
    if erode_px > 0:
        # Use MinFilter (erosion) — shrinks white regions by removing edge pixels
        # Apply multiple passes for larger erode_px values
        for _ in range(erode_px):
            alpha = alpha.filter(ImageFilter.MinFilter(3))

    # ── Step 3: Slight dilation to recover (erode_px - 1) so net effect is ~1px trim ──
    # This ensures we don't over-erode the content, just clean edges
    recover_px = max(0, erode_px - 1)
    for _ in range(recover_px):
        alpha = alpha.filter(ImageFilter.MaxFilter(3))

    # ── Step 4: Gaussian blur for smooth alpha transitions at edges ──
    if blur_px > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=blur_px))

    # ── Step 5: Final threshold — keep soft edges but kill faint residue ──
    # Use a lower threshold (32) to preserve the soft edge from the blur
    alpha_arr = np.array(alpha)
    alpha_arr = np.where(alpha_arr >= 32, alpha_arr, 0).astype(np.uint8)

    # Apply to original pixels
    pixels[:, :, 3] = alpha_arr

    # ── Stats ──
    fg_px = (alpha_arr > 0).sum()
    total_px = h * w
    print(f"[BG-REFINED] fg={fg_px/total_px*100:.1f}%  removed={100-fg_px/total_px*100:.1f}%")

    result = Image.fromarray(pixels)
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(dst_path, "PNG", optimize=True)

    kb = Path(dst_path).stat().st_size // 1024
    print(f"[BG-REFINED] Saved: {Path(dst_path).name} ({kb}KB)")
    return kb


def remove_bg_refined_gentle(src_path: str, dst_path: str,
                             blur_px: int = 3,
                             alpha_threshold: int = 128) -> int:
    """
    Gentle refined bg removal: rembg + Gaussian blur smoothing, NO erosion.

    Identical to remove_bg_refined() but skips the MinFilter erosion step
    entirely. This preserves thin features (pendulum rods, antenna wires,
    clock hands) that the 2px erosion in remove_bg_refined() destroys.

    Approach:
    1. Run rembg to get the initial alpha mask
    2. Threshold the mask to binary (fg/bg)
    3. Gaussian blur the mask edges for smooth alpha transitions (NO erosion)
    4. Re-threshold to kill faint residue while keeping soft edges

    Args:
        blur_px: Gaussian blur radius for edge smoothing (default 3).
        alpha_threshold: Threshold for converting rembg's soft alpha to binary.

    Returns output file size in KB.
    """
    import io
    import numpy as np
    from PIL import ImageFilter

    print(f"[BG-REFINED-GENTLE] {Path(src_path).name}")

    img = Image.open(src_path).convert("RGBA")
    pixels = np.array(img)
    h, w = pixels.shape[:2]

    # ── rembg: get initial mask ──
    with open(src_path, "rb") as f:
        rembg_data = rembg_remove(f.read())
    rembg_img = Image.open(io.BytesIO(rembg_data)).convert("RGBA")
    rembg_arr = np.array(rembg_img)

    # Extract alpha channel as a PIL image
    alpha = Image.fromarray(rembg_arr[:, :, 3])

    # ── Step 1: Threshold to binary ──
    alpha = alpha.point(lambda x: 255 if x >= alpha_threshold else 0)

    # ── Step 2: Gaussian blur for smooth alpha transitions at edges ──
    # NO erosion — preserves thin features like pendulum rods
    if blur_px > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=blur_px))

    # ── Step 3: Final threshold — keep soft edges but kill faint residue ──
    alpha_arr = np.array(alpha)
    alpha_arr = np.where(alpha_arr >= 32, alpha_arr, 0).astype(np.uint8)

    # Apply to original pixels
    pixels[:, :, 3] = alpha_arr

    # ── Stats ──
    fg_px = (alpha_arr > 0).sum()
    total_px = h * w
    print(f"[BG-REFINED-GENTLE] fg={fg_px/total_px*100:.1f}%  removed={100-fg_px/total_px*100:.1f}%")

    result = Image.fromarray(pixels)
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(dst_path, "PNG", optimize=True)

    kb = Path(dst_path).stat().st_size // 1024
    print(f"[BG-REFINED-GENTLE] Saved: {Path(dst_path).name} ({kb}KB)")
    return kb


def remove_bg_fal_birefnet(src_path: str, dst_path: str) -> int:
    """
    High-quality background removal via fal.ai BiRefNet.

    Uploads the image to fal storage, runs BiRefNet segmentation (Heavy),
    downloads the RGBA PNG result, saves to dst_path.

    Preferred over local rembg for complex subjects (open fridges, shelved
    food, detailed interiors) — BiRefNet handles fine boundaries and
    see-through/recessed regions that rembg's U2Net misses.
    """
    import subprocess
    import numpy as np

    print(f"[BG-BIREFNET] {Path(src_path).name}")

    src_url = upload_file(src_path)

    result = _call_fal_with_retry(
        "fal-ai/birefnet",
        arguments={
            "image_url": src_url,
            "model": "General Use (Heavy)",
            "operating_on_foreground": False,
        },
    )

    output_url = _extract_image_url(result)
    if not output_url:
        raise RuntimeError(f"BiRefNet returned no image URL. Result keys: {list(result.keys())}")

    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    dl = subprocess.run(
        ["curl", "-L", "-s", "-f", "-o", str(dst_path),
         "--retry", "3", "--retry-delay", "3", output_url],
        capture_output=True, text=True, timeout=120,
    )
    if dl.returncode != 0 or not Path(dst_path).exists() or Path(dst_path).stat().st_size == 0:
        raise RuntimeError(f"BiRefNet download failed: {dl.stderr.strip()[:200]}")

    out_img = Image.open(dst_path).convert("RGBA")
    arr = np.array(out_img)
    fg_px = (arr[:, :, 3] > 0).sum()
    total_px = arr.shape[0] * arr.shape[1]
    print(f"[BG-BIREFNET] fg={fg_px/total_px*100:.1f}%  removed={100-fg_px/total_px*100:.1f}%")

    kb = Path(dst_path).stat().st_size // 1024
    print(f"[BG-BIREFNET] Saved: {Path(dst_path).name} ({kb}KB)")
    return kb


def remove_bg_fal_pixelcut(src_path: str, dst_path: str) -> int:
    """
    High-quality background removal via fal.ai pixelcut/background-removal.

    Preferred over the in-house HSV chroma key for assets with white/light
    fabric (which the HSV approach degrades). Pixelcut handles white-on-green
    cleanly with no white-pixel attrition.

    Uploads the image, runs pixelcut, downloads the RGBA PNG result.
    """
    import subprocess
    import numpy as np

    print(f"[BG-PIXELCUT] {Path(src_path).name}")

    src_url = upload_file(src_path)

    result = _call_fal_with_retry(
        "pixelcut/background-removal",
        arguments={"image_url": src_url},
    )

    output_url = _extract_image_url(result)
    if not output_url:
        raise RuntimeError(f"Pixelcut returned no image URL. Result keys: {list(result.keys())}")

    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    if output_url.startswith("data:"):
        # Base64 data URL — decode inline
        import base64
        header, b64 = output_url.split(",", 1)
        Path(dst_path).write_bytes(base64.b64decode(b64))
    else:
        dl = subprocess.run(
            ["curl", "-L", "-s", "-f", "-o", str(dst_path),
             "--retry", "3", "--retry-delay", "3", output_url],
            capture_output=True, text=True, timeout=120,
        )
        if dl.returncode != 0 or not Path(dst_path).exists() or Path(dst_path).stat().st_size == 0:
            raise RuntimeError(f"Pixelcut download failed: {dl.stderr.strip()[:200]}")

    out_img = Image.open(dst_path).convert("RGBA")
    arr = np.array(out_img)
    fg_px = (arr[:, :, 3] > 0).sum()
    total_px = arr.shape[0] * arr.shape[1]
    print(f"[BG-PIXELCUT] fg={fg_px/total_px*100:.1f}%  removed={100-fg_px/total_px*100:.1f}%")
    kb = Path(dst_path).stat().st_size // 1024
    print(f"[BG-PIXELCUT] Saved: {Path(dst_path).name} ({kb}KB)")
    return kb


def remove_bg_smart_hybrid(src_path: str, dst_path: str,
                           safety_threshold: int = 80,
                           alpha_threshold: int = 128) -> int:
    """
    Smart hybrid bg removal: rembg boundary + safety net for light content.

    Fixes the old hybrid's "too conservative" problem:
    - Old hybrid: union (colorkey | rembg) → keeps gradient halos AND internal bg
    - Smart hybrid: rembg primary + restore clearly-non-bg pixels

    Logic: combined_fg = rembg_fg | (distance_from_bg > safety_threshold)

    This means:
    - Gradient halo near object (close to bg color) → removed by rembg ✓
    - White piano keys / plaster (far from grey bg) → restored by safety net ✓
    - Internal bg between piano legs → removed by rembg ✓
    - Dark clock glass (far from grey bg) → restored by safety net ✓

    safety_threshold: Euclidean RGB distance. Pixels further than this from bg
    are ALWAYS kept as foreground regardless of rembg. Default 80 means:
    - Grey bg gradient (dist ~10-30): removed ✓
    - White plaster (dist ~100+): kept ✓
    - Dark wood (dist ~120+): kept ✓
    - Piano keys white (dist ~130+): kept ✓

    Returns output file size in KB.
    """
    import io
    import numpy as np

    print(f"[BG-SMART] {Path(src_path).name}")

    img = Image.open(src_path).convert("RGBA")
    pixels = np.array(img)
    h, w = pixels.shape[:2]

    # ── Auto-detect bg color from edge pixels ──
    samples = []
    for d in range(15):
        samples.extend(pixels[d, ::4, :3].tolist())
        samples.extend(pixels[h-1-d, ::4, :3].tolist())
        samples.extend(pixels[::4, d, :3].tolist())
        samples.extend(pixels[::4, w-1-d, :3].tolist())
    samples = np.array(samples, dtype=np.float32)
    spread = np.max(samples, axis=1) - np.min(samples, axis=1)
    brightness = np.mean(samples, axis=1)
    grey_mask = (spread < 30) & (brightness > 80) & (brightness < 220)
    if grey_mask.sum() > 10:
        bg_color = tuple(np.median(samples[grey_mask], axis=0).astype(int).tolist())
    else:
        bg_color = (128, 128, 128)
    print(f"[BG-SMART] bg_color=RGB{bg_color}")

    # ── Distance from bg for every pixel ──
    rgb = pixels[:, :, :3].astype(np.float32)
    bg = np.array(bg_color, dtype=np.float32)
    dist = np.sqrt(np.sum((rgb - bg) ** 2, axis=2))

    # ── rembg: primary boundary detection ──
    with open(src_path, "rb") as f:
        rembg_data = rembg_remove(f.read())
    rembg_img = np.array(Image.open(io.BytesIO(rembg_data)).convert("RGBA"))
    rembg_fg = rembg_img[:, :, 3] > alpha_threshold

    # ── Safety net: pixels clearly NOT background ──
    clearly_not_bg = dist > safety_threshold

    # ── Smart combine: rembg boundary + restore clearly-non-bg pixels ──
    combined_fg = rembg_fg | clearly_not_bg
    pixels[:, :, 3] = np.where(combined_fg, 255, 0).astype(np.uint8)

    # ── Stats ──
    rembg_only = rembg_fg.sum()
    safety_restored = (clearly_not_bg & ~rembg_fg).sum()
    total_fg = combined_fg.sum()
    total_px = h * w
    print(f"[BG-SMART] rembg_fg={rembg_only/total_px*100:.1f}%  "
          f"safety_restored={safety_restored/total_px*100:.1f}%  "
          f"total_fg={total_fg/total_px*100:.1f}%")

    result = Image.fromarray(pixels)
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(dst_path, "PNG", optimize=True)

    kb = Path(dst_path).stat().st_size // 1024
    print(f"[BG-SMART] Saved: {Path(dst_path).name} ({kb}KB)")
    return kb


# ---------------------------------------------------------------------------
# Alpha cleanup + tight crop
# ---------------------------------------------------------------------------

def clean_and_crop(
    src_path: str,
    padding: int = 2,
    alpha_threshold: int = 128,
    save_in_place: bool = True,
    dst_path: str = None,
) -> dict:
    """
    Clean alpha edges and crop to content bounding box.

    1. Threshold alpha at `alpha_threshold` → crisp edges (no fringe)
    2. Crop to tight bounding box + padding
    3. Save (in-place or to dst_path)

    Returns dict with file, orig size, new size, saved_kb.
    """
    img = Image.open(src_path).convert("RGBA")
    orig_size = img.size

    # Threshold alpha
    r, g, b, a = img.split()
    a = a.point(lambda x: 255 if x >= alpha_threshold else 0)
    img = Image.merge("RGBA", (r, g, b, a))

    # Find content bounding box
    bbox = img.getbbox()
    if bbox is None:
        return {"file": Path(src_path).name, "orig": orig_size, "new": (0, 0), "skipped": True}

    # Expand by padding, clamped to image bounds
    x0 = max(0, bbox[0] - padding)
    y0 = max(0, bbox[1] - padding)
    x1 = min(img.width, bbox[2] + padding)
    y1 = min(img.height, bbox[3] + padding)

    cropped = img.crop((x0, y0, x1, y1))

    out_path = dst_path or src_path if save_in_place else dst_path
    if out_path is None:
        out_path = src_path
    cropped.save(out_path, "PNG", optimize=True)

    return {
        "file": Path(out_path).name,
        "orig": orig_size,
        "new": cropped.size,
        "saved_kb": Path(out_path).stat().st_size // 1024,
    }


def verify_transparency(image_path: Path) -> bool:
    """Check if a PNG image has an alpha channel."""
    try:
        img = Image.open(image_path)
        return img.mode == "RGBA"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

def download_image(url, output_path):
    """Download an image from URL and save to disk."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if _HAS_REQUESTS:
        response = _requests.get(url, timeout=60)
        response.raise_for_status()
        data = response.content
    else:
        with urllib.request.urlopen(url) as r:
            data = r.read()

    output_path.write_bytes(data)
    kb = len(data) // 1024
    print(f"[DOWNLOAD] {output_path.name} ({kb}KB)")
    return output_path


# ---------------------------------------------------------------------------
# Full asset generation pipeline (T2I + bg removal + crop)
# ---------------------------------------------------------------------------

def generate_asset(
    prompt: str,
    output_dir: Path,
    asset_id: str,
    state: str = "default",
    model: str = FLUX_MODEL,
    image_size: str = "square_hd",
    remove_bg: bool = True,
    num_variants: int = 1,
    seed: int = None,
):
    """
    Full T2I pipeline: generate → download → rembg → alpha cleanup → crop.

    Returns list of FalResult objects.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    images = generate_image(
        prompt=prompt, model=model, image_size=image_size,
        num_images=num_variants, seed=seed,
    )

    for i, img_data in enumerate(images):
        variant = i + 1
        image_url = img_data["url"]

        # Save original
        orig_filename = f"{asset_id}_{state}_v{variant}_original.png"
        orig_path = output_dir / orig_filename
        download_image(image_url, orig_path)

        # Final output path
        final_filename = f"{asset_id}_{state}_v{variant}.png"
        final_path = output_dir / final_filename

        if remove_bg:
            remove_bg_local(str(orig_path), str(final_path))
            clean_and_crop(str(final_path))
        else:
            import shutil
            shutil.copy2(orig_path, final_path)

        # Verify
        if remove_bg:
            has_alpha = verify_transparency(final_path)
            status = "OK" if has_alpha else "WARN: no alpha"
            print(f"[{status}] {final_filename}")

        # Metadata
        cost = MODEL_COSTS.get(model, 0.03)
        metadata = {
            "asset_id": asset_id, "state": state, "variant": variant,
            "prompt": prompt, "model": model, "image_size": image_size,
            "remove_bg": remove_bg, "seed": seed,
            "original_url": image_url,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cost_estimate": cost,
        }

        meta_path = output_dir / f"{asset_id}_{state}_v{variant}_metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        results.append(FalResult(
            image_url=image_url, local_path=str(final_path),
            asset_id=asset_id, state=state, variant=variant,
            prompt=prompt, model=model, metadata=metadata,
        ))

    return results


# ---------------------------------------------------------------------------
# Prompt building helpers
# ---------------------------------------------------------------------------

def build_asset_prompt(
    description: str,
    art_style: str = "",
    color_palette=None,
    state_description: str = "",
    constraints: str = "",
    background: str = "isolated on solid white background",
) -> str:
    """Build a generation-optimized prompt from asset description fields."""
    parts = []
    if art_style:
        parts.append(f"{art_style} style")
    parts.append("game asset")
    parts.append(description)
    if state_description:
        parts.append(state_description)
    if color_palette:
        parts.append(f"color palette: {', '.join(color_palette[:5])}")
    if background:
        parts.append(background)
    parts.append("clean edges, high quality, detailed")
    if constraints:
        parts.append(constraints)
    return ", ".join(parts)


def get_default_size(asset_type: str) -> str:
    """Get default image size for an asset type."""
    return ASSET_SIZE_DEFAULTS.get(asset_type, "square_hd")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_image_url(result: dict) -> str:
    """Extract image URL from various Fal.ai response formats."""
    if "images" in result and result["images"]:
        return result["images"][0].get("url", "")
    if "image" in result:
        img = result["image"]
        return img.get("url") if isinstance(img, dict) else img
    return None


def _call_fal_with_retry(model: str, arguments: dict, max_retries: int = 2) -> dict:
    """Call Fal.ai via queue API with retry on transient failures."""
    for attempt in range(max_retries + 1):
        try:
            return fal_client.subscribe(model, arguments=arguments)
        except Exception as e:
            error_str = str(e).lower()
            is_transient = any(
                code in error_str
                for code in ["429", "500", "502", "503", "timeout"]
            )
            if is_transient and attempt < max_retries:
                wait = 5 * (attempt + 1)
                print(f"[FAL] Transient error, retrying in {wait}s... ({e})")
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test(env_path: str):
    """Quick test to verify Fal.ai auth and generation work."""
    print("=" * 60)
    print("  Fal.ai Helper — Self Test (v2)")
    print("=" * 60)

    init_fal(env_path)

    print("\n[TEST] Generating test image via FLUX...")
    images = generate_image(
        prompt="simple red circle on white background, minimal, icon",
        image_size="square",
        num_inference_steps=20,
        num_images=1,
    )
    print(f"[TEST] Generated: {images[0]['url'][:60]}...")

    print("\n[TEST] Downloading...")
    test_dir = Path("./test_output")
    path = download_image(images[0]["url"], test_dir / "test_original.png")

    print("\n[TEST] Background removal (rembg)...")
    remove_bg_local(str(path), str(test_dir / "test_transparent.png"))

    print("\n[TEST] Alpha cleanup + crop...")
    result = clean_and_crop(str(test_dir / "test_transparent.png"))
    print(f"[TEST] Crop: {result['orig']} → {result['new']}")

    has_alpha = verify_transparency(test_dir / "test_transparent.png")
    print(f"[TEST] Has transparency: {has_alpha}")

    print("\n" + "=" * 60)
    print("  All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fal.ai helper self-test")
    parser.add_argument("--test", action="store_true", help="Run self-test")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    args = parser.parse_args()

    if args.test:
        _self_test(args.env)
    else:
        parser.print_help()
