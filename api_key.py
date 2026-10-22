#This allows the user to input their api key
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
