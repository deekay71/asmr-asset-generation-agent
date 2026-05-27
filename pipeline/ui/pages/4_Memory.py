"""Memory — CRUD over step_patterns.json (the agent's persistent brain)."""
from __future__ import annotations
import streamlit as st

from services import memory_io, learner
from services.i18n import t, language_toggle, get_lang


language_toggle()
st.title(t("mem.title"))
st.caption(f"`{memory_io.PATTERNS_PATH}`")

# ── Cross-session learner ────────────────────────────────────────────────
with st.container(border=True):
    st.markdown("### 🔬 Analyze patterns across all sessions")
    st.caption(
        "Walks every approved generation, deny, and regen comment across active + "
        "archived sessions. Distils what's working and what isn't, and appends "
        "the new rules as **candidates** here — promote the ones you like."
    )
    if st.button("🔬 Analyze patterns now", type="primary"):
        try:
            sig = learner.collect_signals()
            counts = (f"approved={len(sig['approved'])} · "
                      f"denied={len(sig['denied'])} · "
                      f"regen={len(sig['regen'])}")
            with st.spinner(f"Analyzing {counts}…"):
                result = learner.analyse(sig, lang=get_lang())
                summary = learner.merge_into_patterns(result)
            st.success(f"+{summary['candidates_added']} candidate(s) added.")
            if summary.get("summary"):
                st.info(f"**Summary:** {summary['summary']}")
        except Exception as e:
            import traceback
            st.error(f"Learner failed: {type(e).__name__}: {e}")
            with st.expander("Traceback"):
                st.code(traceback.format_exc())

st.divider()

step_types = memory_io.list_step_types()
if not step_types:
    st.warning(t("mem.empty"))
    st.stop()

pick = st.selectbox(t("mem.pattern"), step_types)
pat = memory_io.get(pick)

description = st.text_area(t("mem.description"), value=pat.get("description", ""), height=80)


def _list_editor(label: str, items: list[str], key: str) -> list[str]:
    blob = st.text_area(label, value="\n".join(items), height=180, key=key)
    return [line.rstrip() for line in blob.splitlines() if line.strip()]


col1, col2 = st.columns(2)
with col1:
    best_practices = _list_editor(t("mem.best_practices"), pat.get("best_practices", []), "bp")
    required_qualities = _list_editor(t("mem.required_qualities"), pat.get("required_qualities", []), "rq")
with col2:
    forbid = _list_editor(t("mem.forbid"), pat.get("forbid", []), "fb")
    common_pitfalls = _list_editor(t("mem.common_pitfalls"), pat.get("common_pitfalls", []), "cp")

sensory_words = _list_editor(t("mem.sensory_words"), pat.get("sensory_words", []), "sw")

if st.button(t("mem.save_pattern"), type="primary"):
    memory_io.update_pattern(
        pick,
        description=description,
        best_practices=best_practices,
        forbid=forbid,
        required_qualities=required_qualities,
        common_pitfalls=common_pitfalls,
        sensory_words=sensory_words,
    )
    st.success(t("mem.saved", name=pick))

st.divider()
st.subheader(t("mem.candidates"))

cands = pat.get("candidates", [])
if not cands:
    st.caption(t("mem.no_candidates"))
else:
    for idx, c in enumerate(list(cands)):
        with st.container(border=True):
            cols = st.columns([4, 1, 1])
            cols[0].markdown(
                f"**{c.get('polarity','best_practice')}** · seen {c.get('seen_count',1)}× "
                f"· from `{','.join(c.get('from_assets', []))}`"
            )
            cols[0].markdown(f"> {c.get('text','')}")
            if c.get("source_comment"):
                cols[0].caption(f"source: {c['source_comment'][:200]}")
            if cols[1].button(t("common.promote"), key=f"prom_{pick}_{idx}"):
                clause = memory_io.promote_candidate(pick, idx)
                st.success(f"{t('common.promote')}: {clause}")
                st.rerun()
            if cols[2].button(t("common.delete"), key=f"del_{pick}_{idx}"):
                doc = memory_io.load()
                doc["patterns"][pick]["candidates"].pop(idx)
                memory_io.save(doc)
                st.rerun()

st.divider()
st.subheader(t("mem.history"))
hist = memory_io.history(limit=20)
if not hist:
    st.caption(t("mem.no_history"))
else:
    for h in hist:
        st.caption(
            f"`{h.get('ts','')}` · {h.get('step_type','?')} · {h.get('outcome','')} — "
            f"{h.get('clause','')[:140]}"
        )
