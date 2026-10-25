"""
Backtest engine behaviour: fill timing, sizing, stops, targets, time stops,
costs and end-of-period liquidation, all on hand-built price series where
the right answer can be worked out by hand.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import make_bars, random_walk
from quantedge.backtest.costs import ZERO_COSTS, CostModel
from quantedge.backtest.engine import BacktestConfig, run_signal_backtest
from quantedge.strategies.base import SignalStrategy


class Scripted(SignalStrategy):
    """Fires an entry on given bar numbers with a fixed stop distance."""
    key, name, family, params = "scripted", "Scripted", "test", []

    def __init__(self, entries=(), exits=(), stop=5.0, target_r=np.nan, max_bars=np.nan):
        self.entries, self.exits = set(entries), set(exits)
        self.stop, self.target_r, self.max_bars = stop, target_r, max_bars

    def entry_rule(self, p): return "scripted"
    def exit_rule(self, p): return "scripted"

    def generate(self, bars, p):
        n = len(bars)
        out = pd.DataFrame(index=bars.index)
        out["entry"] = [i in self.entries for i in range(n)]
        out["exit"] = [i in self.exits for i in range(n)]
        out["stop_dist"] = np.where(out["entry"], self.stop, np.nan)
        out["target_r"] = self.target_r
        out["max_bars"] = self.max_bars
        out["reason"] = "test"
        return out


def cfg(**kw):
    base = dict(initial_capital=100_000, risk_per_trade=0.01, max_position_pct=1.0, max_positions=5, costs=ZERO_COSTS)
    base.update(kw)
    return BacktestConfig(**base)


def flat_then(values, n_flat=5):
    return make_bars([100.0] * n_flat + list(values), spread=0.0)


def test_entry_fills_at_next_open_not_signal_close():
    bars = flat_then([101, 102, 103, 104, 105])
    bars.loc[bars.index[3], "close"] = 100.0
    bars.loc[bars.index[4], "open"] = 100.7      # distinct next open
    r = run_signal_backtest(Scripted(entries=[3]), {}, {"X": bars}, "US", cfg())
    t = r.trades.iloc[0]
    assert t.entry_ts == bars.index[4]
    assert t.entry_price == pytest.approx(100.7)


def test_risk_based_position_size():
    bars = flat_then([100] * 5)
    r = run_signal_backtest(Scripted(entries=[2], stop=5.0), {}, {"X": bars}, "US", cfg())
    # 1% of 100,000 = 1,000 risk / 5 stop distance = 200 shares
    assert r.trades.iloc[0].shares == 200


def test_position_cap_and_cash_limit():
    bars = flat_then([100] * 5)
    r = run_signal_backtest(Scripted(entries=[2], stop=0.5), {}, {"X": bars}, "US", cfg(max_position_pct=0.10))
    # risk sizing wants 2,000 shares; 10% cap allows 10,000 / 100 = 100
    assert r.trades.iloc[0].shares == 100


def test_stop_fills_at_stop_price_when_touched_intraday():
    bars = flat_then([100, 100, 100, 100])
    bars.loc[bars.index[7], "low"] = 94.0          # entry at bar 6 open (100), stop 95
    r = run_signal_backtest(Scripted(entries=[5], stop=5.0), {}, {"X": bars}, "US", cfg())
    t = r.trades.iloc[0]
    assert t.exit_reason == "stop" and t.exit_price == pytest.approx(95.0) and t.exit_ts == bars.index[7]


def test_gap_below_stop_fills_at_open():
    bars = flat_then([100, 100, 100, 100])
    i = bars.index[7]
    bars.loc[i, ["open", "high", "low", "close"]] = [90.0, 91.0, 89.0, 90.5]
    r = run_signal_backtest(Scripted(entries=[5], stop=5.0), {}, {"X": bars}, "US", cfg())
    t = r.trades.iloc[0]
    assert t.exit_reason == "stop_gap" and t.exit_price == pytest.approx(90.0)


def test_target_hit():
    bars = flat_then([100, 100, 100, 100])
    bars.loc[bars.index[7], "high"] = 111.0        # target = 100 + 2R × 5 = 110
    r = run_signal_backtest(Scripted(entries=[5], stop=5.0, target_r=2.0), {}, {"X": bars}, "US", cfg())
    t = r.trades.iloc[0]
    assert t.exit_reason == "target" and t.exit_price == pytest.approx(110.0)


def test_stop_assumed_first_when_bar_hits_both():
    bars = flat_then([100, 100, 100, 100])
    bars.loc[bars.index[7], ["high", "low"]] = [115.0, 90.0]
    r = run_signal_backtest(Scripted(entries=[5], stop=5.0, target_r=2.0), {}, {"X": bars}, "US", cfg())
    assert r.trades.iloc[0].exit_reason == "stop"


def test_exit_signal_fills_next_open():
    bars = flat_then([100, 101, 102, 103, 104])
    r = run_signal_backtest(Scripted(entries=[5], exits=[7]), {}, {"X": bars}, "US", cfg())
    t = r.trades.iloc[0]
    assert t.exit_reason == "exit_signal" and t.exit_ts == bars.index[8]
    assert t.exit_price == pytest.approx(bars["open"].iloc[8])


def test_time_stop():
    bars = flat_then([100] * 10)
    r = run_signal_backtest(Scripted(entries=[5], max_bars=3), {}, {"X": bars}, "US", cfg())
    t = r.trades.iloc[0]
    assert t.exit_reason == "time_stop" and t.bars_held == 3
    assert t.exit_ts == bars.index[6 + 3]


def test_open_positions_liquidated_at_end():
    bars = flat_then([100, 101, 102])
    r = run_signal_backtest(Scripted(entries=[5]), {}, {"X": bars}, "US", cfg())
    assert r.trades.iloc[-1].exit_reason == "end_of_period"
    assert r.exposure.iloc[-1] == 0.0


def test_costs_reduce_pnl_and_stamp_duty_only_on_uk_buys():
    bars = flat_then([100] * 6)
    costs = CostModel(spread_bps=10, slippage_bps=5, commission_bps=0, uk_stamp_duty_bps=50)
    us = run_signal_backtest(Scripted(entries=[5], exits=[7]), {}, {"X": bars}, "US", cfg(costs=costs))
    uk = run_signal_backtest(Scripted(entries=[5], exits=[7]), {}, {"X": bars}, "UK", cfg(costs=costs))
    t_us, t_uk = us.trades.iloc[0], uk.trades.iloc[0]
    assert t_us.entry_price == pytest.approx(100 * (1 + 10e-4))   # half spread 5 + slippage 5 = 10 bps
    assert t_us.exit_price == pytest.approx(100 * (1 - 10e-4))
    assert t_us.net_pnl < 0                                        # flat prices: costs are the only P&L
    stamp = t_uk.shares * t_uk.entry_price * 0.005
    assert t_uk.shares == t_us.shares
    assert t_us.net_pnl - t_uk.net_pnl == pytest.approx(stamp, rel=1e-9)


def test_zero_cost_flat_market_is_break_even():
    bars = flat_then([100] * 6)
    r = run_signal_backtest(Scripted(entries=[5], exits=[7]), {}, {"X": bars}, "US", cfg())
    assert r.trades.iloc[0].net_pnl == pytest.approx(0.0)
    assert r.equity.iloc[-1] == pytest.approx(100_000)


def test_max_positions_respected():
    bars = {f"S{i}": flat_then([100] * 6) for i in range(4)}
    r = run_signal_backtest(Scripted(entries=[5]), {}, bars, "US", cfg(max_positions=2, risk_per_trade=0.001))
    assert r.trades["symbol"].nunique() == 2


def test_equity_accounting_consistent_with_trades():
    bars = random_walk(400, seed=11)
    from quantedge.strategies.trend import EmaPullback
    s = EmaPullback()
    r = run_signal_backtest(s, s.defaults(), {"X": bars}, "US", cfg(costs=CostModel()))
    assert r.equity.iloc[-1] - 100_000 == pytest.approx(r.trades["net_pnl"].sum(), abs=1e-6)
