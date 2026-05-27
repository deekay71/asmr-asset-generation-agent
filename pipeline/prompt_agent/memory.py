"""Read/write step_patterns.json (the learned pattern library)."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any


def _resolve_patterns_path() -> Path:
    """Find step_patterns.json by walking up from this file."""
    here = Path(__file__).resolve().parent
    for candidate in (here.parent, here.parent.parent, here.parent.parent.parent):
        p = candidate / "step_patterns.json"
        if p.exists():
            return p
    # Default: write next to the agent if missing
    return here.parent / "step_patterns.json"


PATTERNS_PATH = _resolve_patterns_path()


def load_patterns() -> dict:
    """Load the full step_patterns.json document. Returns the top-level dict.
    If missing, returns an empty schema-shaped dict."""
    if not PATTERNS_PATH.exists():
        return {"schema_version": "1.0", "patterns": {}, "history": []}
    try:
        with open(PATTERNS_PATH) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"[ERR] {PATTERNS_PATH} is malformed JSON: {e}")


def save_patterns(doc: dict) -> None:
    """Write back step_patterns.json, formatted."""
    with open(PATTERNS_PATH, "w") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def get_pattern(step_type: str) -> dict | None:
    """Look up one pattern by id. Returns None if not found."""
    doc = load_patterns()
    return doc.get("patterns", {}).get(step_type)


def ensure_pattern_fields(pattern: dict) -> dict:
    """Ensure a pattern dict has all V6 fields (forbid, candidates).
    Returns the same dict (mutated in place)."""
    pattern.setdefault("description", "")
    pattern.setdefault("required_qualities", [])
    pattern.setdefault("best_practices", [])
    pattern.setdefault("common_pitfalls", [])
    pattern.setdefault("forbid", [])
    pattern.setdefault("candidates", [])
    pattern.setdefault("sensory_words", [])
    return pattern


def list_step_types() -> list[str]:
    """All known step_type ids."""
    return sorted(load_patterns().get("patterns", {}).keys())
