"""Experiment views shared by the Strategy Lab and Backtests pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from quantedge.backtest.runner import TestSetLocked, rerun_on_period
from quantedge.reports import compare_rows, sweep_summary
from quantedge.strategies import REGISTRY
from quantedge.ui import charts
from quantedge.ui import components as C
from quantedge.ui import theme as T

from . import data as D

LEVEL_CLASS = {"good": "qe-pos", "info": "qe-muted", "caution": "qe-warn", "warning": "qe-neg"}
LEVEL_ICON = {"good": "✓", "info": "·", "caution": "!", "warning": "✗"}


def strategy_name(key: str) -> str:
    return REGISTRY[key].name if key in REGISTRY else key


def findings(items) -> None:
    C.list_rows([(f.title, f.detail, f'<span class="{LEVEL_CLASS[f.level]}">{LEVEL_ICON[f.level]} {C.esc(f.level)}</span>')
                 for f in items])


def metrics_grid(m: dict, benchmark: str) -> None:
    pf = m.get("profit_factor")
    C.metric_grid([
        ("Net return", C.fmt_pct(m.get("net_return"), digits=1), C.tone(m.get("net_return"))),
        ("CAGR", C.fmt_pct(m.get("cagr"), digits=1), C.tone(m.get("cagr"))),
        (benchmark, C.fmt_pct(m.get("benchmark_return"), digits=1), C.tone(m.get("benchmark_return"))),
        ("Excess", C.fmt_pct(m.get("excess_return"), digits=1), C.tone(m.get("excess_return"))),
        ("EW universe B&H", C.fmt_pct(m.get("universe_return"), digits=1), C.tone(m.get("universe_return"))),
        ("Excess vs universe", C.fmt_pct(m.get("excess_vs_universe"), digits=1), C.tone(m.get("excess_vs_universe"))),
        ("EW monthly (secondary)", C.fmt_pct(m.get("universe_monthly_return"), digits=1)
         if m.get("universe_monthly_return") is not None else "n/a", ""),
        ("Sharpe", f"{m.get('sharpe') or 0:.2f}", ""),
        ("Sharpe 95% CI", f"{m['sharpe_ci_low']:.2f} – {m['sharpe_ci_high']:.2f}"
         if m.get("sharpe_ci_low") is not None else "n/a", ""),
        ("Deflated Sharpe", f"{m['deflated_sharpe']:.2f} ({int(m.get('trials') or 1)} tries)"
         if m.get("deflated_sharpe") is not None else "n/a", ""),
        ("Max drawdown", C.fmt_pct(m.get("max_drawdown"), digits=1), "qe-neg"),
        ("Trades", f"{int(m.get('trade_count') or 0)}", ""),
        ("Win rate", C.fmt_pct(m.get("win_rate"), signed=False, digits=0), ""),
        ("Profit factor", "—" if pf is None else f"{pf:.2f}", ""),
        ("Expectancy", C.fmt_pct(m.get("expectancy_pct"), digits=2), C.tone(m.get("expectancy_pct"))),
        ("Avg win / loss", f"{C.fmt_pct(m.get('avg_win_pct'), digits=1)} / {C.fmt_pct(m.get('avg_loss_pct'), digits=1)}", ""),
        ("Exposure", C.fmt_pct(m.get("exposure"), signed=False, digits=0), ""),
        ("Turnover / yr", f"{m.get('turnover') or 0:.1f}×", ""),
        ("Costs", C.fmt_pct(m.get("costs_pct_of_start"), signed=False, digits=1), ""),
        ("Tie-break range", f"{C.fmt_pct(m['tiebreak_net_min'], digits=0)} … {C.fmt_pct(m['tiebreak_net_max'], digits=0)}"
         if m.get("tiebreak_net_min") is not None else ("no contention" if m.get("tiebreak_seeds") == 1 else "n/a"), ""),
        ("Net at 0× / 2× / 3× costs", " / ".join(C.fmt_pct(m.get(f"cost_x{k}_net_return"), digits=0) for k in (0, 2, 3))
         if m.get("cost_x2_net_return") is not None else "n/a", ""),
        ("Cash rate (avg)", C.fmt_pct(m.get("avg_cash_rate"), signed=False, digits=1)
         if m.get("avg_cash_rate") is not None else "0% (off)", ""),
    ])


def regime_table(m: dict) -> None:
    rows = []
    for name, label in (("uptrend", "Benchmark uptrend"), ("downtrend", "Benchmark downtrend"),
                        ("highvol", "High volatility"), ("lowvol", "Low volatility")):
        if m.get(f"regime_{name}_days_pct") is None:
            continue
        rows.append({"Regime": label, "Share of days": (m.get(f"regime_{name}_days_pct") or 0) * 100,
                     "Strategy %/yr": (m.get(f"regime_{name}_ann") or float("nan")) * 100,
                     "Benchmark %/yr": (m.get(f"regime_{name}_bench_ann") or float("nan")) * 100,
                     "Sharpe above cash": m.get(f"regime_{name}_sharpe")})
    if not rows:
        st.caption("No regime breakdown (needs a year of benchmark history before the window).")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={"Share of days": st.column_config.NumberColumn(format="%.0f%%"),
                                "Strategy %/yr": st.column_config.NumberColumn(format="%+.1f"),
                                "Benchmark %/yr": st.column_config.NumberColumn(format="%+.1f"),
                                "Sharpe above cash": st.column_config.NumberColumn(format="%.2f")})
    st.caption("Days are labelled by the benchmark's state at the previous close: above/below its 200-day "
               "average, and 21-day volatility above/below its trailing 1-year median. Regimes with under 20 "
               "days show no figures.")


def folds_table(m: dict) -> None:
    folds = m.get("folds") or []
    if not folds:
        return
    st.dataframe(pd.DataFrame([{
        "In-sample": " → ".join(f["in_sample"]), "Out-of-sample": " → ".join(f["out_of_sample"]),
        "Chosen": str(f["chosen"]), "OOS net %": f["oos_net_return"] * 100,
        "OOS ETF %": (f.get("oos_benchmark_return") or float("nan")) * 100,
        "OOS same-stocks %": (f.get("oos_universe_return") or float("nan")) * 100,
        "In-sample Sharpe by value": ", ".join(f"{k}: {v:.2f}" for k, v in f["in_sample_sharpe"].items()),
    } for f in folds]), hide_index=True, use_container_width=True,
        column_config={"OOS net %": st.column_config.NumberColumn(format="%+.1f"),
                       "OOS ETF %": st.column_config.NumberColumn(format="%+.1f"),
                       "OOS same-stocks %": st.column_config.NumberColumn(format="%+.1f")})


ADJUSTED_NOTE = "Prices are dividend/split-adjusted, in account currency (UK in pounds)."


def trades_table(trades: pd.DataFrame, key: str, price_note: str = ADJUSTED_NOTE) -> None:
    if trades.empty:
        C.empty_state("No trades", "The rules never fired in this window.")
        return
    reasons = sorted(trades["exit_reason"].unique())
    a, b = st.columns([2, 1])
    with a:
        pick = st.multiselect("Exit reason", reasons, key=f"{key}_reasons", placeholder="All exit reasons")
    with b:
        sym = st.multiselect("Symbol", sorted(trades["symbol"].unique()), key=f"{key}_syms", placeholder="All symbols")
    t = trades
    if pick:
        t = t[t["exit_reason"].isin(pick)]
    if sym:
        t = t[t["symbol"].isin(sym)]
    st.dataframe(pd.DataFrame({
        "Symbol": t["symbol"], "Entry": pd.to_datetime(t["entry_ts"]), "Entry px": t["entry_price"],
        "Exit": pd.to_datetime(t["exit_ts"]), "Exit px": t["exit_price"], "Shares": t["shares"],
        "Net P&L": t["net_pnl"], "Return %": t["return_pct"] * 100, "Bars": t["bars_held"],
        "Exit reason": t["exit_reason"], "Costs": t["costs"], "Entry reason": t["entry_reason"],
    }), hide_index=True, use_container_width=True, height=360, key=f"{key}_tbl",
        column_config={"Entry": st.column_config.DateColumn(format="DD MMM YY"),
                       "Exit": st.column_config.DateColumn(format="DD MMM YY"),
                       "Entry px": st.column_config.NumberColumn(format="%.2f"),
                       "Exit px": st.column_config.NumberColumn(format="%.2f"),
                       "Net P&L": st.column_config.NumberColumn(format="%+.0f"),
                       "Return %": st.column_config.NumberColumn(format="%+.2f"),
                       "Costs": st.column_config.NumberColumn(format="%.0f")})
    st.caption(f"{len(t)} of {len(trades)} trades. {price_note} Click a header to sort.")


def retest_buttons(e: pd.Series, key: str) -> None:
    """Offer the next honest step: validate a train run; test only behind explicit confirmation."""
    if e["period"] == "train":
        if st.button("Re-test on validation period", icon=":material/fact_check:", key=f"{key}_val",
                     help="Same rules, parameters, symbols and costs on data the run hasn't seen"):
            with st.spinner("Running validation…"):
                new_id = rerun_on_period(int(e["id"]), "validation")
            D.clear()
            st.session_state["bt_focus"] = new_id
            st.success(f"Validation run recorded as experiment #{new_id}.")
            st.rerun()
    elif e["period"] == "validation":
        with st.popover("Final test period…", icon=":material/lock:"):
            st.markdown("The **test period** is the last 20% of history, kept untouched. Run it once, at the end, "
                        "for an honest estimate. Every run on it is flagged and counted in Reports.")
            ok = st.checkbox("I understand: this is a final check, not a tuning step", key=f"{key}_tok")
            if st.button("Run on test period", disabled=not ok, key=f"{key}_test", type="primary"):
                try:
                    new_id = rerun_on_period(int(e["id"]), "test", confirm_test=True)
                except TestSetLocked as exc:
                    st.error(str(exc))
                else:
                    D.clear()
                    st.session_state["bt_focus"] = new_id
                    st.rerun()


def experiment_detail(e: pd.Series, key: str = "detail") -> None:
    name = strategy_name(e["strategy_key"])
    if e["status"] != "completed":
        with C.panel(f"{key}_fail", f"#{int(e['id'])} · {name}", C.tag(e["status"], "neg")):
            st.code(e["error"] or "No error recorded", language=None)
        return
    m = e["metrics"] or {}
    curve = D.experiment_curve(int(e["id"]))
    meta = (f"{C.tag(e['period'], 'accent')} &nbsp;{pd.Timestamp(e['start_date']):%d %b %Y} → "
            f"{pd.Timestamp(e['end_date']):%d %b %Y} · {len(e['universe'])} symbols · {C.esc(e['data_provider'])}")
    if e.get("parent_id") is not None and not pd.isna(e.get("parent_id")):
        meta += f" · re-test of #{int(e['parent_id'])}"
    with C.panel(f"{key}_main", f"#{int(e['id'])} · {name} · {e['market']}", meta):
        metrics_grid(m, e["benchmark"] or "Benchmark")
        st.plotly_chart(charts.equity_vs_benchmark(curve["equity"], curve["benchmark"], height=270, strategy_label=name,
                                                   benchmark_label=f"{e['benchmark']} (total return)",
                                                   universe=curve["universe"]),
                        config=charts.CONFIG, use_container_width=True, key=f"{key}_eq")
        st.plotly_chart(charts.drawdown(curve["equity"], height=140, bm=curve["benchmark"]), config=charts.CONFIG,
                        use_container_width=True, key=f"{key}_dd")
        for n in e["bias_notes"] or []:
            st.caption("⚠ " + n)
        from quantedge.trials import project_trials_from_db
        st.caption(f"Multiple testing: Deflated Sharpe uses {int(m.get('trials') or 1)} tries on these symbols. "
                   f"Project-wide by #{int(e['id'])}: {project_trials_from_db(int(e['id'])).describe()} "
                   "(context only; see quantedge/trials.py for how they are counted).")
        retest_buttons(e, key)
    tabs = [f"Trades ({int(m.get('trade_count') or 0)})", "By regime"] + \
        (["Walk-forward folds"] if m.get("folds") else []) + ["Configuration"]
    t = st.tabs(tabs)
    with t[0]:
        trades_table(D.experiment_trades(int(e["id"])), key)
    with t[1]:
        regime_table(m)
    if m.get("folds"):
        with t[2]:
            folds_table(m)
    with t[-1]:
        st.json({"params": e["params"], "config": e["config"], "universe": e["universe"],
                 "split": m.get("split"), "missing_symbols": m.get("missing_symbols"),
                 "notes": e.get("notes"), "source": e.get("source")}, expanded=False)


def comparison(exps: pd.DataFrame, key: str = "cmp", title: str = "Comparison") -> None:
    exps = exps[exps["status"] == "completed"].head(T.MAX_COMPARE)
    if len(exps) < 2:
        C.empty_state("Select at least two completed experiments", "Up to six can be compared at once.")
        return
    curves = {}
    for e in exps.itertuples():
        label = f"#{e.id} {strategy_name(e.strategy_key)}"
        if e.notes and str(e.notes).startswith("sweep "):
            label = f"#{e.id} {str(e.notes)[6:]}"
        curves[label] = D.experiment_curve(int(e.id))["equity"]
    same_window = exps[["market", "start_date", "end_date"]].drop_duplicates().shape[0] == 1
    bm = D.experiment_curve(int(exps.iloc[0]["id"]))["benchmark"] if same_window else None
    with C.panel(f"{key}_panel", title, f"{len(exps)} experiments · rebased to 100"):
        st.plotly_chart(charts.compare_equity(curves, benchmark=bm,
                                              benchmark_label=f"{exps.iloc[0]['benchmark']} (total return)"),
                        config=charts.CONFIG, use_container_width=True, key=f"{key}_chart")
        if not same_window:
            st.caption("Different markets or date windows: curves are not directly comparable, so the benchmark is hidden.")
        st.dataframe(compare_rows(exps.to_dict("records")), use_container_width=True, key=f"{key}_tbl")
        batches = exps["batch"].dropna().unique() if "batch" in exps else []
        if len(batches) == 1 and exps["batch"].notna().all():
            s = sweep_summary(exps.to_dict("records"))
            if s:
                findings([s.finding])
