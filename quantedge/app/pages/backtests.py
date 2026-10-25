"""Backtests: the experiment log (every run, including failures), per-run detail and side-by-side comparison."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from quantedge.app import data as D
from quantedge.app import views as V
from quantedge.ui import components as C
from quantedge.ui import theme as T

mkt = C.market_filter()
C.page_header("Backtests", "Experiment log: every run is kept, including failures")

exps = D.experiments(1000)
if exps.empty:
    C.empty_state("No experiments yet", "Run one from the Strategy Lab, or from the CLI.",
                  "python3 -m quantedge.cli backtest donchian_breakout --universe us_large")
    st.stop()
if mkt:
    exps = exps[exps["market"] == mkt]

f1, f2, f3, f4 = st.columns([1.6, 1, 1, 1.2])
with f1:
    strat = st.multiselect("Strategy", sorted(exps["strategy_key"].unique()), format_func=V.strategy_name,
                           placeholder="All strategies")
with f2:
    period = st.multiselect("Period", ["train", "validation", "test", "full"], placeholder="All periods")
with f3:
    status = st.multiselect("Status", ["completed", "failed", "running"], placeholder="All")
with f4:
    hide_timing = st.toggle("Hide timing/diagnostic runs", value=True)
view = exps
if strat:
    view = view[view["strategy_key"].isin(strat)]
if period:
    view = view[view["period"].isin(period)]
if status:
    view = view[view["status"].isin(status)]
if hide_timing:
    view = view[view["source"] != "timing"]
view = view.reset_index(drop=True)

met = lambda col: view["metrics"].map(lambda m: (m or {}).get(col))  # noqa: E731
table = pd.DataFrame({
    "ID": view["id"], "Created": pd.to_datetime(view["created_at"]), "Strategy": view["strategy_key"].map(V.strategy_name),
    "Mkt": view["market"], "Period": view["period"], "Status": view["status"],
    "Net %": met("net_return") * 100, "Bench %": met("benchmark_return") * 100,
    "Sharpe": met("sharpe"), "Max DD %": met("max_drawdown") * 100, "Trades": met("trade_count"),
    "Notes": view["notes"].fillna(""), "Test set": view["used_test_set"].map({True: "used", False: ""}),
    "Source": view["source"],
})
with C.panel("bt_log", "Experiments", f"{len(view)} of {len(exps)} runs · select one to inspect, several to compare"):
    sel = st.dataframe(table, hide_index=True, use_container_width=True, height=320, on_select="rerun",
                       selection_mode="multi-row", key="bt_tbl",
                       column_config={"Created": st.column_config.DatetimeColumn(format="DD MMM HH:mm"),
                                      "Net %": st.column_config.NumberColumn(format="%+.1f"),
                                      "Bench %": st.column_config.NumberColumn(format="%+.1f"),
                                      "Sharpe": st.column_config.NumberColumn(format="%.2f"),
                                      "Max DD %": st.column_config.NumberColumn(format="%.1f"),
                                      "Trades": st.column_config.NumberColumn(format="%d")})

rows = sel.selection.rows if sel else []
if len(rows) >= 2:
    if len(rows) > T.MAX_COMPARE:
        st.caption(f"Comparing the first {T.MAX_COMPARE} selected.")
    V.comparison(view.iloc[rows], key="bt_cmp")
elif view.empty:
    C.empty_state("No experiments match the filters", "Clear a filter to see more.")
else:
    focus = st.session_state.pop("bt_focus", None)
    if rows:
        e = view.iloc[rows[0]]
    elif focus is not None and (exps["id"] == focus).any():
        e = exps[exps["id"] == focus].iloc[0]
    else:
        e = view.iloc[0]
    V.experiment_detail(e, key="bt")
