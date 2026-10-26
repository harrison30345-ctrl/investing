"""
Cash interest rates (audit fix 5).

Uninvested cash in a backtest or paper account earns the market's short-term rate, and
Sharpe ratios are measured on returns above that rate. Without this, low-exposure
strategies look worse than they are and every Sharpe in a high-rate period looks better.

Sources (FRED, free, no key; daily, percent per year):
  US: DTB3     — 3-month Treasury bill, secondary market
  UK: IUDSOIA  — SONIA overnight rate (Bank of England)

Series are cached in ~/.quantedge/rates/ and refreshed weekly. QE_CASH_RATE controls it:
  "fred" (default) — the historical series above
  "zero"           — no interest (the pre-audit behaviour)
  a number, e.g. "0.04" — a constant annual rate
If the series can't be fetched and there's no cache, the rate is 0% and the source says
"unavailable". Nothing is invented.
"""
from __future__ import annotations

import io
import logging
import os
import time
from typing import Optional, Tuple

import pandas as pd
import requests

from .config import HOME_DIR

log = logging.getLogger(__name__)

SERIES = {"US": "DTB3", "UK": "IUDSOIA"}
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd=2000-01-01"
CACHE_DAYS = 7
PERIODS = 252


def _mode() -> str:
    return (os.environ.get("QE_CASH_RATE") or "fred").strip().lower()


def _cache_path(series_id: str):
    return HOME_DIR / "rates" / f"{series_id}.csv"


def _fetch(series_id: str) -> Optional[pd.Series]:
    path = _cache_path(series_id)
    fresh = path.exists() and time.time() - path.stat().st_mtime < CACHE_DAYS * 86400
    if not fresh:
        try:
            r = requests.get(URL.format(id=series_id), timeout=20)
            r.raise_for_status()
            pd.read_csv(io.StringIO(r.text))          # validate before replacing the cache
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(r.text)
        except Exception as exc:                       # network down: fall back to an older cache if any
            log.warning("Cash-rate fetch for %s failed: %s", series_id, exc)
    if not path.exists():
        return None
    df = pd.read_csv(path)
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")  # FRED writes "." for missing days
    s.index = pd.to_datetime(df.iloc[:, 0])
    return (s / 100.0).dropna()


def annual_rates(market: str, index: pd.DatetimeIndex) -> Tuple[Optional[pd.Series], str]:
    """
    Annual cash rate (decimal) for each date in `index`, plus a description of the source.
    `index` may be daily dates or intraday timestamps (naive); values are carried forward.
    """
    mode = _mode()
    if mode == "zero":
        return None, "none (QE_CASH_RATE=zero)"
    if mode not in ("fred",):
        try:
            rate = float(mode)
        except ValueError:
            return None, f"none (unrecognised QE_CASH_RATE={mode!r})"
        return pd.Series(rate, index=index), f"constant {rate:.2%} (QE_CASH_RATE)"
    sid = SERIES[market]
    s = _fetch(sid)
    if s is None or s.empty:
        return None, f"unavailable ({sid} could not be fetched): 0%"
    days = pd.DatetimeIndex(index).normalize()
    unique = pd.DatetimeIndex(sorted(set(days)))
    by_day = s.reindex(s.index.union(unique)).sort_index().ffill().reindex(unique)
    return pd.Series(by_day.reindex(days).values, index=index).fillna(0.0), f"FRED {sid}"
