from __future__ import annotations

"""
Historical price data for backtesting. Downloads daily closes for a universe via
yfinance and caches to parquet so repeat backtests don't re-hit the network.
"""

import hashlib
import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".trading" / "backtest_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(tickers: list[str], start: str, end: str) -> Path:
    key = hashlib.md5(
        ("|".join(sorted(tickers)) + start + end).encode()
    ).hexdigest()[:16]
    return CACHE_DIR / f"px_{key}.parquet"


def load_history(tickers: list[str], start: str, end: str,
                 use_cache: bool = True) -> pd.DataFrame:
    """
    Return a DataFrame of daily closing prices: index=dates, columns=tickers.
    Columns with no data are dropped. Rows are forward-filled then leading NaNs
    dropped so every remaining row has a full cross-section.
    """
    path = _cache_path(tickers, start, end)
    if use_cache and path.exists():
        logger.info("loading cached prices %s", path.name)
        return pd.read_parquet(path)

    logger.info("downloading %d tickers %s → %s", len(tickers), start, end)
    raw = yf.download(tickers, start=start, end=end,
                      auto_adjust=True, progress=False)

    # yf.download returns a column MultiIndex; pull the "Close" level.
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"].copy()
    else:  # single ticker → flat columns
        close = raw[["Close"]].copy()
        close.columns = tickers[:1]

    close = close.dropna(axis=1, how="all")      # drop dead tickers
    close = close.ffill().dropna(how="any")      # complete cross-sections only

    if not close.empty:
        close.to_parquet(path)
    return close
