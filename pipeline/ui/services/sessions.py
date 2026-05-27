"""Session model for freeform generations.

Storage layout (under `<repo>/runs/`):

    runs/
    └── 20260526_153012_my-session/
        ├── session.json
        ├── step_01/
        │   ├── refs/                     ← uploaded reference images
        │   ├── gen_001.png               ← generated outputs
        │   ├── gen_001.prompt.txt
        │   └── gen_002.png ...

session.json schema:

    {
      "id": "20260526_153012_my-session",
      "name": "my-session",
      "created_at": "ISO",
      "archived_at": null,                ← set when user clicks Archive
      "backend": "google_flash",
      "steps": [
        {
          "step_id": "step_01",
          "kind": "state" | "tool",
          "fields": { ...template fields, all optional... },
          "refs": ["step_01/refs/anchor.png", ...],
          "generations": [
            {"file": "step_01/gen_001.png",
             "prompt": "<full text>",
             "verdict": "approve" | "deny" | "regen" | null,
             "comment": "",
             "ts": "ISO"}
          ]
        }
      ]
    }
"""
from __future__ import annotations
import datetime as _dt
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .projects import REPO_ROOT

RUNS_DIR = REPO_ROOT / "runs"


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9-_]+", "-", s.lower().strip())
    return s.strip("-")[:48] or "session"


def _now_id(name: str) -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + _slugify(name)


def _now_iso() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def session_path(session_id: str) -> Path:
    return RUNS_DIR / session_id


def list_sessions(*, archived: bool | None = None) -> list[dict]:
    """Return all session.json docs (sorted newest-first).
    archived=None → all; archived=True → only archived; archived=False → active."""
    if not RUNS_DIR.exists():
        return []
    out: list[dict] = []
    for d in sorted(RUNS_DIR.iterdir(), reverse=True):
        meta = d / "session.json"
        if not meta.exists():
            continue
        try:
            doc = json.loads(meta.read_text())
        except Exception:
            continue
        is_arch = bool(doc.get("archived_at"))
        if archived is True and not is_arch:
            continue
        if archived is False and is_arch:
            continue
        out.append(doc)
    return out


def current_session() -> dict | None:
    """The newest non-archived session, or None."""
    actives = list_sessions(archived=False)
    return actives[0] if actives else None


def load(session_id: str) -> dict:
    return json.loads((session_path(session_id) / "session.json").read_text())


def save(doc: dict) -> None:
    p = session_path(doc["id"])
    p.mkdir(parents=True, exist_ok=True)
    (p / "session.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))


def create(name: str, backend: str) -> dict:
    sid = _now_id(name)
    doc = {
        "id": sid,
        "name": name or sid,
        "created_at": _now_iso(),
        "archived_at": None,
        "backend": backend,
        "steps": [],
    }
    save(doc)
    return doc


def set_backend(session_id: str, backend: str) -> None:
    doc = load(session_id)
    doc["backend"] = backend
    save(doc)


def set_style_anchor(session_id: str, filename: str | None,
                     data: bytes | None = None) -> str | None:
    """Set or clear the session-level style anchor image. Returns the rel path
    stored, or None if cleared."""
    doc = load(session_id)
    if filename is None:
        # Clear
        if doc.get("style_anchor"):
            p = session_path(session_id) / doc["style_anchor"]
            if p.exists():
                try: p.unlink()
                except Exception: pass
        doc["style_anchor"] = None
        save(doc)
        return None
    base = session_path(session_id)
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"style_anchor{Path(filename).suffix or '.png'}"
    target.write_bytes(data or b"")
    doc["style_anchor"] = target.name
    save(doc)
    return target.name


def previous_approved_image(session_id: str, step_id: str) -> Path | None:
    """For sequential state steps, find the immediately-previous state step's
    most recent APPROVED generation. Prefers the processed (bg-removed) merged
    file if available, else the raw."""
    doc = load(session_id)
    state_steps = [s for s in doc["steps"] if s.get("kind", "state") == "state"]
    idx = next((i for i, s in enumerate(state_steps) if s["step_id"] == step_id), None)
    if idx is None or idx == 0:
        return None
    prev = state_steps[idx - 1]
    # Walk approved generations from newest to oldest.
    for g in reversed(prev.get("generations", [])):
        if g.get("verdict") != "approve":
            continue
        proc = g.get("processed")
        if proc:
            p = session_path(session_id) / proc["merged"]
            if p.exists():
                return p
        raw = session_path(session_id) / g["file"]
        if raw.exists():
            return raw
    return None


def assembled_refs(session_id: str, step_id: str,
                   user_refs: list[Path]) -> list[Path]:
    """Build the final ref list for a generation:
       1. style_anchor (session-level) — pin style across all steps
       2. previous_approved_image (for state steps) — pin chain consistency
       3. user-uploaded refs for this step (in the order they were uploaded)
       Duplicates are dropped by absolute path."""
    doc = load(session_id)
    out: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path | None):
        if p is None:
            return
        key = str(p.resolve())
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    sa = doc.get("style_anchor")
    if sa:
        _add(session_path(session_id) / sa)
    _add(previous_approved_image(session_id, step_id))
    for r in user_refs:
        _add(r)
    return out


