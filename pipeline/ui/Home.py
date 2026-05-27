"""Home — generic dashboard (no level concept).

Shows current active session (if any) + recent activity. Sessions are the
new primary unit of work; create one on the Generate tab.

Launch:  streamlit run pipeline/ui/Home.py
"""
from __future__ import annotations
import streamlit as st

from services import sessions
from services.memory_io import history as mem_history
from services.i18n import t, language_toggle


st.set_page_config(page_title="Shine It V6", page_icon="🧽", layout="wide")

with st.sidebar:
    st.markdown(f"### {t('app.title')}")
language_toggle()

st.title("🧽 Shine It V6")

cur = sessions.current_session()
all_active = sessions.list_sessions(archived=False)
all_archived = sessions.list_sessions(archived=True)

c1, c2, c3 = st.columns(3)
c1.metric("Active sessions", len(all_active))
c2.metric("Archived (History)", len(all_archived))
c3.metric("Total generations",
          sum(len(s.get("generations", [])) for ses in (all_active + all_archived)
              for s in ses.get("steps", [])))

st.divider()

if cur:
    st.subheader(f"🟢 {t('ses.current')}: `{cur['name']}`")
    n_steps = len(cur.get("steps", []))
    n_gens = sum(len(s.get("generations", [])) for s in cur.get("steps", []))
    st.caption(f"id `{cur['id']}` · backend `{cur.get('backend','?')}` · "
               f"{n_steps} steps · {n_gens} generations")
    st.markdown("Open **Generate** to keep working, **Review** to give verdicts, "
                "or archive the session to move it to **History**.")
else:
    st.info(t("ses.no_session"))

st.divider()
recent = mem_history(limit=5)
if recent:
    with st.container(border=True):
        st.markdown(f"**{t('home.recent_learning')}**")
        for h in recent:
            st.caption(f"`{h.get('ts','')}` · {h.get('step_type','?')} · "
                       f"{h.get('outcome','')} — {h.get('clause','')[:120]}")

st.divider()
st.markdown(t("home.tip"))
