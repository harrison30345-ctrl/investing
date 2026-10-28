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
from .portfolio import SignalPortfolio, signal_rows, tie_break_priority


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    risk_per_trade: float = 0.01
    max_position_pct: float = 0.20
    max_positions: int = 5
    rebalance_drift: float = 0.25        # momentum: only resize a holding if >25% off target
    tie_break_seed: int = 0              # which of several same-bar signals fills first when slots are short
    cash_interest: bool = True           # uninvested cash earns the market's short rate (rates.py)
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
    universe: Optional[pd.Series] = None
    universe_monthly: Optional[pd.Series] = None


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


def universe_curve(bars: Dict[str, pd.DataFrame], index: pd.DatetimeIndex, cfg: "BacktestConfig", market: str,
                   intraday: bool = False) -> Optional[pd.Series]:
    """
    Equal-weight buy-and-hold of the experiment's own symbols, bought at the first close of the
    window and held (not rebalanced), with the same entry and exit costs as the strategy.

    It's the honest baseline for a strategy that trades a hand-picked universe. If simply owning
    the same stocks did better, the strategy added nothing. When the universe is today's winners,
    this also shows how much of any "edge" is survivorship.
    """
    if not bars or len(index) == 0:
        return None
    closes = {}
    for sym, b in bars.items():
        c = b["close"].dropna()
        if intraday:
            day = pd.DatetimeIndex(c.index).tz_localize("UTC").tz_convert(MARKETS[market].tz).normalize().tz_localize(None)
            c = c.groupby(day).last()
        c = c.reindex(index).ffill()
        if len(c) and pd.notna(c.iloc[0]):
            closes[sym] = c
    if not closes:
        return None
    cm = cfg.costs
    per = cfg.initial_capital / len(closes)
    fee_rate = ((cm.uk_stamp_duty_bps / 1e4) if market == "UK" else 0.0) + cm.commission_bps / 1e4
    shares = {sym: per / (cm.buy_price(float(c.iloc[0])) * (1 + fee_rate)) for sym, c in closes.items()}
    total = sum(c * shares[sym] for sym, c in closes.items()).astype(float)
    # Selling at the end pays the spread and slippage too, as the strategy's final liquidation does.
    total.iloc[-1] = sum(cm.sell_price(float(c.iloc[-1])) * shares[sym] for sym, c in closes.items())
    return total.rename("universe")


def universe_monthly_curve(bars: Dict[str, pd.DataFrame], index: pd.DatetimeIndex, cfg: "BacktestConfig",
                           market: str) -> Optional[pd.Series]:
    """
    Secondary, exploratory baseline (Phase Four): the same symbols equally weighted, rebalanced back to
    equal weights at the first close of each calendar month. Bought at the first close of the window
    with the same costs as `universe_curve`; each rebalance pays spread, slippage and commission on the
    value traded (and UK stamp duty on purchases); the final value is sold at the bid like the strategy.

    It is reported next to the buy-and-hold baseline, never instead of it: pass/fail criteria that name
    "the equal-weight universe" mean `universe_curve`.
    """
    if not bars or len(index) < 2:
        return None
    closes = pd.DataFrame({s: b["close"].dropna().reindex(index).ffill() for s, b in bars.items()})
    closes = closes.loc[:, closes.iloc[0].notna()]
    if closes.empty:
        return None
    cm = cfg.costs
    side = (cm.spread_bps / 2 + cm.slippage_bps + cm.commission_bps) / 1e4
    stamp = cm.uk_stamp_duty_bps / 1e4 if market == "UK" else 0.0
    n = closes.shape[1]
    first = closes.iloc[0].values
    shares = (cfg.initial_capital / n) / (first * (1 + side + stamp))
    month = pd.DatetimeIndex(index).to_period("M")
    values = []
    for i in range(len(index)):
        px = closes.iloc[i].values
        if i > 0 and month[i] != month[i - 1]:
            value = float((shares * px).sum())
            target = value / n
            buys = np.clip(target - shares * px, 0, None).sum()
            sells = np.clip(shares * px - target, 0, None).sum()
            value -= sells * side + buys * (side + stamp)
            shares = (value / n) / px
        values.append(float((shares * px).sum()))
    values[-1] *= (1 - (cm.spread_bps / 2 + cm.slippage_bps) / 1e4)
    return pd.Series(values, index=index, name="universe_monthly")


def _cash_series(cash_rates: Optional[pd.Series], cfg: "BacktestConfig") -> Optional[pd.Series]:
    return cash_rates if (cash_rates is not None and getattr(cfg, "cash_interest", True)) else None


def _rates_lookup(cash_rates: Optional[pd.Series], cfg: "BacktestConfig"):
    """date → annual rate (0 when interest is off or no series)."""
    s = _cash_series(cash_rates, cfg)
    if s is None or s.empty:
        return lambda d: 0.0
    by_day = s[~s.index.duplicated(keep="last")]
    by_day = by_day.groupby(pd.DatetimeIndex(by_day.index).normalize()).last().to_dict()
    return lambda d: float(by_day.get(pd.Timestamp(d).normalize(), 0.0))


