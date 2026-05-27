"""Insights — sessions, generations, cost, approval rate."""
from __future__ import annotations
import datetime as _dt

import pandas as pd
import streamlit as st

from services.insights import all_generations, approval_by_kind
from services.i18n import t, language_toggle


language_toggle()
st.title(t("ins.title"))

df = all_generations()
if df.empty:
    st.info("No generations yet. Create a session and click ▶️ Generate.")
    st.stop()

total_gens = len(df)
total_cost = float(df["cost"].sum())
week_ago = pd.Timestamp(_dt.datetime.utcnow() - _dt.timedelta(days=7), tz="UTC")
last_week = df[df["ts"] >= week_ago] if "ts" in df else df
week_cost = float(last_week["cost"].sum()) if not last_week.empty else 0.0
verdicted = df.dropna(subset=["verdict"])
approved_pct = ((verdicted["verdict"] == "approve").mean() * 100) if not verdicted.empty else 0.0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Generations", total_gens)
c2.metric(t("ins.total"), f"${total_cost:.3f}")
c3.metric(t("ins.week"), f"${week_cost:.3f}")
c4.metric("Approved %", f"{approved_pct:.0f}%")

st.divider()
col1, col2, col3 = st.columns(3)
with col1:
    st.caption("Cost by backend")
    st.bar_chart(df.groupby("backend")["cost"].sum())
with col2:
    st.caption("Generations by session")
    st.bar_chart(df.groupby("session_name").size())
with col3:
    st.caption("Generations by step kind")
    st.bar_chart(df.groupby("kind").size())

st.divider()
st.subheader(t("ins.approval_title"))
ak = approval_by_kind()
if not ak.empty:
    st.dataframe(ak, use_container_width=True, hide_index=True)

# Verdict breakdown
st.divider()
st.subheader("Verdict mix")
vdf = df.copy()
vdf["verdict"] = vdf["verdict"].fillna("—")
vc = vdf["verdict"].value_counts().reset_index()
vc.columns = ["verdict", "count"]
st.dataframe(vc, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Recent generations")
# Build a tidy view with model + size visible.
view = df.sort_values("ts", ascending=False).head(50).copy()
view["resolution"] = view.apply(
    lambda r: f"{int(r['width'])}×{int(r['height'])}"
    if pd.notna(r["width"]) and pd.notna(r["height"]) else "?",
    axis=1,
)
st.dataframe(
    view[["ts", "session_name", "step_id", "kind", "backend", "model_id",
          "resolution", "cost", "verdict"]],
    use_container_width=True, hide_index=True,
)