def archive(session_id: str) -> None:
    doc = load(session_id)
    doc["archived_at"] = _now_iso()
    save(doc)


def delete(session_id: str) -> None:
    p = session_path(session_id)
    if p.exists():
        shutil.rmtree(p)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

EMPTY_FIELDS = {
    # Used for both 'state' and 'tool' kinds — all optional.
    "step": "",
    "negative_prompt": "",
    "concept": "",
    "camera": "",
    "object_count": "",
    "object_shape": "",
    "object_position": "",
    "per_object_description": "",
    # tool-specific
    "step_consistency": "",
    "tool_count": "",
    "name": "",
    "tool_camera": "",
    "color": "",
    "style": "",
    "output": "",
    "ref_link": "",
}


def import_image_as_generation(session_id: str, step_id: str,
                               filename: str, data: bytes) -> dict:
    """For 'image' steps — treat an uploaded image as its own generation entry.
    Returns the generation record."""
    p = session_path(session_id) / step_id
    p.mkdir(parents=True, exist_ok=True)
    doc = load(session_id)
    step = next(s for s in doc["steps"] if s["step_id"] == step_id)
    n = len(step["generations"]) + 1
    # Use the original filename suffix; rename to gen_NNN so the rest of the
    # pipeline (postprocess output dir, naming) is consistent.
    ext = Path(filename).suffix.lower() or ".png"
    out_name = f"gen_{n:03d}{ext}"
    (p / out_name).write_bytes(data)
    rec = {
        "file": f"{step_id}/{out_name}",
        "prompt": f"(imported image: {filename})",
        "verdict": None,
        "comment": "",
        "ts": _now_iso(),
        "imported_from": filename,
    }
    step["generations"].append(rec)
    save(doc)
    return rec


def add_step(session_id: str, kind: str = "state") -> str:
    """Append an empty step. Returns the new step_id.
    kind: 'state' | 'tool' | 'image'  ('image' = pre-made, no prompt fields)."""
    doc = load(session_id)
    # Use max-existing-index+1 so deleting a middle step doesn't collide.
    used = []
    for s in doc["steps"]:
        sid = s.get("step_id", "")
        if sid.startswith("step_"):
            try:
                used.append(int(sid.split("_", 1)[1]))
            except ValueError:
                pass
    idx = (max(used) + 1) if used else 1
    step_id = f"step_{idx:02d}"
    doc["steps"].append({
        "step_id": step_id,
        "kind": kind,
        "fields": dict(EMPTY_FIELDS),
        "refs": [],
        "generations": [],
    })
    save(doc)
    (session_path(session_id) / step_id / "refs").mkdir(parents=True, exist_ok=True)
    return step_id


def rename_step(session_id: str, old_id: str, new_id: str) -> str:
    """Rename a step's step_id. Moves the folder on disk and updates every
    file path stored in `refs` / `generations` / `processed`. Idempotent;
    if `new_id` already exists, picks `new_id_2` etc."""
    if old_id == new_id:
        return old_id
    doc = load(session_id)
    used = {s["step_id"] for s in doc["steps"] if s["step_id"] != old_id}
    final_id = new_id
    n = 2
    while final_id in used or not final_id:
        final_id = f"{new_id}_{n}" if new_id else f"step_{n:02d}"
        n += 1

    base = session_path(session_id)
    old_dir = base / old_id
    new_dir = base / final_id
    if old_dir.exists() and not new_dir.exists():
        old_dir.rename(new_dir)

    for s in doc["steps"]:
        if s["step_id"] != old_id:
            continue
        s["step_id"] = final_id
        s["refs"] = [r.replace(f"{old_id}/", f"{final_id}/", 1) for r in s.get("refs", [])]
        for g in s.get("generations", []):
            g["file"] = g["file"].replace(f"{old_id}/", f"{final_id}/", 1)
            if g.get("processed"):
                g["processed"]["merged"] = g["processed"]["merged"].replace(f"{old_id}/", f"{final_id}/", 1)
                g["processed"]["parts"] = [
                    p.replace(f"{old_id}/", f"{final_id}/", 1)
                    for p in g["processed"].get("parts", [])
                ]
        break
    save(doc)
    return final_id


