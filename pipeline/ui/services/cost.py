"""Cost aggregation across all levels by reading cost_log.jsonl files."""
from __future__ import annotations
import json
from pathlib import Path

import pandas as pd

from .projects import scan_levels


def load_cost_df() -> pd.DataFrame:
    """Concatenate every level's cost_log.jsonl into a single DataFrame.
    Columns: ts, phase, asset_id, model, cost, success, level, level_name."""
    rows: list[dict] = []
    for lv in scan_levels():
        log = lv.path / "cost_log.jsonl"
        if not log.exists():
            continue
        for line in log.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            d["level"] = lv.level
            d["level_name"] = lv.name
            rows.append(d)
    if not rows:
        return pd.DataFrame(columns=["ts", "phase", "asset_id", "model", "cost", "success",
                                     "level", "level_name"])
    df = pd.DataFrame(rows)
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    if "cost" in df.columns:
        df["cost"] = pd.to_numeric(df["cost"], errors="coerce").fillna(0.0)
    return df
