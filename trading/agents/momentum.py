from __future__ import annotations

"""
Pure momentum scoring — shared by the live crypto agent and the backtester so
both score identically. Takes a series of closing prices (oldest → newest) and
returns a 0-100 momentum score, or None if there isn't enough history.
"""

import pandas as pd

LOOKBACK = 31  # need at least this many bars to score


def _normalize(value: float, low: float, high: float) -> float:
    """Map value into 0-100 over [low, high], clamped."""
    if high == low:
        return 50.0
    clamped = max(low, min(high, value))
    return (clamped - low) / (high - low) * 100


def momentum_score(closes: pd.Series) -> float | None:
    """0-100 score from 7d/30d returns and trend vs the 30-bar average."""
    if closes is None or len(closes) < LOOKBACK:
        return None

    price = float(closes.iloc[-1])
    p7 = float(closes.iloc[-8])
    p30 = float(closes.iloc[-31])
    sma30 = float(closes.iloc[-30:].mean())

    r7 = (price / p7 - 1) * 100 if p7 else 0.0
    r30 = (price / p30 - 1) * 100 if p30 else 0.0
    trend = (price / sma30 - 1) * 100 if sma30 else 0.0

    score = (
        0.40 * _normalize(r7, -15, 15) +
        0.40 * _normalize(r30, -40, 40) +
        0.20 * _normalize(trend, -20, 20)
    )
    return round(score, 1)
