from __future__ import annotations

"""
Performance metrics for a backtest equity curve. All pure functions so they're
easy to eyeball and test.
"""

import math
from dataclasses import dataclass

import pandas as pd


@dataclass
class Performance:
    total_return_pct: float
    cagr_pct: float
    ann_vol_pct: float
    sharpe: float
    max_drawdown_pct: float
    win_rate_pct: float | None
    num_trades: int
    final_equity: float


def compute_performance(equity: pd.Series, *,
                        periods_per_year: int = 365,
                        realized_trade_pnls: list[float] | None = None,
                        ) -> Performance:
    """
    equity: daily equity curve indexed by date.
    periods_per_year: 365 for crypto (trades every day), 252 for stocks.
    realized_trade_pnls: P&L of each closed round-trip, for win rate.
    """
    equity = equity.dropna()
    start_val = float(equity.iloc[0])
    final_val = float(equity.iloc[-1])
    total_return = final_val / start_val - 1

    days = (equity.index[-1] - equity.index[0]).days or 1
    years = days / 365.25
    cagr = (final_val / start_val) ** (1 / years) - 1 if years > 0 else 0.0

    daily_ret = equity.pct_change().dropna()
    std = float(daily_ret.std())
    mean = float(daily_ret.mean())
    ann_vol = std * math.sqrt(periods_per_year)
    sharpe = (mean / std * math.sqrt(periods_per_year)) if std > 0 else 0.0

    # Max drawdown: worst peak-to-trough on the equity curve.
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_dd = float(drawdown.min())

    win_rate = None
    if realized_trade_pnls:
        wins = sum(1 for p in realized_trade_pnls if p > 0)
        win_rate = wins / len(realized_trade_pnls) * 100

    return Performance(
        total_return_pct=total_return * 100,
        cagr_pct=cagr * 100,
        ann_vol_pct=ann_vol * 100,
        sharpe=round(sharpe, 2),
        max_drawdown_pct=max_dd * 100,
        win_rate_pct=win_rate,
        num_trades=len(realized_trade_pnls) if realized_trade_pnls else 0,
        final_equity=final_val,
    )
