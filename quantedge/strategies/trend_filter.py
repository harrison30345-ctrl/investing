"""
10-month moving-average trend filter (Phase Four, pre-registered in
docs/quantedge/RESEARCH-PHASE4-TREND-PLAN.md).

At each month-end close, hold a symbol for the next month if its close is above the average of its
last 10 month-end closes (including this one); otherwise hold cash. Prices are the engine's adjusted
closes, so the signal is a total-return trend signal; later dividends rescale every earlier price by
the same factor and so can't change a past decision.

Month-ends are the last session of each calendar month in the trading dates. The final stored date
is never treated as a month-end: whether the month is over isn't known from the data yet, and a
decision there could not be filled anyway.

The look-back is fixed at 10 months: it is the only value the plan allows.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from .base import Param, RebalanceStrategy

MONTHS = 10


def month_end_mask(index: pd.DatetimeIndex) -> np.ndarray:
    """True where the next trading date falls in a different calendar month."""
    m = pd.DatetimeIndex(index).to_period("M")
    out = np.zeros(len(m), dtype=bool)
    if len(m) > 1:
        out[:-1] = m[1:] != m[:-1]
    return out


class SmaTrendFilter(RebalanceStrategy):
    key = "trend_sma10m"
    name = "10-month trend filter"
    family = "trend"
    description = "Hold while the month-end close is above its 10-month average; otherwise cash."
    params = [
        Param("sma_months", "Moving average (month-ends)", "choice", MONTHS, choices=(MONTHS,),
              help="Fixed at 10 by the pre-registered plan."),
    ]
    entry_label = "Month-end close above its 10-month average"
    exit_label = "below_10m_average"

    def __init__(self):
        self._mask_cache: Dict[tuple, np.ndarray] = {}

    def _mask(self, index: pd.DatetimeIndex) -> np.ndarray:
        key = (len(index), index[0], index[-1]) if len(index) else (0,)
        if key not in self._mask_cache:
            self._mask_cache = {key: month_end_mask(index)}
        return self._mask_cache[key]

    def entry_rule(self, p):
        return (f"At each month-end close: hold if the close is above the average of the last {p['sma_months']} "
                "month-end closes. Trades at the next open.")

    def exit_rule(self, p):
        return "Sold at the next open after a month-end close at or below the average; cash earns the T-bill rate."

    def warmup_bars(self, p):
        return 23 * int(p["sma_months"])          # comfortably more than 10 month-ends of sessions

    def rebalance_due(self, index, t, k, every):
        # The window's first session sets the position from the latest completed month-end.
        return k == 0 or bool(self._mask(index)[t])

    def target_weights(self, closes: pd.DataFrame, t: int, p: Dict[str, Any],
                       regime: Optional[pd.Series] = None) -> Dict[str, float]:
        n = int(p["sma_months"])
        ends = np.flatnonzero(self._mask(closes.index)[: t + 1])
        hold = []
        for sym in closes.columns:
            s = closes[sym].iloc[ends].dropna()
            if len(s) >= n and s.iloc[-1] > s.iloc[-n:].mean():
                hold.append(sym)
        w = 1.0 / len(closes.columns)
        return {s: w for s in hold}
