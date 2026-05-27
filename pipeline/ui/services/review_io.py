"""Read/write approved_ids.json and regen_queue.json per the schema that
phase_6_review writes (see shine_it_pipeline.py:1599-1617)."""
from __future__ import annotations
import datetime as _dt
import json
from pathlib import Path

APPROVED = "approved_ids.json"
REGEN = "regen_queue.json"


def _now() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def load_verdicts(level_dir: Path) -> dict:
    """Return the union of any saved verdicts so the UI can rehydrate state."""
    out = {"approved": [], "regen": [], "rejected": []}
    ap = level_dir / APPROVED
    if ap.exists():
        try:
            out["approved"] = json.loads(ap.read_text()).get("approved", [])
        except Exception:
            pass
    rq = level_dir / REGEN
    if rq.exists():
        try:
            doc = json.loads(rq.read_text())
            out["regen"] = doc.get("regen", [])
            out["rejected"] = doc.get("rejected", [])
        except Exception:
            pass
    return out


def save_verdicts(level_dir: Path, *, level: int, name: str,
                  approved: list[str], regen: list[dict], rejected: list[str]) -> tuple[Path, Path]:
    """Write both files, return their paths."""
    ts = _now()
    approved_doc = {"level": level, "name": name, "saved_at": ts, "approved": approved}
    regen_doc = {"level": level, "name": name, "saved_at": ts,
                 "regen": regen, "rejected": rejected}
    ap = level_dir / APPROVED
    rq = level_dir / REGEN
    ap.write_text(json.dumps(approved_doc, indent=2))
    rq.write_text(json.dumps(regen_doc, indent=2))
    return ap, rq
