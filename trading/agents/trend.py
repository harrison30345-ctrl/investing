from __future__ import annotations

"""
Trend-following signals. The golden cross (50-day SMA over 200-day SMA) is a
robust, low-tuning trend filter: long when the fast average is above the slow
average, in cash otherwise.

Expressed as a 0/100 score so it plugs into the same rebalance + backtest
machinery as every other strategy: 100 = golden (hold), 0 = death (exit).
"""

import pandas as pd

FAST = 50
SLOW = 200
LOOKBACK = SLOW  # need at least SLOW bars before the signal is defined


def golden_cross_score(closes: pd.Series) -> float | None:
    """100 when the 50-bar SMA is above the 200-bar SMA, else 0."""
    if closes is None or len(closes) < SLOW:
        return None
    sma_fast = float(closes.iloc[-FAST:].mean())
    sma_slow = float(closes.iloc[-SLOW:].mean())
    return 100.0 if sma_fast > sma_slow else 0.0


def price_vs_sma_score(closes: pd.Series, window: int = SLOW) -> float | None:
    """
    Faber-style trend filter: 100 when price is above its `window`-bar SMA, else
    0. More responsive than the golden cross (reacts to price, not a lagging
    average-of-averages), which historically gives similar drawdown protection
    with better return capture.
    """
    if closes is None or len(closes) < window:
        return None
    price = float(closes.iloc[-1])
    sma = float(closes.iloc[-window:].mean())
    return 100.0 if price > sma else 0.0
