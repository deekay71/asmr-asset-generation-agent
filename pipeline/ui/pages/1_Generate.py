"""Generate — freeform session builder.

Workflow:
  1. Create a session (name + backend).
  2. Add as many steps as you like; each step has the optional template fields
     plus reference image uploads.
  3. Click ▶️ Generate on a step → image is produced, shown inline with
     Approve / Deny / Regenerate / Improve-with-comment buttons.
  4. When done, click Archive → session moves to History.
"""
from __future__ import annotations
from pathlib import Path

from PIL import Image
import streamlit as st

from services import sessions, generator, postprocess
from services.i18n import t, language_toggle, get_lang


def _bytes_of_upload(up) -> bytes:
    return up.getvalue() if hasattr(up, "getvalue") else up.read()


def _postprocess_approved(session_id: str, step_id: str, gen_index: int) -> None:
    """Run rembg + tight crop + component split on a generation. Idempotent."""
    doc = sessions.load(session_id)
    step = next(s for s in doc["steps"] if s["step_id"] == step_id)
    g = step["generations"][gen_index]
    src = sessions.abs_path(session_id, g["file"])
    out_dir = sessions.session_path(session_id) / step_id / "approved"
    result = postprocess.process(src, out_dir)
    base = sessions.session_path(session_id)
    merged_rel = str(result["merged"].relative_to(base))
    parts_rel = [str(p.relative_to(base)) for p in result["parts"]]
    sessions.set_processed(session_id, step_id, gen_index, merged_rel, parts_rel)


BACKENDS = ("google_nb2_2k", "google_nb_pro_2k", "google_flash", "fal_nb_pro", "fal_nb2")


def _bytes_of(upload) -> bytes:
    return upload.getvalue() if hasattr(upload, "getvalue") else upload.read()


def _img_path(session_id: str, rel: str) -> Path:
    return sessions.abs_path(session_id, rel)


# ────────────────────────────────────────────────────────────────────────────
language_toggle()
st.title(t("nav.generate"))

cur = sessions.current_session()

with st.expander(t("ses.expander"), expanded=(cur is None)):
    cols = st.columns([3, 2, 2])
    new_name = cols[0].text_input(t("ses.new_name"), value="", key="new_session_name")
    new_backend = cols[1].selectbox(t("gen.backend"), BACKENDS, index=0, key="new_session_backend")
    if cols[2].button(t("ses.new"), type="primary"):
        cur = sessions.create(new_name or "session", new_backend)
        st.success(t("ses.created_ok", sid=cur["id"]))
        st.rerun()

    if cur:
        st.caption(f"Active: `{cur['id']}` · {len(cur.get('steps',[]))} steps")
        bcols = st.columns([3, 2])
        cur_backend = cur.get("backend") or BACKENDS[0]
        try:
            idx = BACKENDS.index(cur_backend)
        except ValueError:
            idx = 0
        # Key includes the current backend value so any server-side change
        # makes Streamlit treat this as a fresh widget (no stale carryover).
        switched = bcols[0].selectbox(
            t("gen.backend"), BACKENDS, index=idx,
            key=f"bk_switch_{cur['id']}_{cur_backend}",
        )
        if switched != cur_backend:
            sessions.set_backend(cur["id"], switched)
            st.success(t("ses.backend_switched", name=switched))
            st.rerun()
        if bcols[1].button(t("ses.archive")):
            sessions.archive(cur["id"])
            st.success(t("ses.archived"))
            st.rerun()

        # ── Style anchor (pinned for every generation in this session) ─────
        st.markdown(f"**{t('ses.style_anchor')}**")
        sa = cur.get("style_anchor")
        if sa:
            sa_path = sessions.session_path(cur["id"]) / sa
            cols = st.columns([2, 4])
            if sa_path.exists():
                cols[0].image(str(sa_path), caption=sa, use_container_width=True)
            cols[1].caption(t("ses.style_anchor_help"))
            if cols[1].button(t("ses.style_anchor_clear")):
                sessions.set_style_anchor(cur["id"], None)
                st.rerun()
        else:
            sa_up = st.file_uploader(
                t("ses.style_anchor_upload"), type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=False, key=f"sa_up_{cur['id']}",
            )
            if sa_up is not None:
                sessions.set_style_anchor(cur["id"], sa_up.name, _bytes_of_upload(sa_up))
                st.success(t("ses.style_anchor_saved"))
                st.rerun()

