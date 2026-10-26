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
                     ("Paper trading", "Simulated only; each account needs a validated run and your recorded approval",
                      C.pill("", "simulated", "ok"))])
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

# ── system health ───────────────────────────────────────────────────────────
from quantedge import health as _health  # noqa: E402
from quantedge.scheduler import JOBS, next_run  # noqa: E402

st.write("")
C.page_header("System health", "Background worker, schedule, alerts and checks")
runs = D.job_history(300)
h1, h2 = st.columns([1, 1.3], gap="small")
with h1:
    with C.panel("set_worker", "Worker"):
        w = _health.heartbeat("worker")
        if w is None:
            C.empty_state("Worker has never run",
                          "Start it in a terminal (it keeps running until stopped). For always-on operation, deploy it "
                          "with the dashboard (see docs/quantedge/DEPLOY.md).", "python3 -m quantedge.worker")
        else:
            C.kv([("Status", C.esc(w.status)), ("Last heartbeat", C.esc(f"{C.fmt_time(w.last_seen)} ({C.ago(w.last_seen)})")),
                  ("Detail", C.esc(w.detail or ""))])
        if st.button("Run health checks now", icon=":material/monitor_heart:"):
            res = _health.run_checks(include_worker=True, force_provider=True)
            D.clear()
            for c in res:
                (st.success if c.ok else st.error if c.level == "error" else st.warning)(f"{c.key}: {c.message}")
    with C.panel("set_alerts", "Open alerts"):
        al = D.open_alerts()
        if not al:
            st.caption("No open alerts.")
        else:
            C.list_rows([(a["message"], f"{a['key']} · since {C.fmt_time(a['opened_at'])}",
                          f'<span class="{"qe-neg" if a["level"] == "error" else "qe-warn"}">{C.esc(a["level"])}</span>')
                         for a in al])
        st.caption("Alerts open when a check fails and resolve when it passes. Set QE_ALERT_WEBHOOK_URL to also "
                   "receive them by webhook.")
with h2:
    with C.panel("set_runs", "Recent job runs"):
        if runs.empty:
            st.caption("No job runs yet.")
        else:
            st.dataframe(pd.DataFrame({"Job": runs["job"], "Started": pd.to_datetime(runs["started_at"]),
                                       "Status": runs["status"], "Trigger": runs["trigger"],
                                       "Detail": runs["detail"].fillna("")}).head(60),
                         hide_index=True, use_container_width=True, height=280,
                         column_config={"Started": st.column_config.DatetimeColumn(format="DD MMM HH:mm")})

with C.panel("set_sched", "Schedule"):
    rows = []
    for j in JOBS:
        mine = runs[runs["job"] == j.key] if not runs.empty else runs
        last = mine.iloc[0] if not mine.empty else None
        cadence = {"trading_days": f"trading days {j.at} {j.market}", "weekdays": f"weekdays {j.at} UK",
                   "interval": f"every {j.every_minutes} min",
                   "session_interval": f"every {j.every_minutes} min in {j.market} session"}[j.cadence]
        rows.append({"Job": j.name, "When": cadence + (" · needs AI" if j.needs_llm else ""),
                     "Last run": pd.Timestamp(last["started_at"]) if last is not None else None,
                     "Status": last["status"] if last is not None else "never",
                     "Next (UK)": C.fmt_time(next_run(j), "%a %d %b %H:%M")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={"Last run": st.column_config.DatetimeColumn(format="DD MMM HH:mm")})
