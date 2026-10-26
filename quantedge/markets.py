"""
Exchange calendars and session status for the two markets QuantEdge covers.

Calendars come from `pandas_market_calendars`, which knows each exchange's
holidays and early closes (e.g. the LSE's 12:30 close on Christmas Eve).
Every "is the market open?" answer in the app comes from here, never from
a hard-coded 9-to-5 assumption.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

import pandas as pd
import pandas_market_calendars as mcal


@dataclass(frozen=True)
class Market:
    code: str            # "UK" | "US"
    exchange: str        # display name
    short: str           # "LSE" | "NYSE"
    calendar: str        # pandas_market_calendars name
    tz: str
    currency: str        # quote currency of equities on this market
    benchmark: str       # total-return ETF used as backtest benchmark
    index: str           # headline index symbol


MARKETS = {
    "UK": Market("UK", "London Stock Exchange", "LSE", "XLON", "Europe/London", "GBX", "ISF.L", "^FTSE"),
    "US": Market("US", "NYSE / Nasdaq", "NYSE", "NYSE", "America/New_York", "USD", "SPY", "^GSPC"),
}


@lru_cache(maxsize=4)
def _calendar(name: str):
    return mcal.get_calendar(name)


def schedule(market: str, start, end) -> pd.DataFrame:
    """Sessions (market_open, market_close in UTC) between two dates inclusive."""
    return _calendar(MARKETS[market].calendar).schedule(start_date=start, end_date=end)


def sessions(market: str, start, end) -> pd.DatetimeIndex:
    """Trading dates (naive, midnight) between start and end inclusive."""
    sched = schedule(market, start, end)
    return pd.DatetimeIndex(sched.index).normalize().tz_localize(None)


@dataclass(frozen=True)
class SessionStatus:
    market: str
    is_open: bool
    label: str                          # "Open" | "Closed" | "Early close"
    detail: str                         # e.g. "closes 16:30" / "opens Mon 08:00"
    last_completed_session: Optional[date]


def _now_utc(now: Optional[datetime]) -> pd.Timestamp:
    ts = pd.Timestamp(now or datetime.now(timezone.utc))
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def session_status(market: str, now: Optional[datetime] = None) -> SessionStatus:
    m = MARKETS[market]
    now_ts = _now_utc(now)
    sched = schedule(market, (now_ts - timedelta(days=12)).date(), (now_ts + timedelta(days=12)).date())
    local = lambda ts: ts.tz_convert(m.tz)  # noqa: E731

    completed = sched[sched["market_close"] <= now_ts]
    last_done = completed.index[-1].date() if len(completed) else None

    current = sched[(sched["market_open"] <= now_ts) & (sched["market_close"] > now_ts)]
    if len(current):
        close = local(current["market_close"].iloc[0])
        regular = _calendar(m.calendar).close_time
        early = close.time() < regular
        return SessionStatus(market, True, "Early close" if early else "Open",
                             f"closes {close:%H:%M}", last_done)

    upcoming = sched[sched["market_open"] > now_ts]
    if len(upcoming):
        nxt = local(upcoming["market_open"].iloc[0])
        same_day = nxt.date() == now_ts.tz_convert(m.tz).date()
        when = f"{nxt:%H:%M}" if same_day else f"{nxt:%a %H:%M}"
        return SessionStatus(market, False, "Closed", f"opens {when}", last_done)
    return SessionStatus(market, False, "Closed", "", last_done)


def last_completed_session(market: str, now: Optional[datetime] = None) -> Optional[date]:
    return session_status(market, now).last_completed_session


def market_for_symbol(symbol: str) -> str:
    """Infer market from the Yahoo-style symbol convention used internally."""
    s = symbol.upper()
    if s.endswith(".L") or s in ("^FTSE", "^FTMC", "^FTAS"):
        return "UK"
    return "US"
