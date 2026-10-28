"""
Market backdrop -- the conditions every stock is priced into.

A company's own numbers explain part of what its shares do. The rest comes from
the weather around it: how much volatility is being paid for, what cash costs,
whether the dollar is squeezing overseas revenue, whether credit is being lent
freely, and whether a rise is broad or carried by a handful of names.

WHAT THIS MODULE IS CAREFUL ABOUT
---------------------------------
Each reading carries a `tilt` of "supportive", "restrictive" or "neutral". That
describes the conditions equities have *historically* risen or fallen alongside.
It is an association, not a cause and not a forecast, and the thresholds below
are conventional rules of thumb rather than estimated parameters. They are
written down here, in one place, so a reader can disagree with a specific number
instead of with an opinion they cannot see.

Nothing here is tuned on, or validated against, forward returns. If it ever is,
that work belongs in a backtest with its own honest error bars, not in a
threshold quietly edited in this file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

__all__ = [
    "Reading", "MarketContext", "fetch_context", "SERIES", "TILT_ORDER",
]

# Yahoo symbols for the backdrop. Kept small on purpose: each extra series is
# another thing that can be stale or missing, and nine readings already cover
# volatility, the price of money, the dollar, commodities, credit and breadth.
SERIES = {
    "^VIX": "volatility",
    "^TNX": "ten_year",
    "^IRX": "thirteen_week",
    "DX-Y.NYB": "dollar",
    "CL=F": "oil",
    "GC=F": "gold",
    "HYG": "high_yield",
    "IEF": "treasuries",
    "RSP": "equal_weight",
    "^GSPC": "sp500",
}

TILT_ORDER = ("restrictive", "neutral", "supportive", "unknown")

SUPPORTIVE = "supportive"
RESTRICTIVE = "restrictive"
NEUTRAL = "neutral"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Reading:
    """One backdrop indicator, with its own change history and reading."""
    key: str
    label: str
    value: float | None
    display: str               # value formatted with its unit
    chg_1d: float | None       # in the unit's own terms (pp for yields, % for prices)
    chg_1m: float | None
    chg_3m: float | None
    range_pct: float | None    # where in the trailing 1y range, 0 = low, 100 = high
    tilt: str
    note: str                  # what the level and change are
    meaning: str               # why this indicator matters to share prices

    @property
    def unknown(self) -> bool:
        return self.value is None


@dataclass(frozen=True)
class MarketContext:
    readings: list = field(default_factory=list)
    fetched_at: datetime | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.readings)

    def by_tilt(self, tilt: str) -> list:
        return [r for r in self.readings if r.tilt == tilt]

    @property
    def summary(self) -> str:
        """A factual tally. Deliberately not a verdict or a score."""
        known = [r for r in self.readings if r.tilt != UNKNOWN]
        if not known:
            return "The backdrop could not be read -- no indicator returned data."
        supportive = len(self.by_tilt(SUPPORTIVE))
        restrictive = len(self.by_tilt(RESTRICTIVE))
        neutral = len(self.by_tilt(NEUTRAL))
        lead = (f"Of {len(known)} backdrop indicators, {supportive} "
                f"{'sits' if supportive == 1 else 'sit'} in territory equities have "
                f"historically risen alongside, {restrictive} in territory they have "
                f"historically struggled in, and {neutral} "
                f"{'is' if neutral == 1 else 'are'} unremarkable.")
        if restrictive > supportive:
            return lead + " The weather is currently more against equities than for them."
        if supportive > restrictive:
            return lead + " The weather is currently more for equities than against them."
        return lead + " The weather is mixed, with no clear lean either way."


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _pct_change(series: pd.Series, days: int) -> float | None:
    """Percentage change over `days` trading days."""
    if series is None or len(series) <= days:
        return None
    prev = _num(series.iloc[-days - 1])
    last = _num(series.iloc[-1])
    if prev is None or last is None or prev <= 0:
        return None
    return (last / prev - 1) * 100


def _point_change(series: pd.Series, days: int) -> float | None:
    """Absolute change over `days` trading days, for things quoted in percent."""
    if series is None or len(series) <= days:
        return None
    prev, last = _num(series.iloc[-days - 1]), _num(series.iloc[-1])
    if prev is None or last is None:
        return None
    return last - prev


def _range_position(series: pd.Series) -> float | None:
    """Where the latest value sits in its own trailing range, 0-100."""
    if series is None or len(series) < 30:
        return None
    low, high, last = _num(series.min()), _num(series.max()), _num(series.iloc[-1])
    if low is None or high is None or last is None or high <= low:
        return None
    return (last - low) / (high - low) * 100


def _arrow(change: float | None, unit: str) -> str:
    if change is None:
        return "no change data"
    word = "up" if change > 0 else ("down" if change < 0 else "flat")
    if word == "flat":
        return "flat"
    return f"{word} {abs(change):.2f}{unit}"


# ── Indicator builders ───────────────────────────────────────
#
# One function per reading. Each returns a Reading or None. They are separate
# functions rather than a config table because the interpretation genuinely
# differs -- a yield moves in percentage points, a ratio in percent, and "high"
# means the opposite thing for volatility and for breadth.

def _volatility(closes: pd.DataFrame) -> Reading | None:
    s = _series(closes, "^VIX")
    if s is None:
        return None
    level = _num(s.iloc[-1])
    chg_1m = _pct_change(s, 21)
    if level is None:
        return None
    if level >= 25:
        tilt = RESTRICTIVE
    elif level <= 16:
        tilt = SUPPORTIVE
    else:
        tilt = NEUTRAL
    # A fast rise matters even from a low level: it is the change in the price
    # of protection, not the level, that tends to accompany falling prices.
    if tilt != RESTRICTIVE and chg_1m is not None and chg_1m >= 40:
        tilt = RESTRICTIVE
    return Reading(
        key="volatility", label="Volatility (VIX)", value=level,
        display=f"{level:.1f}",
        chg_1d=_pct_change(s, 1), chg_1m=chg_1m, chg_3m=_pct_change(s, 63),
        range_pct=_range_position(s), tilt=tilt,
        note=f"At {level:.1f}, {_arrow(chg_1m, '%')} over a month.",
        meaning=("What traders are paying to insure against a fall in the S&P 500. "
                 "High or fast-rising readings have historically come with falling "
                 "share prices; calm readings with rising ones."),
    )


def _ten_year(closes: pd.DataFrame) -> Reading | None:
    s = _series(closes, "^TNX")
    if s is None:
        return None
    level = _num(s.iloc[-1])
    if level is None:
        return None
    chg_3m = _point_change(s, 63)
    tilt = NEUTRAL
    if chg_3m is not None and chg_3m >= 0.50:
        tilt = RESTRICTIVE
    elif chg_3m is not None and chg_3m <= -0.50:
        tilt = SUPPORTIVE
    return Reading(
        key="ten_year", label="10-year Treasury yield", value=level,
        display=f"{level:.2f}%",
        chg_1d=_point_change(s, 1), chg_1m=_point_change(s, 21), chg_3m=chg_3m,
        range_pct=_range_position(s), tilt=tilt,
        note=f"At {level:.2f}%, {_arrow(chg_3m, 'pp')} over three months.",
        meaning=("The benchmark cost of money. A rising yield lowers what a future "
                 "pound of profit is worth today, which weighs most on companies "
                 "whose value sits far in the future -- growth and technology names."),
    )


def _curve(closes: pd.DataFrame) -> Reading | None:
    long_s, short_s = _series(closes, "^TNX"), _series(closes, "^IRX")
    if long_s is None or short_s is None:
        return None
    frame = pd.concat([long_s, short_s], axis=1).dropna()
    if frame.empty:
        return None
    spread = frame.iloc[:, 0] - frame.iloc[:, 1]
    level = _num(spread.iloc[-1])
    if level is None:
        return None
    tilt = RESTRICTIVE if level < 0 else NEUTRAL
    shape = "inverted" if level < 0 else ("flat" if level < 0.5 else "upward-sloping")
    return Reading(
        key="curve", label="Yield curve (10y − 3m)", value=level,
        display=f"{level:+.2f}pp",
        chg_1d=_point_change(spread, 1), chg_1m=_point_change(spread, 21),
        chg_3m=_point_change(spread, 63), range_pct=_range_position(spread),
        tilt=tilt,
        note=f"{level:+.2f} percentage points -- {shape}.",
        meaning=("Long rates minus short rates. When short-term money costs more "
                 "than long-term money the curve is inverted, which has preceded "
                 "most US recessions since the 1960s -- though with a lag of many "
                 "months and several false alarms."),
    )


def _dollar(closes: pd.DataFrame) -> Reading | None:
    s = _series(closes, "DX-Y.NYB")
    if s is None:
        return None
    level = _num(s.iloc[-1])
    chg_3m = _pct_change(s, 63)
    if level is None:
        return None
    tilt = NEUTRAL
    if chg_3m is not None and chg_3m >= 4:
        tilt = RESTRICTIVE
    elif chg_3m is not None and chg_3m <= -4:
        tilt = SUPPORTIVE
    return Reading(
        key="dollar", label="US dollar (DXY)", value=level,
        display=f"{level:.1f}",
        chg_1d=_pct_change(s, 1), chg_1m=_pct_change(s, 21), chg_3m=chg_3m,
        range_pct=_range_position(s), tilt=tilt,
        note=f"At {level:.1f}, {_arrow(chg_3m, '%')} over three months.",
        meaning=("The dollar against a basket of currencies. A stronger dollar "
                 "shrinks the dollar value of overseas sales, which matters most "
                 "to large US companies earning abroad, and tightens conditions "
                 "for emerging markets. For a UK holder it also moves the sterling "
                 "value of US holdings, separately from the share price."),
    )


def _oil(closes: pd.DataFrame) -> Reading | None:
    s = _series(closes, "CL=F")
    if s is None:
        return None
    level = _num(s.iloc[-1])
    chg_3m = _pct_change(s, 63)
    if level is None:
        return None
    tilt = NEUTRAL
    if chg_3m is not None and chg_3m >= 25:
        tilt = RESTRICTIVE
    return Reading(
        key="oil", label="Crude oil (WTI)", value=level,
        display=f"${level:.0f}",
        chg_1d=_pct_change(s, 1), chg_1m=_pct_change(s, 21), chg_3m=chg_3m,
        range_pct=_range_position(s), tilt=tilt,
        note=f"At ${level:.0f}, {_arrow(chg_3m, '%')} over three months.",
        meaning=("An input cost for most of the economy and revenue for part of it. "
                 "A sharp rise feeds inflation and squeezes transport, chemicals and "
                 "consumer spending, while lifting energy producers."),
    )


def _gold(closes: pd.DataFrame) -> Reading | None:
    s = _series(closes, "GC=F")
    if s is None:
        return None
    level = _num(s.iloc[-1])
    chg_3m = _pct_change(s, 63)
    if level is None:
        return None
    # Gold is read as information, not as a tilt: it rises on fear and on falling
    # real rates, and those point in opposite directions for equities.
    return Reading(
        key="gold", label="Gold", value=level,
        display=f"${level:,.0f}",
        chg_1d=_pct_change(s, 1), chg_1m=_pct_change(s, 21), chg_3m=chg_3m,
        range_pct=_range_position(s), tilt=NEUTRAL,
        note=f"At ${level:,.0f}, {_arrow(chg_3m, '%')} over three months.",
        meaning=("Bought when investors want to hold something no government can "
                 "print. A strong run usually means either falling real interest "
                 "rates or rising anxiety -- which pull share prices opposite ways, "
                 "so this one is context rather than a lean."),
    )


def _credit(closes: pd.DataFrame) -> Reading | None:
    hy, govt = _series(closes, "HYG"), _series(closes, "IEF")
    if hy is None or govt is None:
        return None
    frame = pd.concat([hy, govt], axis=1).dropna()
    if frame.empty:
        return None
    ratio = frame.iloc[:, 0] / frame.iloc[:, 1]
    chg_3m = _pct_change(ratio, 63)
    tilt = NEUTRAL
    if chg_3m is not None and chg_3m <= -2:
        tilt = RESTRICTIVE
    elif chg_3m is not None and chg_3m >= 2:
        tilt = SUPPORTIVE
    direction = "widening" if (chg_3m or 0) < 0 else "tightening"
    return Reading(
        key="credit", label="Credit appetite (HY vs Treasuries)",
        value=_num(ratio.iloc[-1]), display=f"{_arrow(chg_3m, '%')} (3m)",
        chg_1d=_pct_change(ratio, 1), chg_1m=_pct_change(ratio, 21), chg_3m=chg_3m,
        range_pct=_range_position(ratio), tilt=tilt,
        note=f"Risky debt is {direction} against government debt, {_arrow(chg_3m, '%')} over three months.",
        meaning=("High-yield bonds measured against Treasuries. Credit markets "
                 "usually flinch before equities do: when lenders stop funding "
                 "weaker borrowers cheaply, the companies that depend on that "
                 "funding feel it first."),
    )


def _breadth(closes: pd.DataFrame) -> Reading | None:
    equal, cap = _series(closes, "RSP"), _series(closes, "^GSPC")
    if equal is None or cap is None:
        return None
    frame = pd.concat([equal, cap], axis=1).dropna()
    if frame.empty:
        return None
    ratio = frame.iloc[:, 0] / frame.iloc[:, 1]
    chg_3m = _pct_change(ratio, 63)
    tilt = NEUTRAL
    if chg_3m is not None and chg_3m <= -3:
        tilt = RESTRICTIVE
    elif chg_3m is not None and chg_3m >= 3:
        tilt = SUPPORTIVE
    if chg_3m is None:
        shape = "cannot be read"
    elif chg_3m <= -3:
        shape = "narrow -- a few large companies are carrying the index"
    elif chg_3m >= 3:
        shape = "broad -- the average company is keeping up with the index"
    else:
        shape = "even, with no strong lean to large or small"
    return Reading(
        key="breadth", label="Market breadth (equal vs cap weight)",
        value=_num(ratio.iloc[-1]), display=f"{_arrow(chg_3m, '%')} (3m)",
        chg_1d=_pct_change(ratio, 1), chg_1m=_pct_change(ratio, 21), chg_3m=chg_3m,
        range_pct=_range_position(ratio), tilt=tilt,
        note=f"The rise is {shape}.",
        meaning=("The equal-weighted S&P against the normal one. If the average "
                 "company is lagging badly, the index's strength rests on a handful "
                 "of names -- which flatters an index return and hides what most "
                 "holdings are doing."),
    )


def _trend(closes: pd.DataFrame) -> Reading | None:
    s = _series(closes, "^GSPC")
    if s is None or len(s) < 200:
        return None
    level = _num(s.iloc[-1])
    sma = _num(s.rolling(200).mean().iloc[-1])
    if level is None or sma is None or sma <= 0:
        return None
    gap = (level - sma) / sma * 100
    tilt = SUPPORTIVE if gap > 0 else RESTRICTIVE
    side = "above" if gap > 0 else "below"
    return Reading(
        key="trend", label="S&P 500 vs 200-day average", value=gap,
        display=f"{gap:+.1f}%",
        chg_1d=_pct_change(s, 1), chg_1m=_pct_change(s, 21), chg_3m=_pct_change(s, 63),
        range_pct=_range_position(s), tilt=tilt,
        note=f"The index is {abs(gap):.1f}% {side} its 200-day average.",
        meaning=("A crude read on whether the market's long trend is up or down. "
                 "It tells you where prices have been, not where they are going, "
                 "and it turns late at every major turning point."),
    )


_BUILDERS = (_trend, _volatility, _ten_year, _curve, _credit, _breadth,
             _dollar, _oil, _gold)


def _series(closes: pd.DataFrame, symbol: str) -> pd.Series | None:
    if closes is None or symbol not in closes.columns:
        return None
    s = closes[symbol].dropna()
    return s if len(s) >= 2 else None


def fetch_context(period: str = "1y") -> MarketContext:
    """Download the backdrop series and build every reading that has data.

    One batched request for all of them. A missing series costs its own reading
    and nothing else -- the rest of the page still works.
    """
    now = datetime.now(timezone.utc)
    try:
        raw = yf.download(list(SERIES), period=period, progress=False,
                          auto_adjust=True, threads=True)
    except Exception as exc:
        return MarketContext([], now, f"Could not reach the data provider ({type(exc).__name__}).")

    if raw is None or raw.empty:
        return MarketContext([], now, "The data provider returned no market data.")

    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    readings = []
    for build in _BUILDERS:
        try:
            reading = build(closes)
        except Exception:
            reading = None       # one broken series must not blank the page
        if reading is not None:
            readings.append(reading)

    if not readings:
        return MarketContext([], now, "No backdrop indicator returned usable data.")
    return MarketContext(readings, now)
