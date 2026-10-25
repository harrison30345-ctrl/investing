from __future__ import annotations

"""
Generic price-signal agent. Fetches recent daily history for each name in its
universe, scores each with a pluggable score function, and rebalances into an
equal-weighted basket of the names the signal likes — in cash on the rest.

This is what both Vega (stocks) and Nova (crypto) run once they adopt the
trend-following strategy: same brain, different universe. The score function is
the exact one the backtester validates, so live behavior matches the backtest.
"""

import logging
from typing import Callable

import pandas as pd
import yfinance as yf

from .base import BaseAgent, Decision
from .rebalance import rebalance_decisions

logger = logging.getLogger(__name__)

ScoreFn = Callable[[pd.Series], "float | None"]


class PriceSignalAgent(BaseAgent):
    def __init__(self, broker, universe: list[str], score_fn: ScoreFn, *,
                 name: str, room: str, avatar: str, personality: str,
                 buy_threshold: float = 50.0,
                 sell_threshold: float = 50.0,
                 max_positions: int | None = None,
                 stop_loss_pct: float = 100.0,   # trend exit is the signal itself
                 fractional: bool = False,
                 history_period: str = "2y"):
        super().__init__(name=name, broker=broker, personality=personality,
                         room=room, avatar=avatar)
        self.universe = [t.upper() for t in universe]
        self.score_fn = score_fn
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        # Default: willing to hold every trending name (equal-weight the basket).
        self.max_positions = max_positions or len(self.universe)
        self.stop_loss_pct = stop_loss_pct
        self.fractional = fractional
        self.history_period = history_period

    def _score_universe(self) -> dict[str, float]:
        scores: dict[str, float] = {}
        for symbol in self.universe:
            try:
                hist = yf.Ticker(symbol).history(period=self.history_period)
            except Exception as e:  # noqa: BLE001
                logger.warning("history fetch failed for %s: %s", symbol, e)
                continue
            if hist.empty:
                continue
            s = self.score_fn(hist["Close"])
            if s is not None:
                scores[symbol] = s
        return scores

    def decide(self) -> list[Decision]:
        scores = self._score_universe()
        if not scores:
            logger.warning("%s: no scores available, standing pat", self.name)
            return []
        return rebalance_decisions(
            self.broker, scores,
            buy_threshold=self.buy_threshold,
            sell_threshold=self.sell_threshold,
            max_positions=self.max_positions,
            stop_loss_pct=self.stop_loss_pct,
            fractional=self.fractional,
        )
