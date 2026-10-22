"""
app_pages/theme.py — THE LOOK OF THE APP.

The colours and the font (Lato) are set in .streamlit/config.toml. This module adds what the config cannot:

  apply_theme()        CSS for the whole app, added on every page load (home.py): the navy gradient,
                       the sidebar tiles, the large light headlines, the white start bar with pill-shaped
                       choices, the offence panel, cards, expanders, buttons, the step pills and the result card
  hero(title, sub)     the large, light headline at the top of a page
  go_button(href)      the blue arrow at the end of a start bar (a link to a place on the page)
  anchor(name)         a place on the page that a link can go to
  steps_row(steps, n)  the run's progress as a row of rounded labels

Styles are attached to containers by their key: st.container(key="hero_bar") gives the CSS class
"st-key-hero_bar", so a page opts in by using these keys (hero_bar, search_bar, areas, card_…). The result
card's HTML is built in app_pages/result_card.py. No brand's name, logo or wordmark is used.
To change a colour, edit PALETTE (and the matching colours in config.toml).
"""

from __future__ import annotations

import html
from string import Template
from urllib.parse import quote

import streamlit as st

PALETTE = {
    "rail": "#0a1634",          # the sidebar
    "ink": "#0b1a3a",           # the darkest part of the background
    "deep": "#0f2149",
    "sky": "#18336d",           # the lightest part of the background
    "panel": "rgba(5, 13, 34, 0.72)",
    "line": "rgba(126, 156, 226, 0.18)",
    "accent": "#4d8dff",        # active tab, rail marker
    "violet": "#5146d8",        # pill borders on the white bar
    "violet_text": "#3a2db3",
    "go_from": "#4f46e5",       # the gradient of the main buttons and the arrow
    "go_to": "#3b82f6",
    "text": "#e6ebf5",
    "muted": "#a9b6d3",
    # The result card's chart (emphasis: one accent hue and a grey, checked for colour-blind separation and
    # contrast on the card's navy with the dataviz validator) and the fixed status colours.
    "within": "#3f86ff",
    "outside": "#6b7590",
    "good": "#0ca30c",
    "warning": "#fab219",
    "critical": "#d03b3b",
}

