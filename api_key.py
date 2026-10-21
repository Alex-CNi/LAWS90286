"""
api_key.py — THE API CREDENTIALS SIDEBAR MODULE.

This is the course module from https://github.com/stoneman-mls/openai_credentials,
included as the assignment requires, so that each user enters their OWN OpenAI
API key. Its sidebar title, text box, "Update API Key" button and the session
variable st.session_state.openai_api_key are unchanged.

Three changes, all for safety (noted in README.md):
  1. The text box is a password field (type="password"), so the key is not
     shown on screen.
  2. The last line no longer prints the whole key on the page; it shows a
     masked status ("sk-…abcd") in the sidebar instead.
  3. The module's top-level code is wrapped in render_sidebar_module(), which
     streamlit_app.py calls on every rerun. (Python runs a module's top-level
     code only once per server process, so a plain `import api_key` would draw
     the sidebar only for the first visitor's first page load.)

The key lives only in this user's session (st.session_state). The app never
copies it to os.environ, a cache, a file or a log.
"""

import streamlit as st


def render_sidebar():
    st.sidebar.title("API Key Input")
    api_key = st.sidebar.text_input("Enter your API key:", type="password")   # change 1
    update_button = st.sidebar.button("Update API Key")
    return api_key, update_button


def masked(key: str | None) -> str:
    if not key:
        return "not set"
    return f"{key[:3]}…{key[-4:]}" if len(key) > 8 else "set"


def render_sidebar_module():
    """The course module's steps, run on every page load (change 3)."""
    if "openai_api_key" not in st.session_state:
        st.session_state.openai_api_key = None

    api_key, update_button = render_sidebar()

    if update_button:
        st.session_state.openai_api_key = api_key.strip() or None

    st.sidebar.caption(f"Current API Key: {masked(st.session_state.openai_api_key)}")   # change 2