def default_config(strategy) -> "BacktestConfig":
    """Defaults by timeframe: intraday gets higher spread/slippage (costs.INTRADAY_COSTS)."""
    from .costs import INTRADAY_COSTS
    if getattr(strategy, "timeframe", "daily") == "intraday":
        return BacktestConfig(costs=INTRADAY_COSTS)
    return BacktestConfig()


def _daily_rf(cash_rates: Optional[pd.Series], index: pd.DatetimeIndex) -> Optional[pd.Series]:
    """Per-day cash return aligned to the equity index (annual rate / 252)."""
    if cash_rates is None or cash_rates.empty:
        return None
    r = cash_rates[~cash_rates.index.duplicated(keep="last")]
    by_day = r.groupby(pd.DatetimeIndex(r.index).normalize()).last()
    return (by_day.reindex(pd.DatetimeIndex(index).normalize()).ffill().fillna(0.0) / 252).set_axis(index)


def _finish(equity, exposure, trades, benchmark, dates, cfg, costs, notional, universe=None,
            skipped_capacity: int = 0, cash_rates: Optional[pd.Series] = None, interest: float = 0.0,
            regime_benchmark: Optional[pd.Series] = None,
            universe_monthly: Optional[pd.Series] = None) -> BacktestResult:
    from . import stats as S
    eq = pd.Series(equity, index=dates[: len(equity)], name="equity")
    ex = pd.Series(exposure, index=eq.index, name="exposure")
    tr = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    bm = _benchmark_curve(benchmark, eq.index, cfg.initial_capital)
    rf = _daily_rf(cash_rates, eq.index)
    mets = M.compute(eq, tr, exposure=ex, benchmark=bm, traded_notional=notional, total_costs=costs, rf=rf)
    if rf is not None:
        mets["avg_cash_rate"] = float(rf.mean() * 252)
        mets["interest_earned_pct"] = interest / cfg.initial_capital
        # Cash benchmark: 100% in cash at the short rate over the same window (Phase Two, benchmark 3).
        mets["cash_return"] = float((1 + rf.iloc[1:]).prod() - 1)
        mets["excess_vs_cash"] = mets["net_return"] - mets["cash_return"]
    uni = universe.reindex(eq.index).ffill() if universe is not None else None
    if uni is not None and uni.dropna().size > 1:
        mets["universe_return"] = float(uni.iloc[-1] / uni.iloc[0] - 1)
        mets["universe_sharpe"] = M.sharpe(uni.pct_change(), rf)
        mets["universe_max_drawdown"] = M.max_drawdown(uni)
        mets["excess_vs_universe"] = mets["net_return"] - mets["universe_return"]
    um = universe_monthly.reindex(eq.index).ffill() if universe_monthly is not None else None
    if um is not None and um.dropna().size > 1:
        # Secondary baseline: reported only; no verdict or criterion uses it.
        mets["universe_monthly_return"] = float(um.iloc[-1] / um.iloc[0] - 1)
        mets["universe_monthly_sharpe"] = M.sharpe(um.pct_change(), rf)
        mets["universe_monthly_max_drawdown"] = M.max_drawdown(um)
        mets["excess_vs_universe_monthly"] = mets["net_return"] - mets["universe_monthly_return"]
    rf_v = rf.values[1:] if rf is not None else 0.0
    daily = eq.pct_change().fillna(0.0).values[1:]
    bdaily = bm.reindex(eq.index).ffill().pct_change().fillna(0.0).values[1:] if bm is not None else None
    udaily = uni.pct_change().fillna(0.0).values[1:] if uni is not None and uni.dropna().size > 1 else None
    # Sharpe-type statistics use returns above cash; excess-over-benchmark intervals use raw differences.
    mets.update(S.uncertainty(daily - rf_v, bdaily - rf_v if bdaily is not None else None,
                              udaily - rf_v if udaily is not None else None,
                              tr["return_pct"].values if len(tr) else []))
    mets["entries_skipped_capacity"] = float(skipped_capacity)
    from .regimes import regime_breakdown
    mets.update(regime_breakdown(eq, regime_benchmark, rf))
    return BacktestResult(eq, ex, tr, bm, mets, costs, notional, uni, um)


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
                        benchmark: Optional[pd.Series] = None, intraday: bool = False,
                        cash_rates: Optional[pd.Series] = None) -> BacktestResult:
    """
    Drives SignalPortfolio over history. `cash_rates` (annual, by date) pays interest on cash held
    overnight, once per session. With intraday=True, positions are closed at
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
    sig_rows = {s: signal_rows(g) for s, g in sigs.items()}
    ends = session_ends(dates, MARKETS[market].tz) if intraday and len(dates) else None

    pf = SignalPortfolio(cfg, market)
    rates = _rates_lookup(cash_rates, cfg)
    equity_curve: List[float] = []
    exposure_curve: List[float] = []
    prev = None
    for d in dates:
        if not intraday and prev is not None:
            pf.accrue(rates(prev))                       # interest on cash held overnight
        today = {s: rows[d] for s, rows in bar_rows.items() if d in rows}
        end_of_session = bool(ends[d]) if ends is not None else False
        pf.step(d, today, {s: sig_rows[s][d] for s in today}, session_end=end_of_session)
        if intraday and end_of_session:
            pf.accrue(rates(d))
        equity_curve.append(pf.mark())
        exposure_curve.append(pf.exposure())
        prev = d

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
        bm_full = None
        if benchmark is not None and not benchmark.dropna().empty:
            b = benchmark.dropna()
            bm_full = b.groupby(pd.DatetimeIndex(b.index).tz_localize("UTC").tz_convert(MARKETS[market].tz)
                                .normalize().tz_localize(None)).last()
        return _finish(list(eq_d.values), list(ex_d.values), pf.trades, bm_d, eq_d.index, cfg,
                       pf.total_costs, pf.notional_traded,
                       universe_curve(bars, eq_d.index, cfg, market, intraday=True), pf.skipped_capacity,
                       _cash_series(cash_rates, cfg), pf.interest_earned, bm_full)
    idx = dates[: len(equity_curve)]
    return _finish(equity_curve, exposure_curve, pf.trades, benchmark, dates, cfg, pf.total_costs,
                   pf.notional_traded, universe_curve(bars, idx, cfg, market), pf.skipped_capacity,
                   _cash_series(cash_rates, cfg), pf.interest_earned, benchmark,
                   universe_monthly_curve(bars, idx, cfg, market))


# ─────────────────────────────────────────────────────────────────────────────
# Rebalance strategies (cross-sectional momentum)
# ─────────────────────────────────────────────────────────────────────────────

def run_rebalance_backtest(strategy: RebalanceStrategy, params: Dict[str, Any], bars: Dict[str, pd.DataFrame],
                           market: str, config: Optional[BacktestConfig] = None, start=None, end=None,
                           benchmark: Optional[pd.Series] = None,
                           cash_rates: Optional[pd.Series] = None) -> BacktestResult:
    cfg = config or BacktestConfig()
    cm = cfg.costs
    rates = _rates_lookup(cash_rates, cfg)
    interest_earned = 0.0
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
        if k > 0 and cash > 0:                           # interest on cash held overnight
            i = cash * rates(dates[k - 1]) / 252
            cash += i
            interest_earned += i
        if pending is not None:
            eq = mark(t - 1) if t > 0 else cash
            fee_rate = (cm.uk_stamp_duty_bps if market == "UK" else 0) / 1e4 + cm.commission_bps / 1e4
            for sym in list(holdings):                       # sells first
                o = opens.iloc[t].get(sym)
                if pd.isna(o):
                    continue
                if sym not in pending:
                    sell(sym, holdings[sym]["shares"], float(o), d, strategy.exit_label)
                else:
                    target = math.floor(pending[sym] * eq / cm.buy_price(float(o)))
                    cur = holdings[sym]["shares"]
                    if target < cur and (cur - target) > cfg.rebalance_drift * target:
                        sell(sym, cur - target, float(o), d, "rebalance_trim")
            # Then buys, in seeded-hash order (Phase Two): when cash runs short, which name gets the
            # remainder must not depend on the alphabet.
            for sym, w in sorted(pending.items(), key=lambda kv: tie_break_priority(cfg.tie_break_seed, kv[0], d)):
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
                    buy(sym, qty, float(o), d, strategy.entry_label or f"Top-{params.get('top_n')} momentum rank")
            pending = None

        for h in holdings.values():
            h["bars_held"] += 1
        if strategy.rebalance_due(all_dates, t, k, every) and t >= strategy.warmup_bars(params):
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

    return _finish(equity_curve, exposure_curve, trades, benchmark, dates, cfg, total_costs, notional_traded,
                   universe_curve(bars, dates[: len(equity_curve)], cfg, market), 0,
                   _cash_series(cash_rates, cfg), interest_earned, benchmark,
                   universe_monthly_curve(bars, dates[: len(equity_curve)], cfg, market))


def run_backtest(strategy, params, bars, market, config=None, start=None, end=None, benchmark=None,
                 cash_rates=None) -> BacktestResult:
    if isinstance(strategy, RebalanceStrategy):
        return run_rebalance_backtest(strategy, params, bars, market, config, start, end, benchmark, cash_rates)
    return run_signal_backtest(strategy, params, bars, market, config, start, end, benchmark,
                               intraday=strategy.timeframe == "intraday", cash_rates=cash_rates)