def reorder_step(session_id: str, step_id: str, direction: int) -> None:
    """Move a step up (-1) or down (+1) in the list."""
    doc = load(session_id)
    steps = doc["steps"]
    idx = next((i for i, s in enumerate(steps) if s["step_id"] == step_id), None)
    if idx is None:
        return
    new_idx = max(0, min(len(steps) - 1, idx + direction))
    if new_idx == idx:
        return
    steps.insert(new_idx, steps.pop(idx))
    save(doc)


def list_all_steps_for_prefill() -> list[dict]:
    """Every step across every session (active + archived) — for the
    'prefill from past' picker. Includes session label + verdict flag."""
    out: list[dict] = []
    for ses in (list_sessions(archived=False) + list_sessions(archived=True)):
        for s in ses.get("steps", []):
            has_app = any(g.get("verdict") == "approve"
                          for g in s.get("generations", []))
            out.append({
                "session_id": ses["id"],
                "session_name": ses.get("name", ses["id"]),
                "step_id": s["step_id"],
                "kind": s.get("kind", "state"),
                "fields": s.get("fields", {}),
                "has_approved": has_app,
                "archived": bool(ses.get("archived_at")),
            })
    return out


def remove_step(session_id: str, step_id: str) -> None:
    doc = load(session_id)
    doc["steps"] = [s for s in doc["steps"] if s["step_id"] != step_id]
    save(doc)


def update_fields(session_id: str, step_id: str, fields: dict) -> None:
    doc = load(session_id)
    for s in doc["steps"]:
        if s["step_id"] == step_id:
            s["fields"].update({k: v for k, v in fields.items() if k in EMPTY_FIELDS})
            break
    save(doc)


def add_ref(session_id: str, step_id: str, filename: str, data: bytes) -> str:
    """Save an uploaded reference image. Returns the relative path stored in session."""
    base = session_path(session_id) / step_id / "refs"
    base.mkdir(parents=True, exist_ok=True)
    # Avoid collisions.
    target = base / filename
    n = 1
    while target.exists():
        stem, dot, ext = filename.rpartition(".")
        target = base / f"{stem}_{n}.{ext}" if dot else base / f"{filename}_{n}"
        n += 1
    target.write_bytes(data)
    rel = f"{step_id}/refs/{target.name}"
    doc = load(session_id)
    for s in doc["steps"]:
        if s["step_id"] == step_id:
            if rel not in s["refs"]:
                s["refs"].append(rel)
            break
    save(doc)
    return rel


def remove_ref(session_id: str, step_id: str, rel_path: str) -> None:
    doc = load(session_id)
    for s in doc["steps"]:
        if s["step_id"] == step_id:
            s["refs"] = [r for r in s["refs"] if r != rel_path]
            break
    save(doc)
    p = session_path(session_id) / rel_path
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Generations
# ---------------------------------------------------------------------------

def record_generation(session_id: str, step_id: str, png_bytes: bytes,
                      prompt: str, *, backend: str | None = None,
                      model_id: str | None = None) -> dict:
    """Write the PNG and append a generation record. Returns the record dict.
    Stamps the backend + model_id + image dimensions on the record so the UI
    can show exactly what produced it."""
    p = session_path(session_id) / step_id
    p.mkdir(parents=True, exist_ok=True)
    doc = load(session_id)
    step = next(s for s in doc["steps"] if s["step_id"] == step_id)
    n = len(step["generations"]) + 1
    fname = f"gen_{n:03d}.png"
    (p / fname).write_bytes(png_bytes)
    (p / f"gen_{n:03d}.prompt.txt").write_text(prompt)

    # Read back the actual size from PNG bytes (cheaper than re-parsing).
    try:
        from PIL import Image
        from io import BytesIO
        size = Image.open(BytesIO(png_bytes)).size
    except Exception:
        size = None

    rec = {
        "file": f"{step_id}/{fname}",
        "prompt": prompt,
        "verdict": None,
        "comment": "",
        "ts": _now_iso(),
        "backend": backend,
        "model_id": model_id,
        "size": list(size) if size else None,
    }
    step["generations"].append(rec)
    save(doc)
    return rec


def set_verdict(session_id: str, step_id: str, gen_index: int,
                verdict: str | None, comment: str = "") -> None:
    doc = load(session_id)
    for s in doc["steps"]:
        if s["step_id"] == step_id:
            s["generations"][gen_index]["verdict"] = verdict
            s["generations"][gen_index]["comment"] = comment
            break
    save(doc)


def set_processed(session_id: str, step_id: str, gen_index: int,
                  merged_rel: str, parts_rel: list[str]) -> None:
    """Record the bg-removed + split outputs for an approved generation."""
    doc = load(session_id)
    for s in doc["steps"]:
        if s["step_id"] == step_id:
            s["generations"][gen_index]["processed"] = {
                "merged": merged_rel,
                "parts": list(parts_rel),
                "ts": _now_iso(),
            }
            break
    save(doc)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def abs_path(session_id: str, rel: str) -> Path:
    return session_path(session_id) / rel
