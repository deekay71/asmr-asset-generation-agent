"""Review — verdicts for the CURRENT (un-archived) session only.

Iterates every generation in the active session and lets you set
approve / deny / regen-comment verdicts. Save persists to session.json.
For historical sessions, use the History tab.
"""
from __future__ import annotations
from pathlib import Path

from PIL import Image
import streamlit as st

from services import sessions, postprocess
from services.i18n import t, language_toggle


def _postprocess(session_id: str, step_id: str, gen_index: int):
    doc = sessions.load(session_id)
    step = next(s for s in doc["steps"] if s["step_id"] == step_id)
    g = step["generations"][gen_index]
    if g.get("processed"):
        return  # already done
    src = sessions.abs_path(session_id, g["file"])
    out_dir = sessions.session_path(session_id) / step_id / "approved"
    result = postprocess.process(src, out_dir)
    base = sessions.session_path(session_id)
    sessions.set_processed(
        session_id, step_id, gen_index,
        str(result["merged"].relative_to(base)),
        [str(p.relative_to(base)) for p in result["parts"]],
    )


language_toggle()
st.title(t("nav.review"))

cur = sessions.current_session()
if cur is None:
    st.info(t("ses.no_session"))
    st.stop()

st.caption(f"`{cur['id']}` · {len(cur.get('steps', []))} step(s)")

total = 0
undecided = 0
for step in cur.get("steps", []):
    sid = step["step_id"]
    gens = step.get("generations", [])
    if not gens:
        continue
    st.markdown(f"### `{sid}` · {step.get('kind','state')}")
    for gi, g in enumerate(gens):
        total += 1
        with st.container(border=True):
            cols = st.columns([2, 3])
            gp = sessions.abs_path(cur["id"], g["file"])
            if gp.exists():
                try:
                    cols[0].image(Image.open(gp), caption=Path(g["file"]).name,
                                  use_container_width=True)
                except Exception:
                    cols[0].caption(str(gp))
            else:
                cols[0].error("missing image")

            with cols[1]:
                cur_verdict = g.get("verdict") or "—"
                if cur_verdict == "—":
                    undecided += 1
                verdict = st.radio(
                    t("rev.verdict"),
                    ["—", "approve", "deny", "regen"],
                    format_func=lambda k: {
                        "—": t("common.none"),
                        "approve": t("rev.v_approve"),
                        "deny": t("rev.v_reject"),
                        "regen": t("rev.v_regen"),
                    }[k],
                    index=["—", "approve", "deny", "regen"].index(
                        cur_verdict if cur_verdict in ("—", "approve", "deny", "regen") else "—"
                    ),
                    horizontal=True,
                    key=f"rv_{sid}_{gi}",
                )
                comment = st.text_area(
                    t("rev.comment"),
                    value=g.get("comment", ""),
                    key=f"rc_{sid}_{gi}",
                    height=100,
                )
                if st.button(t("common.save"), key=f"rs_{sid}_{gi}"):
                    v = None if verdict == "—" else verdict
                    sessions.set_verdict(cur["id"], sid, gi, v, comment)
                    if v == "approve" and not g.get("processed"):
                        with st.spinner("Removing background + splitting…"):
                            try:
                                _postprocess(cur["id"], sid, gi)
                            except Exception as e:
                                st.error(f"postprocess failed: {e}")
                    st.success("Saved ✓")
                    st.rerun()

                # Show processed outputs if present
                processed = g.get("processed")
                if processed:
                    mp = sessions.abs_path(cur["id"], processed["merged"])
                    if mp.exists():
                        st.markdown("**Processed:**")
                        st.image(Image.open(mp), caption=Path(processed["merged"]).name,
                                 use_container_width=True)
                    parts = processed.get("parts", [])
                    if parts:
                        st.caption(f"Split into {len(parts)} part(s)")
                        pcols = st.columns(min(len(parts), 4))
                        for pi, rel in enumerate(parts):
                            pp = sessions.abs_path(cur["id"], rel)
                            if pp.exists():
                                pcols[pi % len(pcols)].image(
                                    Image.open(pp), caption=Path(rel).name,
                                    use_container_width=True,
                                )

if total == 0:
    st.info(t("ses.no_gens"))
else:
    st.divider()
    c1, c2 = st.columns([2, 1])
    c1.caption(f"{total} generations · {undecided} without verdict")
    if c2.button(t("ses.archive"), type="primary"):
        sessions.archive(cur["id"])
        st.success(t("ses.archived"))
        st.rerun()