if cur is None:
    st.info(t("ses.no_session"))
    st.stop()

# Loud session-state strip so backend + folder are never ambiguous.
ses_dir = sessions.session_path(cur["id"])
fcols = st.columns([3, 2])
fcols[0].info(t("ses.session_folder", backend=cur.get("backend"), folder=ses_dir))
if fcols[1].button(t("ses.open_finder")):
    import subprocess
    subprocess.Popen(["open", str(ses_dir)])

st.caption(t("ses.fields_optional"))

# ── Add step controls ──────────────────────────────────────────────────────
add_a, add_b, add_c = st.columns(3)
if add_a.button(t("ses.add_state")):
    sessions.add_step(cur["id"], kind="state")
    st.rerun()
if add_b.button(t("ses.add_tool")):
    sessions.add_step(cur["id"], kind="tool")
    st.rerun()
if add_c.button(t("ses.add_image")):
    sessions.add_step(cur["id"], kind="image")
    st.rerun()

# Re-fetch (we may have just mutated)
cur = sessions.load(cur["id"])

if not cur["steps"]:
    st.info(t("ses.add_to_begin"))
    st.stop()


# ── Step cards ─────────────────────────────────────────────────────────────
def _kind_label(k: str) -> str:
    return {
        "tool": t("ses.kind_tool"),
        "image": t("ses.kind_image"),
    }.get(k, t("ses.kind_state"))


