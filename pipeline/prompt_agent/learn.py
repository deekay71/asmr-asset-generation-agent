"""V6 learn() — process user feedback into permanent pattern rules.

Reads regen_queue.json (written by the review HTML) and updates
step_patterns.json:
  • Distils each comment into a 1-line candidate rule via Gemini Flash text
  • Detects whether it's a positive guideline (best_practice) or anti-pattern (forbid)
  • Tracks recurrence: 2 occurrences of similar feedback → promotion
  • Appends to the pattern's history log

Returns a summary of changes made.
"""
from __future__ import annotations
import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .memory import load_patterns, save_patterns, ensure_pattern_fields

# Promotion threshold: candidate must be seen this many times to become permanent
PROMOTION_THRESHOLD = 2

# Crude similarity threshold (0–1). Two candidates with normalised-text overlap
# above this are treated as "the same lesson."
SIMILARITY_THRESHOLD = 0.55


@dataclass
class LearnSummary:
    """Summary of one learning pass."""
    queue_path: Path
    entries_processed: int = 0
    candidates_added: int = 0
    candidates_incremented: int = 0
    rules_promoted: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def banner(self) -> str:
        """One-line summary for the review HTML banner."""
        n = len(self.rules_promoted)
        if n == 0 and self.candidates_added == 0:
            return ""
        parts = []
        if n > 0:
            parts.append(f"{n} new rule(s) learned & promoted")
        if self.candidates_added > 0:
            parts.append(f"{self.candidates_added} candidate(s) added (need {PROMOTION_THRESHOLD-1} more occurrence to promote)")
        return " · ".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def _norm(text: str) -> set[str]:
    """Word-level normalisation for similarity comparison."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _similar(a: str, b: str) -> float:
    """Jaccard similarity between two text snippets, 0–1."""
    A, B = _norm(a), _norm(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _step_type_for(level_dir: Path, asset_id: str) -> str | None:
    """Look up an asset's step_type from items_config.json by id."""
    cfg_path = level_dir / "items_config.json"
    if not cfg_path.exists():
        return None
    cfg = json.loads(cfg_path.read_text())
    # Scan all asset arrays for matching id
    for key in ("states", "subparts", "trash_overlays", "overlay_effects",
                "style_variants", "backgrounds", "tools_required", "subflows"):
        for it in cfg.get(key, []):
            if isinstance(it, dict) and it.get("id") == asset_id:
                # subflows + style variants don't have step_type — infer
                if it.get("step_type"):
                    return it["step_type"]
                if key == "subflows":
                    return "subflow_composite"
                if key == "style_variants":
                    return "polish_complete"
                if key == "trash_overlays":
                    return "trash_overlay"
                if key == "backgrounds":
                    return "background_scene"
                if key == "tools_required":
                    return "tool_sprite"
                return None
    return None


# ---------------------------------------------------------------------------
# LLM distillation
# ---------------------------------------------------------------------------

_LLM_CLIENT_CACHE = {"client": None, "tried": False}


def _llm_client():
    if _LLM_CLIENT_CACHE["tried"]:
        return _LLM_CLIENT_CACHE["client"]
    _LLM_CLIENT_CACHE["tried"] = True
    try:
        import os
        from google import genai
        sa_path = None
        here = Path(__file__).resolve().parent
        for c in (here.parent.parent / "gemini_service_account.json",
                  here.parent.parent.parent / "gemini_service_account.json"):
            if c.exists():
                sa_path = str(c)
                break
        if sa_path and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
        proj = None
        if sa_path:
            try:
                proj = json.load(open(sa_path)).get("project_id")
            except Exception:
                pass
        if not proj:
            return None
        client = genai.Client(vertexai=True, project=proj, location="us-central1")
        _LLM_CLIENT_CACHE["client"] = client
        return client
    except Exception:
        return None


