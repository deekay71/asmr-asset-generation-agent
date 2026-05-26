"""V6 — Prompt Agent.

A self-improving prompt-engineering layer for the Shine It pipeline. Replaces
the V3/V4/V5 envelope wrapper with structured slot-filled templates per
step_type. Learns from user feedback (via review_chain.html → regen_queue.json)
by promoting recurring lessons into permanent best_practices and forbid rules
in step_patterns.json.

Public API:
    from prompt_agent import compose, learn

    # Before generation:
    composed = compose(level_dir, cfg, asset_dict, asset_kind, prev_state_id=None)
    # composed.text          → the assembled prompt (sent to backend)
    # composed.log           → JSON-able record of choices made
    # composed.confidence    → 0-1 confidence in the assembly

    # After user review:
    learn(level_dir, regen_queue_path)
    # → reads regen_queue.json, distils comments, updates step_patterns.json
"""
from .compose import compose, ComposedPrompt
from .learn import learn

__all__ = ["compose", "learn", "ComposedPrompt"]
