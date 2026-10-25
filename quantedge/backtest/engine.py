"""
Deterministic daily backtester, long-only, for stocks.

TIMING (the most important rule in this file)
---------------------------------------------
A signal is evaluated on bar t's *close*. The order fills at bar t+1's *open*.
Nothing that happens after bar t can influence a decision made at bar t.

Order of events on each bar:
  1. Open   — fill exit orders, then entry orders queued at the previous close.
  2. Intraday — check stops and targets against the bar's low/high.
               Gap through a stop/target: fill at the open.
               Bar touches both stop and target: assume the stop hit first
               (we can't know the intrabar path, so assume the worse case).
  3. Close  — count bars held, queue time-stop and exit-signal orders,
              queue new entries, then mark equity to the close.

POSITION SIZING
---------------
Fixed-fractional risk: shares = (equity × risk_per_trade) / stop distance,
capped so one position is at most max_position_pct of equity and never
needs more cash than is available. Whole shares only. No leverage, no shorting.

Prices passed in must already be adjusted (dividends/splits) and in the
account currency (pounds, not pence, for UK stocks).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..strategies.base import RebalanceStrategy, SignalStrategy
from ..strategies.indicators import sma
from . import metrics as M
from ..markets import MARKETS
from .costs import CostModel
from .portfolio import SignalPortfolio


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    risk_per_trade: float = 0.01
    max_position_pct: float = 0.20
    max_positions: int = 5
    rebalance_drift: float = 0.25        # momentum: only resize a holding if >25% off target
    costs: CostModel = field(default_factory=CostModel)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["costs"] = self.costs.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "BacktestConfig":
        d = dict(d or {})
        costs = CostModel(**d.pop("costs", {}))
        return cls(costs=costs, **d)


@dataclass
class BacktestResult:
    equity: pd.Series
    exposure: pd.Series
    trades: pd.DataFrame
    benchmark: Optional[pd.Series]
    metrics: Dict[str, Any]
    total_costs: float
    traded_notional: float


TRADE_COLUMNS = ["symbol", "entry_ts", "entry_price", "exit_ts", "exit_price", "shares", "costs",
                 "net_pnl", "return_pct", "bars_held", "entry_reason", "exit_reason"]


def _period_dates(bars: Dict[str, pd.DataFrame], start, end) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(sorted(set().union(*[set(b.index) for b in bars.values()]))) if bars else pd.DatetimeIndex([])
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start)]
    if end is not None:
        idx = idx[idx <= pd.Timestamp(end)]
    return idx


def _benchmark_curve(benchmark: Optional[pd.Series], dates: pd.DatetimeIndex, capital: float) -> Optional[pd.Series]:
    if benchmark is None or benchmark.dropna().empty or len(dates) == 0:
        return None
    b = benchmark.reindex(dates).ffill().dropna()
    if b.empty:
        return None
    return b / b.iloc[0] * capital


def _finish(equity, exposure, trades, benchmark, dates, cfg, costs, notional) -> BacktestResult:
    eq = pd.Series(equity, index=dates[: len(equity)], name="equity")
    ex = pd.Series(exposure, index=eq.index, name="exposure")
    tr = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    bm = _benchmark_curve(benchmark, eq.index, cfg.initial_capital)
    mets = M.compute(eq, tr, exposure=ex, benchmark=bm, traded_notional=notional, total_costs=costs)
    return BacktestResult(eq, ex, tr, bm, mets, costs, notional)


# ─────────────────────────────────────────────────────────────────────────────
# Signal strategies (trend following)
# ─────────────────────────────────────────────────────────────────────────────

def session_ends(index: pd.DatetimeIndex, tz: str) -> pd.Series:
    """True on the last bar of each exchange-local trading day (intraday bars are naive UTC)."""
    local_dates = pd.DatetimeIndex(index).tz_localize("UTC").tz_convert(tz).date
    nxt = pd.Series(local_dates).shift(-1)
    return pd.Series((pd.Series(local_dates) != nxt).values, index=index)


def run_signal_backtest(strategy: SignalStrategy, params: Dict[str, Any], bars: Dict[str, pd.DataFrame],
                        market: str, config: Optional[BacktestConfig] = None, start=None, end=None,
                        benchmark: Optional[pd.Series] = None, intraday: bool = False) -> BacktestResult:
    """
    Drives SignalPortfolio over history. With intraday=True, positions are closed at
    each session's last bar and metrics use the end-of-day equity curve (so Sharpe
    and drawdown are on the same daily footing as the daily strategies).
    """
    cfg = config or BacktestConfig()
    # Signals are computed on the full series (including warm-up before `start`).
    # This is safe because every indicator is causal, and tests enforce it.
    sigs = {s: strategy.generate(b, params) for s, b in bars.items() if not b.empty}
    dates = _period_dates(bars, start, end)
    # Plain dict lookups: pandas .loc per symbol per bar dominated run time.
    bar_rows = {s: b[["open", "high", "low", "close"]].to_dict("index") for s, b in bars.items() if not b.empty}
    sig_rows = {s: g[["entry", "exit", "stop_dist", "target_r", "max_bars", "reason"]].to_dict("index")
                for s, g in sigs.items()}
    ends = session_ends(dates, MARKETS[market].tz) if intraday and len(dates) else None

    pf = SignalPortfolio(cfg, market)
    equity_curve: List[float] = []
    exposure_curve: List[float] = []
    for d in dates:
        today = {s: rows[d] for s, rows in bar_rows.items() if d in rows}
        pf.step(d, today, {s: sig_rows[s][d] for s in today}, session_end=bool(ends[d]) if ends is not None else False)
        equity_curve.append(pf.mark())
        exposure_curve.append(pf.exposure())

    # Liquidate whatever is still open at the last close, so P&L includes exit costs.
    if pf.positions and len(dates):
        pf.liquidate(dates[-1], "end_of_period")
        equity_curve[-1] = pf.cash
        exposure_curve[-1] = 0.0

    if intraday and len(dates):
        # Report on end-of-day equity; exposure is the day's average.
        eq = pd.Series(equity_curve, index=dates)
        ex = pd.Series(exposure_curve, index=dates)
        day = pd.DatetimeIndex(dates).tz_localize("UTC").tz_convert(MARKETS[market].tz).normalize().tz_localize(None)
        eq_d = eq.groupby(day).last()
        ex_d = ex.groupby(day).mean()
        bm_d = None
        if benchmark is not None and not benchmark.dropna().empty:
            # Benchmark = buy-and-hold from the first bar's close; one end-of-day value per session.
            b = benchmark.reindex(dates).ffill().dropna()
            bday = pd.DatetimeIndex(b.index).tz_localize("UTC").tz_convert(MARKETS[market].tz).normalize().tz_localize(None)
            bm_d = b.groupby(bday).last()
        return _finish(list(eq_d.values), list(ex_d.values), pf.trades, bm_d, eq_d.index, cfg,
                       pf.total_costs, pf.notional_traded)
    return _finish(equity_curve, exposure_curve, pf.trades, benchmark, dates, cfg, pf.total_costs,
                   pf.notional_traded)


# ─────────────────────────────────────────────────────────────────────────────
# Rebalance strategies (cross-sectional momentum)
# ─────────────────────────────────────────────────────────────────────────────

def run_rebalance_backtest(strategy: RebalanceStrategy, params: Dict[str, Any], bars: Dict[str, pd.DataFrame],
                           market: str, config: Optional[BacktestConfig] = None, start=None, end=None,
                           benchmark: Optional[pd.Series] = None) -> BacktestResult:
    cfg = config or BacktestConfig()
    cm = cfg.costs
    all_dates = _period_dates(bars, None, None)
    closes = pd.DataFrame({s: b["close"] for s, b in bars.items()}).reindex(all_dates)
    opens = pd.DataFrame({s: b["open"] for s, b in bars.items()}).reindex(all_dates)
    regime = None
    if benchmark is not None and params.get("regime_filter"):
        bm = benchmark.reindex(all_dates).ffill()
        regime = (bm > sma(bm, 200)).fillna(False)
    dates = _period_dates(bars, start, end)
    pos_of = {d: i for i, d in enumerate(all_dates)}
    every = int(params.get("rebalance_days", strategy.rebalance_every))

    cash = cfg.initial_capital
    holdings: Dict[str, dict] = {}
    pending: Optional[Dict[str, float]] = None
    trades: List[dict] = []
    equity_curve: List[float] = []
    exposure_curve: List[float] = []
    total_costs = notional_traded = 0.0
    valuation = closes.ffill()

    def mark(t: int) -> float:
        return cash + sum(h["shares"] * valuation.iloc[t][s] for s, h in holdings.items())

    def sell(sym: str, shares: float, raw: float, ts, reason: str) -> None:
        nonlocal cash, total_costs, notional_traded
        h = holdings[sym]
        fill = cm.sell_price(raw)
        proceeds = shares * fill
        fee = cm.fees(proceeds, "sell", market)
        cash += proceeds - fee
        total_costs += fee + shares * (raw - fill)
        notional_traded += proceeds
        cost_part = h["cost"] * shares / h["shares"]
        h["realised"] += proceeds - fee - cost_part
        h["costs"] += fee + shares * (raw - fill)
        h["cost"] -= cost_part
        h["shares"] -= shares
        h["exit_value"] += proceeds
        h["exit_shares"] += shares
        if h["shares"] <= 1e-9:
            holdings.pop(sym)
            trades.append({
                "symbol": sym, "entry_ts": h["entry_ts"], "entry_price": h["bought"] / h["bought_shares"],
                "exit_ts": ts, "exit_price": h["exit_value"] / h["exit_shares"], "shares": h["bought_shares"],
                "costs": h["costs"], "net_pnl": h["realised"], "return_pct": h["realised"] / h["max_cost"],
                "bars_held": h["bars_held"], "entry_reason": h["reason"], "exit_reason": reason,
            })

    def buy(sym: str, shares: float, raw: float, ts, reason: str) -> None:
        nonlocal cash, total_costs, notional_traded
        fill = cm.buy_price(raw)
        notional = shares * fill
        fee = cm.fees(notional, "buy", market)
        cash -= notional + fee
        total_costs += fee + shares * (fill - raw)
        notional_traded += notional
        h = holdings.setdefault(sym, {"shares": 0.0, "cost": 0.0, "realised": 0.0, "costs": 0.0, "entry_ts": ts,
                                      "bought": 0.0, "bought_shares": 0.0, "exit_value": 0.0, "exit_shares": 0.0,
                                      "max_cost": 0.0, "bars_held": 0, "reason": reason})
        h["shares"] += shares
        h["cost"] += notional + fee
        h["costs"] += fee + shares * (fill - raw)
        h["bought"] += notional
        h["bought_shares"] += shares
        h["max_cost"] = max(h["max_cost"], h["cost"])

    for k, d in enumerate(dates):
        t = pos_of[d]
        if pending is not None:
            eq = mark(t - 1) if t > 0 else cash
            fee_rate = (cm.uk_stamp_duty_bps if market == "UK" else 0) / 1e4 + cm.commission_bps / 1e4
            for sym in list(holdings):                       # sells first
                o = opens.iloc[t].get(sym)
                if pd.isna(o):
                    continue
                if sym not in pending:
                    sell(sym, holdings[sym]["shares"], float(o), d, "dropped_from_ranking")
                else:
                    target = math.floor(pending[sym] * eq / cm.buy_price(float(o)))
                    cur = holdings[sym]["shares"]
                    if target < cur and (cur - target) > cfg.rebalance_drift * target:
                        sell(sym, cur - target, float(o), d, "rebalance_trim")
            for sym, w in sorted(pending.items()):           # then buys
                o = opens.iloc[t].get(sym)
                if pd.isna(o):
                    continue
                fill = cm.buy_price(float(o))
                cur = holdings.get(sym, {}).get("shares", 0.0)
                target = math.floor(w * eq / fill)
                if cur and (target - cur) <= cfg.rebalance_drift * max(cur, 1):
                    continue
                qty = math.floor(min(target - cur, max(cash - cm.commission_min, 0) / (fill * (1 + fee_rate))))
                if qty >= 1:
                    buy(sym, qty, float(o), d, f"Top-{params.get('top_n')} momentum rank")
            pending = None

        for h in holdings.values():
            h["bars_held"] += 1
        if k % every == 0 and t >= strategy.warmup_bars(params):
            pending = strategy.target_weights(closes, t, params, regime)

        eq = mark(t)
        equity_curve.append(eq)
        exposure_curve.append((eq - cash) / eq if eq > 0 else 0.0)

    if holdings and len(dates):
        t = pos_of[dates[-1]]
        for sym in list(holdings):
            sell(sym, holdings[sym]["shares"], float(valuation.iloc[t][sym]), dates[-1], "end_of_period")
        equity_curve[-1] = cash
        exposure_curve[-1] = 0.0

    return _finish(equity_curve, exposure_curve, trades, benchmark, dates, cfg, total_costs, notional_traded)


def run_backtest(strategy, params, bars, market, config=None, start=None, end=None, benchmark=None) -> BacktestResult:
    if isinstance(strategy, RebalanceStrategy):
        return run_rebalance_backtest(strategy, params, bars, market, config, start, end, benchmark)
    return run_signal_backtest(strategy, params, bars, market, config, start, end, benchmark,
                               intraday=strategy.timeframe == "intraday")
