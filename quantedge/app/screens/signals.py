"""Signals: every recorded rule firing, filterable, with full rule/risk/context detail."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from quantedge.app import data as D
from quantedge.strategies import REGISTRY
from quantedge.ui import components as C

KIND_LABEL = {"observation": "Live-data observation (delayed)", "paper": "Paper signal", "backtest": "Backtest result"}

pkey = D.provider()
mkt = C.market_filter()
C.page_header("Signals", "Rules firing on stored daily bars. Observations only, never orders.")

sig = D.signals(pkey, 2000)
if sig.empty:
    C.empty_state("No signals recorded", "Refresh data on Home or Watchlists. The signal scan runs automatically "
                  "after each refresh.", "python3 -m quantedge.cli scan")
    st.stop()

f1, f2, f3, f4 = st.columns([1.4, 1.4, 1, 1.2])
with f1:
    strat = st.multiselect("Strategy", sorted(sig["strategy_key"].unique()),
                           format_func=lambda k: REGISTRY[k].name if k in REGISTRY else k)
with f2:
    syms = st.multiselect("Symbol", sorted(sig["symbol"].unique()))
with f3:
    direction = st.segmented_control("Direction", ["All", "Long", "Exit"], default="All") or "All"
with f4:
    kinds = st.multiselect("Type", list(KIND_LABEL), default=list(KIND_LABEL), format_func=KIND_LABEL.get)

view = sig.copy()
if mkt:
    view = view[view["market"] == mkt]
if strat:
    view = view[view["strategy_key"].isin(strat)]
if syms:
    view = view[view["symbol"].isin(syms)]
if direction != "All":
    view = view[view["direction"] == direction.lower()]
view = view[view["kind"].isin(kinds)]

with C.panel("sig_table", "Signal log", f"{len(view)} of {len(sig)}"):
    table = pd.DataFrame({
        "Bar date": pd.to_datetime(view["ts"]), "Symbol": view["symbol"], "Mkt": view["market"],
        "Strategy": view["strategy_key"].map(lambda k: REGISTRY[k].name if k in REGISTRY else k),
        "Signal": view["direction"].str.upper(), "Close": view["price"],
        "Type": view["kind"].map(KIND_LABEL), "Data as of": pd.to_datetime(view["data_as_of"]),
        "Reason": view["reason"],
    })
    sel = st.dataframe(table, hide_index=True, use_container_width=True, height=380, on_select="rerun",
                       selection_mode="single-row", key="sig_tbl",
                       column_config={"Bar date": st.column_config.DateColumn(format="DD MMM YYYY"),
                                      "Data as of": st.column_config.DateColumn(format="DD MMM YYYY"),
                                      "Close": st.column_config.NumberColumn(format="%.2f"),
                                      "Reason": st.column_config.TextColumn(width="large")})
    st.caption("Select a row for full detail. Prices are the unadjusted close; UK in pence.")

rows = sel.selection.rows if sel else []
if rows:
    r = view.iloc[rows[0]]
    name = REGISTRY[r["strategy_key"]].name if r["strategy_key"] in REGISTRY else r["strategy_key"]
    with C.panel("sig_detail", f"{r['symbol']} · {name}",
                 C.tag(KIND_LABEL[r["kind"]], "") + f" &nbsp;bar {pd.Timestamp(r['ts']):%d %b %Y}"):
        a, b = st.columns(2, gap="medium")
        with a:
            C.kv([("Symbol / market", C.esc(f"{r['symbol']} · {r['market']}")),
                  ("Signal", C.esc(r["direction"].upper())),
                  ("Bar close", f"{r['price']:,.2f}"),
                  ("Signal timestamp", C.esc(f"{pd.Timestamp(r['ts']):%d %b %Y} close")),
                  ("Data as of", C.esc(f"{pd.Timestamp(r['data_as_of']):%d %b %Y} (delayed, {r['provider']})")),
                  ("Recorded", C.esc(C.fmt_time(r["created_at"])))])
            st.markdown(f"**Reason.** {r['reason']}")
        with b:
            st.markdown(f"**Entry rule.** {r['entry_rule']}")
            st.markdown(f"**Exit rule.** {r['exit_rule']}")
            risk = r["risk"] or {}
            if risk:
                C.kv([("Stop distance", f"{risk.get('stop_distance', 0):,.2f} ({C.fmt_pct(risk.get('stop_pct'), signed=False)})"),
                      ("Indicative stop", f"{risk.get('indicative_stop', 0):,.2f} (adjusted price)"),
                      ("Target", "none" if not risk.get("target_r") else f"{risk['target_r']}R"),
                      ("Time stop", "none" if not risk.get("time_stop_bars") else f"{risk['time_stop_bars']} bars"),
                      ("Sizing assumption", C.esc(risk.get("sizing", "")))])
        st.caption("Market context: " + json.dumps(r["context"]) + " · Parameters: " + json.dumps(r["params"]))
        st.caption("A rule firing is not a forecast. Check the strategy's backtest, validation and costs before "
                   "drawing conclusions.")
