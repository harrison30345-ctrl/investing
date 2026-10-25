from __future__ import annotations

"""
Stock trader agent. Reuses the existing fundamental screener as its brain:
scores each watchlist name 0-100, then holds an equal-weighted basket of the
top names, rebalancing on each run.

Strategy (deliberately simple + explainable — the game UI can narrate it):
  * Score every ticker in the watchlist via the screener.
  * BUY  : names scoring >= buy_threshold that rank in the top `max_positions`,
           sized to `target_weight` of equity, if not already held.
  * SELL : names we hold whose score fell below sell_threshold, dropped out of
           the top ranks, or breached the stop-loss.

Because fundamentals move slowly, this behaves like a periodic rebalancer — run
it daily (or on any cadence) rather than every few seconds.
"""

import logging

from screener.data_fetcher import YFinanceFetcher
from screener.metrics import calculate_all_metrics
from screener.scoring import score_stock

from .base import BaseAgent, Decision
from .rebalance import rebalance_decisions

logger = logging.getLogger(__name__)


class StockTraderAgent(BaseAgent):
    def __init__(self, broker, watchlist: list[str], *,
                 name: str = "Vega",
                 buy_threshold: float = 65.0,
                 sell_threshold: float = 50.0,
                 max_positions: int = 5,
                 stop_loss_pct: float = 15.0,
                 avatar: str = "📈",
                 fetcher=None):
        super().__init__(
            name=name,
            broker=broker,
            personality="A patient quant who buys quality and cuts losers.",
            room="The Trading Floor",
            avatar=avatar,
        )
        self.watchlist = [t.upper() for t in watchlist]
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.max_positions = max_positions
        self.stop_loss_pct = stop_loss_pct
        self._fetcher = fetcher or YFinanceFetcher()

    def _score_watchlist(self) -> dict[str, float]:
        """Return {symbol: composite_score} for every watchlist name we can score."""
        scores: dict[str, float] = {}
        for ticker in self.watchlist:
            try:
                data = self._fetcher.fetch_ticker_data(ticker)
                metrics = calculate_all_metrics(ticker, data)
                scores[ticker] = score_stock(metrics)["composite_score"]
            except Exception as e:  # noqa: BLE001 — one bad ticker shouldn't stop the run
                logger.warning("could not score %s: %s", ticker, e)
        return scores

    def decide(self) -> list[Decision]:
        scores = self._score_watchlist()
        if not scores:
            logger.warning("%s: no scores available, standing pat", self.name)
            return []
        return rebalance_decisions(
            self.broker, scores,
            buy_threshold=self.buy_threshold,
            sell_threshold=self.sell_threshold,
            max_positions=self.max_positions,
            stop_loss_pct=self.stop_loss_pct,
        )