CSS = """
<style>
/* ── 1. Background: a deep navy gradient behind every page ───────────────── */
[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(1100px 560px at 58% -8%, rgba(78, 116, 205, 0.34), transparent 65%),
    linear-gradient(118deg, ${ink} 0%, ${deep} 48%, ${sky} 100%);
  background-attachment: fixed;
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { max-width: 1280px; padding-top: 2.2rem; padding-bottom: 4rem; }

/* ── 2. Sidebar: a dark rail; the page links become tiles, icon above label ─ */
[data-testid="stSidebar"] { background: linear-gradient(180deg, #091431 0%, ${rail} 100%);
                            border-right: 1px solid ${line}; }
[data-testid="stSidebarNavItems"] { display: grid; grid-template-columns: 1fr 1fr; gap: 0.45rem;
                                    padding: 0.4rem 0.75rem 0.6rem; }
[data-testid="stSidebarNavLinkContainer"] { margin: 0; }
[data-testid="stSidebarNavLink"] { flex-direction: column; align-items: center; justify-content: center;
                                   gap: 0.35rem; height: 5.4rem; padding: 0.5rem 0.3rem; border-radius: 0.6rem;
                                   text-align: center; background: rgba(255, 255, 255, 0.025); }
[data-testid="stSidebarNavLink"] span { text-align: center; line-height: 1.2; white-space: normal; }
[data-testid="stSidebarNavLink"] .stMarkdownEllipsis, [data-testid="stSidebarNavLink"] p {
  white-space: normal; overflow: visible; text-overflow: clip; text-align: center; font-size: 0.9rem; line-height: 1.2; }
[data-testid="stSidebarNavLink"] [data-testid="stIconMaterial"] { font-size: 1.85rem; color: #9fbaff; }
[data-testid="stSidebarNavLink"]:hover { background: rgba(77, 141, 255, 0.10); }
[data-testid="stSidebarNavLink"][aria-current="page"] { background: rgba(77, 141, 255, 0.16);
                                                         box-shadow: inset -3px 0 0 ${accent}; }
[data-testid="stSidebarNavLink"][aria-current="page"] [data-testid="stIconMaterial"] { color: #ffffff; }
[data-testid="stSidebarNavSeparator"] { border-color: ${line}; }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { font-weight: 700; }

/* ── 3. Headline at the top of a page ─────────────────────────────────────── */
.vse-hero h1 { font-weight: 300; font-size: clamp(2.2rem, 4.2vw, 3.6rem); line-height: 1.12; color: #ffffff;
               letter-spacing: -0.01em; margin: 1.2rem 0 0.6rem; padding: 0; }
.vse-hero p { color: ${muted}; font-size: 1.02rem; max-width: 62rem; margin: 0 0 1.2rem; line-height: 1.55; }
.vse-hero.small h1 { font-size: clamp(1.9rem, 3.2vw, 2.7rem); margin-top: 0.6rem; }

/* ── 4. The white start bar with pill-shaped choices and the blue arrow ──── */
.st-key-hero_bar, .st-key-search_bar {
  background: #ffffff; border-radius: 5px; padding: 0.5rem 0 0.5rem 1.1rem;
  box-shadow: 0 14px 40px rgba(2, 8, 30, 0.45); margin: 0.4rem 0 1.8rem; container-type: inline-size; }
.st-key-hero_bar [data-testid="stWidgetLabel"], .st-key-search_bar [data-testid="stWidgetLabel"] { display: none; }
/* the wide first field: no border, grey text */
.st-key-hero_bar [data-testid="stColumn"]:first-child [data-testid="stSelectbox"] [role="group"],
.st-key-hero_bar [data-testid="stColumn"]:first-child [data-baseweb="select"] > div,
.st-key-search_bar [data-testid="stTextInputRootElement"],
.st-key-search_bar [data-baseweb="input"], .st-key-search_bar [data-baseweb="base-input"] {
  background: transparent; border: none; box-shadow: none; }
.st-key-hero_bar [data-testid="stColumn"]:first-child [data-testid="stSelectbox"] *,
.st-key-search_bar [data-testid="stTextInput"] input {
  background: transparent; color: #4b5563; -webkit-text-fill-color: #4b5563; font-size: 1.12rem; }
.st-key-search_bar [data-testid="stTextInput"] input::placeholder { color: #6b7280; -webkit-text-fill-color: #6b7280; }
.st-key-hero_bar [data-testid="stColumn"]:first-child [data-testid="stSelectbox"] svg { color: #6b7280; fill: #6b7280; }
/* the pills: a choice list (react-aria combobox) and the two-way switch (segmented control) */
.st-key-hero_bar [data-testid="stColumn"]:not(:first-child) [data-testid="stSelectbox"] [role="group"],
.st-key-search_bar [data-testid="stSelectbox"] [role="group"] {
  background: #ffffff; border: 2px solid ${violet}; border-radius: 999px; min-height: 2.7rem; padding-left: 0.55rem;
  box-shadow: none; }
.st-key-hero_bar [data-testid="stColumn"]:not(:first-child) [data-testid="stSelectbox"] input,
.st-key-search_bar [data-testid="stSelectbox"] input {
  background: transparent; color: ${violet_text}; -webkit-text-fill-color: ${violet_text}; font-weight: 700;
  font-size: 1.02rem; }
.st-key-hero_bar [data-testid="stColumn"]:not(:first-child) [data-testid="stSelectbox"] svg,
.st-key-search_bar [data-testid="stSelectbox"] svg { color: ${violet_text}; fill: ${violet_text}; }
.st-key-hero_bar [data-testid="stButtonGroup"] [role="radiogroup"] { gap: 0; flex-wrap: nowrap; }
.st-key-hero_bar [data-testid="stButtonGroup"] button {
  background: #ffffff; border: 2px solid ${violet}; color: ${violet_text}; min-height: 2.7rem; padding: 0 1.05rem;
  border-radius: 0; box-shadow: none; }
.st-key-hero_bar [data-testid="stButtonGroup"] button:first-child { border-radius: 999px 0 0 999px; }
.st-key-hero_bar [data-testid="stButtonGroup"] button:last-child { border-radius: 0 999px 999px 0; margin-left: -2px; }
.st-key-hero_bar [data-testid="stButtonGroup"] button p { color: inherit; font-weight: 700; font-size: 1rem;
  white-space: nowrap; }
.st-key-hero_bar [data-testid="stButtonGroup"] button[aria-checked="true"] { background: ${violet}; color: #ffffff; }
.st-key-hero_bar [data-testid="stButtonGroup"] button:hover { filter: brightness(0.97); }
/* the arrow / search button at the right end of the bar */
.vse-go, .st-key-search_bar [data-testid="stBaseButton-secondary"] {
  display: block; height: 3.75rem; width: 100%; min-width: 4rem; margin: -0.5rem 0; border: none;
  border-radius: 0 5px 5px 0; color: #ffffff; text-decoration: none;
  background: ${arrow} center / 1.8rem no-repeat, linear-gradient(135deg, ${go_from} 0%, ${go_to} 100%); }
.st-key-search_bar [data-testid="stButton"], .st-key-search_bar [data-testid="stButton"] > button { width: 100%; }
.st-key-search_bar [data-testid="stBaseButton-secondary"] p { display: none; }
.vse-go:hover, .st-key-search_bar [data-testid="stBaseButton-secondary"]:hover { filter: brightness(1.12); }
/* the bar's fields share one line; in a narrow bar they wrap onto more lines instead of being squeezed */
.st-key-hero_bar [data-testid="stHorizontalBlock"], .st-key-search_bar [data-testid="stHorizontalBlock"] {
  flex-wrap: nowrap; }
.st-key-hero_bar [data-testid="stColumn"]:nth-child(1), .st-key-search_bar [data-testid="stColumn"]:nth-child(1) {
  min-width: 12rem; }
.st-key-hero_bar [data-testid="stColumn"]:nth-child(2) { min-width: max-content; }
.st-key-search_bar [data-testid="stColumn"]:nth-child(2) { min-width: 13rem; }
.st-key-hero_bar [data-testid="stColumn"]:last-child, .st-key-search_bar [data-testid="stColumn"]:last-child {
  min-width: 4rem; flex: 0 0 4rem; }
@container (max-width: 47rem) {
  .st-key-hero_bar [data-testid="stHorizontalBlock"], .st-key-search_bar [data-testid="stHorizontalBlock"] {
    flex-wrap: wrap; row-gap: 0.45rem; padding-right: 1.1rem; }
  .st-key-hero_bar [data-testid="stColumn"]:nth-child(n), .st-key-search_bar [data-testid="stColumn"]:nth-child(n) {
    min-width: 100%; flex: 1 1 100%; }
  .st-key-hero_bar [data-testid="stButtonGroup"] [role="radiogroup"] { width: 100%; }
  .st-key-hero_bar [data-testid="stButtonGroup"] button { flex: 1 1 0; min-width: 0; padding: 0.3rem 0.6rem; }
  .st-key-hero_bar [data-testid="stButtonGroup"] button p { white-space: normal; line-height: 1.15; font-size: 0.95rem; }
  .vse-go, .st-key-search_bar [data-testid="stBaseButton-secondary"] { margin: 0.1rem 0 0.2rem; border-radius: 5px;
    height: 2.9rem; }
}
@media (max-width: 640px) { .st-key-areas [role="tablist"]::before { display: none; } .st-key-areas { padding: 0.5rem 1rem 1.2rem; } }

/* ── 5. The offence panel: a dark card with a titled tab row and a grid of links ── */
.st-key-areas { background: ${panel}; border: 1px solid ${line}; border-radius: 6px; padding: 0.5rem 2rem 1.4rem; }
.st-key-areas [role="tablist"] { gap: 1.9rem; border-bottom: 1px solid rgba(77, 141, 255, 0.6); }
.st-key-areas [role="tablist"]::before { content: "Offences"; align-self: center; font-weight: 700;
  font-size: 1.15rem; color: #ffffff; margin-right: 1.4rem; padding-left: 2rem; white-space: nowrap;
  background: ${chevron} left center / 1.15rem no-repeat; }
.st-key-areas [data-testid="stTab"] p { font-size: 1.08rem; }
.st-key-areas [data-testid="stTab"][aria-selected="true"] p { color: #ffffff; }
.st-key-areas [data-testid="stBaseButton-tertiary"] { padding: 0.2rem 0; min-height: 0; justify-content: flex-start;
  text-align: left; }
.st-key-areas [data-testid="stBaseButton-tertiary"] .stMarkdownEllipsis,
.st-key-areas [data-testid="stBaseButton-tertiary"] p {
  white-space: normal; overflow: visible; text-overflow: clip; font-size: 1.06rem; color: #ffffff; text-align: left; }
.st-key-areas [data-testid="stBaseButton-tertiary"]:hover p { color: #9fbaff; text-decoration: underline; }
.vse-area { font-size: 1.06rem; color: #c3cde3; padding: 0.2rem 0; }
.vse-area small { color: ${muted}; font-size: 0.9rem; }

/* ── 6. Cards, expanders, buttons, metrics ────────────────────────────────── */
[data-testid="stExpander"] details { background: rgba(6, 15, 40, 0.62); border: 1px solid ${line}; border-radius: 10px; }
[data-testid="stExpander"] summary { padding-top: 0.85rem; padding-bottom: 0.85rem; }
[data-testid="stExpander"] summary p { font-size: 1.05rem; }
[class*="st-key-card_"] { background: rgba(6, 15, 40, 0.55); border-radius: 10px; }
[data-testid="stBaseButton-primary"] { background: linear-gradient(135deg, ${go_from} 0%, ${go_to} 100%);
                                       border: none; font-weight: 700; }
[data-testid="stBaseButton-primary"]:hover { filter: brightness(1.1); }
[data-testid="stBaseButton-primary"]:disabled { filter: grayscale(0.7); opacity: 0.45; }
[data-testid="stBaseButton-secondary"] { border-color: rgba(143, 176, 255, 0.45); }
[data-testid="stMetricValue"] { font-weight: 300; }
.st-key-status_row [data-testid="stHorizontalBlock"] { flex-wrap: wrap; row-gap: 0.6rem; }
.st-key-status_row [data-testid="stColumn"]:nth-child(1) { min-width: min(100%, 26rem); }
.st-key-status_row [data-testid="stColumn"]:nth-child(2) { min-width: 9rem; }
.st-key-status_row [data-testid="stColumn"]:nth-child(3) { min-width: 7.5rem; }
.vse-steps { display: flex; flex-wrap: wrap; gap: 0.4rem 0.5rem; align-items: center; margin: 0.4rem 0; }
.vse-step { padding: 0.25rem 0.8rem; border-radius: 999px; border: 1px solid ${line}; color: ${muted};
            font-size: 0.92rem; }
.vse-step.done { background: rgba(77, 141, 255, 0.18); border-color: rgba(77, 141, 255, 0.55); color: #ffffff; }
.vse-step.next { border-color: #ffffff; color: #ffffff; }
.vse-anchor { position: relative; top: -4.5rem; height: 0; }

/* ── 7. The result card: the estimate, large and centred ──────────────────── */
.vse-result { container-type: inline-size; position: relative; margin: 0.4rem 0 1.2rem; padding: 2.3rem 2.4rem 1.6rem;
  border-radius: 14px; text-align: center; color: ${text};
  background: radial-gradient(900px 320px at 50% -10%, rgba(77, 141, 255, 0.20), transparent 70%),
              linear-gradient(180deg, rgba(14, 30, 72, 0.94) 0%, rgba(8, 18, 46, 0.94) 100%);
  border: 1px solid rgba(126, 156, 226, 0.24); box-shadow: 0 24px 60px rgba(2, 8, 30, 0.55); }
.vse-result .vr-alert { display: inline-flex; gap: 0.5rem; align-items: center; margin: -0.6rem 0 1.2rem;
  padding: 0.45rem 0.95rem; border-radius: 999px; background: rgba(208, 59, 59, 0.16);
  border: 1px solid rgba(208, 59, 59, 0.7); color: #ffd9d9; font-weight: 700; font-size: 0.95rem; }
.vse-result .vr-alert .ic { color: ${critical}; }
.vse-result .vr-overline { font-size: 0.78rem; letter-spacing: 0.16em; text-transform: uppercase; color: #9fbaff;
  font-weight: 700; }
.vse-result .vr-charge { color: #c3cde3; font-size: 1.02rem; margin-top: 0.35rem; }
.vse-result .vr-sentence { font-weight: 300; font-size: clamp(2.5rem, 5.4vw, 4.6rem); line-height: 1.04;
  color: #ffffff; margin: 1.05rem 0 0.35rem; letter-spacing: -0.015em; }
.vse-result .vr-kind { font-size: 0.98rem; letter-spacing: 0.14em; text-transform: uppercase; color: #9fbaff;
  font-weight: 700; }
.vse-result .vr-detail { color: ${text}; font-size: 1.12rem; margin-top: 0.6rem; }
.vse-result .vr-range { display: inline-block; margin-top: 1.15rem; padding: 0.45rem 1.05rem; border-radius: 999px;
  border: 1px solid rgba(126, 156, 226, 0.38); color: #c3cde3; font-size: 1rem; }
.vse-result .vr-range b { color: #ffffff; font-weight: 700; }
.vse-result .vr-explain { color: #c3cde3; font-size: 0.92rem; line-height: 1.5; max-width: 52rem;
  margin: 0.8rem auto 0; }
.vse-result .vr-explain b { color: #ffffff; }
.vse-result details.vr-assume { max-width: 52rem; margin: 0.5rem auto 0; text-align: left; font-size: 0.9rem; }
.vse-result details.vr-assume summary { cursor: pointer; color: #9fbaff; text-align: center; }
.vse-result details.vr-assume ul { margin: 0.4rem 0 0 1.2rem; color: #c3cde3; }
.vse-result .vr-prosecution { display: inline-block; margin-top: 0.7rem; padding: 0.4rem 0.9rem; border-radius: 8px;
  border: 1px solid rgba(250, 178, 25, 0.55); color: #ffe7b0; font-size: 0.9rem; }
.vse-result .vr-revised { color: ${muted}; font-size: 0.88rem; margin-top: 0.55rem; }
.vse-result .vr-grid { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(0, 1fr); gap: 1.1rem;
  margin-top: 1.7rem; text-align: left; }
@container (max-width: 760px) { .vse-result .vr-grid { grid-template-columns: minmax(0, 1fr); } }
.vse-result .vr-panel { background: rgba(5, 13, 34, 0.55); border: 1px solid rgba(126, 156, 226, 0.16);
  border-radius: 10px; padding: 1.05rem 1.2rem 1rem; min-width: 0; }
.vse-result .vr-h { font-size: 0.82rem; letter-spacing: 0.08em; text-transform: uppercase; color: ${muted};
  font-weight: 700; margin-bottom: 0.2rem; }
.vse-result .vr-sub { color: ${muted}; font-size: 0.88rem; line-height: 1.45; }
/* the strip: the band, the estimate and each comparable case on one months axis */
.vse-strip { position: relative; height: 150px; margin: 0.6rem 0.6rem 0.2rem; }
.vse-strip .axis { position: absolute; left: 0; right: 0; bottom: 26px; height: 1px; background: rgba(163, 180, 220, 0.38); }
.vse-strip .tick { position: absolute; bottom: 4px; transform: translateX(-50%); font-size: 0.76rem; color: #8d9bbd;
  font-variant-numeric: tabular-nums; white-space: nowrap; }
.vse-strip .tick.lane-label { white-space: normal; text-align: center; line-height: 1.1; bottom: 0; width: 4.2rem; }
.vse-strip .tickline { position: absolute; bottom: 21px; width: 1px; height: 5px; background: rgba(163, 180, 220, 0.38); }
.vse-strip .lane { position: absolute; bottom: 27px; top: 30px; border-right: 1px solid rgba(163, 180, 220, 0.20); }
.vse-strip .band { position: absolute; bottom: 27px; top: 30px; background: rgba(63, 134, 255, 0.14);
  border-left: 2px solid rgba(63, 134, 255, 0.75); border-right: 2px solid rgba(63, 134, 255, 0.75);
  border-radius: 4px 4px 0 0; }
.vse-strip .est { position: absolute; bottom: 27px; top: 22px; width: 2px; margin-left: -1px; background: #ffffff;
  border-radius: 2px; }
.vse-strip .est-label { position: absolute; top: 0; transform: translateX(-50%); font-size: 0.78rem; color: #ffffff;
  font-weight: 700; white-space: nowrap; }
.vse-strip .est-label.left { transform: none; }
.vse-strip .est-label.right { transform: translateX(-100%); }
.vse-dot { position: absolute; width: 24px; height: 24px; margin-left: -12px; display: flex; align-items: center;
  justify-content: center; outline: none; cursor: default; z-index: 2; }
.vse-dot::before { content: ""; width: 12px; height: 12px; border-radius: 50%; background: var(--dot);
  box-shadow: 0 0 0 2px #0a1636; transition: transform 0.12s ease; }
.vse-dot.within { --dot: ${within}; }
.vse-dot.out { --dot: ${outside}; }
.vse-dot:hover::before, .vse-dot:focus::before { transform: scale(1.3); }
.vse-dot:focus-visible { outline: 2px solid #ffffff; outline-offset: 1px; border-radius: 50%; }
.vse-dot .tip { position: absolute; bottom: 27px; left: 50%; transform: translateX(-50%); min-width: 12rem;
  max-width: 18rem; background: #0b1838; border: 1px solid rgba(126, 156, 226, 0.38); color: ${text};
  padding: 0.5rem 0.65rem; border-radius: 6px; font-size: 0.8rem; line-height: 1.35; text-align: left;
  white-space: normal; opacity: 0; pointer-events: none; transition: opacity 0.12s; z-index: 10;
  box-shadow: 0 10px 24px rgba(2, 8, 30, 0.6); }
.vse-dot .tip b { color: #ffffff; font-size: 0.9rem; }
.vse-dot .tip span { color: ${muted}; }
.vse-dot.tl .tip { left: 0; transform: none; }
.vse-dot.tr .tip { left: auto; right: 0; transform: none; }
.vse-dot:hover .tip, .vse-dot:focus .tip { opacity: 1; }
.vse-legend { display: flex; flex-wrap: wrap; gap: 0.35rem 1.1rem; margin: 0.35rem 0 0.1rem; font-size: 0.82rem;
  color: ${muted}; }
.vse-legend i { display: inline-block; vertical-align: -1px; margin-right: 0.35rem; }
.vse-legend .sw-dot { width: 10px; height: 10px; border-radius: 50%; }
.vse-legend .sw-dot.within { background: ${within}; }
.vse-legend .sw-dot.out { background: ${outside}; }
.vse-legend .sw-band { width: 16px; height: 10px; background: rgba(63, 134, 255, 0.25);
  border-left: 2px solid rgba(63, 134, 255, 0.75); border-right: 2px solid rgba(63, 134, 255, 0.75); }
.vse-legend .sw-est { width: 2px; height: 12px; background: #ffffff; }
/* the stat tile: the share of similar cases within the range */
.vse-result .vr-value { font-weight: 300; font-size: 3.1rem; line-height: 1.05; color: #ffffff; margin: 0.35rem 0 0.15rem; }
.vse-result .vr-value small { font-size: 1.5rem; color: #c3cde3; margin-left: 0.1rem; }
.vse-result .vr-value.none { color: #8d9bbd; }
.vse-meter { height: 8px; border-radius: 999px; background: rgba(63, 134, 255, 0.18); overflow: hidden; margin: 0.5rem 0 0.55rem; }
.vse-meter > div { height: 100%; border-radius: 999px; background: ${within}; }
.vse-result .vr-counts { color: ${text}; font-size: 0.92rem; margin-bottom: 0.45rem; }
.vse-result .vr-note { color: ${muted}; font-size: 0.82rem; line-height: 1.45; margin-top: 0.5rem;
  border-top: 1px solid rgba(126, 156, 226, 0.14); padding-top: 0.55rem; }
/* the similar cases as a list (the chart's table view) */
.vse-cases { width: 100%; border-collapse: collapse; margin-top: 0.8rem; font-size: 0.86rem; }
.vse-cases th { text-align: left; color: ${muted}; font-weight: 700; font-size: 0.74rem; letter-spacing: 0.06em;
  text-transform: uppercase; padding: 0.3rem 0.5rem; border-bottom: 1px solid rgba(126, 156, 226, 0.18); }
.vse-cases td { padding: 0.38rem 0.5rem; border-bottom: 1px solid rgba(126, 156, 226, 0.10); vertical-align: top;
  color: ${text}; }
.vse-cases td.num { font-variant-numeric: tabular-nums; color: #c3cde3; white-space: nowrap; }
.vse-cases td.pos { white-space: nowrap; }
.vse-cases .key { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 0.4rem; }
.vse-cases .key.within { background: ${within}; }
.vse-cases .key.out { background: ${outside}; }
.vse-cases .key.none { background: transparent; border: 1px solid ${outside}; }
.vse-cases .muted { color: #8d9bbd; }
.vse-result details.vr-more { margin-top: 1rem; text-align: left; background: rgba(5, 13, 34, 0.45);
  border: 1px solid rgba(126, 156, 226, 0.14); border-radius: 10px; padding: 0.7rem 1.2rem; }
.vse-result details.vr-more summary { cursor: pointer; color: #9fbaff; font-size: 0.92rem; }
.vse-cases th:nth-child(1) { width: 36%; }
.vse-cases th:nth-child(2) { width: 34%; }
.vse-cases th:nth-child(3) { width: 12%; }
.vse-cases .lbl { display: none; }       /* the score's name, shown only when the table's header is hidden */
@container (max-width: 640px) {        /* a narrow card: each case as a block instead of a table row */
  .vse-cases thead { display: none; }
  .vse-cases tr { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0.15rem 0.8rem; padding: 0.55rem 0;
    border-bottom: 1px solid rgba(126, 156, 226, 0.12); }
  .vse-cases td { display: block; border: none; padding: 0; }
  .vse-cases td:nth-child(1) { grid-column: 1 / -1; font-weight: 700; }
  .vse-cases td:nth-child(2) { grid-column: 1; }
  .vse-cases td:nth-child(3) { grid-column: 1; }
  .vse-cases .lbl { display: inline; }
  .vse-cases td:nth-child(4) { grid-column: 2; grid-row: 2; }
}
@container (max-width: 560px) {        /* a phone: less padding, smaller axis labels */
  .vse-result { padding: 1.6rem 0.9rem 1.2rem; }
  .vse-result .vr-panel { padding: 0.9rem 0.75rem 0.85rem; }
  .vse-strip { margin: 0.6rem 0.2rem 0.2rem; }
  .vse-strip .tick { font-size: 0.68rem; }
  .vse-strip .tick.lane-label { display: none; }      /* the caption says where other orders sit */
  .vse-result details.vr-more { padding: 0.6rem 0.75rem; }
}
.vse-result .vr-caption { color: ${muted}; font-size: 0.84rem; line-height: 1.45; margin-top: 0.55rem; }
/* the framework and the checks, as chips */
.vse-chips { display: flex; flex-wrap: wrap; justify-content: center; gap: 0.5rem 0.55rem; margin-top: 1.5rem; }
.vse-chip { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.38rem 0.85rem; border-radius: 999px;
  background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(126, 156, 226, 0.26); font-size: 0.9rem; color: ${text}; }
.vse-chip .k { color: ${muted}; }
.vse-chip .ic { font-weight: 700; }
.vse-chip.good { border-color: rgba(12, 163, 12, 0.55); }
.vse-chip.good .ic { color: ${good}; }
.vse-chip.warn { border-color: rgba(250, 178, 25, 0.55); }
.vse-chip.warn .ic { color: ${warning}; }
.vse-chip.bad { border-color: rgba(208, 59, 59, 0.75); background: rgba(208, 59, 59, 0.10); }
.vse-chip.bad .ic { color: ${critical}; }
.vse-result .vr-disclaimer { color: #8d9bbd; font-size: 0.82rem; line-height: 1.5; margin: 1.3rem auto 0; max-width: 60rem; }
</style>
"""

