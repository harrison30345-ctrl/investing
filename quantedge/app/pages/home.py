"""Home: market summary, watchlist, chart, signals, strategy vs benchmark, risk, data health, system status."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from quantedge.app import actions
from quantedge.app import data as D
from quantedge.config import get_settings
from quantedge.data.providers import provider_info
from quantedge.markets import MARKETS
from quantedge.scheduler import upcoming
from quantedge.strategies import REGISTRY
from quantedge.ui import charts
from quantedge.ui import components as C
from quantedge.universe import INDICES
from quantedge.watchlists import get_or_create_default, list_symbols

pkey = D.provider()
mkt = C.market_filter()
settings = get_settings()

head_l, head_r = st.columns([6, 1.4], vertical_alignment="bottom")
with head_l:
    fresh = D.market_freshness(pkey)
    latest = [f["latest_bar"] for f in fresh.values() if f["latest_bar"] is not None]
    C.page_header("Home", f"Daily close data to {max(latest):%a %d %b %Y}" if latest else "No market data stored yet")
with head_r:
    if st.button("Refresh data", icon=":material/sync:", width="stretch",
                 help="Fetch the latest daily bars for indices, benchmarks and your watchlist, then rescan signals"):
        actions.refresh(actions.core_symbols())
        st.rerun()

# ── Market summary ──────────────────────────────────────────────────────────
indices = [i.symbol for i in INDICES if mkt is None or i.market == mkt]
snap = D.snapshot(tuple(indices), pkey)
if snap["last"].isna().all():
    C.empty_state("No index data stored",
                  f"Nothing has been fetched from {provider_info().name} yet. Press Refresh data to load "
                  f"{settings.history_years} years of daily history (takes about 10–20 seconds).")
else:
    C.tiles(snap)

# ── Chart + watchlist ───────────────────────────────────────────────────────
RANGES = {"1M": 31, "3M": 92, "6M": 183, "YTD": None, "1Y": 366, "5Y": 1827, "Max": 0}
left, right = st.columns([1.75, 1], gap="small")
with left:
    with C.panel("chart", "Market chart", "Daily closes · delayed"):
        names = {i.symbol: i.name for i in INDICES if mkt is None or i.market == mkt}
        cc1, cc2 = st.columns([1.3, 2], vertical_alignment="center")
        with cc1:
            sym = st.selectbox("Index", list(names), format_func=names.get, label_visibility="collapsed", key="home_idx")
        with cc2:
            rng = st.segmented_control("Range", list(RANGES), default="1Y", key="home_rng",
                                       label_visibility="collapsed") or "1Y"
        hist = D.history(sym, pkey)
        if hist.empty:
            C.empty_state("No data for this index", "Refresh data to fetch it.")
        else:
            days = RANGES[rng]
            if rng == "YTD":
                hist = hist[hist.index >= pd.Timestamp(datetime.now().year, 1, 1)]
            elif days:
                hist = hist[hist.index >= hist.index[-1] - pd.Timedelta(days=days)]
            chg = hist["close"].iloc[-1] / hist["close"].iloc[0] - 1
            st.plotly_chart(charts.price_line(hist, names[sym], height=290), config=charts.CONFIG,
                            use_container_width=True)
            st.caption(f"{names[sym]} {rng}: {C.fmt_pct(chg)} · {hist.index[0]:%d %b %Y} → {hist.index[-1]:%d %b %Y} "
                       f"· source {provider_info().name}")

with right:
    wid = get_or_create_default()
    wl_syms = [s for s in list_symbols(wid) if mkt is None or (s.endswith(".L")) == (mkt == "UK")]
    with C.panel("watch", "Watchlist", f"{len(wl_syms)} symbols"):
        wsnap = D.snapshot(tuple(wl_syms), pkey)
        if wsnap.empty:
            C.empty_state("Watchlist is empty", "Add symbols on the Watchlists page.")
        else:
            view = pd.DataFrame({
                "Symbol": wsnap["symbol"],
                "Last": [C.fmt_price(v, c) for v, c in zip(wsnap["last"], wsnap["currency"])],
                "Chg %": wsnap["change_pct"] * 100,
                "30d": wsnap["trend"],
            })
            st.dataframe(view, hide_index=True, use_container_width=True, height=min(38 + 35 * len(view), 330),
                         column_config={
                             "Chg %": st.column_config.NumberColumn(format="%+.2f%%"),
                             "30d": st.column_config.LineChartColumn("30-day trend", width="small"),
                         })
            missing = wsnap[wsnap["last"].isna()]["symbol"].tolist()
            if missing:
                st.caption(f"No stored data: {', '.join(missing)}")

# ── Signals + data health ───────────────────────────────────────────────────
left, right = st.columns([1.75, 1], gap="small")
with left:
    sig = D.signals(pkey, 200)
    if mkt and not sig.empty:
        sig = sig[sig["market"] == mkt]
    with C.panel("signals", "Recent signals",
                 C.tag("observation · delayed data", "") + " &nbsp;not trades, not recommendations"):
        t_daily, t_intra = st.tabs(["Daily", "Intraday"])
        with t_daily:
            if sig.empty:
                C.empty_state("No signals recorded",
                              "Signals appear after a data refresh. The scan runs each daily rule on stored bars and "
                              "records any entry or exit that fired in the last 10 sessions.")
            else:
                recent = sig.head(12)
                st.dataframe(pd.DataFrame({
                    "Date": pd.to_datetime(recent["ts"]).dt.strftime("%d %b"),
                    "Symbol": recent["symbol"], "Mkt": recent["market"],
                    "Strategy": recent["strategy_key"].map(lambda k: REGISTRY[k].name if k in REGISTRY else k),
                    "Signal": recent["direction"].str.upper(),
                    "Close": recent["price"], "Reason": recent["reason"],
                }), hide_index=True, use_container_width=True, height=min(38 + 35 * len(recent), 300),
                    column_config={"Close": st.column_config.NumberColumn(format="%.2f"),
                                   "Reason": st.column_config.TextColumn(width="large")})
                st.caption("Full detail (rules, risk, context) on the Signals page.")
        with t_intra:
            C.empty_state("Intraday signals not built yet",
                          "US opening-range breakout and VWAP continuation arrive in Phase 7, with a separate intraday "
                          "data feed. Nothing intraday is being monitored now.")

with right:
    info = provider_info()
    with C.panel("health", "Data & connection health"):
        rows = [("Provider", C.esc(info.name)), ("Latency", C.esc(info.latency)),
                ("Mode", C.tag("demo", "demo") if info.is_demo else C.tag("real", "accent"))]
        for m in ("UK", "US"):
            f = fresh[m]
            if f["status"] == "none":
                rows.append((f"{m} daily bars", '<span class="qe-neg">none stored</span>'))
            else:
                cls = "qe-pos" if f["status"] == "current" else "qe-neg"
                rows.append((f"{m} daily bars", f'<span class="{cls}">{f["latest_bar"]:%d %b} · {f["status"]}</span>'))
                rows.append((f"{m} last fetch", C.esc(C.ago(f["last_fetch"]))))
                if f["issues"] or f["errors"]:
                    rows.append((f"{m} problems", f'<span class="qe-neg">{f["issues"]} quality · {f["errors"]} fetch</span>'))
        C.kv(rows)
        st.caption(info.licence_note)

# ── Strategy vs benchmark + risk ────────────────────────────────────────────
exps = D.experiments(50)
done = exps[exps["status"] == "completed"] if not exps.empty else exps
if mkt is not None and not done.empty:
    done = done[done["market"] == mkt]
left, right = st.columns([1.75, 1], gap="small")
if done.empty:
    with left:
        with C.panel("perf", "Strategy vs benchmark", C.tag("backtest", "")):
            C.empty_state("No completed backtests",
                          "Run one from the CLI. Results are simulated on historical data and say nothing certain "
                          "about the future.", "python3 -m quantedge.cli backtest ema_pullback --universe us_large")
    with right:
        with C.panel("risk", "Risk & drawdown", C.tag("simulated", "")):
            C.empty_state("Nothing to assess yet", "Risk metrics come from completed backtests.")
else:
    e = done.iloc[0]
    m = e["metrics"] or {}
    curve = D.experiment_curve(int(e["id"]))
    strat_name = REGISTRY[e["strategy_key"]].name if e["strategy_key"] in REGISTRY else e["strategy_key"]
    with left:
        meta = (C.tag("backtest", "") + f" &nbsp;#{int(e['id'])} · {C.esc(strat_name)} · {e['market']} · "
                f"{C.esc(e['period'])} {pd.Timestamp(e['start_date']):%b %Y}–{pd.Timestamp(e['end_date']):%b %Y}")
        with C.panel("perf", "Strategy vs benchmark", meta):
            st.plotly_chart(charts.equity_vs_benchmark(curve["equity"], curve["benchmark"], height=230,
                                                       strategy_label=strat_name,
                                                       benchmark_label=f"{e['benchmark']} (total return)"),
                            config=charts.CONFIG, use_container_width=True)
            C.metric_grid([
                ("Net return", C.fmt_pct(m.get("net_return"), digits=1), C.tone(m.get("net_return"))),
                ("Benchmark", C.fmt_pct(m.get("benchmark_return"), digits=1), C.tone(m.get("benchmark_return"))),
                ("Excess", C.fmt_pct(m.get("excess_return"), digits=1), C.tone(m.get("excess_return"))),
                ("Sharpe", f"{m.get('sharpe', 0):.2f}", ""),
                ("Trades", f"{int(m.get('trade_count', 0))}", ""),
                ("Win rate", C.fmt_pct(m.get("win_rate"), signed=False, digits=0), ""),
            ])
            notes = e["bias_notes"] or []
            if notes:
                st.caption("⚠ " + " ".join(notes))
    with right:
        with C.panel("risk", "Risk & drawdown", C.tag("simulated", "") + f" &nbsp;#{int(e['id'])}"):
            st.plotly_chart(charts.drawdown(curve["equity"], height=130, bm=curve["benchmark"]),
                            config=charts.CONFIG, use_container_width=True)
            pf = m.get("profit_factor")
            C.kv([
                ("Max drawdown", f'<span class="qe-neg">{C.fmt_pct(m.get("max_drawdown"), digits=1)}</span>'),
                ("Benchmark max DD", C.fmt_pct(m.get("benchmark_max_drawdown"), digits=1)),
                ("Volatility (ann.)", C.fmt_pct(m.get("volatility"), signed=False, digits=1)),
                ("Longest losing streak", f"{int(m.get('longest_losing_streak', 0))} trades"),
                ("Avg exposure", C.fmt_pct(m.get("exposure"), signed=False, digits=0)),
                ("Profit factor", "—" if pf is None else f"{pf:.2f}"),
                ("Costs paid", C.fmt_pct(m.get("costs_pct_of_start"), signed=False, digits=1) + " of capital"),
            ])

# ── AI research / agents / activity ─────────────────────────────────────────
c1, c2, c3 = st.columns(3, gap="small")
with c1:
    with C.panel("ai", "AI research summaries"):
        C.empty_state("No agent reports",
                      "The research agents are built in Phase 6. "
                      + ("An LLM key is configured." if settings.llm_configured else
                         "No LLM provider is configured (QE_LLM_PROVIDER=none), so no AI calls are made."))
with c2:
    with C.panel("agents", "Background agents & workers"):
        hb = {h["service"]: h for h in D.heartbeats()}
        agents = ["Research Director", "Trend Research", "Momentum Research", "Intraday Research",
                  "Risk Manager", "Independent Validation"]
        rows = [("Scheduler / worker", "Runs data refreshes and scans on a timetable",
                 C.pill("", "online", "ok") if "worker" in hb else C.pill("", "not running", "off"))]
        rows += [(a, "Phase 6", C.pill("", "not built", "off")) for a in agents]
        C.list_rows(rows)
with c3:
    with C.panel("activity", "Activity & schedule"):
        jobs = D.job_runs(6)
        rows = []
        for r in jobs.itertuples() if not jobs.empty else []:
            cls = {"ok": "qe-pos", "partial": "qe-muted", "failed": "qe-neg"}.get(r.status, "qe-muted")
            rows.append((r.job.replace("_", " ").capitalize(), r.detail or "",
                         f'<span class="{cls}">{C.esc(r.status)}</span><br>{C.esc(C.ago(r.started_at))}'))
        if rows:
            C.list_rows(rows[:4])
        else:
            C.empty_state("No activity yet", "Data refreshes, scans and backtests appear here.")
        st.caption("Upcoming (UK time). Not executing until the worker is deployed.")
        C.list_rows([(j.name, j.description, C.esc(C.fmt_time(t, "%a %H:%M")) if t is not None else "—")
                     for j, t in upcoming()[:4]])
