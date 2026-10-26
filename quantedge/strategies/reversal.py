"""
Cross-sectional short-term reversal (Phase Two, hypothesis H2). A mean-reversion test.

Every `rebalance_days` sessions, rank the universe by total return over the last `lookback`
sessions (measured to the rebalance close) and hold the `bottom_n` biggest losers equally weighted
from the next open. A holding is sold at the next rebalance if it's no longer among the losers. No stops.

Rationale: short-horizon over-reaction and the premium for supplying liquidity to forced sellers
(Lehmann 1990; Jegadeesh 1990). Known weaknesses: weak in the largest stocks, and high turnover
makes it cost-sensitive. UK stamp duty is charged on every purchase.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from .base import Param, RebalanceStrategy


class CrossSectionalReversal(RebalanceStrategy):
    key = "xs_reversal"
    name = "Short-term reversal"
    family = "mean_reversion"
    description = "Buy the biggest recent losers in the universe; rebalance weekly."
    params = [
        Param("lookback", "Loss look-back (bars)", "int", 5, 2, 63, 1, help="5 ≈ one week"),
        Param("bottom_n", "Holdings", "int", 5, 1, 20, 1),
        Param("rebalance_days", "Rebalance every (bars)", "int", 5, 1, 63, 1),
    ]

    def entry_rule(self, p):
        return (f"Every {p['rebalance_days']} bars: rank by return over the last {p['lookback']} bars; hold the "
                f"{p['bottom_n']} worst performers equal-weight. Trades at next open.")

    def exit_rule(self, p):
        return "Sold at the next rebalance if no longer among the worst performers. No stop-loss."

    def warmup_bars(self, p):
        return int(p["lookback"]) + 5

    def ranking(self, closes: pd.DataFrame, t: int, p: Dict[str, Any]) -> pd.Series:
        lb = int(p["lookback"])
        if t < lb:
            return pd.Series(dtype=float)
        return (closes.iloc[t] / closes.iloc[t - lb] - 1.0).dropna().sort_values(ascending=True)

    def target_weights(self, closes: pd.DataFrame, t: int, p: Dict[str, Any],
                       regime: Optional[pd.Series] = None) -> Dict[str, float]:
        picks = list(self.ranking(closes, t, p).index[: int(p["bottom_n"])])
        return {s: 1.0 / int(p["bottom_n"]) for s in picks}
