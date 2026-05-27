"""Preview the composed prompt for an asset without spending API credits.

Calls prompt_agent.compose.compose() with allow_llm_assist=False so no
network calls are made — just the deterministic slot rendering.
"""
from __future__ import annotations
import sys
from pathlib import Path

from .projects import PIPELINE_DIR

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from prompt_agent.compose import compose, ComposedPrompt  # noqa: E402


def preview(level_dir: Path, cfg: dict, asset: dict, asset_kind: str,
            prev_state_id: str | None = None,
            regen_comment: str = "") -> ComposedPrompt:
    """Run compose() with no LLM assist. Returns ComposedPrompt(text, log, confidence)."""
    return compose(level_dir, cfg, asset, asset_kind,
                   prev_state_id=prev_state_id,
                   regen_comment=regen_comment,
                   allow_llm_assist=False)
