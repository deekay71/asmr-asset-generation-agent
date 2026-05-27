"""Analytics derived from runs/ sessions (active + archived)."""
from __future__ import annotations
import pandas as pd

from . import sessions


BACKEND_COST = {
    "google_flash": 0.040,
    "google_nb2_2k": 0.040,
    "google_nb_pro_2k": 0.140,
    "fal_nb2": 0.030,
    "fal_nb_pro": 0.060,
}


def all_generations() -> pd.DataFrame:
    """One row per generation across every session.

    Uses the per-generation `backend` field if present (newer records);
    falls back to the session's current backend for older records that
    pre-date generation-level backend stamping.
    """
    rows: list[dict] = []
    for ses in (sessions.list_sessions(archived=False)
                + sessions.list_sessions(archived=True)):
        session_backend = ses.get("backend") or "unknown"
        for step in ses.get("steps", []):
            for g in step.get("generations", []):
                gen_backend = g.get("backend") or session_backend
                cost = BACKEND_COST.get(gen_backend, 0.0)
                size = g.get("size") or [None, None]
                rows.append({
                    "session_id": ses["id"],
                    "session_name": ses.get("name", ses["id"]),
                    "archived": bool(ses.get("archived_at")),
                    "step_id": step["step_id"],
                    "kind": step.get("kind", "state"),
                    "backend": gen_backend,
                    "model_id": g.get("model_id") or "?",
                    "cost": cost,
                    "verdict": g.get("verdict"),
                    "ts": g.get("ts"),
                    "width": size[0],
                    "height": size[1],
                })
    df = pd.DataFrame(rows)
    if not df.empty and "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    return df


def approval_by_kind() -> pd.DataFrame:
    df = all_generations()
    if df.empty:
        return pd.DataFrame(columns=["kind", "total", "approved", "denied", "regen", "approval_pct"])
    df["is_app"] = (df["verdict"] == "approve").astype(int)
    df["is_den"] = (df["verdict"] == "deny").astype(int)
    df["is_rgn"] = (df["verdict"] == "regen").astype(int)
    g = df.groupby("kind").agg(total=("verdict", "size"),
                                approved=("is_app", "sum"),
                                denied=("is_den", "sum"),
                                regen=("is_rgn", "sum"))
    g["approval_pct"] = (g["approved"] / g["total"] * 100).round(1)
    return g.reset_index()
