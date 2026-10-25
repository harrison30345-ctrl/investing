"""Backtests: the experiment log (every run, including failures) and per-run detail."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from quantedge.app import data as D
from quantedge.strategies import REGISTRY
from quantedge.ui import charts
from quantedge.ui import components as C

mkt = C.market_filter()
C.page_header("Backtests", "Experiment log: every run is kept, including failures")

exps = D.experiments(500)
if exps.empty:
    C.empty_state("No experiments yet", "Run a backtest from the command line. Phase 5 adds running them from the "
                  "Strategy Lab.", "python3 -m quantedge.cli backtest donchian_breakout --universe us_large")
    st.stop()
if mkt:
    exps = exps[exps["market"] == mkt]

met = lambda col: exps["metrics"].map(lambda m: (m or {}).get(col))  # noqa: E731
table = pd.DataFrame({
    "ID": exps["id"], "Created": pd.to_datetime(exps["created_at"]),
    "Strategy": exps["strategy_key"].map(lambda k: REGISTRY[k].name if k in REGISTRY else k),
    "Mkt": exps["market"], "Period": exps["period"], "Status": exps["status"],
    "Net %": met("net_return") * 100, "Bench %": met("benchmark_return") * 100,
    "Sharpe": met("sharpe"), "Max DD %": met("max_drawdown") * 100, "Trades": met("trade_count"),
    "Test set": exps["used_test_set"].map({True: "used", False: ""}), "Data": exps["data_provider"],
    "Source": exps["source"],
})
with C.panel("bt_log", "Experiments", f"{len(table)} runs"):
    sel = st.dataframe(table, hide_index=True, use_container_width=True, height=300, on_select="rerun",
                       selection_mode="single-row", key="bt_tbl",
                       column_config={"Created": st.column_config.DatetimeColumn(format="DD MMM HH:mm"),
                                      "Net %": st.column_config.NumberColumn(format="%+.1f"),
                                      "Bench %": st.column_config.NumberColumn(format="%+.1f"),
                                      "Sharpe": st.column_config.NumberColumn(format="%.2f"),
                                      "Max DD %": st.column_config.NumberColumn(format="%.1f"),
                                      "Trades": st.column_config.NumberColumn(format="%d")})

rows = sel.selection.rows if sel else []
e = exps.iloc[rows[0]] if rows else exps.iloc[0]
name = REGISTRY[e["strategy_key"]].name if e["strategy_key"] in REGISTRY else e["strategy_key"]
if e["status"] != "completed":
    with C.panel("bt_fail", f"#{int(e['id'])} · {name}", C.tag(e["status"], "neg")):
        st.code(e["error"] or "No error recorded", language=None)
    st.stop()

m = e["metrics"] or {}
curve = D.experiment_curve(int(e["id"]))
trades = D.experiment_trades(int(e["id"]))
meta = (f"{C.tag(e['period'], 'accent')} &nbsp;{pd.Timestamp(e['start_date']):%d %b %Y} → "
        f"{pd.Timestamp(e['end_date']):%d %b %Y} · {len(e['universe'])} symbols · {C.esc(e['data_provider'])}")
with C.panel("bt_detail", f"#{int(e['id'])} · {name} · {e['market']}", meta):
    C.metric_grid([
        ("Net return", C.fmt_pct(m.get("net_return"), digits=1), C.tone(m.get("net_return"))),
        ("CAGR", C.fmt_pct(m.get("cagr"), digits=1), C.tone(m.get("cagr"))),
        (f"{e['benchmark']}", C.fmt_pct(m.get("benchmark_return"), digits=1), C.tone(m.get("benchmark_return"))),
        ("Excess", C.fmt_pct(m.get("excess_return"), digits=1), C.tone(m.get("excess_return"))),
        ("Sharpe", f"{m.get('sharpe', 0):.2f}", ""),
        ("Max drawdown", C.fmt_pct(m.get("max_drawdown"), digits=1), "qe-neg"),
        ("Trades", f"{int(m.get('trade_count', 0))}", ""),
        ("Win rate", C.fmt_pct(m.get("win_rate"), signed=False, digits=0), ""),
        ("Profit factor", "—" if m.get("profit_factor") is None else f"{m['profit_factor']:.2f}", ""),
        ("Expectancy", C.fmt_pct(m.get("expectancy_pct"), digits=2), C.tone(m.get("expectancy_pct"))),
        ("Avg win / loss", f"{C.fmt_pct(m.get('avg_win_pct'), digits=1)} / {C.fmt_pct(m.get('avg_loss_pct'), digits=1)}", ""),
        ("Exposure", C.fmt_pct(m.get("exposure"), signed=False, digits=0), ""),
        ("Turnover / yr", f"{m.get('turnover', 0):.1f}×", ""),
        ("Costs", C.fmt_pct(m.get("costs_pct_of_start"), signed=False, digits=1), ""),
    ])
    st.plotly_chart(charts.equity_vs_benchmark(curve["equity"], curve["benchmark"], height=280, strategy_label=name,
                                               benchmark_label=f"{e['benchmark']} (total return)"),
                    config=charts.CONFIG, use_container_width=True)
    st.plotly_chart(charts.drawdown(curve["equity"], height=140, bm=curve["benchmark"]), config=charts.CONFIG,
                    use_container_width=True)
    for n in e["bias_notes"] or []:
        st.caption("⚠ " + n)

t1, t2 = st.tabs([f"Trades ({len(trades)})", "Configuration"])
with t1:
    if trades.empty:
        C.empty_state("No trades", "The rules never fired in this window.")
    else:
        st.dataframe(pd.DataFrame({
            "Symbol": trades["symbol"], "Entry": pd.to_datetime(trades["entry_ts"]), "Entry px": trades["entry_price"],
            "Exit": pd.to_datetime(trades["exit_ts"]), "Exit px": trades["exit_price"], "Shares": trades["shares"],
            "Net P&L": trades["net_pnl"], "Return %": trades["return_pct"] * 100, "Bars": trades["bars_held"],
            "Exit reason": trades["exit_reason"], "Costs": trades["costs"],
        }), hide_index=True, use_container_width=True, height=360,
            column_config={"Entry": st.column_config.DateColumn(format="DD MMM YY"),
                           "Exit": st.column_config.DateColumn(format="DD MMM YY"),
                           "Entry px": st.column_config.NumberColumn(format="%.2f"),
                           "Exit px": st.column_config.NumberColumn(format="%.2f"),
                           "Net P&L": st.column_config.NumberColumn(format="%+,.0f"),
                           "Return %": st.column_config.NumberColumn(format="%+.2f"),
                           "Costs": st.column_config.NumberColumn(format="%.0f")})
        st.caption("Prices are dividend/split-adjusted and in account currency (UK in pounds).")
with t2:
    st.json({"params": e["params"], "config": e["config"], "universe": e["universe"],
             "split": m.get("split"), "missing_symbols": m.get("missing_symbols")}, expanded=False)