# The chevron before the offence panel's title, and the arrow on the start bar's button, drawn in CSS
# (st.html does not keep inline SVG).
CHEVRON_CSS = 'url("data:image/svg+xml,' + quote(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='2.4' "
    "stroke-linecap='round' stroke-linejoin='round'><path d='M6 9l6 6 6-6'/></svg>") + '")'
ARROW_CSS = 'url("data:image/svg+xml,' + quote(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='2.2' "
    "stroke-linecap='round' stroke-linejoin='round'><path d='M5 12h14'/><path d='M13 6l6 6-6 6'/></svg>") + '")'


def theme_css() -> str:
    """The app's CSS with the palette filled in."""
    return Template(CSS).substitute(PALETTE, arrow=ARROW_CSS, chevron=CHEVRON_CSS)


def apply_theme() -> None:
    """Add the app's CSS (call once per page load, before the page runs)."""
    st.html(theme_css())


def hero(title: str, subtitle: str = "", small: bool = False) -> None:
    """The large, light headline at the top of a page (and a muted line under it)."""
    st.html(f'<div class="vse-hero{" small" if small else ""}"><h1>{html.escape(title)}</h1>'
            + (f"<p>{html.escape(subtitle)}</p>" if subtitle else "") + "</div>")


def go_button(href: str, title: str) -> None:
    """The blue arrow at the end of a start bar: a link to a place on the same page."""
    st.html(f'<a class="vse-go" href="{html.escape(href)}" title="{html.escape(title)}" '
            f'aria-label="{html.escape(title)}"></a>')


def anchor(name: str) -> None:
    """A place on the page that a link ("#name") can go to."""
    st.html(f'<div class="vse-anchor" id="{html.escape(name)}"></div>')


def steps_row(steps: list[str], done: int) -> None:
    """The run's progress as a row of rounded labels (done, next, to do)."""
    chips = []
    for k, s in enumerate(steps):
        cls = "done" if k < done else ("next" if k == done else "")
        chips.append(f'<span class="vse-step {cls}">{"✓ " if k < done else ""}{html.escape(s)}</span>')
    st.html('<div class="vse-steps">' + "".join(chips) + "</div>")
