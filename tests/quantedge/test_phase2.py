"""Phase Two support: reversal strategy, calendar holdout, seeded rebalance buy order, cash benchmark."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import make_bars, random_walk
from quantedge.backtest.costs import ZERO_COSTS
from quantedge.backtest.engine import BacktestConfig, run_rebalance_backtest, run_signal_backtest
from quantedge.backtest.runner import holdout_split
from quantedge.strategies import get_strategy
from test_engine import Scripted, flat_then

SYMS = ["AAPL", "MSFT", "NVDA", "JPM", "V", "KO"]


# ── H2 strategy ────────────────────────────────────────────────────────────

def test_reversal_picks_the_biggest_recent_losers():
    s = get_strategy("xs_reversal")
    p = s.resolve({"lookback": 5, "bottom_n": 2})
    idx = pd.bdate_range("2024-01-01", periods=10)
    closes = pd.DataFrame({"UP": np.linspace(100, 110, 10), "FLAT": [100.0] * 10,
                           "DOWN": np.linspace(100, 90, 10), "CRASH": np.linspace(100, 70, 10)}, index=idx)
    assert set(s.target_weights(closes, 9, p)) == {"CRASH", "DOWN"}
    assert all(w == pytest.approx(0.5) for w in s.target_weights(closes, 9, p).values())


def test_reversal_ranking_ignores_the_future():
    s = get_strategy("xs_reversal")
    p = s.resolve({"lookback": 10})
    closes = pd.DataFrame({f"S{i}": random_walk(200, seed=i)["close"] for i in range(6)})
    before = s.ranking(closes, 120, p)
    closes.iloc[121:] *= 7
    pd.testing.assert_series_equal(s.ranking(closes, 120, p), before)


def test_reversal_backtest_rotates_into_losers():
    s = get_strategy("xs_reversal")
    p = s.resolve({"lookback": 5, "bottom_n": 1, "rebalance_days": 5})
    n = 120
    up = make_bars(100 * np.exp(np.linspace(0, 0.3, n)))
    down = make_bars(100 * np.exp(np.linspace(0, -0.3, n)))
    r = run_rebalance_backtest(s, p, {"UP": up, "DOWN": down}, "US", BacktestConfig(costs=ZERO_COSTS),
                               start=up.index[10])
    assert set(r.trades["symbol"]) == {"DOWN"}


# ── calendar holdout ───────────────────────────────────────────────────────

def test_holdout_split_is_75_25_then_locked_calendar_window():
    d = pd.bdate_range("2017-01-02", "2026-10-23")
    sp = holdout_split(d, 100, "2025-02-03")
    dev = d[100:][d[100:] < pd.Timestamp("2025-02-03")]
    assert sp.train[0] == dev[0] and sp.validation[1] == dev[-1] and sp.train[1] < sp.validation[0]
    assert sp.test[0] == pd.Timestamp("2025-02-03") and sp.test[1] == d[-1]
    assert sp.to_dict()["holdout_start"] == "2025-02-03"
    n_train = d.get_loc(sp.train[1]) - d.get_loc(sp.train[0]) + 1
    assert n_train == int(len(dev) * 0.75)


def test_runs_with_holdout_never_touch_it(isolated_db):
    from quantedge.backtest.runner import TestSetLocked, run_experiment
    from quantedge.backtest.walkforward import run_walk_forward
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment, Trade
    refresh_daily(SYMS + ["SPY"], years=8)
    hold = "2025-02-03"
    v = run_experiment("xs_momentum", {"lookback": 126, "abs_filter": False}, symbols=SYMS, period="validation",
                       holdout_start=hold)
    wf = run_walk_forward("xs_reversal", "lookback", [5, 10], symbols=SYMS, train_sessions=300, test_sessions=100,
                          holdout_start=hold)
    with pytest.raises(TestSetLocked):
        run_experiment("xs_momentum", {"lookback": 126, "abs_filter": False}, symbols=SYMS, period="test",
                       holdout_start=hold)
    cut = pd.Timestamp(hold)
    with session_scope() as s:
        for i in (v, wf):
            e = s.get(Experiment, i)
            assert e.end_date < cut and e.metrics["holdout_start"] == hold
            assert all(t.exit_ts < cut for t in s.query(Trade).filter_by(experiment_id=i))
        assert all(pd.Timestamp(f["out_of_sample"][1]) < cut for f in s.get(Experiment, wf).metrics["folds"])
        rows = [{c.name: getattr(x, c.name) for c in Experiment.__table__.columns} for x in s.query(Experiment)]
    from quantedge.reports import config_key
    plain = dict(rows[0], metrics={k: val for k, val in rows[0]["metrics"].items() if k != "holdout_start"})
    assert config_key(rows[0]) != config_key(plain)                  # holdout runs form their own group


# ── seeded rebalance buy order ─────────────────────────────────────────────

def test_rebalance_buy_order_is_seeded_not_alphabetical():
    from quantedge.backtest.costs import CostModel
    s = get_strategy("xs_momentum")
    p = s.resolve({"lookback": 63, "skip": 0, "top_n": 2, "abs_filter": False, "rebalance_days": 63})
    rising = 100 * np.exp(np.linspace(0, 0.5, 120))
    bars = {"AAA": make_bars(rising), "ZZZ": make_bars(rising)}        # identical: a pure tie
    first_bought = set()
    for seed in range(12):
        # A commission leaves the second purchase one share short, so share counts reveal the buy order.
        cfg = BacktestConfig(costs=CostModel(0, 0, 100, 0, 0), tie_break_seed=seed, initial_capital=10_000)
        r = run_rebalance_backtest(s, p, bars, "US", cfg, start=bars["AAA"].index[70])
        shares = r.trades.groupby("symbol")["shares"].max()
        assert shares.nunique() == 2
        first_bought.add(shares.idxmax())
    assert first_bought == {"AAA", "ZZZ"}                           # alphabetical order would always give AAA


# ── cash benchmark ─────────────────────────────────────────────────────────

def test_cash_benchmark_metrics():
    bars = flat_then([100] * 100)
    rate = pd.Series(0.04, index=bars.index)
    r = run_signal_backtest(Scripted(entries=[]), {}, {"X": bars}, "US", BacktestConfig(costs=ZERO_COSTS),
                            cash_rates=rate)
    assert r.metrics["cash_return"] == pytest.approx((1 + 0.04 / 252) ** (len(bars) - 1) - 1)
    assert r.metrics["excess_vs_cash"] == pytest.approx(0.0, abs=1e-12)
