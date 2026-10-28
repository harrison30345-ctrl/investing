"""
Data-quality checks for daily bars.

`validate_daily` never edits data. It reports. `clean_daily` does the only
safe automatic repairs (sort, drop exact duplicate dates keeping the latest
vendor value), and the caller logs that it ran.

Severity:
  error   — the series shouldn't be used for backtesting as-is
  warning — usable, but someone should know (e.g. a holiday gap, zero volume)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import List, Optional

import numpy as np
import pandas as pd

from ..markets import CLOSURE_OVERRIDES, closure_overrides, sessions

JUMP_THRESHOLD = 0.40     # >40% one-day move: possible unadjusted split or bad tick


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    count: int = 0
    examples: List[str] = field(default_factory=list)


@dataclass
class QualityResult:
    symbol: str
    rows: int
    first: Optional[pd.Timestamp]
    last: Optional[pd.Timestamp]
    issues: List[Issue]

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def issues_as_dicts(self) -> list:
        return [asdict(i) for i in self.issues]


def _ex(idx) -> List[str]:
    return [pd.Timestamp(x).strftime("%Y-%m-%d") for x in list(idx)[:5]]


def clean_daily(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_index()
    return df[~df.index.duplicated(keep="last")]


def validate_daily(df: pd.DataFrame, symbol: str, market: str, *, is_index: bool = False,
                   expected_last: Optional[date] = None) -> QualityResult:
    issues: List[Issue] = []
    if df is None or df.empty:
        return QualityResult(symbol, 0, None, None, [Issue("empty", "error", "No bars stored")])

    idx = pd.DatetimeIndex(df.index)
    dupes = idx[idx.duplicated()]
    if len(dupes):
        issues.append(Issue("duplicates", "error", "Duplicate dates", len(dupes), _ex(dupes)))
    if not idx.is_monotonic_increasing:
        issues.append(Issue("unsorted", "error", "Dates are not in chronological order"))

    df = clean_daily(df)
    idx = pd.DatetimeIndex(df.index)
    price = df[["open", "high", "low", "close"]]

    nan_rows = df.index[price.isna().any(axis=1)]
    if len(nan_rows):
        issues.append(Issue("missing_values", "error", "Bars with missing OHLC values", len(nan_rows), _ex(nan_rows)))

    nonpos = df.index[(price <= 0).any(axis=1)]
    if len(nonpos):
        issues.append(Issue("non_positive", "error", "Zero or negative prices", len(nonpos), _ex(nonpos)))

    tol = 1e-6
    bad_hl = df.index[(df["high"] + tol < price.max(axis=1)) | (df["low"] - tol > price.min(axis=1))]
    if len(bad_hl):
        issues.append(Issue("ohlc_inconsistent", "warning",
                            "High/low don't bracket open/close", len(bad_hl), _ex(bad_hl)))

    expected = sessions(market, idx[0].date(), idx[-1].date())
    closed = closure_overrides(market, idx[0], idx[-1])
    if len(closed):
        on_closed = idx.intersection(closed)
        if len(on_closed):
            # A bar on a day the exchange was shut can't be a real price.
            issues.append(Issue("bar_on_override_closure", "error",
                                "Bars on days listed as exchange closures (markets.CLOSURE_OVERRIDES)",
                                len(on_closed), _ex(on_closed)))
        issues.append(Issue("closure_overrides", "info",
                            "Treated as closed by explicit override: "
                            + "; ".join(f"{d:%Y-%m-%d}: {CLOSURE_OVERRIDES[market][f'{d:%Y-%m-%d}']}" for d in closed),
                            len(closed), _ex(closed)))
    missing = expected.difference(idx)
    if len(missing):
        sev = "error" if len(missing) > max(5, 0.02 * len(expected)) else "warning"
        issues.append(Issue("missing_sessions", sev, "Exchange sessions with no bar", len(missing), _ex(missing)))
    extra = idx.difference(expected).difference(closed)
    if len(extra):
        issues.append(Issue("non_session_bars", "warning",
                            "Bars on days the exchange calendar marks closed", len(extra), _ex(extra)))

    ref = df["adj_close"].where(df["adj_close"].notna(), df["close"])
    rets = ref.pct_change().abs()
    jumps = rets[rets > JUMP_THRESHOLD].index
    if len(jumps):
        issues.append(Issue("price_jump", "warning",
                            f"One-day moves above {JUMP_THRESHOLD:.0%} (check for split or bad tick)",
                            len(jumps), _ex(jumps)))

    if not is_index and "volume" in df:
        zero_vol = df.index[(df["volume"].fillna(0) == 0)]
        if len(zero_vol) > 3:
            issues.append(Issue("zero_volume", "warning", "Sessions with zero volume", len(zero_vol), _ex(zero_vol)))

    if df["adj_close"].isna().all():
        issues.append(Issue("no_adjustment", "warning",
                            "Vendor supplied no adjusted close; dividends/splits not adjusted"))

    if expected_last is not None and idx[-1].date() < expected_last:
        lag = int(np.busday_count(idx[-1].date(), expected_last))
        issues.append(Issue("stale", "warning" if lag <= 2 else "error",
                            f"Latest bar {idx[-1]:%Y-%m-%d}; last completed session {expected_last:%Y-%m-%d}",
                            lag))

    return QualityResult(symbol, len(df), idx[0], idx[-1], issues)


def validate_intraday(df: pd.DataFrame, symbol: str, market: str, interval_minutes: int = 5,
                      expected_last: Optional[date] = None) -> QualityResult:
    """
    Intraday checks: duplicates, ordering, missing values, OHLC sanity, bars outside the
    regular session, and sessions with missing bars. Bars are naive UTC.
    """
    issues: List[Issue] = []
    if df is None or df.empty:
        return QualityResult(symbol, 0, None, None, [Issue("empty", "error", "No intraday bars stored")])
    idx = pd.DatetimeIndex(df.index)
    dupes = idx[idx.duplicated()]
    if len(dupes):
        issues.append(Issue("duplicates", "error", "Duplicate timestamps", len(dupes), _ex(dupes)))
    df = clean_daily(df)
    idx = pd.DatetimeIndex(df.index)
    price = df[["open", "high", "low", "close"]]
    nan_rows = df.index[price.isna().any(axis=1)]
    if len(nan_rows):
        issues.append(Issue("missing_values", "error", "Bars with missing OHLC values", len(nan_rows), _ex(nan_rows)))
    tol = 1e-6
    bad_hl = df.index[(df["high"] + tol < price.max(axis=1)) | (df["low"] - tol > price.min(axis=1))]
    if len(bad_hl):
        issues.append(Issue("ohlc_inconsistent", "warning", "High/low don't bracket open/close", len(bad_hl),
                            _ex(bad_hl)))

    from ..markets import schedule
    sched = schedule(market, idx[0].date(), idx[-1].date())
    utc = idx.tz_localize("UTC")
    inside = pd.Series(False, index=idx)
    gaps, gap_examples = 0, []
    for day, row in sched.iterrows():
        mask = (utc >= row["market_open"]) & (utc < row["market_close"])
        inside |= pd.Series(mask, index=idx)
        n = int(mask.sum())
        expected = int((row["market_close"] - row["market_open"]).total_seconds() // (interval_minutes * 60))
        if 0 < n < expected:
            gaps += expected - n
            gap_examples.append(day.strftime("%Y-%m-%d"))
        elif n == 0 and day.date() < idx[-1].date():
            gaps += expected
            gap_examples.append(day.strftime("%Y-%m-%d") + " (no bars)")
    outside = idx[~inside.values]
    if len(outside):
        issues.append(Issue("outside_session", "warning", "Bars outside regular trading hours", len(outside),
                            _ex(outside)))
    if gaps:
        issues.append(Issue("missing_bars", "warning" if gaps < 50 else "error",
                            f"Missing {interval_minutes}-minute bars within sessions", gaps, gap_examples[:5]))
    if expected_last is not None and idx[-1].date() < expected_last:
        issues.append(Issue("stale", "warning", f"Latest bar {idx[-1]:%Y-%m-%d %H:%M} UTC; last completed session "
                                                f"{expected_last:%Y-%m-%d}", 1))
    return QualityResult(symbol, len(df), idx[0], idx[-1], issues)
