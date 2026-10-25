from __future__ import annotations

"""
Crypto trader agent. Crypto has no fundamentals to screen, so this agent runs a
momentum brain: it scores each coin by trailing returns + trend, then holds an
equal-weighted basket of the strongest movers, rebalancing on each run.

Universe is limited to liquid coins that yfinance can price (e.g. BTC-USD), so
the same paper simulator that trades stocks trades crypto with no extra plumbing.
"""

import logging

import yfinance as yf

from .base import BaseAgent, Decision
from .momentum import momentum_score
from .rebalance import rebalance_decisions

logger = logging.getLogger(__name__)

# Liquid, yfinance-priceable coins (Yahoo uses the COIN-USD convention).
DEFAULT_UNIVERSE = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
    "ADA-USD", "DOGE-USD", "AVAX-USD", "LINK-USD", "DOT-USD",
]


class CryptoTraderAgent(BaseAgent):
    def __init__(self, broker, universe: list[str] | None = None, *,
                 name: str = "Nova",
                 buy_threshold: float = 58.0,
                 sell_threshold: float = 42.0,
                 max_positions: int = 3,
                 stop_loss_pct: float = 25.0,   # wider stop — crypto is volatile
                 avatar: str = "🚀"):
        super().__init__(
            name=name,
            broker=broker,
            personality="A momentum degen who rides the strongest coins and bails fast.",
            room="The Crypto Casino",
            avatar=avatar,
        )
        self.universe = [c.upper() for c in (universe or DEFAULT_UNIVERSE)]
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.max_positions = max_positions
        self.stop_loss_pct = stop_loss_pct

    def _momentum_score(self, symbol: str) -> float | None:
        """Fetch recent history and score it with the shared momentum function."""
        try:
            hist = yf.Ticker(symbol).history(period="3mo")
        except Exception as e:  # noqa: BLE001
            logger.warning("history fetch failed for %s: %s", symbol, e)
            return None
        if hist.empty:
            return None
        return momentum_score(hist["Close"])

    def _score_universe(self) -> dict[str, float]:
        scores: dict[str, float] = {}
        for symbol in self.universe:
            score = self._momentum_score(symbol)
            if score is not None:
                scores[symbol] = score
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
            fractional=True,   # buy fractional coins so pricey names aren't skipped
        )
