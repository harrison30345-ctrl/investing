"""
Cross-sectional momentum (research family B).

Every `rebalance_days` sessions, rank the universe by total return over a
look-back, skipping the most recent `skip` days (short-term reversal noise).
Hold the top N equally weighted. Optional filters:
  * absolute momentum: only hold names whose own momentum is positive
  * regime: hold nothing while the benchmark is below its 200-day SMA

The look-back is a parameter so 3, 6 and 12-month variants can be compared
side by side in the Strategy Lab, against the benchmark.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from .base import Param, RebalanceStrategy


class CrossSectionalMomentum(RebalanceStrategy):
    key = "xs_momentum"
    name = "Cross-sectional momentum"
    family = "momentum"
    description = "Rank by past return (skipping the last month), hold the top N, rebalance monthly."
    params = [
        Param("lookback", "Look-back (bars)", "choice", 252, choices=(63, 126, 189, 252),
              help="63≈3m, 126≈6m, 252≈12m"),
        Param("skip", "Skip recent (bars)", "int", 21, 0, 63, 1),
        Param("top_n", "Holdings", "int", 5, 1, 20, 1),
        Param("rebalance_days", "Rebalance every (bars)", "int", 21, 5, 63, 1),
        Param("abs_filter", "Require positive momentum", "bool", True),
        Param("regime_filter", "Benchmark > 200d SMA filter", "bool", False),
    ]

    def entry_rule(self, p):
        rule = (f"Every {p['rebalance_days']} bars: rank by return from t-{p['lookback']} to t-{p['skip']}; "
                f"hold top {p['top_n']} equal-weight")
        if p["abs_filter"]:
            rule += "; only names with positive momentum"
        if p["regime_filter"]:
            rule += "; only while benchmark > 200-day SMA"
        return rule + ". Trades at next open."

    def exit_rule(self, p):
        return "Sold at the next rebalance if no longer in the top N (or filters fail)."

    def warmup_bars(self, p):
        return int(p["lookback"]) + 5

    def ranking(self, closes: pd.DataFrame, t: int, p: Dict[str, Any]) -> pd.Series:
        lb, skip = int(p["lookback"]), int(p["skip"])
        if t < lb:
            return pd.Series(dtype=float)
        now, past = closes.iloc[t - skip], closes.iloc[t - lb]
        return (now / past - 1.0).dropna().sort_values(ascending=False)

    def target_weights(self, closes: pd.DataFrame, t: int, p: Dict[str, Any],
                       regime: Optional[pd.Series] = None) -> Dict[str, float]:
        if p["regime_filter"] and regime is not None and not bool(regime.iloc[t]):
            return {}
        ranks = self.ranking(closes, t, p)
        if p["abs_filter"]:
            ranks = ranks[ranks > 0]
        picks = list(ranks.index[: int(p["top_n"])])
        if not picks:
            return {}
        w = 1.0 / int(p["top_n"])     # unfilled slots stay in cash rather than levering up the rest
        return {s: w for s in picks}
