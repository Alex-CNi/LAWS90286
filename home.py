"""
home.py — THE ENTRY POINT (Streamlit Cloud runs this file).

On every page load it:
  1. sets up the page, its look (app_pages/theme.py, .streamlit/config.toml) and the logo (assets/);
  2. draws the sidebar: the course's API Credentials module (api_key.py) and the
     model settings;
  3. runs the page the user chose (app_pages/).

Run locally with:   streamlit run home.py
"""

import streamlit as st

import api_key
from app_pages.common import model_settings_sidebar
from app_pages.theme import apply_theme
from core import config

st.set_page_config(page_title=config.APP_TITLE, page_icon="⚖️", layout="wide")
apply_theme()                    # the look: app_pages/theme.py and .streamlit/config.toml
st.logo(str(config.ROOT / "assets" / "logo.svg"), size="large", icon_image=str(config.ROOT / "assets" / "icon.svg"))

# 1a. Keep the Estimate form's values while another page is open. Streamlit deletes
#     the value of a widget that is not drawn in a run (e.g. on the About page), and
#     a field shown only conditionally (the scheme question, the end of the offence
#     period, the sentence-indication fields) could come back with a stale default.
#     Re-assigning each form key at the top of every run is Streamlit's documented
#     way to keep widget values across pages. The file uploader cannot be set.
#     "f_" = the form, "s_" = the simple input's own answers, "cdb_exclude" = the
#     cases left out of the comparisons.
for _key in [k for k in st.session_state
             if (str(k).startswith(("f_", "s_")) or k == "cdb_exclude") and k != "f_upload"]:
    st.session_state[_key] = st.session_state[_key]

# 2. Sidebar — called on every rerun so it appears on every page, for every user.
api_key.render_sidebar_module()
model_settings_sidebar()

# 3. Pages
page = st.navigation([
    st.Page("app_pages/estimate.py", title="Estimate a sentence", icon=":material/gavel:", default=True),
    st.Page("app_pages/cases.py", title="Case database", icon=":material/folder_open:"),
    st.Page("app_pages/knowledge_base.py", title="Knowledge base", icon=":material/menu_book:"),
    st.Page("app_pages/about.py", title="About and method", icon=":material/info:"),
])
page.run()
