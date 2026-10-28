"""
Audit fixes 4–7: cost curves and intraday cost defaults, cash interest with excess-of-cash Sharpe,
per-regime breakdown and walk-forward, ORB stop at the range level.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from quantedge.backtest.costs import INTRADAY_COSTS, ZERO_COSTS, CostModel
from quantedge.backtest.engine import BacktestConfig, default_config, run_signal_backtest
from quantedge.strategies import get_strategy
from test_engine import Scripted, flat_then
from test_intraday import SUMMER_DAYS, session_bars

SYMS = ["AAPL", "MSFT", "NVDA", "JPM", "V", "KO"]


# ── fix 7: ORB stop at the actual range level ──────────────────────────────

def _orb_day(next_open: float):
    closes = [100.0, 100.4, 100.2] + [101.0] + [101.2] * 74          # breakout closes at 101.0 on bar 4
    bars = session_bars(SUMMER_DAYS[0], closes)
    bars.iloc[4, bars.columns.get_loc("open")] = next_open           # fill bar
    bars.iloc[4, bars.columns.get_loc("high")] = max(next_open, 101.3)
    return bars


def test_orb_stop_sits_on_the_range_low_after_a_gap_up():
    s = get_strategy("orb")
    p = s.resolve({"target_r": 0.0, "exit_on_failure": False})
    bars = _orb_day(next_open=101.6)                                 # gaps above the signal close
    or_low = bars["low"].iloc[:3].min()
    r = run_signal_backtest(s, p, {"X": bars}, "US", BacktestConfig(costs=ZERO_COSTS), intraday=True)
    t = r.trades.iloc[0]
    # Stop distance used for sizing is fill − range low (not signal close − range low).
    assert t.entry_price == pytest.approx(101.6)
    risk = 100_000 * 0.01 / (101.6 - or_low)
    assert t.shares == min(math.floor(risk), math.floor(100_000 * 0.20 / 101.6))


def test_orb_skips_entry_when_open_is_below_the_stop():
    s = get_strategy("orb")
    p = s.resolve({"target_r": 0.0, "exit_on_failure": False})
    bars = _orb_day(next_open=99.0)                                  # gaps below the range low
    r = run_signal_backtest(s, p, {"X": bars}, "US", BacktestConfig(costs=ZERO_COSTS), intraday=True)
    assert r.trades.empty


# ── fix 4: costs ───────────────────────────────────────────────────────────

def test_cost_scaling_keeps_stamp_duty_and_intraday_defaults_are_higher():
    c = CostModel(5, 5, 2, 1, 50).scaled(3)
    assert (c.spread_bps, c.slippage_bps, c.commission_bps, c.commission_min, c.uk_stamp_duty_bps) == (15, 15, 6, 3, 50)
    assert default_config(get_strategy("orb")).costs == INTRADAY_COSTS
    assert default_config(get_strategy("ema_pullback")).costs == CostModel()
    assert INTRADAY_COSTS.buy_price(100) - 100 == pytest.approx(2 * (CostModel().buy_price(100) - 100))


def test_runner_records_monotone_cost_curve(isolated_db):
    from quantedge.backtest.runner import run_experiment
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    refresh_daily(SYMS + ["SPY"], years=6)
    exp = run_experiment("ema_pullback", symbols=SYMS, period="train")
    with session_scope() as s:
        m = s.get(Experiment, exp).metrics
    curve = [m["cost_x0_net_return"], m["net_return"], m["cost_x2_net_return"], m["cost_x3_net_return"]]
    assert curve == sorted(curve, reverse=True)
    if m["net_return"] > 0 > m["cost_x3_net_return"]:
        assert 1 <= m["cost_breakeven_multiple"] <= 3


# ── fix 5: cash interest ───────────────────────────────────────────────────

def test_idle_cash_earns_the_rate_and_sharpe_is_above_cash():
    bars = flat_then([100] * 250)
    rate = pd.Series(0.05, index=bars.index)
    r = run_signal_backtest(Scripted(entries=[]), {}, {"X": bars}, "US", BacktestConfig(costs=ZERO_COSTS),
                            cash_rates=rate)
    days = len(bars) - 1
    assert r.equity.iloc[-1] == pytest.approx(100_000 * (1 + 0.05 / 252) ** days)
    assert abs(r.metrics["sharpe"]) < 1e-6                           # cash earning cash: no excess return
    assert r.metrics["avg_cash_rate"] == pytest.approx(0.05)


def test_cash_interest_off_reproduces_old_behaviour():
    bars = flat_then([100] * 50)
    rate = pd.Series(0.05, index=bars.index)
    r = run_signal_backtest(Scripted(entries=[]), {}, {"X": bars}, "US",
                            BacktestConfig(costs=ZERO_COSTS, cash_interest=False), cash_rates=rate)
    assert r.equity.iloc[-1] == pytest.approx(100_000) and "avg_cash_rate" not in r.metrics


def test_rates_modes_and_fred_parsing(tmp_path, monkeypatch):
    from quantedge import rates
    idx = pd.bdate_range("2024-01-01", periods=5)
    monkeypatch.setenv("QE_CASH_RATE", "zero")
    assert rates.annual_rates("US", idx)[0] is None
    monkeypatch.setenv("QE_CASH_RATE", "0.03")
    s, src = rates.annual_rates("US", idx)
    assert (s == 0.03).all() and "constant" in src
    # FRED file with a missing day ("."): parsed from cache, percent → decimal, carried forward.
    monkeypatch.setenv("QE_CASH_RATE", "fred")
    cache = tmp_path / "DTB3.csv"
    cache.write_text("observation_date,DTB3\n2023-12-29,5.20\n2024-01-02,.\n2024-01-03,5.10\n")
    monkeypatch.setattr(rates, "_cache_path", lambda sid: cache)
    monkeypatch.setattr(rates.requests, "get", lambda *a, **k: (_ for _ in ()).throw(rates.requests.ConnectionError()))
    s, src = rates.annual_rates("US", idx)
    assert src == "FRED DTB3"
    assert s.iloc[0] == pytest.approx(0.052) and s.iloc[2] == pytest.approx(0.051)
    cache.unlink()
    assert "unavailable" in rates.annual_rates("US", idx)[1]        # no cache, no network: 0%, and it says so


def test_paper_interest_survives_restarts(isolated_db, monkeypatch):
    monkeypatch.setenv("QE_CASH_RATE", "0.04")
    from test_paper import FUTURE, _rewind, _validated
    from quantedge.data import repository as repo
    from quantedge.data.service import refresh_daily
    from quantedge.paper import create_account, step_account, summary
    refresh_daily(["AAPL", "MSFT", "NVDA", "JPM", "SPY"], years=6)
    exp = _validated()
    a1, a2 = create_account(exp, "a", "human (test)"), create_account(exp, "b", "human (test)")
    dates = repo.load_bars("demo", "AAPL").index
    for a in (a1, a2):
        _rewind(a, dates[-200])
    step_account(a1, now=FUTURE)
    step_account(a2, now=pd.Timestamp(dates[-100]) + pd.Timedelta(days=1))
    step_account(a2, now=FUTURE)
    assert summary(a1)["equity"] == pytest.approx(summary(a2)["equity"], rel=1e-12)


# ── fix 6: regimes and walk-forward ────────────────────────────────────────

def test_regime_labels_are_lagged_and_split_days():
    from quantedge.backtest.regimes import labels, regime_breakdown
    idx = pd.bdate_range("2020-01-01", periods=700)
    up = np.linspace(100, 200, 450)
    down = np.linspace(200, 120, 250)
    bench = pd.Series(np.r_[up, down], index=idx)
    lab = labels(bench)
    sma = bench.rolling(200).mean()
    cross = (bench < sma) & (bench.shift(1) >= sma.shift(1))
    first_down = cross[cross].index[0]
    nxt = idx[idx.get_loc(first_down) + 1]
    assert lab.loc[first_down, "uptrend"] == True and lab.loc[nxt, "uptrend"] == False  # noqa: E712
    eq = bench.iloc[300:] * 1000
    out = regime_breakdown(eq, bench)
    assert 0 < out["regime_downtrend_days_pct"] < 1
    assert out["regime_uptrend_ann"] > 0 > out["regime_downtrend_ann"]


def test_walk_forward_is_out_of_sample_and_avoids_the_test_period(isolated_db):
    from quantedge.backtest.walkforward import run_walk_forward
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import EquityPoint, Experiment
    from quantedge.reports import build_reports
    refresh_daily(SYMS + ["SPY"], years=8)
    exp_id = run_walk_forward("ema_pullback", "fast", [10, 20], symbols=SYMS, train_sessions=300,
                              test_sessions=100)
    with session_scope() as s:
        e = s.get(Experiment, exp_id)
        m = e.metrics
        assert e.period == "walkforward" and e.status == "completed" and not e.used_test_set
        folds = m["folds"]
        assert len(folds) >= 3
        test_start = pd.Timestamp(m["split"]["test_locked_from"])
        for a, b in zip(folds, folds[1:]):
            assert pd.Timestamp(a["out_of_sample"][1]) < pd.Timestamp(b["out_of_sample"][0])
        assert all(pd.Timestamp(f["out_of_sample"][1]) < test_start for f in folds)
        assert all(pd.Timestamp(f["in_sample"][1]) < pd.Timestamp(f["out_of_sample"][0]) for f in folds)
        assert {f["chosen"] for f in folds} <= {10, 20}
        assert all(set(f["in_sample_sharpe"]) == {"10", "20"} for f in folds)   # every candidate on record
        assert m["trials"] >= 2 and "sharpe_ci_low" in m
        assert s.query(EquityPoint).filter_by(experiment_id=exp_id).count() > 200
        row = {c.name: getattr(e, c.name) for c in Experiment.__table__.columns}
    r = build_reports([row])[0]
    assert "walkforward" in r.periods and "walk-forward" in r.summary


def test_walk_forward_rejects_intraday_and_bad_parameters(isolated_db):
    from quantedge.backtest.walkforward import run_walk_forward
    with pytest.raises(ValueError):
        run_walk_forward("orb", "or_minutes", [15, 30], symbols=["AAPL"])
    with pytest.raises(ValueError):
        run_walk_forward("ema_pullback", "fast", [], symbols=["AAPL"])     # one value is a fixed rule (Phase Four)
    with pytest.raises(ValueError):
        run_walk_forward("ema_pullback", "no_such_param", [10, 20], symbols=["AAPL"])


def test_identical_reruns_are_not_extra_trials(isolated_db):
    from quantedge.backtest.runner import run_experiment
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    refresh_daily(SYMS + ["SPY"], years=5)
    ids = [run_experiment("donchian_breakout", symbols=SYMS) for _ in range(2)]
    ids.append(run_experiment("donchian_breakout", {"entry_n": 40}, symbols=SYMS))
    with session_scope() as s:
        assert [s.get(Experiment, i).metrics["trials"] for i in ids] == [1, 1, 2]
