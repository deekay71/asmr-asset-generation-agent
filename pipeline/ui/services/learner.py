"""Cross-session pattern learner.

Walks every approved generation (positive signal) and every regen / deny
comment (correction signal) across all sessions. Calls Gemini Flash text to:

  1. extract common qualities of approved prompts (what works)
  2. summarise common regen / deny reasons (what to avoid)

Outputs are appended to `pipeline/step_patterns.json`:
  - global section "_cross_session" gets `best_practices` + `forbid` lists
  - per-step-type sections get matching candidates promoted on 2× recurrence
"""
from __future__ import annotations
import datetime as _dt
import json
import os
import sys

from . import sessions
from .projects import PIPELINE_DIR

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from prompt_agent import memory as _mem  # noqa: E402


def _now() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _genai_client():
    from dotenv import load_dotenv
    load_dotenv(str(PIPELINE_DIR.parent / ".env"))
    key = os.environ.get("GOOGLE_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("No GOOGLE_KEY / GEMINI_API_KEY in env or .env")
    from google import genai
    return genai.Client(api_key=key)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect_signals() -> dict:
    """Walk every session and bucket generations by verdict.
    Returns:
      {
        'approved':  [{prompt, kind, session_name, step_id}, ...],
        'denied':    [{prompt, comment, kind, session_name, step_id}, ...],
        'regen':     [{prompt, comment, kind, session_name, step_id}, ...]
      }
    """
    out = {"approved": [], "denied": [], "regen": []}
    for ses in (sessions.list_sessions(archived=False)
                + sessions.list_sessions(archived=True)):
        for step in ses.get("steps", []):
            kind = step.get("kind", "state")
            for g in step.get("generations", []):
                v = g.get("verdict")
                if v not in ("approve", "deny", "regen"):
                    continue
                rec = {
                    "prompt": (g.get("prompt") or "")[:1200],
                    "comment": (g.get("comment") or "")[:600],
                    "kind": kind,
                    "session_name": ses.get("name", ses["id"]),
                    "step_id": step["step_id"],
                }
                out["approved" if v == "approve"
                    else "denied" if v == "deny"
                    else "regen"].append(rec)
    return out


# ---------------------------------------------------------------------------
# LLM analysis
# ---------------------------------------------------------------------------

SYS_PROMPT_EN = """You analyse image-generation prompts for an ASMR cleaning game.

You receive three buckets:
  APPROVED — prompts whose outputs the artist accepted
  DENIED  — prompts whose outputs were rejected (often with a comment)
  REGEN   — prompts marked for re-generation (with a correction comment)

Extract patterns. Output strict JSON ONLY (no prose, no fences):

{
  "best_practices": [
    "<≤80-char rule that consistently appears in APPROVED prompts>",
    ...
  ],
  "forbid": [
    "<≤80-char anti-pattern derived from DENIED/REGEN comments>",
    ...
  ],
  "by_kind": {
    "state": {"best_practices": [...], "forbid": [...]},
    "tool":  {"best_practices": [...], "forbid": [...]},
    "image": {"best_practices": [...], "forbid": [...]}
  },
  "summary": "<2-3 sentence plain-English summary of what's working and what isn't>"
}

Rules:
- 4–10 items per list maximum, prioritised by recurrence.
- Each rule must be self-contained, actionable, and ≤80 characters.
- Skip a `by_kind` block when there's no signal for that kind.
- Do not invent rules — only ones supported by the data.
"""


SYS_PROMPT_VI = """Bạn phân tích các prompt tạo ảnh cho game ASMR dọn dẹp.

Bạn nhận 3 nhóm:
  APPROVED — prompt mà nghệ sĩ đã duyệt
  DENIED   — prompt bị từ chối (thường có nhận xét)
  REGEN    — prompt cần tạo lại (kèm nhận xét hiệu chỉnh)

Trích xuất các pattern. CHỈ xuất JSON (không prose, không fences):

{
  "best_practices": [
    "<quy tắc ≤80 ký tự, lặp lại trong APPROVED>",
    ...
  ],
  "forbid": [
    "<anti-pattern ≤80 ký tự, suy ra từ DENIED/REGEN>",
    ...
  ],
  "by_kind": {
    "state": {"best_practices": [...], "forbid": [...]},
    "tool":  {"best_practices": [...], "forbid": [...]},
    "image": {"best_practices": [...], "forbid": [...]}
  },
  "summary": "<2-3 câu tiếng Việt tóm tắt điều gì đang hiệu quả và điều gì chưa>"
}

Quy tắc:
- Tối đa 4–10 mục mỗi list, ưu tiên theo tần suất.
- Mỗi quy tắc phải tự đứng được, có thể hành động, ≤80 ký tự, viết bằng TIẾNG VIỆT.
- Bỏ block by_kind nào không có tín hiệu.
- Không bịa quy tắc — chỉ những gì dữ liệu hỗ trợ.
"""


def analyse(signals: dict | None = None, lang: str = "en") -> dict:
    """Run the LLM analysis and return the parsed JSON dict.
    Caller is responsible for merging this into step_patterns.json."""
    signals = signals or collect_signals()
    if not any(signals.values()):
        raise RuntimeError("No verdict data yet — approve / deny / regen something first.")

    sys_prompt = SYS_PROMPT_VI if lang == "vi" else SYS_PROMPT_EN
    user_intro = "Xuất JSON ngay." if lang == "vi" else "Output the JSON now."
    user = (
        f"APPROVED ({len(signals['approved'])}):\n"
        + json.dumps(signals["approved"], indent=2, ensure_ascii=False)[:6000]
        + f"\n\nDENIED ({len(signals['denied'])}):\n"
        + json.dumps(signals["denied"], indent=2, ensure_ascii=False)[:4000]
        + f"\n\nREGEN ({len(signals['regen'])}):\n"
        + json.dumps(signals["regen"], indent=2, ensure_ascii=False)[:4000]
        + f"\n\n{user_intro}"
    )

    client = _genai_client()
    r = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[sys_prompt, user],
    )
    text = (r.text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Learner returned non-JSON: {e}\n\n{text[:500]}")


# ---------------------------------------------------------------------------
# Merge into step_patterns.json
# ---------------------------------------------------------------------------

def merge_into_patterns(result: dict) -> dict:
    """Persist learner output into step_patterns.json.

    Strategy:
    - Global lists land under a `_cross_session` pseudo-pattern (so the user
      can review them).
    - Per-kind lists land into the matching step_type (treating 'state' as
      `dirty_base`, since that's the closest concrete pattern; tools land in
      `tool_sprite`; images skipped).
    - We DO NOT auto-promote to permanent — append as `candidates` so the
      user can review in the Memory tab and promote intentionally.
    """
    doc = _mem.load_patterns()
    doc.setdefault("patterns", {})
    doc.setdefault("history", [])

    # 1. Global cross-session pseudo-pattern
    cs = doc["patterns"].setdefault("_cross_session", {
        "description": "Cross-session patterns learned from approved + regen + deny verdicts.",
        "best_practices": [], "forbid": [], "candidates": [],
        "required_qualities": [], "common_pitfalls": [], "sensory_words": [],
    })
    _mem.ensure_pattern_fields(cs)

    added = 0
    for clause in result.get("best_practices", []) or []:
        clause = (clause or "").strip()[:80]
        if not clause:
            continue
        if clause in cs["best_practices"]:
            continue
        if any(c.get("text") == clause for c in cs["candidates"]):
            continue
        cs["candidates"].append({
            "polarity": "best_practice",
            "text": clause,
            "seen_count": 1,
            "first_seen": _now(),
            "last_seen": _now(),
            "from_assets": ["cross_session_learner"],
            "source_comment": "cross-session learner",
        })
        added += 1
    for clause in result.get("forbid", []) or []:
        clause = (clause or "").strip()[:80]
        if not clause:
            continue
        if clause in cs["forbid"]:
            continue
        if any(c.get("text") == clause for c in cs["candidates"]):
            continue
        cs["candidates"].append({
            "polarity": "forbid",
            "text": clause,
            "seen_count": 1,
            "first_seen": _now(),
            "last_seen": _now(),
            "from_assets": ["cross_session_learner"],
            "source_comment": "cross-session learner",
        })
        added += 1

    # 2. Per-kind → mapped patterns
    kind_to_step = {"state": "dirty_base", "tool": "tool_sprite"}
    for kind, payload in (result.get("by_kind") or {}).items():
        target = kind_to_step.get(kind)
        if not target:
            continue
        pat = doc["patterns"].setdefault(target, {
            "description": "",
            "best_practices": [], "forbid": [], "candidates": [],
        })
        _mem.ensure_pattern_fields(pat)
        for clause in payload.get("best_practices", []) or []:
            clause = (clause or "").strip()[:80]
            if not clause or clause in pat["best_practices"]:
                continue
            if any(c.get("text") == clause for c in pat["candidates"]):
                continue
            pat["candidates"].append({
                "polarity": "best_practice",
                "text": clause,
                "seen_count": 1,
                "first_seen": _now(),
                "last_seen": _now(),
                "from_assets": ["cross_session_learner"],
            })
            added += 1
        for clause in payload.get("forbid", []) or []:
            clause = (clause or "").strip()[:80]
            if not clause or clause in pat["forbid"]:
                continue
            if any(c.get("text") == clause for c in pat["candidates"]):
                continue
            pat["candidates"].append({
                "polarity": "forbid",
                "text": clause,
                "seen_count": 1,
                "first_seen": _now(),
                "last_seen": _now(),
                "from_assets": ["cross_session_learner"],
            })
            added += 1

    doc["history"].append({
        "ts": _now(),
        "step_type": "_cross_session",
        "outcome": "analysis",
        "summary": result.get("summary", ""),
        "candidates_added": added,
    })
    _mem.save_patterns(doc)
    return {"candidates_added": added,
            "summary": result.get("summary", ""),
            "cross_session_pattern": cs}
