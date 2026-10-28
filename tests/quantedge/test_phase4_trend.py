"""Phase Four Part B engineering: month-end schedule, 10-month trend filter, fixed-rule walk-forward,
paired Sharpe-difference interval."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import make_bars, random_walk
from quantedge.backtest.costs import ZERO_COSTS
from quantedge.backtest.engine import BacktestConfig, run_backtest
from quantedge.strategies import get_strategy
from quantedge.strategies.trend_filter import month_end_mask


def test_month_end_mask_uses_trading_dates_and_never_the_last_row():
    idx = pd.DatetimeIndex(["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-29", "2024-03-01", "2024-03-04"])
    assert list(month_end_mask(idx)) == [False, True, False, True, False, False]


def test_only_the_pre_registered_length_is_accepted():
    s = get_strategy("trend_sma10m")
    assert s.resolve({})["sma_months"] == 10
    with pytest.raises(ValueError):
        s.resolve({"sma_months": 12})


def _bars(closes, start="2020-01-01"):
    b = make_bars(np.asarray(closes, float))
    return b.set_axis(pd.bdate_range(start, periods=len(closes)))


def test_signal_ignores_the_future_and_needs_strictly_above():
    s = get_strategy("trend_sma10m")
    p = s.resolve({})
    closes = pd.DataFrame({"SPY": random_walk(600, seed=3)["close"].values},
                          index=pd.bdate_range("2020-01-01", periods=600))
    t = 400
    before = s.target_weights(closes, t, p)
    future = closes.copy()
    future.iloc[t + 1:] *= 5
    assert s.target_weights(future, t, p) == before
    flat = pd.DataFrame({"SPY": [100.0] * 600}, index=closes.index)
    assert s.target_weights(flat, t, p) == {}                        # equal to the average → cash


def test_trades_happen_only_at_the_open_after_a_month_end():
    s = get_strategy("trend_sma10m")
    p = s.resolve({})
    n = 700
    up = 100 * np.exp(np.linspace(0, 0.6, 450))
    down = up[-1] * np.exp(np.linspace(0, -0.5, n - 450))
    bars = {"SPY": _bars(np.r_[up, down])}
    idx = bars["SPY"].index
    r = run_backtest(s, p, bars, "US", BacktestConfig(costs=ZERO_COSTS), start=idx[260])
    assert len(r.trades) >= 1
    mask = month_end_mask(idx)
    first_of_month = set(idx[1:][mask[:-1]])
    entries = pd.to_datetime(r.trades["entry_ts"])
    rule_exits = pd.to_datetime(r.trades.loc[r.trades["exit_reason"] == "below_10m_average", "exit_ts"])
    assert len(rule_exits) == 1 and set(rule_exits) <= first_of_month
    # The first entry comes from the window's opening decision; later ones only after month-ends.
    assert entries.iloc[0] == idx[261] and set(entries.iloc[1:]) <= first_of_month
    assert r.trades["entry_reason"].iloc[0] == "Month-end close above its 10-month average"
    exit_day = rule_exits.iloc[0]
    assert r.exposure.loc[exit_day:].iloc[1:].max() == 0.0             # in cash after the exit


def test_momentum_schedule_is_unchanged_by_the_hook():
    s = get_strategy("xs_momentum")
    p = s.resolve({"lookback": 63, "skip": 0, "top_n": 1, "rebalance_days": 21, "abs_filter": False})
    idx = pd.bdate_range("2020-01-01", periods=300)
    assert [k for k in range(60) if s.rebalance_due(idx, 100 + k, k, 21)] == [0, 21, 42]
    bars = {c: make_bars(random_walk(300, seed=i)["close"].values) for i, c in enumerate("AB")}
    r = run_backtest(s, p, bars, "US", BacktestConfig(costs=ZERO_COSTS), start=bars["A"].index[70])
    assert set(r.trades["entry_reason"]) == {"Top-1 momentum rank"}
    assert set(r.trades["exit_reason"]) <= {"dropped_from_ranking", "end_of_period"}


def test_fixed_rule_walk_forward(isolated_db):
    from quantedge.backtest.walkforward import run_walk_forward
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    refresh_daily(["SPY"], years=8)
    i = run_walk_forward("trend_sma10m", "sma_months", [10], symbols=["SPY"], train_sessions=400,
                         test_sessions=120)
    with session_scope() as s:
        e = s.get(Experiment, i)
        m, params, notes = e.metrics, e.params, e.bias_notes
    assert params["walk_forward"]["fixed_rule"] is True
    assert {f["chosen"] for f in m["folds"]} == {10} and len(m["folds"]) >= 3
    assert any("fixed rule" in n for n in notes)
    assert "sharpe_diff_vs_benchmark" in m and "cost_x3_net_return" in m


def test_paired_sharpe_difference_interval():
    from quantedge.backtest.stats import sharpe_diff_ci
    rng = np.random.default_rng(1)
    b = rng.normal(0.0003, 0.01, 3000)
    pt, lo, hi = sharpe_diff_ci(b, b)
    assert pt == 0 and lo == 0 and hi == 0                           # same days resampled for both
    pt, lo, hi = sharpe_diff_ci(b + 0.0005, b)
    assert pt > 0 and lo > 0
