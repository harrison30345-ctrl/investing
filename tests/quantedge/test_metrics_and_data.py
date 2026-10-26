"""Metric definitions, momentum backtest, validation checks, splits and config safety."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from conftest import make_bars
from quantedge.backtest import metrics as M
from quantedge.backtest.splits import chronological_split, walk_forward_windows
from quantedge.data.validation import validate_daily


# ── metrics ────────────────────────────────────────────────────────────────

def test_max_drawdown():
    eq = pd.Series([100, 120, 90, 130, 117.0])
    assert M.max_drawdown(eq) == pytest.approx(90 / 120 - 1)


def test_sharpe_matches_definition():
    r = pd.Series([0.01, -0.005, 0.02, 0.0, 0.003])
    assert M.sharpe(r) == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(252))


def test_trade_stats():
    eq = pd.Series(np.linspace(100, 110, 10), index=pd.bdate_range("2024-01-01", periods=10))
    trades = pd.DataFrame({"net_pnl": [10, -5, 20, -5], "return_pct": [0.1, -0.05, 0.2, -0.05], "bars_held": [1] * 4})
    m = M.compute(eq, trades)
    assert m["win_rate"] == 0.5
    assert m["profit_factor"] == pytest.approx(30 / 10)
    assert m["expectancy"] == pytest.approx(5.0)
    assert m["avg_win"] == 15 and m["avg_loss"] == -5
    assert m["longest_losing_streak"] == 1


def test_profit_factor_without_losers_is_null_not_infinite():
    eq = pd.Series([100.0, 101.0], index=pd.bdate_range("2024-01-01", periods=2))
    m = M.compute(eq, pd.DataFrame({"net_pnl": [1.0], "return_pct": [0.01], "bars_held": [1]}))
    assert m["profit_factor"] is None


# ── momentum ───────────────────────────────────────────────────────────────

def test_momentum_holds_strongest_names():
    from quantedge.backtest.costs import ZERO_COSTS
    from quantedge.backtest.engine import BacktestConfig, run_rebalance_backtest
    from quantedge.strategies.momentum import CrossSectionalMomentum
    n = 400
    up = make_bars(100 * np.exp(np.linspace(0, 1.0, n)))
    flat = make_bars([100.0] * n)
    down = make_bars(100 * np.exp(np.linspace(0, -0.5, n)))
    s = CrossSectionalMomentum()
    p = s.resolve({"lookback": 63, "skip": 0, "top_n": 1})
    r = run_rebalance_backtest(s, p, {"UP": up, "FLAT": flat, "DOWN": down}, "US",
                               BacktestConfig(costs=ZERO_COSTS), start=up.index[100])
    assert set(r.trades["symbol"]) == {"UP"}
    assert r.metrics["net_return"] > 0


# ── validation ─────────────────────────────────────────────────────────────

def _real_calendar_bars(start="2025-03-03", end="2025-05-30"):
    from quantedge.markets import sessions
    idx = sessions("US", start, end)
    df = make_bars(np.linspace(100, 110, len(idx)))
    df.index = idx
    return df


def test_clean_series_passes():
    q = validate_daily(_real_calendar_bars(), "X", "US")
    assert q.ok and not q.issues


def test_detects_duplicates_and_missing_sessions():
    df = _real_calendar_bars()
    df = pd.concat([df, df.iloc[[10]]]).drop(df.index[20:30])
    q = validate_daily(df, "X", "US")
    codes = {i.code: i for i in q.issues}
    assert "duplicates" in codes and codes["missing_sessions"].count == 10 and not q.ok


def test_detects_bad_ohlc_and_jumps_and_staleness():
    from datetime import date
    df = _real_calendar_bars()
    df.iloc[5, df.columns.get_loc("high")] = df.iloc[5]["close"] * 0.5
    df.iloc[30:, df.columns.get_loc("adj_close")] *= 2
    q = validate_daily(df, "X", "US", expected_last=date(2025, 6, 20))
    codes = {i.code for i in q.issues}
    assert {"ohlc_inconsistent", "price_jump", "stale"} <= codes


def test_good_friday_is_not_a_missing_session():
    q = validate_daily(_real_calendar_bars("2025-04-14", "2025-04-22"), "X", "US")
    assert not [i for i in q.issues if i.code == "missing_sessions"]


# ── splits ─────────────────────────────────────────────────────────────────

def test_chronological_split_is_ordered_and_disjoint():
    d = pd.bdate_range("2015-01-01", periods=1000)
    s = chronological_split(d)
    assert s.train[1] < s.validation[0] <= s.validation[1] < s.test[0] <= s.test[1] == d[-1]


def test_walk_forward_windows_never_overlap_out_of_sample():
    d = pd.bdate_range("2015-01-01", periods=1000)
    w = walk_forward_windows(d, 500, 100)
    assert len(w) == 5
    for (ins, oos) in w:
        assert ins[1] < oos[0]


# ── config safety ──────────────────────────────────────────────────────────

def test_live_trading_mode_is_refused(monkeypatch):
    from quantedge.config import ConfigError, load_settings
    monkeypatch.setenv("QE_TRADING_MODE", "live")
    with pytest.raises(ConfigError):
        load_settings()
