"""Settings: active configuration (read-only), provider capabilities, data inventory and safety status."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from quantedge.app import data as D
from quantedge.config import get_settings
from quantedge.data.providers import provider_info
from quantedge.ui import components as C

s = get_settings()
info = provider_info()
C.page_header("Settings", "Configuration comes from environment variables (.env). Edit there and restart.")

a, b = st.columns(2, gap="small")
with a:
    with C.panel("set_cfg", "Configuration"):
        db = s.database_url.split("@")[-1] if "@" in s.database_url else s.database_url
        C.kv([("QE_TRADING_MODE", C.tag(s.trading_mode, "accent")),
              ("QE_DATA_PROVIDER", C.esc(s.data_provider)),
              ("Database", C.esc(db)),
              ("History on first fetch", f"{s.history_years} years"),
              ("EODHD_API_KEY", "set" if s.eodhd_api_key else '<span class="qe-muted">not set</span>'),
              ("QE_LLM_PROVIDER", C.esc(s.llm_provider)),
              ("ANTHROPIC_API_KEY", "set" if s.anthropic_api_key else '<span class="qe-muted">not set</span>'),
              ("LLM monthly budget", f"${s.llm_monthly_budget_usd:.0f}")])
    with C.panel("set_safety", "Safety"):
        C.list_rows([("Real-money execution", "Not implemented; any other trading mode is refused at start-up",
                      C.pill("", "disabled", "off")),
                     ("Broker connection", "None configured", C.pill("", "none", "off")),
                     ("Paper trading", "Phase 7; promotion requires your recorded approval", C.pill("", "not built", "off"))])
with b:
    with C.panel("set_provider", "Data provider", C.tag("demo", "demo") if info.is_demo else C.tag("real", "accent")):
        C.kv([("Name", C.esc(info.name)), ("Latency", C.esc(info.latency)),
              ("Markets", C.esc(", ".join(info.markets))), ("Daily bars", "yes" if info.daily else "no"),
              ("Intraday", C.esc(", ".join(info.intraday_intervals)) if info.intraday else "no"),
              ("Needs API key", "yes" if info.requires_key else "no")])
        st.caption(info.licence_note)
        for n in info.notes:
            st.caption("· " + n)

ft = D.freshness_table(D.provider())
with C.panel("set_inv", "Stored data inventory", f"{len(ft)} symbols"):
    if ft.empty:
        C.empty_state("Nothing stored", "Refresh data from Home.")
    else:
        st.dataframe(pd.DataFrame({"Symbol": ft["symbol"], "Mkt": ft["market"], "Last bar": ft["last_bar"],
                                   "Last fetch (UTC)": ft["last_fetch"], "Fetch status": ft["last_status"],
                                   "Quality OK": ft["quality_ok"], "Issues": ft["issue_count"]}),
                     hide_index=True, use_container_width=True,
                     column_config={"Last bar": st.column_config.DateColumn(format="DD MMM YYYY"),
                                    "Last fetch (UTC)": st.column_config.DatetimeColumn(format="DD MMM HH:mm")})
