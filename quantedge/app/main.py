"""
QuantEdge dashboard entry point.

Run with:  python3 -m quantedge.cli dashboard
(which launches `streamlit run` on this file with the QuantEdge theme flags).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from quantedge.config import ConfigError, get_settings  # noqa: E402

st.set_page_config(page_title="QuantEdge", page_icon=str(Path(__file__).parent / "assets" / "icon.svg"),
                   layout="wide", initial_sidebar_state="auto")

try:
    get_settings()
except ConfigError as exc:   # refuse to run with an unsafe or invalid configuration
    st.error(f"Configuration error: {exc}")
    st.stop()

from quantedge.app import chrome  # noqa: E402

P = lambda path, title, icon, **kw: st.Page(path, title=title, icon=f":material/{icon}:", **kw)  # noqa: E731
pages = {
    "Overview": [P("pages/home.py", "Home", "space_dashboard", default=True),
                 P("pages/markets.py", "Markets", "candlestick_chart"),
                 P("pages/watchlists.py", "Watchlists", "visibility")],
    "Research": [P("pages/signals.py", "Signals", "bolt"),
                 P("pages/strategy_lab.py", "Strategy Lab", "science"),
                 P("pages/backtests.py", "Backtests", "history"),
                 P("pages/ai_research.py", "AI Research", "neurology")],
    "Output": [P("pages/reports.py", "Reports", "description")],
    "System": [P("pages/settings.py", "Settings", "tune")],
}

nav = st.navigation(pages, position="sidebar", expanded=True)
chrome.inject_css()
chrome.sidebar()
chrome.topbar()
nav.run()
