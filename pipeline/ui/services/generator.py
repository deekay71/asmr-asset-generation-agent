"""In-process image generation via i2i_backend.

Builds a prompt from the freeform template fields and calls
i2i_backend.get_backend(name).edit(refs, prompt).
"""
from __future__ import annotations
import sys
from pathlib import Path

from .projects import PIPELINE_DIR

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from i2i_backend import get_backend, BACKEND_CHOICES  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Prompt assembly from template fields
# ---------------------------------------------------------------------------

STATE_LABELS_EN = [
    ("step", "STEP"),
    ("concept", "CONCEPT"),
    ("camera", "CAMERA"),
    ("object_count", "OBJECT COUNT"),
    ("object_shape", "OBJECT SHAPE"),
    ("object_position", "OBJECT POSITION"),
    ("per_object_description", "PER-OBJECT DESCRIPTION"),
    ("negative_prompt", "FORBID (negative)"),
]
TOOL_LABELS_EN = [
    ("name", "TOOL NAME"),
    ("step_consistency", "STEP CONSISTENCY"),
    ("tool_count", "TOOL COUNT"),
    ("tool_camera", "CAMERA"),
    ("color", "COLOR"),
    ("style", "STYLE"),
    ("output", "OUTPUT"),
    ("per_object_description", "DESCRIPTION"),
    ("negative_prompt", "FORBID (negative)"),
]
STATE_LABELS_VI = [
    ("step", "BƯỚC"),
    ("concept", "CONCEPT"),
    ("camera", "GÓC CAM"),
    ("object_count", "SỐ LƯỢNG VẬT"),
    ("object_shape", "HÌNH DÁNG"),
    ("object_position", "VỊ TRÍ"),
    ("per_object_description", "MÔ TẢ TỪNG VẬT"),
    ("negative_prompt", "KHÔNG ĐƯỢC CÓ"),
]
TOOL_LABELS_VI = [
    ("name", "TÊN CÔNG CỤ"),
    ("step_consistency", "ĐỒNG BỘ BƯỚC"),
    ("tool_count", "SỐ LƯỢNG"),
    ("tool_camera", "GÓC CAM"),
    ("color", "MÀU"),
    ("style", "STYLE"),
    ("output", "OUTPUT"),
    ("per_object_description", "MÔ TẢ"),
    ("negative_prompt", "KHÔNG ĐƯỢC CÓ"),
]


def build_prompt(fields: dict, kind: str, extra_comment: str = "",
                 lang: str = "en") -> str:
    """Assemble the model prompt from filled template fields. Blank fields skipped.
    Section labels and 'IMPROVE:' / fallback text follow the language."""
    if lang == "vi":
        labels = TOOL_LABELS_VI if kind == "tool" else STATE_LABELS_VI
        fallback = "Tạo một ảnh mới."
        improve_label = "CẢI THIỆN"
    else:
        labels = TOOL_LABELS_EN if kind == "tool" else STATE_LABELS_EN
        fallback = "Generate a new image."
        improve_label = "IMPROVE"
    lines: list[str] = []
    for key, label in labels:
        val = (fields.get(key) or "").strip()
        if val:
            lines.append(f"{label}: {val}")
    body = "\n".join(lines) if lines else fallback
    if extra_comment.strip():
        body += f"\n\n{improve_label}: {extra_comment.strip()}"
    return body


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate(*, backend_name: str, refs: list[Path], prompt: str) -> tuple[bytes, str]:
    """Call the chosen backend's edit() with the assembled prompt + refs.
    Returns (png_bytes, model_id_actually_used). Raises on failure."""
    backend = get_backend(backend_name)
    model_id = getattr(backend, "_model_id", backend_name)
    # Need at least one ref image for the edit-style backends. If the user
    # provided none, synthesise a blank canvas (2K for high-res backends).
    if not refs:
        size = 2048 if "2k" in backend_name.lower() else 1024
        blank = _blank_png(size=size)
        return backend.edit([blank], prompt), model_id
    return backend.edit([str(r) for r in refs], prompt), model_id


def _blank_png(size: int = 1024) -> str:
    """Write a square white PNG to a temp file, return its path."""
    import tempfile
    from PIL import Image
    img = Image.new("RGB", (size, size), (255, 255, 255))
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(f.name)
    return f.name
