"""
Causal technical indicators. Each value at bar t uses only bars <= t.

`tests/quantedge/test_no_lookahead.py` enforces this: it recomputes on data
truncated at many dates and checks the past never changes.
"""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder's Average True Range."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def prior_high(series: pd.Series, window: int) -> pd.Series:
    """Highest value of the *previous* `window` bars (excludes today)."""
    return series.shift(1).rolling(window, min_periods=window).max()


def prior_low(series: pd.Series, window: int) -> pd.Series:
    return series.shift(1).rolling(window, min_periods=window).min()


def total_return(series: pd.Series, lookback: int, skip: int = 0) -> pd.Series:
    """Return from t-lookback to t-skip. skip=21 gives the classic '12-1' momentum."""
    return series.shift(skip) / series.shift(lookback) - 1.0


def adjusted_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Scale OHLC by adj_close/close so dividends and splits don't create fake gaps."""
    out = df.copy()
    if "adj_close" in df and df["adj_close"].notna().any():
        factor = (df["adj_close"] / df["close"]).fillna(1.0)
        for c in ("open", "high", "low", "close"):
            out[c] = df[c] * factor
    return out