for step in cur["steps"]:
    sid = step["step_id"]
    kind = step.get("kind", "state")
    is_tool = kind == "tool"
    is_image = kind == "image"

    with st.container(border=True):
        # Editable step header — id + reorder + remove
        h_id, h_up, h_dn, h_rm = st.columns([6, 1, 1, 1])
        new_sid = h_id.text_input(
            t("ses.step_id_label"), value=sid, key=f"sid_edit_{sid}",
            label_visibility="collapsed",
        )
        if new_sid and new_sid != sid:
            final = sessions.rename_step(cur["id"], sid, new_sid)
            st.success(t("ses.renamed", name=final))
            st.rerun()
        if h_up.button("⬆️", key=f"up_{sid}", help=t("ses.move_up")):
            sessions.reorder_step(cur["id"], sid, -1)
            st.rerun()
        if h_dn.button("⬇️", key=f"dn_{sid}", help=t("ses.move_down")):
            sessions.reorder_step(cur["id"], sid, +1)
            st.rerun()
        if h_rm.button("❌", key=f"rm_{sid}", help=t("ses.remove_step")):
            sessions.remove_step(cur["id"], sid)
            st.rerun()
        st.caption(f"kind: `{_kind_label(kind)}`")

        # ── Prefill from past step ─────────────────────────────────────────
        if not is_image:
            past = sessions.list_all_steps_for_prefill()
            # Exclude the current step itself
            past = [p for p in past
                    if not (p["session_id"] == cur["id"] and p["step_id"] == sid)]
            if past:
                labels = ["—"] + [
                    f"{'✅' if p['has_approved'] else '·'} "
                    f"{p['session_name']} / {p['step_id']} "
                    f"({p['kind']}) "
                    f"{p['fields'].get('step') or p['fields'].get('name') or ''}".strip()
                    for p in past
                ]
                pcol1, pcol2 = st.columns([4, 1])
                pick = pcol1.selectbox(
                    t("ses.prefill"),
                    labels, index=0, key=f"prefill_{sid}",
                    label_visibility="collapsed",
                )
                if pcol2.button(t("ses.prefill_apply"), key=f"prefill_apply_{sid}") and pick != "—":
                    src = past[labels.index(pick) - 1]
                    sessions.update_fields(cur["id"], sid, src["fields"])
                    st.success(t("ses.prefilled_from", src=f"{src['session_name']}/{src['step_id']}"))
                    st.rerun()

        # ── Image-step branch: skip fields & generation, just import ─────
        if is_image:
            st.caption(t("ses.image_step_help"))
            img_cnt_key = f"imgcnt_{sid}"
            if img_cnt_key not in st.session_state:
                st.session_state[img_cnt_key] = 0
            img_seen_key = f"img_seen_{sid}"
            if img_seen_key not in st.session_state:
                st.session_state[img_seen_key] = {
                    g.get("imported_from") for g in step["generations"]
                    if g.get("imported_from")
                }
            imgs = st.file_uploader(
                t("ses.upload_image"), type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=True,
                key=f"img_up_{sid}_{st.session_state[img_cnt_key]}",
            )
            imported = False
            if imgs:
                for up in imgs:
                    if up.name in st.session_state[img_seen_key]:
                        continue
                    sessions.import_image_as_generation(
                        cur["id"], sid, up.name, _bytes_of_upload(up))
                    st.session_state[img_seen_key].add(up.name)
                    imported = True
            if imported:
                st.session_state[img_cnt_key] += 1
                st.rerun()

            # Skip directly to generations preview (rendered later in this loop).
            step_refreshed = next(s for s in sessions.load(cur["id"])["steps"]
                                  if s["step_id"] == sid)
            gens = step_refreshed.get("generations", [])
            if not gens:
                st.caption(t("ses.no_image_yet"))
            for gi, g in enumerate(gens):
                with st.container(border=True):
                    pcols = st.columns([2, 3])
                    gp = sessions.abs_path(cur["id"], g["file"])
                    if gp.exists():
                        try:
                            pcols[0].image(Image.open(gp),
                                           caption=Path(g["file"]).name,
                                           use_container_width=True)
                        except Exception as e:
                            pcols[0].error(f"render: {e}")
                    with pcols[1]:
                        v = g.get("verdict")
                        if v == "approve":
                            st.success(t("ses.verdict_approve"))
                        elif v == "deny":
                            st.error(t("ses.verdict_deny"))
                        st.caption(f"imported: {g.get('imported_from','?')} · ts {g.get('ts','')}")

                        a, d = st.columns(2)
                        if a.button(t("ses.approve"), key=f"app_{sid}_{gi}"):
                            sessions.set_verdict(cur["id"], sid, gi, "approve")
                            with st.spinner("Removing background + splitting…"):
                                try:
                                    _postprocess_approved(cur["id"], sid, gi)
                                except Exception as e:
                                    st.error(f"postprocess failed: {e}")
                            st.rerun()
                        if d.button(t("ses.deny"), key=f"den_{sid}_{gi}"):
                            sessions.set_verdict(cur["id"], sid, gi, "deny")
                            st.rerun()

                        processed = g.get("processed")
                        if processed:
                            mp = sessions.abs_path(cur["id"], processed["merged"])
                            if mp.exists():
                                st.markdown(f"**{t('ses.processed')}**")
                                st.image(Image.open(mp),
                                         caption=Path(processed["merged"]).name,
                                         use_container_width=True)
                            parts = processed.get("parts", [])
                            if parts:
                                st.caption(f"Split into {len(parts)} part(s)")
                                pcs = st.columns(min(len(parts), 4))
                                for pi, rel in enumerate(parts):
                                    pp = sessions.abs_path(cur["id"], rel)
                                    if pp.exists():
                                        pcs[pi % len(pcs)].image(
                                            Image.open(pp),
                                            caption=Path(rel).name,
                                            use_container_width=True,
                                        )
            continue  # skip the state/tool body below

        # ── Template fields (all optional, commit live on each rerun) ────
        fields = dict(step["fields"])
        if is_tool:
            c1, c2 = st.columns(2)
            fields["name"] = c1.text_input(t("auth.f_name"), value=fields.get("name", ""), key=f"f_name_{sid}")
            fields["tool_count"] = c2.text_input(t("auth.f_tool_count"), value=fields.get("tool_count", ""), key=f"f_tcount_{sid}")
            c3, c4 = st.columns(2)
            fields["tool_camera"] = c3.text_input(t("auth.f_tool_camera"), value=fields.get("tool_camera", ""), key=f"f_tcam_{sid}")
            fields["color"] = c4.text_input(t("auth.f_color"), value=fields.get("color", ""), key=f"f_col_{sid}")
            c5, c6 = st.columns(2)
            fields["style"] = c5.text_input(t("auth.f_style"), value=fields.get("style", ""), key=f"f_sty_{sid}")
            fields["output"] = c6.text_input(t("auth.f_output"), value=fields.get("output", ""), key=f"f_out_{sid}")
            fields["step_consistency"] = st.text_area(t("auth.f_step_consistency"), value=fields.get("step_consistency", ""), height=70, key=f"f_cons_{sid}")
            fields["ref_link"] = st.text_input(t("auth.f_ref_link"), value=fields.get("ref_link", ""), key=f"f_reflk_{sid}")
        else:
            fields["step"] = st.text_input(t("auth.f_step"), value=fields.get("step", ""), key=f"f_step_{sid}")
            fields["concept"] = st.text_area(t("auth.f_concept"), value=fields.get("concept", ""), height=80, key=f"f_concept_{sid}")
            c1, c2, c3 = st.columns(3)
            fields["camera"] = c1.text_input(t("auth.f_camera"), value=fields.get("camera", ""), key=f"f_cam_{sid}")
            fields["object_count"] = c2.text_input(t("auth.f_object_count"), value=fields.get("object_count", ""), key=f"f_cnt_{sid}")
            fields["object_shape"] = c3.text_input(t("auth.f_object_shape"), value=fields.get("object_shape", ""), key=f"f_shp_{sid}")
            fields["object_position"] = st.text_input(t("auth.f_object_position"), value=fields.get("object_position", ""), key=f"f_pos_{sid}")

        fields["per_object_description"] = st.text_area(
            t("auth.f_per_object_desc"), value=fields.get("per_object_description", ""),
            height=120, key=f"f_pod_{sid}",
        )
        fields["negative_prompt"] = st.text_area(
            t("auth.f_neg_prompt"), value=fields.get("negative_prompt", ""),
            height=80, key=f"f_neg_{sid}",
        )

        # ── Reference image uploads (de-duped per filename) ───────────────
        st.markdown(f"**{t('ses.refs')}**")
        if kind == "state":
            prev_approved = sessions.previous_approved_image(cur["id"], sid)
            if prev_approved is not None:
                st.caption(f"{t('ses.auto_ref_hint')}  →  `{prev_approved.name}`")
        seen_key = f"seen_uploads_{sid}"
        if seen_key not in st.session_state:
            st.session_state[seen_key] = {Path(r).name for r in step["refs"]}

        # Counter-suffix on the uploader key so it resets after a successful
        # upload — otherwise Streamlit keeps the file in the widget state
        # forever and tries to re-add it on every rerun.
        upcnt_key = f"upcnt_{sid}"
        if upcnt_key not in st.session_state:
            st.session_state[upcnt_key] = 0

        uploads = st.file_uploader(
            t("ses.upload_ref"), type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key=f"up_{sid}_{st.session_state[upcnt_key]}",
        )
        added_now = False
        if uploads:
            for up in uploads:
                if up.name in st.session_state[seen_key]:
                    continue
                sessions.add_ref(cur["id"], sid, up.name, _bytes_of(up))
                st.session_state[seen_key].add(up.name)
                added_now = True
        if added_now:
            st.session_state[upcnt_key] += 1   # reset uploader widget
            st.rerun()

        if step["refs"]:
            if st.button(f"🧹 Clear all references ({len(step['refs'])})",
                         key=f"clear_refs_{sid}"):
                for rel in list(step["refs"]):
                    sessions.remove_ref(cur["id"], sid, rel)
                st.session_state[seen_key] = set()
                st.session_state[upcnt_key] += 1
                st.rerun()

        if step["refs"]:
            ref_cols = st.columns(min(len(step["refs"]), 4))
            for i, rel in enumerate(step["refs"]):
                col = ref_cols[i % len(ref_cols)]
                p = _img_path(cur["id"], rel)
                if p.exists():
                    try:
                        col.image(Image.open(p), caption=p.name, use_container_width=True)
                    except Exception:
                        col.caption(str(p))
                if col.button(f"❌ {Path(rel).name}", key=f"rm_ref_{sid}_{i}"):
                    sessions.remove_ref(cur["id"], sid, rel)
                    st.rerun()

        # ── Generate button ───────────────────────────────────────────────
        st.divider()
        gen_btn = st.button(t("ses.generate_step"), key=f"gen_{sid}", type="primary")
        if gen_btn:
            sessions.update_fields(cur["id"], sid, fields)
            user_refs = [_img_path(cur["id"], r) for r in step["refs"] if _img_path(cur["id"], r).exists()]
            refs_abs = sessions.assembled_refs(cur["id"], sid, user_refs)
            prompt = generator.build_prompt(fields, kind=kind, lang=get_lang())
            success = False
            with st.spinner(f"{t('ses.generating')}  (backend: {cur.get('backend')})"):
                try:
                    bn = cur.get("backend") or "google_flash"
                    png, model_id = generator.generate(
                        backend_name=bn,
                        refs=refs_abs,
                        prompt=prompt,
                    )
                    sessions.record_generation(cur["id"], sid, png, prompt,
                                               backend=bn, model_id=model_id)
                    success = True
                except Exception as e:
                    import traceback
                    st.error(f"{t('ses.gen_failed')}: {type(e).__name__}: {e}")
                    with st.expander(t("ses.traceback")):
                        st.code(traceback.format_exc())
            if success:
                st.rerun()

        # ── Generations preview + verdicts ────────────────────────────────
        step_refreshed = next(s for s in sessions.load(cur["id"])["steps"] if s["step_id"] == sid)
        gens = step_refreshed.get("generations", [])
        if not gens:
            st.caption(t("ses.no_gens"))
        for gi, g in enumerate(gens):
            with st.container(border=True):
                pcols = st.columns([2, 3])
                gp = _img_path(cur["id"], g["file"])
                if gp.exists():
                    try:
                        pcols[0].image(Image.open(gp), caption=Path(g["file"]).name,
                                       use_container_width=True)
                    except Exception as e:
                        pcols[0].error(f"render: {e}")
                else:
                    pcols[0].error(f"missing: {g['file']}")

                with pcols[1]:
                    v = g.get("verdict")
                    if v == "approve":
                        st.success(t("ses.verdict_approve"))
                    elif v == "deny":
                        st.error(t("ses.verdict_deny"))

                    # Loud model + size badge so it's never ambiguous.
                    sz = g.get("size")
                    sz_txt = f"{sz[0]}×{sz[1]}" if sz else "?"
                    bn = g.get("backend") or "?"
                    mid = g.get("model_id") or "?"
                    st.markdown(f"**🖼 {sz_txt}**  ·  backend `{bn}`  ·  model `{mid}`")
                    st.caption(f"ts: {g.get('ts','')}  ·  file: `{g['file']}`")
                    with st.expander(t("ses.prompt_sent")):
                        st.code(g.get("prompt", ""), language="text")

                    a, d = st.columns(2)
                    if a.button(t("ses.approve"), key=f"app_{sid}_{gi}"):
                        sessions.set_verdict(cur["id"], sid, gi, "approve")
                        with st.spinner("Removing background + splitting…"):
                            try:
                                _postprocess_approved(cur["id"], sid, gi)
                            except Exception as e:
                                st.error(f"postprocess failed: {e}")
                        st.rerun()
                    if d.button(t("ses.deny"), key=f"den_{sid}_{gi}"):
                        sessions.set_verdict(cur["id"], sid, gi, "deny")
                        st.rerun()

                    # ── Show processed outputs if any ────────────────────
                    processed = g.get("processed")
                    if processed:
                        st.markdown(f"**{t('ses.processed_bg')}**")
                        mp = sessions.abs_path(cur["id"], processed["merged"])
                        if mp.exists():
                            st.image(Image.open(mp), caption=Path(processed["merged"]).name,
                                     use_container_width=True)
                        parts = processed.get("parts", [])
                        if parts:
                            st.markdown(f"**Split into {len(parts)} parts:**")
                            pcols = st.columns(min(len(parts), 4))
                            for pi, rel in enumerate(parts):
                                pp = sessions.abs_path(cur["id"], rel)
                                if pp.exists():
                                    pcols[pi % len(pcols)].image(
                                        Image.open(pp), caption=Path(rel).name,
                                        use_container_width=True,
                                    )

                    r, i_ = st.columns(2)
                    if r.button(t("ses.regen"), key=f"rg_{sid}_{gi}"):
                        user_refs = [_img_path(cur["id"], r2) for r2 in step["refs"]
                                     if _img_path(cur["id"], r2).exists()]
                        refs_abs = sessions.assembled_refs(cur["id"], sid, user_refs)
                        prompt = generator.build_prompt(fields, kind=kind, lang=get_lang())
                        success = False
                        with st.spinner(t("ses.generating")):
                            try:
                                bn = cur.get("backend") or "google_flash"
                                png, model_id = generator.generate(
                                    backend_name=bn,
                                    refs=refs_abs, prompt=prompt,
                                )
                                sessions.record_generation(cur["id"], sid, png, prompt,
                                                           backend=bn, model_id=model_id)
                                success = True
                            except Exception as e:
                                import traceback
                                st.error(f"{t('ses.gen_failed')}: {type(e).__name__}: {e}")
                                with st.expander(t("ses.traceback")):
                                    st.code(traceback.format_exc())
                        if success:
                            st.rerun()

                    # Improve-with-comment toggle
                    improve_open = st.session_state.get(f"imp_open_{sid}_{gi}", False)
                    if i_.button(t("ses.improve"), key=f"imp_{sid}_{gi}"):
                        st.session_state[f"imp_open_{sid}_{gi}"] = not improve_open
                        st.rerun()
                    if st.session_state.get(f"imp_open_{sid}_{gi}", False):
                        comment = st.text_area(t("ses.improve_box"), key=f"imp_txt_{sid}_{gi}")
                        if st.button(t("ses.improve_run"), key=f"imp_run_{sid}_{gi}"):
                            user_refs = [_img_path(cur["id"], r2) for r2 in step["refs"]
                                         if _img_path(cur["id"], r2).exists()]
                            refs_abs = sessions.assembled_refs(cur["id"], sid, user_refs)
                            # Iterate from the just-shown generation.
                            if gp.exists() and gp not in refs_abs:
                                refs_abs = [gp] + refs_abs
                            prompt = generator.build_prompt(fields, kind=kind, extra_comment=comment, lang=get_lang())
                            success = False
                            with st.spinner(t("ses.generating")):
                                try:
                                    bn = cur.get("backend") or "google_flash"
                                    png, model_id = generator.generate(
                                        backend_name=bn,
                                        refs=refs_abs, prompt=prompt,
                                    )
                                    sessions.record_generation(cur["id"], sid, png, prompt,
                                                               backend=bn, model_id=model_id)
                                    success = True
                                except Exception as e:
                                    import traceback
                                    st.error(f"{t('ses.gen_failed')}: {type(e).__name__}: {e}")
                                    with st.expander(t("ses.traceback")):
                                        st.code(traceback.format_exc())
                            if success:
                                st.session_state[f"imp_open_{sid}_{gi}"] = False
                                st.rerun()
