"""
Signal scan: run each daily signal strategy (default parameters) over stored
bars and record every entry/exit that fired in the last N sessions.

These are *observations* on delayed data: "the rule fired on this bar".
They are not trades and not recommendations. Paper-trading signals (Phase 7)
are stored with kind="paper", separately.
"""
from __future__ import annotations

from typing import Iterable, List, Optional

import pandas as pd
from sqlalchemy import select

from .config import get_settings
from .data import repository as repo
from .db import session_scope
from .db.models import JobRun, Signal, utcnow
from .strategies import REGISTRY, SignalStrategy
from .strategies.indicators import adjusted_ohlc
from .markets import last_completed_session
from .universe import get as catalogue_get


def _context(sig_row: pd.Series, bar: pd.Series) -> dict:
    ctx = {"close": float(bar["close"])}
    for col in ("ema_fast", "ema_slow", "upper", "lower", "atr"):
        if col in sig_row and pd.notna(sig_row[col]):
            ctx[col] = round(float(sig_row[col]), 4)
    if "ema_slow" in ctx:
        ctx["trend"] = "above slow EMA" if ctx["close"] > ctx["ema_slow"] else "below slow EMA"
    return ctx


def scan_signals(symbols: Iterable[str], lookback_sessions: int = 10, provider: Optional[str] = None,
                 trigger: str = "manual") -> int:
    symbols = list(symbols)
    pkey = provider or get_settings().data_provider
    strategies = [s for s in REGISTRY.values() if isinstance(s, SignalStrategy) and s.timeframe == "daily"]
    found: List[dict] = []
    for sym in symbols:
        raw = repo.load_bars(pkey, sym)
        inst = catalogue_get(sym)
        if raw.empty or inst.kind == "index":
            continue
        # A refresh during market hours can store today's unfinished bar; never treat it as a close.
        done = last_completed_session(inst.market)
        raw = raw[raw.index <= pd.Timestamp(done)] if done else raw.iloc[0:0]
        if raw.empty:
            continue
        bars = adjusted_ohlc(raw)
        as_of = bars.index[-1]
        for strat in strategies:
            p = strat.defaults()
            sig = strat.generate(bars, p)
            recent = sig.iloc[-lookback_sessions:]
            for ts, row in recent[recent["entry"] | recent["exit"]].iterrows():
                bar = bars.loc[ts]
                is_entry = bool(row["entry"])
                risk = {}
                if is_entry and pd.notna(row["stop_dist"]):
                    sd = float(row["stop_dist"])
                    risk = {"stop_distance": round(sd, 4), "stop_pct": round(sd / float(bar["close"]), 4),
                            "indicative_stop": round(float(bar["close"]) - sd, 4),
                            "target_r": None if pd.isna(row["target_r"]) else float(row["target_r"]),
                            "time_stop_bars": None if pd.isna(row["max_bars"]) else int(row["max_bars"]),
                            "sizing": "1% of equity at risk per trade (backtest default)"}
                found.append(dict(
                    provider=pkey, symbol=sym, market=inst.market, strategy_key=strat.key, timeframe="daily",
                    ts=ts.to_pydatetime(), kind="observation", direction="long" if is_entry else "exit",
                    price=float(raw.loc[ts, "close"]), entry_rule=strat.entry_rule(p), exit_rule=strat.exit_rule(p),
                    reason=row["reason"] or "", context=_context(row, bar), risk=risk, params=p,
                    data_as_of=as_of.to_pydatetime()))

    with session_scope() as s:
        job = JobRun(job="signal_scan", status="running", trigger=trigger)
        s.add(job)
        s.flush()
        existing = {(r.symbol, r.strategy_key, r.ts) for r in s.execute(
            select(Signal.symbol, Signal.strategy_key, Signal.ts).where(
                Signal.provider == pkey, Signal.kind == "observation"))}
        new = [Signal(**f) for f in found if (f["symbol"], f["strategy_key"], f["ts"]) not in existing]
        s.add_all(new)
        job.status, job.finished_at = "ok", utcnow()
        job.detail = f"{len(new)} new observations across {len(symbols)} symbols"
    return len(new)
