"""Reports: plain-English performance and validation summaries per strategy configuration."""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from quantedge.app import data as D
from quantedge.app import views as V
from quantedge.backtest.runner import rerun_on_period
from quantedge.reports import PERIOD_ORDER, build_reports, pct, report_markdown, sweep_summary
from quantedge.ui import components as C

VERDICT_TAG = {"good": "pos", "caution": "demo", "warning": "neg", "info": ""}

mkt = C.market_filter()
C.page_header("Reports", "Generated from stored backtests. Every number comes from the engine; no AI text.")

exps = D.experiments(2000)
if not exps.empty:
    exps = exps[exps["source"] != "timing"]
if exps.empty:
    C.empty_state("Nothing to report", "Run a backtest in the Strategy Lab first.")
    st.stop()
if mkt:
    exps = exps[exps["market"] == mkt]

records = exps.to_dict("records")
reports = build_reports(records)

f1, f2 = st.columns([2, 1.2])
with f1:
    verdicts = st.multiselect("Verdict", ["good", "caution", "warning"], placeholder="All verdicts",
                              format_func={"good": "Held up", "caution": "Not validated / inconclusive",
                                           "warning": "Failed / degraded"}.get)
with f2:
    sort = st.segmented_control("Sort", ["Recent", "Validation Sharpe"], default="Recent") or "Recent"
if verdicts:
    reports = [r for r in reports if r.verdict_level in verdicts]
if sort != "Recent":
    reports.sort(key=lambda r: ((r.periods.get("validation") or {}).get("metrics") or {}).get("sharpe") or -99,
                 reverse=True)

tab_cfg, tab_sweeps = st.tabs([f"Strategy configurations ({len(reports)})", "Parameter sweeps"])

with tab_cfg:
    if not reports:
        C.empty_state("No reports match", "Clear the verdict filter.")
    held = sum(r.verdict_level == "good" for r in reports)
    st.caption(f"{held} of {len(reports)} configurations held up on validation. "
               "A configuration is one strategy + parameters + symbols + costs.")
    for i, r in enumerate(reports[:40]):
        meta = (C.tag(r.verdict, VERDICT_TAG[r.verdict_level]) +
                f" &nbsp;{len(r.experiment_ids)} runs{f' · {r.failed_runs} failed' if r.failed_runs else ''}")
        with C.panel(f"rep_{i}", f"{r.strategy_name} · {r.universe_label} · {r.market}", meta):
            st.markdown(r.summary)
            rows = []
            for p in PERIOD_ORDER:
                e = r.periods.get(p)
                if not e:
                    continue
                m = e["metrics"] or {}
                rows.append({"Period": p, "Window": f"{pd.Timestamp(e['start_date']):%b %Y} → {pd.Timestamp(e['end_date']):%b %Y}",
                             "Net": pct(m.get("net_return")), "Benchmark": pct(m.get("benchmark_return")),
                             "Sharpe": f"{m.get('sharpe') or 0:.2f}", "Max DD": pct(m.get("max_drawdown")),
                             "Trades": int(m.get("trade_count") or 0), "Exp #": int(e["id"])})
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, key=f"rep_tbl_{i}")
            V.findings(r.findings)
            st.caption("Parameters: " + ", ".join(f"{k}={v}" for k, v in r.params.items()))
            for c in r.caveats:
                st.caption("⚠ " + c)
            b1, b2, _ = st.columns([1.3, 1.1, 3])
            with b1:
                train = r.periods.get("train")
                if train is not None and "validation" not in r.periods:
                    if st.button("Run validation", icon=":material/fact_check:", key=f"rep_val_{i}"):
                        with st.spinner("Running validation…"):
                            rerun_on_period(int(train["id"]), "validation")
                        D.clear()
                        st.rerun()
            with b2:
                slug = re.sub(r"[^a-z0-9]+", "-", f"{r.strategy_key}-{r.universe_label}-{r.market}".lower())
                st.download_button("Markdown", report_markdown(r), file_name=f"quantedge-{slug}.md",
                                   icon=":material/download:", key=f"rep_dl_{i}")
    if len(reports) > 40:
        st.caption(f"Showing 40 of {len(reports)}. Use the filters to narrow down.")

with tab_sweeps:
    sweeps = exps[exps["batch"].notna()] if "batch" in exps else exps.iloc[0:0]
    if sweeps.empty:
        C.empty_state("No parameter sweeps yet",
                      "In the Strategy Lab choose “Sweep one parameter” to see how sensitive a result is to one setting.")
    for j, (batch, grp) in enumerate(sorted(sweeps.groupby("batch"), key=lambda g: -g[1]["id"].max())):
        grp = grp.sort_values("id")
        first = grp.iloc[0]
        s = sweep_summary(grp.to_dict("records"))
        title = f"{V.strategy_name(first['strategy_key'])} · {first['market']} · {first['period']}"
        with C.panel(f"sw_{j}", title, f"{len(grp)} variants · batch {batch}"):
            if s:
                V.findings([s.finding])
            st.dataframe(pd.DataFrame({
                "Variant": grp["notes"].str.replace("sweep ", "", regex=False), "Exp #": grp["id"],
                "Status": grp["status"],
                "Net": grp["metrics"].map(lambda m: pct((m or {}).get("net_return"))),
                "Excess": grp["metrics"].map(lambda m: pct((m or {}).get("excess_return"))),
                "Sharpe": grp["metrics"].map(lambda m: (m or {}).get("sharpe")),
                "Max DD": grp["metrics"].map(lambda m: pct((m or {}).get("max_drawdown"))),
                "Trades": grp["metrics"].map(lambda m: (m or {}).get("trade_count")),
            }), hide_index=True, use_container_width=True, key=f"sw_tbl_{j}",
                column_config={"Sharpe": st.column_config.NumberColumn(format="%.2f"),
                               "Trades": st.column_config.NumberColumn(format="%d")})
