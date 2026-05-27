"""CRUD over step_patterns.json. Thin wrapper around prompt_agent.memory."""
from __future__ import annotations
import sys
from pathlib import Path

from .projects import PIPELINE_DIR

# Make `prompt_agent` importable.
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from prompt_agent import memory as _mem  # noqa: E402


PATTERNS_PATH: Path = _mem.PATTERNS_PATH


def load() -> dict:
    return _mem.load_patterns()


def save(doc: dict) -> None:
    _mem.save_patterns(doc)


def list_step_types() -> list[str]:
    return _mem.list_step_types()


def get(step_type: str) -> dict:
    p = _mem.get_pattern(step_type) or {}
    return _mem.ensure_pattern_fields(dict(p))


def update_pattern(step_type: str, *, description: str | None = None,
                   best_practices: list[str] | None = None,
                   forbid: list[str] | None = None,
                   required_qualities: list[str] | None = None,
                   common_pitfalls: list[str] | None = None,
                   sensory_words: list[str] | None = None,
                   candidates: list[dict] | None = None) -> None:
    """Patch one pattern. Pass only what changed."""
    doc = load()
    doc.setdefault("patterns", {})
    pat = _mem.ensure_pattern_fields(dict(doc["patterns"].get(step_type, {})))
    if description is not None:
        pat["description"] = description
    if best_practices is not None:
        pat["best_practices"] = best_practices
    if forbid is not None:
        pat["forbid"] = forbid
    if required_qualities is not None:
        pat["required_qualities"] = required_qualities
    if common_pitfalls is not None:
        pat["common_pitfalls"] = common_pitfalls
    if sensory_words is not None:
        pat["sensory_words"] = sensory_words
    if candidates is not None:
        pat["candidates"] = candidates
    doc["patterns"][step_type] = pat
    save(doc)


def promote_candidate(step_type: str, candidate_idx: int) -> str:
    """Move a candidate into best_practices (or forbid) based on its polarity.
    Returns the clause that was promoted."""
    doc = load()
    pat = _mem.ensure_pattern_fields(doc["patterns"][step_type])
    cand = pat["candidates"].pop(candidate_idx)
    clause = cand.get("promoted_clause") or cand.get("text", "").strip()
    polarity = cand.get("polarity", "best_practice")
    target = "forbid" if polarity == "forbid" else "best_practices"
    if clause and clause not in pat[target]:
        pat[target].append(clause)
    save(doc)
    return clause


def history(limit: int = 20) -> list[dict]:
    doc = load()
    hist = doc.get("history", []) or []
    return list(reversed(hist[-limit:]))
