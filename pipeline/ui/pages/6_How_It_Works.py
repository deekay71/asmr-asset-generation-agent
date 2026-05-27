"""How It Works — renders the static markdown explainer."""
from __future__ import annotations
from pathlib import Path
import streamlit as st

from services.i18n import t, language_toggle, get_lang

BASE = Path(__file__).resolve().parent.parent / "static"

language_toggle()
st.title(t("how.title"))

doc = BASE / f"how_it_works.{get_lang()}.md"
if not doc.exists():
    doc = BASE / "how_it_works.md"

if doc.exists():
    st.markdown(doc.read_text())
else:
    st.error(f"Missing {doc}")
