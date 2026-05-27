"""Start Level — brainstorm a cleaning chain for a new item and create a session."""
from __future__ import annotations
import json

import streamlit as st

from services import sessions, brainstorm
from services.i18n import t, language_toggle, get_lang


language_toggle()
st.title(t("sl.title"))
st.caption(t("sl.caption"))

BACKENDS = ("google_nb2_2k", "google_nb_pro_2k", "google_flash", "fal_nb_pro", "fal_nb2")

cols = st.columns([3, 1, 1])
item = cols[0].text_input(
    t("sl.item_name"), value=st.session_state.get("sl_item", ""),
    placeholder=t("sl.item_placeholder"),
)
n_states = cols[1].number_input(t("sl.n_states"), 2, 12, 5, 1)
n_tools = cols[2].number_input(t("sl.n_tools"), 0, 8, 3, 1)

hints = st.text_area(
    t("sl.hints"),
    value=st.session_state.get("sl_hints", ""), height=80,
)

if st.button(t("sl.brainstorm"), type="primary", disabled=not item.strip()):
    st.session_state["sl_item"] = item
    st.session_state["sl_hints"] = hints
    with st.spinner(t("sl.thinking")):
        try:
            plan = brainstorm.brainstorm(
                item,
                n_states_hint=int(n_states),
                n_tools_hint=int(n_tools),
                extra_hints=hints,
                lang=get_lang(),
            )
            st.session_state["sl_plan"] = plan
        except Exception as e:
            st.error(f"{t('sl.failed')}: {e}")
            st.stop()

plan = st.session_state.get("sl_plan", [])
if not plan:
    st.info(t("sl.empty"))
    st.stop()

st.success(t("sl.proposed", n=len(plan)))

# Editable plan
edited: list[dict] = []
for i, step in enumerate(plan):
    kind = step.get("kind", "state")
    with st.container(border=True):
        h_l, h_r = st.columns([5, 1])
        h_l.markdown(f"**{t('sl.step_n')} {i+1} · `{kind}`**")
        if h_r.button(t("sl.remove"), key=f"sl_rm_{i}"):
            plan.pop(i)
            st.session_state["sl_plan"] = plan
            st.rerun()

        if kind == "tool":
            c1, c2 = st.columns(2)
            step["name"] = c1.text_input(t("sl.field_name"), value=step.get("name", ""), key=f"sl_name_{i}")
            step["tool_count"] = c2.text_input(t("sl.field_tool_count"), value=step.get("tool_count", "1"), key=f"sl_tc_{i}")
            c3, c4 = st.columns(2)
            step["color"] = c3.text_input(t("sl.field_color"), value=step.get("color", ""), key=f"sl_col_{i}")
            step["style"] = c4.text_input(t("sl.field_style"), value=step.get("style", "2D game asset"), key=f"sl_sty_{i}")
            step["tool_camera"] = st.text_input(t("sl.field_camera"), value=step.get("tool_camera", ""), key=f"sl_tcam_{i}")
            step["output"] = st.text_input(t("sl.field_output"), value=step.get("output", ""), key=f"sl_out_{i}")
            step["per_object_description"] = st.text_area(t("sl.field_desc"), value=step.get("per_object_description", ""), height=110, key=f"sl_pod_{i}")
            step["negative_prompt"] = st.text_area(t("sl.field_neg"), value=step.get("negative_prompt", ""), height=60, key=f"sl_neg_{i}")
        else:
            step["step"] = st.text_input(t("sl.field_step"), value=step.get("step", ""), key=f"sl_step_{i}")
            step["concept"] = st.text_area(t("sl.field_concept"), value=step.get("concept", ""), height=70, key=f"sl_concept_{i}")
            c1, c2, c3 = st.columns(3)
            step["camera"] = c1.text_input(t("sl.field_camera"), value=step.get("camera", ""), key=f"sl_cam_{i}")
            step["object_count"] = c2.text_input(t("sl.field_count"), value=step.get("object_count", ""), key=f"sl_cnt_{i}")
            step["object_shape"] = c3.text_input(t("sl.field_shape"), value=step.get("object_shape", ""), key=f"sl_shp_{i}")
            step["object_position"] = st.text_input(t("sl.field_position"), value=step.get("object_position", ""), key=f"sl_pos_{i}")
            step["per_object_description"] = st.text_area(t("sl.field_pod"), value=step.get("per_object_description", ""), height=110, key=f"sl_pod2_{i}")
            step["negative_prompt"] = st.text_area(t("sl.field_neg"), value=step.get("negative_prompt", ""), height=60, key=f"sl_neg2_{i}")
        edited.append(step)

add_l, add_r = st.columns(2)
if add_l.button(t("sl.add_state")):
    plan.append({"kind": "state", "step": "", "concept": "", "per_object_description": ""})
    st.session_state["sl_plan"] = plan
    st.rerun()
if add_r.button(t("sl.add_tool")):
    plan.append({"kind": "tool", "name": "", "per_object_description": ""})
    st.session_state["sl_plan"] = plan
    st.rerun()

# Persist edits
st.session_state["sl_plan"] = edited

st.divider()
cols = st.columns([3, 2, 2])
ses_name = cols[0].text_input(t("sl.session_name"), value=item or "session")
backend = cols[1].selectbox(t("gen.backend"), BACKENDS, index=0)
if cols[2].button(t("sl.create"), type="primary"):
    ses = brainstorm.create_session_from_plan(ses_name, backend, st.session_state["sl_plan"])
    st.session_state.pop("sl_plan", None)
    st.success(t("sl.created", sid=ses["id"], n=len(ses["steps"])))
