"""
app_pages/theme.py — THE COLOURS BEHIND THE PAGES.

The colour scheme is set in .streamlit/config.toml ([theme]: dark navy, light
text, blue accents). Two things cannot be set there, so this module adds them
as CSS on every page load (streamlit_app.py calls apply_theme()):
  * the navy gradient behind every page;
  * a blue gradient on the main ("primary") buttons.
Nothing here changes the layout. To change a colour, edit GRADIENT / BUTTON
below and the matching colours in config.toml; delete the apply_theme() call in
streamlit_app.py to go back to the plain theme colours.
"""

import streamlit as st

# From the darkest navy (top left) to a lighter blue (bottom right), with a soft light patch near the top.
GRADIENT = ("radial-gradient(1100px 560px at 58% -8%, rgba(78, 116, 205, 0.34), transparent 65%), "
            "linear-gradient(118deg, #0a1433 0%, #0f2149 48%, #18336d 100%)")
BUTTON = "linear-gradient(135deg, #4f46e5 0%, #3b82f6 100%)"     # indigo to blue

CSS = f"""
<style>
[data-testid="stAppViewContainer"] {{ background: {GRADIENT}; background-attachment: fixed; }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stBaseButton-primary"] {{ background: {BUTTON}; border: none; }}
[data-testid="stBaseButton-primary"]:hover {{ filter: brightness(1.1); }}
[data-testid="stBaseButton-primary"]:disabled {{ filter: grayscale(0.7); opacity: 0.45; }}
</style>
"""


def apply_theme() -> None:
    """Add the gradient background and the button colour (call once per page load)."""
    st.html(CSS)
