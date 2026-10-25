"""Paper Trading: forward-only simulated accounts, kept apart from historical backtests."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from quantedge import paper
from quantedge.app import data as D
from quantedge.app import views as V
from quantedge.db import session_scope
from quantedge.db.models import PaperAccount, PaperEquity, PaperEvent, PaperTrade, Proposal
from quantedge.strategies import REGISTRY, SignalStrategy
from quantedge.ui import charts
from quantedge.ui import components as C

C.page_header("Paper Trading", "Simulated money, forward only. Not a backtest, and never a real order.")
C.html_block('<div class="qe-banner" style="border-color:var(--qe-accent);background:#0d1d1f;color:#bfe9e9">'
             '<b style="color:var(--qe-accent)">PAPER</b> &nbsp;Accounts trade only bars that arrive <i>after</i> you '
             'approve them, on delayed data, using the same rules as their backtest. Results here are a live test, '
             'kept separate from historical backtests.</div>')


def _accounts() -> pd.DataFrame:
    with session_scope() as s:
        rows = s.scalars(select(PaperAccount).order_by(PaperAccount.id.desc())).all()
        return pd.DataFrame([{c.name: getattr(r, c.name) for c in PaperAccount.__table__.columns} for r in rows])


accts = _accounts()
tab_accts, tab_new = st.tabs([f"Accounts ({len(accts)})", "Start a paper account"])

# ── new account ─────────────────────────────────────────────────────────────
with tab_new:
    exps = D.experiments(1000)
    eligible = exps[(exps["status"] == "completed") & exps["period"].isin(paper.ELIGIBLE_PERIODS)
                    & exps["strategy_key"].map(lambda k: isinstance(REGISTRY.get(k), SignalStrategy))] \
        if not exps.empty else exps
    with C.panel("pp_new", "Promote a validated configuration", C.tag("needs your approval", "demo")):
        st.markdown("Only configurations that completed a **validation** (or test) run can be paper traded. "
                    "Momentum (rebalancing) strategies aren't supported in paper trading yet.")
        with session_scope() as s:
            approved = s.scalars(select(Proposal).where(Proposal.kind == "promote_to_paper",
                                                        Proposal.status == "approved")).all()
            hints = [(p.id, p.title) for p in approved]
        if hints:
            st.caption("Approved agent suggestions: " + "; ".join(f"#{i} {t}" for i, t in hints))
        if eligible.empty:
            C.empty_state("No eligible configurations",
                          "Run a strategy on train, then use “Re-test on validation period” on the Backtests page.")
        else:
            label = lambda i: (lambda e: f"#{i} · {V.strategy_name(e['strategy_key'])} · {e['market']} · "  # noqa: E731
                                         f"{e['period']} · net {C.fmt_pct((e['metrics'] or {}).get('net_return'), digits=1)}"
                                         f" vs {C.fmt_pct((e['metrics'] or {}).get('benchmark_return'), digits=1)}")(
                eligible.set_index("id").loc[i])
            pick = st.selectbox("Validated experiment", eligible["id"].tolist(), format_func=label, key="pp_exp")
            e = eligible.set_index("id").loc[pick]
            st.caption("Parameters: " + ", ".join(f"{k}={v}" for k, v in e["params"].items())
                       + f" · {len(e['universe'])} symbols")
            a, b = st.columns(2)
            with a:
                name = st.text_input("Account name", value=f"{V.strategy_name(e['strategy_key'])} {e['market']}",
                                     key="pp_name")
            with b:
                capital = st.number_input("Simulated starting capital", 1_000.0, 10_000_000.0,
                                          float((e["config"] or {}).get("initial_capital", 100_000)), 10_000.0,
                                          key="pp_cap")
            note = st.text_input("Approval note (recorded)", key="pp_note", placeholder="Why you're approving this")
            ok = st.checkbox("I approve paper trading this configuration with simulated money. I understand a good "
                             "backtest doesn't mean it will work.", key="pp_ok")
            if st.button("Start paper account", type="primary", disabled=not ok, icon=":material/play_arrow:"):
                try:
                    new_id = paper.create_account(int(pick), name, "human (dashboard)", note, capital=capital)
                except paper.PaperError as exc:
                    st.error(str(exc))
                else:
                    st.success(f"Paper account #{new_id} started. It trades only bars that arrive from now on.")
                    st.rerun()

# ── accounts ────────────────────────────────────────────────────────────────
with tab_accts:
    if accts.empty:
        C.empty_state("No paper accounts",
                      "Start one from a validated backtest on the “Start a paper account” tab. The background worker "
                      "steps active accounts after each data refresh; you can also step them by hand here.")
        st.stop()
    rows = []
    for a in accts.itertuples():
        sm = paper.summary(int(a.id))
        rows.append({"ID": a.id, "Name": a.name, "Status": a.status, "Strategy": V.strategy_name(a.strategy_key),
                     "Mkt": a.market, "Timeframe": a.timeframe, "Equity": sm["equity"], "Return %": sm["return"] * 100,
                     "Open": len(sm["positions"]), "Trades": sm["trades"],
                     "Last bar": pd.Timestamp(a.last_bar_ts), "Approved by": a.approved_by})
    sel = st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, on_select="rerun",
                       selection_mode="single-row", key="pp_tbl",
                       column_config={"Equity": st.column_config.NumberColumn(format="%,.0f"),
                                      "Return %": st.column_config.NumberColumn(format="%+.2f"),
                                      "Last bar": st.column_config.DatetimeColumn(format="DD MMM HH:mm")})
    c1, c2, _ = st.columns([1, 1, 4])
    with c1:
        if st.button("Step all now", icon=":material/fast_forward:",
                     help="Process any completed bars that arrived since each account's last step"):
            res = paper.step_all()
            st.toast(f"Processed {sum(v for v in res.values() if v > 0)} new bars")
            st.rerun()
    pick_rows = sel.selection.rows if sel else []
    a = accts.iloc[pick_rows[0]] if pick_rows else accts.iloc[0]
    aid = int(a["id"])
    sm = paper.summary(aid)
    with session_scope() as s:
        eq = pd.DataFrame(s.execute(select(PaperEquity.ts, PaperEquity.equity, PaperEquity.exposure)
                                    .where(PaperEquity.account_id == aid).order_by(PaperEquity.ts)).all(),
                          columns=["ts", "equity", "exposure"]).set_index("ts")
        trades = pd.DataFrame([{c.name: getattr(t, c.name) for c in PaperTrade.__table__.columns}
                               for t in s.scalars(select(PaperTrade).where(PaperTrade.account_id == aid)
                                                  .order_by(PaperTrade.exit_ts.desc()))])
        events = pd.DataFrame([{c.name: getattr(t, c.name) for c in PaperEvent.__table__.columns}
                               for t in s.scalars(select(PaperEvent).where(PaperEvent.account_id == aid)
                                                  .order_by(PaperEvent.id.desc()).limit(300))])
    meta = (C.tag(a["status"], "accent" if a["status"] == "active" else "") +
            f" &nbsp;from experiment #{a['source_experiment_id']} · approved by {C.esc(a['approved_by'])} "
            f"{C.fmt_time(a['created_at'])} · trading bars after {pd.Timestamp(a['started_after']):%d %b %Y %H:%M}")
    with C.panel("pp_detail", f"#{aid} · {a['name']}", meta):
        C.metric_grid([
            ("Equity", f"{sm['equity']:,.0f}", ""), ("Return", C.fmt_pct(sm["return"]), C.tone(sm["return"])),
            ("Cash", f"{sm['cash']:,.0f}", ""), ("Open positions", str(len(sm["positions"])), ""),
            ("Closed trades", str(sm["trades"]), ""),
            ("Win rate", C.fmt_pct(sm["wins"] / sm["trades"], signed=False, digits=0) if sm["trades"] else "—", ""),
            ("Realised P&L", f"{sm['realised_pnl']:+,.0f}", C.tone(sm["realised_pnl"])),
            ("Last processed bar", f"{pd.Timestamp(a['last_bar_ts']):%d %b %H:%M}", ""),
        ])
        if len(eq) > 1:
            st.plotly_chart(charts.equity_vs_benchmark(eq["equity"], None, height=220, strategy_label="Paper equity"),
                            config=charts.CONFIG, use_container_width=True, key="pp_eq")
        else:
            st.caption("No bars processed since approval yet. The equity curve starts with the first new bar.")
        if a["status_note"]:
            st.caption(a["status_note"])
        b1, b2, b3, _ = st.columns([1, 1, 1.4, 3])
        if a["status"] == "active":
            with b1:
                if st.button("Pause", key="pp_pause"):
                    paper.set_status(aid, "paused", "human (dashboard)")
                    st.rerun()
        elif a["status"] == "paused":
            with b1:
                if st.button("Resume", key="pp_resume"):
                    paper.set_status(aid, "active", "human (dashboard)")
                    st.rerun()
        if a["status"] != "closed":
            with b2:
                with st.popover("Close…"):
                    st.markdown("Closing sells all paper positions at the last processed price and stops the "
                                "account permanently.")
                    if st.checkbox("Confirm close", key="pp_close_ok") and st.button("Close account", key="pp_close"):
                        paper.set_status(aid, "closed", "human (dashboard)")
                        st.rerun()

    t1, t2, t3 = st.tabs([f"Open positions ({len(sm['positions'])})", f"Trades ({len(trades)})", "Event log"])
    with t1:
        if not sm["positions"]:
            C.empty_state("Flat", "No open paper positions. Queued orders appear in the event log.")
        else:
            st.dataframe(pd.DataFrame([{
                "Symbol": sym, "Shares": p["shares"], "Entry": p["entry_price"], "Last": sm["last_close"].get(sym),
                "Unrealised": (sm["last_close"].get(sym, p["entry_price"]) - p["entry_price"]) * p["shares"],
                "Stop": p["stop"], "Target": p["target"], "Bars held": p["bars_held"],
                "Entered": pd.Timestamp(p["entry_ts"]), "Reason": p["reason"]} for sym, p in sm["positions"].items()]),
                hide_index=True, use_container_width=True,
                column_config={"Entry": st.column_config.NumberColumn(format="%.2f"),
                               "Last": st.column_config.NumberColumn(format="%.2f"),
                               "Unrealised": st.column_config.NumberColumn(format="%+,.0f"),
                               "Stop": st.column_config.NumberColumn(format="%.2f"),
                               "Target": st.column_config.NumberColumn(format="%.2f")})
        if sm["pending_entries"] or sm["pending_exits"]:
            st.caption("Queued for the next bar's open: "
                       + ", ".join([f"BUY {o['symbol']}" for o in sm["pending_entries"]]
                                   + [f"SELL {k} ({v})" for k, v in sm["pending_exits"].items()]))
    with t2:
        if trades.empty:
            C.empty_state("No closed paper trades yet", "Trades appear once a position is exited.")
        else:
            V.trades_table(trades, "pp_tr")
    with t3:
        if not events.empty:
            st.dataframe(pd.DataFrame({"Bar": pd.to_datetime(events["ts"]), "Type": events["type"],
                                       "Side": events["side"].fillna(""), "Symbol": events["symbol"].fillna(""),
                                       "Shares": events["shares"], "Price": events["price"],
                                       "Reason": events["reason"].fillna("")}),
                         hide_index=True, use_container_width=True, height=320,
                         column_config={"Bar": st.column_config.DatetimeColumn(format="DD MMM HH:mm"),
                                        "Price": st.column_config.NumberColumn(format="%.2f")})
