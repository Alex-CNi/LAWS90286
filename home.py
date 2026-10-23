import streamlit as st
import api_key
from app_pages.common import model_settings_sidebar
from app_pages.theme import apply_theme
from core import config

st.set_page_config(page_title=config.APP_TITLE, page_icon="⚖️", layout="wide")
apply_theme()                    # app theme: app_pages/theme.py and .streamlit/config.toml
st.logo(str(config.ROOT / "assets" / "logo.svg"), size="large", icon_image=str(config.ROOT / "assets" / "icon.svg"))

# 1 API KEY MODULE
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