def _distil_feedback(step_type: str, asset_id: str, verdict: str,
                     comment: str) -> dict | None:
    """Use Gemini Flash text to turn one piece of feedback into a structured rule.
    Returns:
        {
            "polarity": "best_practice" | "forbid",
            "clause": "≤80 chars rule text",
            "rationale": "one-sentence explanation"
        }
    or None on error.
    """
    client = _llm_client()
    if client is None:
        # Fallback: heuristic — keyword-based polarity detection
        is_negative = bool(re.search(
            r"\b(not|don'?t|shouldn'?t|never|avoid|wrong|too|overly|excessive|bleed|outside)\b",
            comment.lower(),
        ))
        return {
            "polarity": "forbid" if is_negative else "best_practice",
            "clause": comment.strip()[:120],
            "rationale": "(LLM unavailable — heuristic polarity)",
        }
    prompt = (
        f"You are extracting a permanent rule for a 2D ASMR cleaning game prompt library.\n"
        f"Step type: {step_type}\n"
        f"Asset that triggered the feedback: {asset_id}\n"
        f"User verdict: {verdict}\n"
        f"User comment: \"{comment}\"\n\n"
        f"Output STRICT JSON (no markdown, no extra text):\n"
        f"{{\n"
        f'  "polarity": "best_practice" or "forbid",\n'
        f'  "clause": "≤80 chars, written as an imperative rule clause",\n'
        f'  "rationale": "one short sentence explaining the rule"\n'
        f"}}\n\n"
        f"Use \"forbid\" if the feedback describes something that SHOULD NOT happen.\n"
        f"Use \"best_practice\" if it describes something that SHOULD happen.\n"
    )
    try:
        r = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = (r.text or "").strip()
        # Strip code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        if parsed.get("polarity") in ("best_practice", "forbid") and parsed.get("clause"):
            return parsed
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def learn(level_dir: Path, queue_path: Path | None = None) -> LearnSummary:
    """Process regen_queue.json into permanent step_pattern updates.
    Returns a LearnSummary describing what changed."""
    queue_path = Path(queue_path or (level_dir / "regen_queue.json"))
    summary = LearnSummary(queue_path=queue_path)

    if not queue_path.exists():
        summary.skipped.append(f"no regen_queue.json at {queue_path}")
        return summary

    queue_data = json.loads(queue_path.read_text())
    regen_entries = queue_data.get("regen", [])

    if not regen_entries:
        summary.skipped.append("regen_queue.json has no regen entries")
        return summary

    doc = load_patterns()
    doc.setdefault("history", [])
    patterns = doc.setdefault("patterns", {})

    for entry in regen_entries:
        asset_id = entry.get("id")
        comment = (entry.get("comment") or "").strip()
        if not asset_id or not comment:
            summary.skipped.append(f"{asset_id or '?'}: empty comment or id")
            continue

        step_type = _step_type_for(level_dir, asset_id)
        if not step_type:
            summary.skipped.append(f"{asset_id}: no step_type resolved")
            continue

        # Ensure pattern exists
        pattern = patterns.setdefault(step_type, {})
        ensure_pattern_fields(pattern)

        # Distil
        distilled = _distil_feedback(step_type, asset_id, "regen", comment)
        if not distilled:
            summary.skipped.append(f"{asset_id}: distillation failed")
            continue

        clause = distilled["clause"].strip()
        polarity = distilled["polarity"]
        target_list_key = "forbid" if polarity == "forbid" else "best_practices"

        # Idempotency check: already in target list?
        existing_rules = pattern.get(target_list_key, [])
        if any(_similar(clause, r) > SIMILARITY_THRESHOLD for r in existing_rules):
            summary.skipped.append(f"{asset_id}: rule already in {target_list_key}")
            continue

        # Check candidates for recurrence
        candidates = pattern.setdefault("candidates", [])
        match = None
        for cand in candidates:
            if cand.get("polarity") != polarity:
                continue
            if _similar(clause, cand.get("text", "")) > SIMILARITY_THRESHOLD:
                match = cand
                break

        if match:
            match["seen_count"] = match.get("seen_count", 1) + 1
            match.setdefault("from_assets", []).append(asset_id)
            match["last_seen"] = _now()
            summary.candidates_incremented += 1

            if match["seen_count"] >= PROMOTION_THRESHOLD and not match.get("promoted_at"):
                pattern[target_list_key].append(clause)
                match["promoted_at"] = _now()
                match["promoted_clause"] = clause
                summary.rules_promoted.append({
                    "step_type": step_type,
                    "polarity": polarity,
                    "clause": clause,
                    "from_assets": match.get("from_assets", []),
                })
        else:
            # First-time candidate
            candidates.append({
                "polarity": polarity,
                "text": clause,
                "seen_count": 1,
                "first_seen": _now(),
                "last_seen": _now(),
                "from_assets": [asset_id],
                "source_comment": comment,
                "rationale": distilled.get("rationale", ""),
            })
            summary.candidates_added += 1

        # Append history
        doc["history"].append({
            "ts": _now(),
            "pattern": step_type,
            "asset_id": asset_id,
            "verdict": "regen",
            "comment": comment,
            "distilled_clause": clause,
            "polarity": polarity,
        })

        summary.entries_processed += 1

    save_patterns(doc)
    return summary
