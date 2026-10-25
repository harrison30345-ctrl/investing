"""
App chrome rendered on every page: sidebar brand/footer and the top bar
(search, market selector, data freshness, system status).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st

from quantedge.config import get_settings
from quantedge.data.providers import provider_info
from quantedge.markets import MARKETS, session_status
from quantedge.ui import theme as T
from quantedge.ui.components import MARKET_KEY, SYMBOL_KEY, ago, demo_banner, esc, html_block, pill, tag
from quantedge.universe import CATALOGUE

from . import data as D

MARKETS_PAGE = "screens/markets.py"
WORKER_STALE_AFTER = timedelta(minutes=15)

ASSETS = Path(__file__).resolve().parent / "assets"


def inject_css() -> None:
    # st.html injects <style>/<link> straight into the page. st.markdown would stop at the first blank line
    # and print the rest of the stylesheet as text.
    st.html(T.CSS)


def sidebar() -> None:
    """Logo above the navigation; mode and data source below it."""
    s = get_settings()
    info = provider_info()
    st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "icon.svg"))
    mode = "PAPER" if s.trading_mode == "paper" else "RESEARCH"
    with st.sidebar:
        html_block(
            f'<div class="qe-sidefoot">{tag(mode, "accent")} &nbsp;No live execution<br>'
            f'Data: {esc(info.name)}<br>{esc(info.latency)} · {"synthetic demo" if info.is_demo else "real, delayed"}<br>'
            f'Times shown in UK time</div>')


def _search_options():
    stored = D.stored_symbols(D.provider())
    syms = list(dict.fromkeys(list(CATALOGUE) + stored))
    return [f"{s} — {CATALOGUE[s].name}" if s in CATALOGUE else s for s in syms]


def _on_search():
    v = st.session_state.get("qe_search")
    if v:
        st.session_state[SYMBOL_KEY] = v.split(" — ")[0].strip().upper()
        st.session_state["_qe_go_markets"] = True
    st.session_state["qe_search"] = None


def _status_pills() -> str:
    pkey = D.provider()
    info = provider_info()
    fresh = D.market_freshness(pkey)
    parts = []
    parts.append(pill("Data", f"{info.name.split(' (')[0]} · {info.latency}",
                      "warn" if info.is_demo else "ok", info.licence_note))
    for m in ("UK", "US"):
        f = fresh[m]
        if f["status"] == "none":
            parts.append(pill(f"{m} bars", "none stored", "err", "No daily bars stored yet. Use Refresh data."))
        else:
            state = "ok" if f["status"] == "current" else "warn"
            exp = f["expected"].strftime("%d %b") if f["expected"] else "?"
            parts.append(pill(f"{m} bars", f"{f['latest_bar']:%d %b}", state,
                              f"Newest stored daily bar. Last completed session {exp}. Fetched {ago(f['last_fetch'])}."))
    for m in ("UK", "US"):
        ss = session_status(m)
        parts.append(pill(MARKETS[m].short, f"{ss.label}", "ok" if ss.is_open else "off", ss.detail))
    hb = D.heartbeats()
    live = [h for h in hb if h["last_seen"] and datetime.utcnow() - h["last_seen"] < WORKER_STALE_AFTER]
    if live:
        parts.append(pill("Worker", "online", "ok", f"Heartbeat {ago(max(h['last_seen'] for h in live))}"))
    else:
        parts.append(pill("Worker", "not running", "off",
                          "No background worker heartbeat. Scheduled jobs are not executing (Phase 7)."))
    return f'<div class="qe-topbar-status">{"".join(parts)}</div>'


def topbar() -> None:
    if st.session_state.pop("_qe_go_markets", False):
        st.switch_page(MARKETS_PAGE)
    c1, c2, c3 = st.columns([3.0, 1.4, 6.6], vertical_alignment="center", gap="small")
    with c1:
        st.selectbox("Search", _search_options(), index=None, key="qe_search", on_change=_on_search,
                     placeholder="Search symbol or company  (any Yahoo symbol, e.g. TSCO.L)",
                     accept_new_options=True, label_visibility="collapsed")
    with c2:
        st.segmented_control("Market", ["All", "UK", "US"], key=MARKET_KEY, default="All",
                             label_visibility="collapsed")
    with c3:
        html_block(_status_pills())
    if get_settings().is_demo:
        demo_banner()
