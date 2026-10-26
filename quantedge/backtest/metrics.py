"""
Performance metrics, computed from an equity curve and a trade list.

Definitions (written out so nothing is ambiguous):
  net_return        equity_end / equity_start - 1, after all costs
  cagr              compound annual growth, using 252 sessions per year
  benchmark_return  same window, buy-and-hold benchmark (total-return ETF)
  excess_return     net_return - benchmark_return
  sharpe            mean(daily return − cash rate) / std(same) * sqrt(252); cash rate from rates.py
                    (0 when cash interest is off or the rate series is unavailable)
  max_drawdown      worst peak-to-trough fall of the equity curve (negative number)
  win_rate          winning trades / closed trades
  profit_factor     gross profit / gross loss (inf if no losers)
  expectancy        mean net P&L per trade, in currency and as % return
  exposure          average fraction of equity held in positions
  turnover          annualised traded notional / average equity
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = 252


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float((equity / equity.cummax() - 1).min())


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


def sharpe(returns: pd.Series, rf: Optional[pd.Series] = None) -> float:
    """Annualised Sharpe of returns above the cash rate `rf` (per-period, aligned); rf=None means 0."""
    r = returns.dropna()
    if rf is not None:
        r = (r - rf.reindex(r.index).fillna(0.0)).dropna()
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * math.sqrt(PERIODS_PER_YEAR))


def longest_losing_streak(pnls: List[float]) -> int:
    best = cur = 0
    for p in pnls:
        cur = cur + 1 if p < 0 else 0
        best = max(best, cur)
    return best


def compute(equity: pd.Series, trades: pd.DataFrame, *, exposure: Optional[pd.Series] = None,
            benchmark: Optional[pd.Series] = None, traded_notional: float = 0.0,
            total_costs: float = 0.0, rf: Optional[pd.Series] = None) -> Dict[str, float]:
    m: Dict[str, float] = {}
    if equity.empty:
        return m
    start, end = float(equity.iloc[0]), float(equity.iloc[-1])
    years = max(len(equity) - 1, 1) / PERIODS_PER_YEAR
    rets = equity.pct_change()
    m["start_equity"], m["end_equity"] = start, end
    m["net_return"] = end / start - 1
    m["cagr"] = (end / start) ** (1 / years) - 1 if end > 0 else -1.0
    m["volatility"] = float(rets.std(ddof=1) * math.sqrt(PERIODS_PER_YEAR)) if len(rets) > 2 else 0.0
    m["sharpe"] = sharpe(rets, rf)
    m["max_drawdown"] = max_drawdown(equity)
    m["sessions"] = float(len(equity))
    m["years"] = years

    if benchmark is not None and benchmark.dropna().size > 1:
        b = benchmark.dropna()
        m["benchmark_return"] = float(b.iloc[-1] / b.iloc[0] - 1)
        m["benchmark_cagr"] = float((b.iloc[-1] / b.iloc[0]) ** (1 / years) - 1)
        m["benchmark_max_drawdown"] = max_drawdown(b)
        m["benchmark_sharpe"] = sharpe(b.pct_change(), rf)
        m["excess_return"] = m["net_return"] - m["benchmark_return"]

    n = int(len(trades))
    m["trade_count"] = float(n)
    if n:
        pnl = trades["net_pnl"].astype(float)
        wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
        m["win_rate"] = len(wins) / n
        m["avg_win"] = float(wins.mean()) if len(wins) else 0.0
        m["avg_loss"] = float(losses.mean()) if len(losses) else 0.0
        m["avg_win_pct"] = float(trades.loc[pnl > 0, "return_pct"].mean()) if len(wins) else 0.0
        m["avg_loss_pct"] = float(trades.loc[pnl <= 0, "return_pct"].mean()) if len(losses) else 0.0
        gl = -losses.sum()
        m["profit_factor"] = float(wins.sum() / gl) if gl > 0 else float("inf")
        m["expectancy"] = float(pnl.mean())
        m["expectancy_pct"] = float(trades["return_pct"].mean())
        m["avg_bars_held"] = float(trades["bars_held"].mean())
        m["longest_losing_streak"] = float(longest_losing_streak(list(pnl)))

    if exposure is not None and len(exposure):
        m["exposure"] = float(exposure.mean())
    avg_eq = float(equity.mean())
    m["turnover"] = traded_notional / avg_eq / years if avg_eq > 0 else 0.0
    m["total_costs"] = total_costs
    m["costs_pct_of_start"] = total_costs / start if start else 0.0
    # JSON (and Postgres) cannot store NaN/inf: a profit factor with no losing trades is reported as None.
    return {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in m.items()}
