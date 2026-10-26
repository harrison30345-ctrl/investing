"""
Paper trading: forward-only simulated accounts. No broker, no real orders.

How it differs from a backtest:
  * It only moves forward. An account starts at the latest stored bar when a human
    approves it, and only trades bars that arrive after that.
  * It only uses completed bars: daily bars up to the last finished session, and
    5-minute bars whose interval has ended. A half-formed bar is never traded.
  * Fills use actual (unadjusted) prices. Signals are computed on adjusted prices
    like the backtest, and the stop distance is rescaled to unadjusted terms.
    Dividends are not credited.
  * State (cash, positions, queued orders) lives in the database between runs.
    The rules themselves are the backtester's: both drive SignalPortfolio.

Safety: an account can only be created from a completed *validation* or *test*
experiment, with a named human approver. Agents can propose promotion
(proposal kind promote_to_paper) but nothing in the agent code path can create
an account.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import numpy as np
import pandas as pd
from sqlalchemy import select

from .backtest.engine import BacktestConfig
from .backtest.portfolio import SignalPortfolio
from .backtest.runner import INTRADAY_INTERVAL
from .data import repository as repo
from .db import session_scope
from .db.models import (Experiment, PaperAccount, PaperEquity, PaperEvent, PaperTrade, Proposal, Signal, utcnow)
from .markets import MARKETS, last_completed_session, schedule
from .strategies import REGISTRY, SignalStrategy
from .strategies.indicators import adjusted_ohlc
from .universe import get as catalogue_get

log = logging.getLogger(__name__)

ELIGIBLE_PERIODS = ("validation", "test")
INTRADAY_MINUTES = int(INTRADAY_INTERVAL.rstrip("m"))


class PaperError(RuntimeError):
    pass


# ── creation and lifecycle ──────────────────────────────────────────────────

def create_account(experiment_id: int, name: str, approved_by: str, note: str = "",
                   capital: Optional[float] = None, proposal_id: Optional[int] = None) -> int:
    """Start a paper account from a validated experiment. `approved_by` must name a human."""
    if not approved_by or not approved_by.lower().startswith("human"):
        raise PaperError("Paper trading needs an explicit human approval (approved_by='human …').")
    with session_scope() as s:
        e = s.get(Experiment, experiment_id)
        if e is None or e.status != "completed":
            raise PaperError(f"Experiment #{experiment_id} is not a completed run.")
        if e.period not in ELIGIBLE_PERIODS:
            raise PaperError("Only configurations that completed a validation (or test) run can be paper traded. "
                             f"#{experiment_id} is a {e.period} run; re-test it on validation first.")
        strat = REGISTRY.get(e.strategy_key)
        if not isinstance(strat, SignalStrategy):
            raise PaperError(f"{e.strategy_key} is a rebalancing strategy; paper trading currently supports "
                             "signal strategies (trend and intraday) only.")
        if proposal_id is not None:
            p = s.get(Proposal, proposal_id)
            if p is None or p.kind != "promote_to_paper" or p.status != "approved":
                raise PaperError(f"Proposal #{proposal_id} is not an approved promote_to_paper proposal.")
        interval = INTRADAY_INTERVAL if strat.timeframe == "intraday" else "1d"
        last = max((repo.last_bar_ts(s, e.data_provider, sym, interval) or datetime.min) for sym in e.universe)
        if last == datetime.min:
            raise PaperError(f"No stored {interval} bars for this universe; refresh data first.")
        cfg = BacktestConfig.from_dict(e.config)
        if capital:
            cfg.initial_capital = float(capital)
        acct = PaperAccount(
            name=name.strip() or f"{strat.name} #{experiment_id}", status="active", strategy_key=e.strategy_key,
            params=e.params, universe=list(e.universe), market=e.market, timeframe=strat.timeframe,
            config=cfg.to_dict(), data_provider=e.data_provider, starting_capital=cfg.initial_capital,
            source_experiment_id=experiment_id, proposal_id=proposal_id, approved_by=approved_by,
            approval_note=note or None, started_after=last, last_bar_ts=last,
            state=SignalPortfolio(cfg, e.market).to_state())
        s.add(acct)
        s.flush()
        s.add(PaperEquity(account_id=acct.id, ts=last, equity=cfg.initial_capital, cash=cfg.initial_capital,
                          exposure=0.0))
        s.add(PaperEvent(account_id=acct.id, ts=last, type="note",
                         reason=f"Account opened by {approved_by}; trading bars after {last:%Y-%m-%d %H:%M}"))
        return acct.id


def set_status(account_id: int, status: str, by: str, note: str = "") -> None:
    if status not in ("active", "paused", "closed"):
        raise ValueError(status)
    with session_scope() as s:
        a = s.get(PaperAccount, account_id)
        if a is None:
            raise PaperError(f"No paper account #{account_id}")
        if a.status == "closed":
            raise PaperError("Closed accounts can't be changed.")
        if status == "closed":
            cfg = BacktestConfig.from_dict(a.config)
            pf = SignalPortfolio.from_state(cfg, a.market, a.state)
            pf.liquidate(a.last_bar_ts, "account_closed")
            _record(s, a, pf, 0, 0)
            a.state, a.closed_at = pf.to_state(), utcnow()
            s.add(PaperEquity(account_id=a.id, ts=a.last_bar_ts + timedelta(seconds=1), equity=pf.cash, cash=pf.cash,
                              exposure=0.0))
        a.status, a.status_note = status, f"{status} by {by}" + (f": {note}" if note else "")
        s.add(PaperEvent(account_id=a.id, ts=a.last_bar_ts, type="note", reason=a.status_note))


# ── stepping ────────────────────────────────────────────────────────────────

def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)


def _scale(sym: str) -> float:
    return 0.01 if catalogue_get(sym).currency == "GBX" else 1.0


def _completed(df: pd.DataFrame, market: str, timeframe: str, now: pd.Timestamp) -> pd.DataFrame:
    if df.empty:
        return df
    if timeframe == "daily":
        last = last_completed_session(market, now.to_pydatetime().replace(tzinfo=timezone.utc))
        return df[df.index <= pd.Timestamp(last)] if last else df.iloc[0:0]
    return df[df.index + pd.Timedelta(minutes=INTRADAY_MINUTES) <= now]


def _session_closes(market: str, start, end) -> Dict[object, pd.Timestamp]:
    sched = schedule(market, start, end)
    return {d.date(): row["market_close"].tz_convert("UTC").tz_localize(None) for d, row in sched.iterrows()}


def _record(s, acct: PaperAccount, pf: SignalPortfolio, n_events: int, n_trades: int) -> None:
    for ev in pf.events[n_events:]:
        s.add(PaperEvent(account_id=acct.id, ts=pd.Timestamp(ev["ts"]).to_pydatetime(), type=ev["type"],
                         side=ev.get("side"), symbol=ev.get("symbol"), shares=ev.get("shares"),
                         price=ev.get("price"), fees=ev.get("fees"), reason=ev.get("reason"),
                         detail={k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                                 for k, v in ev.items() if k in ("stop", "target", "stop_dist")} or None))
    for t in pf.trades[n_trades:]:
        s.add(PaperTrade(account_id=acct.id, symbol=t["symbol"], entry_ts=pd.Timestamp(t["entry_ts"]).to_pydatetime(),
                         entry_price=t["entry_price"], exit_ts=pd.Timestamp(t["exit_ts"]).to_pydatetime(),
                         exit_price=t["exit_price"], shares=t["shares"], costs=t["costs"], net_pnl=t["net_pnl"],
                         return_pct=t["return_pct"], bars_held=int(t["bars_held"]), entry_reason=t["entry_reason"],
                         exit_reason=t["exit_reason"]))


def step_account(account_id: int, now: Optional[pd.Timestamp] = None) -> int:
    """Process every completed bar since the account's last step. Returns the number of bars processed."""
    now = now if now is not None else _now()
    with session_scope() as s:
        acct = s.get(PaperAccount, account_id)
        if acct is None or acct.status != "active":
            return 0
        s.expunge(acct)
    strat = REGISTRY[acct.strategy_key]
    intraday = acct.timeframe == "intraday"
    interval = INTRADAY_INTERVAL if intraday else "1d"
    # Daily: full stored history, exactly like the backtest. Exponential averages depend on where they
    # start, so a shorter window could shift a borderline signal. Intraday indicators reset each session
    # (VWAP) or converge within a day (5-minute ATR), so 20 days is plenty.
    start = acct.last_bar_ts - timedelta(days=20) if intraday else None
    raw, sig_in = {}, {}
    for sym in acct.universe:
        df = repo.load_bars(acct.data_provider, sym, interval, start=start)
        df = _completed(df.dropna(subset=["open", "high", "low", "close"]), acct.market, acct.timeframe, now)
        if df.empty:
            continue
        k = _scale(sym)
        raw[sym] = df[["open", "high", "low", "close"]] * k
        sig_in[sym] = df if intraday else adjusted_ohlc(df)
    new_ts = sorted({t for df in raw.values() for t in df.index if t > pd.Timestamp(acct.last_bar_ts)})
    if not new_ts:
        with session_scope() as s:
            s.get(PaperAccount, account_id).last_step_at = utcnow()
        return 0

    sigs = {}
    for sym, df in sig_in.items():
        g = strat.generate(df, acct.params)
        if not intraday:
            # Signals come from adjusted prices; express the stop distance in actual traded prices.
            ratio = (df["close"] / df["adj_close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0) \
                if "adj_close" in df else 1.0
            g = g.assign(stop_dist=g["stop_dist"] * ratio)
        g = g.assign(stop_dist=g["stop_dist"] * _scale(sym))
        sigs[sym] = g[["entry", "exit", "stop_dist", "target_r", "max_bars", "reason"]].to_dict("index")
    rows = {sym: df.to_dict("index") for sym, df in raw.items()}
    closes = _session_closes(acct.market, new_ts[0].date() - timedelta(days=1), new_ts[-1].date()) if intraday else {}
    tz = MARKETS[acct.market].tz

    cfg = BacktestConfig.from_dict(acct.config)
    pf = SignalPortfolio.from_state(cfg, acct.market, acct.state)
    with session_scope() as s:
        a = s.get(PaperAccount, account_id)
        prev_ts = pd.Timestamp(a.last_bar_ts)
        prev_session = prev_ts.tz_localize("UTC").tz_convert(tz).date() if intraday else None
        for ts in new_ts:
            n_ev, n_tr = len(pf.events), len(pf.trades)
            if intraday:
                session = ts.tz_localize("UTC").tz_convert(tz).date()
                if prev_session is not None and session != prev_session and (pf.positions or pf.pending_entries):
                    # The previous session's last bar never arrived; close at the last known price.
                    pf.liquidate(prev_ts, "missed_session_close")
                prev_session = session
                close = closes.get(session)
                session_end = close is not None and ts + pd.Timedelta(minutes=INTRADAY_MINUTES) >= close
            else:
                session_end = False
            today = {sym: r[ts] for sym, r in rows.items() if ts in r}
            pf.step(ts, today, {sym: sigs[sym][ts] for sym in today if ts in sigs.get(sym, {})},
                    session_end=session_end)
            _record(s, a, pf, n_ev, n_tr)
            for ev in pf.events[n_ev:]:
                if ev["type"] == "order" and ev["side"] == "buy":
                    _paper_signal(s, a, ev, strat)
            s.merge(PaperEquity(account_id=a.id, ts=ts.to_pydatetime(), equity=pf.mark(), cash=pf.cash,
                                exposure=pf.exposure()))
            prev_ts = ts
        a.state, a.last_bar_ts, a.last_step_at = pf.to_state(), new_ts[-1].to_pydatetime(), utcnow()
    return len(new_ts)


def _paper_signal(s, acct: PaperAccount, ev: dict, strat) -> None:
    ts = pd.Timestamp(ev["ts"]).to_pydatetime()
    exists = s.scalars(select(Signal).where(Signal.provider == acct.data_provider, Signal.symbol == ev["symbol"],
                                            Signal.strategy_key == strat.key, Signal.ts == ts,
                                            Signal.kind == "paper")).first()
    if exists:
        return
    s.add(Signal(provider=acct.data_provider, symbol=ev["symbol"], market=acct.market, strategy_key=strat.key,
                 timeframe=acct.timeframe, ts=ts, kind="paper", direction="long", price=0.0,
                 entry_rule=strat.entry_rule(acct.params), exit_rule=strat.exit_rule(acct.params),
                 reason=ev.get("reason") or "", context={"paper_account": acct.id},
                 risk={"stop_distance": ev.get("stop_dist"), "sizing": f"{acct.config.get('risk_per_trade', 0):.1%} "
                                                                         "of paper equity at risk"},
                 params=acct.params, data_as_of=ts))


def step_all(timeframe: Optional[str] = None, now: Optional[pd.Timestamp] = None) -> Dict[int, int]:
    with session_scope() as s:
        q = select(PaperAccount.id).where(PaperAccount.status == "active")
        if timeframe:
            q = q.where(PaperAccount.timeframe == timeframe)
        ids = list(s.scalars(q))
    out = {}
    for i in ids:
        try:
            out[i] = step_account(i, now)
        except Exception as exc:   # one bad account must not stop the others
            log.exception("paper account %s failed", i)
            with session_scope() as s:
                a = s.get(PaperAccount, i)
                a.status_note = f"Last step failed: {exc}"
                s.add(PaperEvent(account_id=i, ts=a.last_bar_ts, type="note", reason=f"Step failed: {exc}"))
            out[i] = -1
    return out


def summary(account_id: int) -> dict:
    with session_scope() as s:
        a = s.get(PaperAccount, account_id)
        cfg = BacktestConfig.from_dict(a.config)
        pf = SignalPortfolio.from_state(cfg, a.market, a.state)
        trades = s.scalars(select(PaperTrade).where(PaperTrade.account_id == account_id)).all()
        eq = pf.mark()
        return {"equity": eq, "cash": pf.cash, "return": eq / a.starting_capital - 1, "positions": pf.positions,
                "pending_entries": pf.pending_entries, "pending_exits": pf.pending_exits,
                "trades": len(trades), "wins": sum(1 for t in trades if t.net_pnl > 0),
                "realised_pnl": sum(t.net_pnl for t in trades), "last_close": pf.last_close}
