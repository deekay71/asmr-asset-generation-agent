"""History — browse archived sessions."""
from __future__ import annotations
from pathlib import Path

from PIL import Image
import streamlit as st

from services import sessions
from services.i18n import t, language_toggle


language_toggle()
st.title(t("his.title"))

archived = sessions.list_sessions(archived=True)
if not archived:
    st.info(t("his.none"))
    st.stop()

# Sidebar list of sessions.
opened = st.session_state.get("history_open_id")

if opened:
    try:
        ses = sessions.load(opened)
    except Exception:
        ses = None
        opened = None

if not opened:
    for s in archived:
        with st.container(border=True):
            cols = st.columns([4, 1])
            n_gens = sum(len(st_.get("generations", [])) for st_ in s.get("steps", []))
            cols[0].markdown(f"### `{s['id']}`")
            cols[0].caption(
                f"{t('his.created_at')}: {s.get('created_at','?')} · "
                f"{t('his.archived_at')}: {s.get('archived_at','?')} · "
                f"backend `{s.get('backend','?')}` · "
                f"{len(s.get('steps', []))} {t('his.steps')} · "
                f"{n_gens} {t('his.gens')}"
            )
            if cols[1].button(t("his.open"), key=f"open_{s['id']}"):
                st.session_state["history_open_id"] = s["id"]
                st.rerun()
    st.stop()


# ── Detail view ────────────────────────────────────────────────────────────
back, _, dele = st.columns([1, 4, 1])
if back.button(t("his.back")):
    st.session_state["history_open_id"] = None
    st.rerun()
if dele.button(t("his.delete")):
    if st.session_state.get(f"confirm_del_{opened}", False):
        sessions.delete(opened)
        st.session_state["history_open_id"] = None
        st.rerun()
    else:
        st.session_state[f"confirm_del_{opened}"] = True
        st.warning(t("his.delete_confirm"))

st.subheader(f"`{ses['id']}`")
st.caption(
    f"name `{ses.get('name','?')}` · backend `{ses.get('backend','?')}` · "
    f"{t('his.created_at')} {ses.get('created_at','?')} · "
    f"{t('his.archived_at')} {ses.get('archived_at','?')}"
)

for step in ses.get("steps", []):
    sid = step["step_id"]
    with st.container(border=True):
        st.markdown(f"#### `{sid}` · {step.get('kind','state')}")
        with st.expander("Template fields"):
            st.json(step.get("fields", {}))
        if step.get("refs"):
            cols = st.columns(min(len(step["refs"]), 4))
            for i, rel in enumerate(step["refs"]):
                p = sessions.abs_path(ses["id"], rel)
                if p.exists():
                    cols[i % len(cols)].image(Image.open(p), caption=p.name,
                                              use_container_width=True)
        gens = step.get("generations", [])
        for gi, g in enumerate(gens):
            with st.container(border=True):
                cols = st.columns([2, 3])
                gp = sessions.abs_path(ses["id"], g["file"])
                if gp.exists():
                    cols[0].image(Image.open(gp), caption=Path(g["file"]).name,
                                  use_container_width=True)
                with cols[1]:
                    v = g.get("verdict") or "—"
                    badge = {"approve": "✅", "deny": "❌", "regen": "🔁"}.get(v, "⚪️")
                    st.markdown(f"{badge} **{v}**")
                    st.caption(f"ts: {g.get('ts','')}")
                    if g.get("comment"):
                        st.caption(f"comment: {g['comment']}")
                    with st.expander("Prompt"):
                        st.code(g.get("prompt", ""), language="text")
                    processed = g.get("processed")
                    if processed:
                        mp = sessions.abs_path(ses["id"], processed["merged"])
                        if mp.exists():
                            st.markdown("**Processed:**")
                            st.image(Image.open(mp), caption=Path(processed["merged"]).name,
                                     use_container_width=True)
                        parts = processed.get("parts", [])
                        if parts:
                            st.caption(f"{len(parts)} part(s)")
                            pcols = st.columns(min(len(parts), 4))
                            for pi, rel in enumerate(parts):
                                pp = sessions.abs_path(ses["id"], rel)
                                if pp.exists():
                                    pcols[pi % len(pcols)].image(
                                        Image.open(pp), caption=Path(rel).name,
                                        use_container_width=True,
                                    )
