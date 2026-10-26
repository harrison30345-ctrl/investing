"""
Yahoo Finance via the `yfinance` library: the free default.

What you get: daily OHLCV for UK (`.L`) and US symbols, plus the headline
indices; adjusted close for dividends and splits; and intraday bars with a
short look-back (1m bars for ~7 days, 5m/15m for ~60 days).

Limitations, shown in the UI:
  * Unofficial. yfinance scrapes Yahoo's web endpoints, which can change or
    throttle without notice.
  * Delayed, not real-time (~15 min for US, ~15-20 min for LSE).
  * Yahoo's terms permit personal, non-commercial use only.
  * London prices are quoted in pence (GBX), not pounds.
"""
from __future__ import annotations

import logging
import warnings
from datetime import date, timedelta
from typing import Dict, Iterable

import pandas as pd

from .base import (MarketDataProvider, NotSupported, ProviderError, ProviderInfo, SymbolNotFound,
                   normalise, with_retries)

log = logging.getLogger(__name__)

_INTRADAY_LIMITS = {"1m": 7, "5m": 59, "15m": 59, "30m": 59, "60m": 729}


def _rename(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                              "Adj Close": "adj_close", "Volume": "volume"})


class YahooProvider(MarketDataProvider):
    info = ProviderInfo(
        key="yahoo",
        name="Yahoo Finance (yfinance)",
        latency="Delayed",
        is_demo=False,
        requires_key=False,
        markets=("UK", "US"),
        daily=True,
        intraday=True,
        intraday_intervals=tuple(_INTRADAY_LIMITS),
        max_intraday_days=59,
        licence_note="Unofficial API. Personal, non-commercial research use only.",
        notes=["US quotes delayed ~15 min, LSE ~15-20 min", "LSE prices are in pence (GBX)"],
    )

    def __init__(self):
        import yfinance  # noqa: F401  (imported only to fail early with a clear ImportError)
        warnings.filterwarnings("ignore", module="yfinance")

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self.fetch_daily_many([symbol], start, end)[symbol]  # type: ignore[return-value]

    def fetch_daily_many(self, symbols: Iterable[str], start: date, end: date
                         ) -> Dict[str, "pd.DataFrame | ProviderError"]:
        import yfinance as yf

        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            return {}

        def _download():
            try:
                return yf.download(symbols, start=start, end=end + timedelta(days=1), interval="1d",
                                   auto_adjust=False, actions=False, group_by="ticker",
                                   progress=False, threads=True)
            except Exception as exc:  # yfinance raises a zoo of exception types
                raise ProviderError(f"Yahoo request failed: {exc}") from exc

        raw = with_retries(_download)
        out: Dict[str, "pd.DataFrame | ProviderError"] = {}
        for sym in symbols:
            try:
                frame = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                out[sym] = SymbolNotFound(f"{sym}: no data returned by Yahoo")
                continue
            frame = _rename(frame).dropna(how="all", subset=["open", "high", "low", "close"])
            if frame.empty:
                out[sym] = SymbolNotFound(f"{sym}: no data returned by Yahoo")
            else:
                out[sym] = normalise(frame)
        return out

    def fetch_intraday(self, symbol: str, interval: str, start: date, end: date) -> pd.DataFrame:
        import yfinance as yf

        if interval not in _INTRADAY_LIMITS:
            raise NotSupported(f"Yahoo intraday interval {interval!r} not supported")
        earliest = date.today() - timedelta(days=_INTRADAY_LIMITS[interval])
        start = max(start, earliest)

        def _get():
            try:
                return yf.Ticker(symbol).history(start=start, end=end + timedelta(days=1),
                                                 interval=interval, auto_adjust=False, actions=False)
            except Exception as exc:
                raise ProviderError(f"Yahoo intraday request failed: {exc}") from exc

        df = with_retries(_get)
        if df is None or df.empty:
            raise SymbolNotFound(f"{symbol}: no intraday data returned by Yahoo")
        df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
        return normalise(_rename(df))

    def describe_symbol(self, symbol: str):
        import yfinance as yf
        try:
            fi = yf.Ticker(symbol).fast_info
            return {"currency": fi.currency, "exchange": fi.exchange, "timezone": fi.timezone}
        except Exception:  # metadata is best-effort
            return None
