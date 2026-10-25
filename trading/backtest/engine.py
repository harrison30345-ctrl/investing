from __future__ import annotations

"""
Backtest engine. Replays a price-based strategy over historical daily bars with
NO look-ahead: on each date, scores are computed only from data up to and
including that date. Models slippage and per-trade fees, checks a daily
stop-loss, and rebalances on a fixed cadence.

A "strategy" here is just a scoring function `score_fn(closes: pd.Series) ->
float | None`, plus the same threshold/sizing knobs the live agents use — so the
backtest exercises the real decision logic, not a re-implementation.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

logger = logging.getLogger(__name__)

ScoreFn = Callable[[pd.Series], "float | None"]


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    realized_pnls: list[float] = field(default_factory=list)
    trade_log: list[dict] = field(default_factory=list)


@dataclass
class Backtester:
    prices: pd.DataFrame            # index=dates, columns=symbols, values=close
    score_fn: ScoreFn
    buy_threshold: float
    sell_threshold: float
    max_positions: int
    stop_loss_pct: float
    lookback: int = 31             # bars needed before the strategy can act
    rebalance_days: int = 7        # calendar days between rebalances
    starting_cash: float = 10_000.0
    slippage_bps: float = 10.0     # per-side slippage in basis points
    fee_bps: float = 0.0           # per-trade fee in basis points of notional
    fractional: bool = True

    def _buy_price(self, px: float) -> float:
        return px * (1 + self.slippage_bps / 1e4)

    def _sell_price(self, px: float) -> float:
        return px * (1 - self.slippage_bps / 1e4)

    def _fee(self, notional: float) -> float:
        return abs(notional) * self.fee_bps / 1e4

    def run(self) -> BacktestResult:
        dates = self.prices.index
        cash = self.starting_cash
        # symbol -> {"qty": float, "avg": float}
        positions: dict[str, dict] = {}
        equity_curve: list[float] = []
        realized: list[float] = []
        trade_log: list[dict] = []
        last_rebalance: pd.Timestamp | None = None

        def sell(symbol: str, date, px_close: float, reason: str):
            nonlocal cash
            pos = positions.pop(symbol)
            fill = self._sell_price(px_close)
            proceeds = fill * pos["qty"]
            cash += proceeds - self._fee(proceeds)
            pnl = (fill - pos["avg"]) * pos["qty"]
            realized.append(pnl)
            trade_log.append({"date": date, "side": "sell", "symbol": symbol,
                              "qty": pos["qty"], "price": fill, "pnl": pnl,
                              "reason": reason})

        def buy(symbol: str, date, px_close: float, dollars: float):
            nonlocal cash
            fill = self._buy_price(px_close)
            budget = min(dollars, cash)
            qty = budget / fill if self.fractional else float(int(budget // fill))
            if qty <= 0:
                return
            cost = fill * qty
            if cost > cash + 1e-9:
                return
            cash -= cost + self._fee(cost)
            positions[symbol] = {"qty": qty, "avg": fill}
            trade_log.append({"date": date, "side": "buy", "symbol": symbol,
                              "qty": qty, "price": fill, "pnl": 0.0,
                              "reason": "rebalance buy"})

        for i, date in enumerate(dates):
            px = self.prices.loc[date]

            if i < self.lookback:
                equity_curve.append(self.starting_cash)
                continue

            # --- daily stop-loss (checked every day, not just on rebalance) ---
            for symbol in list(positions):
                pos = positions[symbol]
                price = float(px[symbol])
                if price / pos["avg"] - 1 <= -self.stop_loss_pct / 100:
                    sell(symbol, date, price, f"stop-loss {self.stop_loss_pct:.0f}%")

            # --- rebalance on cadence ---
            due = last_rebalance is None or \
                (date - last_rebalance).days >= self.rebalance_days
            if due:
                hist = self.prices.loc[:date]
                scores: dict[str, float] = {}
                for symbol in self.prices.columns:
                    s = self.score_fn(hist[symbol].dropna())
                    if s is not None:
                        scores[symbol] = s

                if scores:
                    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
                    targets = {sym for sym, sc in ranked[:self.max_positions]
                               if sc >= self.buy_threshold}

                    # Sell holdings that fell out of favor.
                    for symbol in list(positions):
                        sc = scores.get(symbol)
                        if symbol not in targets or (sc is not None and sc < self.sell_threshold):
                            sell(symbol, date, float(px[symbol]), "rebalance exit")

                    # Buy new targets, equal-weight of current equity.
                    equity_now = cash + sum(
                        positions[s]["qty"] * float(px[s]) for s in positions)
                    slice_dollars = equity_now / self.max_positions
                    for symbol in targets:
                        if symbol not in positions:
                            buy(symbol, date, float(px[symbol]), slice_dollars)

                last_rebalance = date

            equity = cash + sum(
                positions[s]["qty"] * float(px[s]) for s in positions)
            equity_curve.append(equity)

        curve = pd.Series(equity_curve, index=dates, name="equity")
        return BacktestResult(equity_curve=curve, realized_pnls=realized,
                              trade_log=trade_log)


def buy_and_hold(prices: pd.DataFrame, starting_cash: float = 10_000.0,
                 lookback: int = 31) -> pd.Series:
    """
    Benchmark: equal-weight buy-and-hold of the whole universe, entered on the
    first tradable bar (after `lookback` so it lines up with the strategy).
    """
    dates = prices.index
    entry_date = dates[lookback]
    entry_px = prices.loc[entry_date]
    per_name = starting_cash / len(prices.columns)
    qty = {sym: per_name / float(entry_px[sym]) for sym in prices.columns}

    curve = []
    for i, date in enumerate(dates):
        if i < lookback:
            curve.append(starting_cash)
        else:
            px = prices.loc[date]
            curve.append(sum(qty[s] * float(px[s]) for s in prices.columns))
    return pd.Series(curve, index=dates, name="buy_and_hold")
