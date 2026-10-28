"""Phase Four engineering: universe bias notes, closure overrides, walk-forward cost curves,
project-wide trial accounting, monthly equal-weight baseline."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import make_bars, random_walk
from quantedge.backtest.costs import ZERO_COSTS, CostModel
from quantedge.backtest.engine import BacktestConfig, run_backtest, universe_monthly_curve
from quantedge.strategies import get_strategy

SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]


# ── 1. universe bias notes ─────────────────────────────────────────────────

def test_universe_kinds_and_notes():
    from quantedge.universe import BIAS_NOTES, universe_kind
    assert universe_kind("us_sectors") == "etf"
    assert universe_kind("us_large") == "current_stocks"
    assert universe_kind(None, ["XLK", "SPY"]) == "etf"                 # inferred: all ETFs
    assert universe_kind(None, ["XLK", "AAPL"]) == "current_stocks"
    etf = BIAS_NOTES["etf"].lower()
    assert "not zero" in etf and "today's large caps" not in etf       # never implies ETFs remove the bias


def test_experiments_record_the_universe_type_note(isolated_db):
    from quantedge.backtest.runner import run_experiment
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    from quantedge.universe import BIAS_NOTES
    refresh_daily(SECTORS[:4] + ["SPY"], years=4)
    i = run_experiment("xs_momentum", {"lookback": 63, "top_n": 2, "abs_filter": False}, symbols=SECTORS[:4])
    with session_scope() as s:
        notes = s.get(Experiment, i).bias_notes
    assert BIAS_NOTES["etf"] in notes and BIAS_NOTES["current_stocks"] not in notes


# ── 3. calendar overrides ──────────────────────────────────────────────────

def test_override_removes_the_session_but_raw_calendar_is_unchanged():
    from quantedge.markets import sessions
    d = pd.Timestamp("2025-01-09")
    assert d not in sessions("US", "2025-01-06", "2025-01-10")
    assert d in sessions("US", "2025-01-06", "2025-01-10", overrides=False)
    assert len(sessions("US", "2025-01-06", "2025-01-10")) == 4


def _week_bars(days):
    idx = pd.DatetimeIndex(days)
    c = np.linspace(100, 101, len(idx))
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "adj_close": c, "volume": 1e6}, index=idx)


def test_validator_accepts_the_closure_and_still_flags_real_gaps():
    from quantedge.data.validation import validate_daily
    ok = validate_daily(_week_bars(["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-10"]), "SPY", "US")
    codes = {i.code: i for i in ok.issues}
    assert "missing_sessions" not in codes and ok.ok
    assert codes["closure_overrides"].severity == "info" and "Carter" in codes["closure_overrides"].message
    gap = validate_daily(_week_bars(["2025-01-06", "2025-01-07", "2025-01-10"]), "SPY", "US")
    assert {i.code: i for i in gap.issues}["missing_sessions"].examples == ["2025-01-08"]


def test_a_bar_on_an_override_closure_is_an_error_and_never_traded():
    from quantedge.backtest.runner import _prepare
    from quantedge.data.validation import validate_daily
    bad = _week_bars(["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"])
    q = validate_daily(bad, "SPY", "US")
    assert not q.ok and "bar_on_override_closure" in {i.code for i in q.issues}
    assert pd.Timestamp("2025-01-09") not in _prepare(bad, "SPY").index


def test_engine_has_no_equity_point_or_fill_on_a_closed_day():
    idx = pd.bdate_range("2024-10-01", "2025-02-28")
    idx = idx[idx != pd.Timestamp("2025-01-09")]
    bars = {s: make_bars(random_walk(len(idx), seed=k)["close"].values, start="2024-10-01").set_axis(idx)
            for k, s in enumerate(["A", "B", "C"])}
    s = get_strategy("xs_momentum")
    p = s.resolve({"lookback": 63, "skip": 0, "top_n": 1, "rebalance_days": 5, "abs_filter": False})
    r = run_backtest(s, p, bars, "US", BacktestConfig(costs=ZERO_COSTS), start=idx[70])
    closed = pd.Timestamp("2025-01-09")
    assert closed not in r.equity.index
    assert closed not in set(pd.to_datetime(r.trades["entry_ts"])) | set(pd.to_datetime(r.trades["exit_ts"]))


# ── 2. walk-forward cost curves ────────────────────────────────────────────

def test_costs_do_not_change_rebalance_signals():
    bars = {s: make_bars(random_walk(400, seed=k)["close"].values) for k, s in enumerate("ABCDE")}
    s = get_strategy("xs_momentum")
    p = s.resolve({"lookback": 63, "skip": 0, "top_n": 2, "rebalance_days": 21, "abs_filter": False})
    start = bars["A"].index[70]
    cheap = run_backtest(s, p, bars, "US", BacktestConfig(costs=ZERO_COSTS), start=start)
    dear = run_backtest(s, p, bars, "US", BacktestConfig(costs=CostModel().scaled(3)), start=start)
    key = lambda r: sorted(zip(r.trades["symbol"], pd.to_datetime(r.trades["entry_ts"])))  # noqa: E731
    assert key(cheap) == key(dear) and len(cheap.trades) > 5
    assert dear.metrics["net_return"] < cheap.metrics["net_return"]


def test_walk_forward_records_a_stitched_cost_curve(isolated_db):
    from quantedge.backtest.walkforward import run_walk_forward
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    refresh_daily(SECTORS[:5] + ["SPY"], years=6)
    i = run_walk_forward("xs_momentum", "lookback", [63, 126], base_params={"top_n": 2, "abs_filter": False},
                         symbols=SECTORS[:5], train_sessions=300, test_sessions=100)
    with session_scope() as s:
        m = s.get(Experiment, i).metrics
    assert m["result_basis"] == "stitched out-of-sample folds" and "baseline costs" in m["cost_curve_basis"]
    nets = [m["cost_x0_net_return"], m["net_return"], m["cost_x2_net_return"], m["cost_x3_net_return"]]
    assert nets == sorted(nets, reverse=True) and nets[0] > nets[-1]
    assert m["cost_x0_trade_count"] == m["cost_x3_trade_count"] == m["trade_count"]   # same trades, dearer fills
    assert "universe_monthly_return" in m


# ── 4. project-wide trial accounting ───────────────────────────────────────

def _e(i, key="xs_momentum", params=None, syms=("A", "B"), period="train", source="phase3", provider="yahoo"):
    return {"id": i, "strategy_key": key, "params": params or {"lookback": 126}, "market": "US",
            "universe": list(syms), "period": period, "source": source, "data_provider": provider}


def test_project_trial_counting_rules():
    from quantedge.trials import project_trials
    exps = [
        _e(1), _e(2, period="validation"),                                   # same config twice: one
        _e(3, syms=("C", "D")),                                              # another universe: counts
        _e(4, params={"lookback": 126, "walk_forward": {"param": "lookback", "values": [126, 252]}},
           period="walkforward"),                                            # adds 252 only
        _e(5, key="xs_reversal", params={"lookback": 5}),                    # another family
        _e(6, params={"lookback": 63}, source="timing"),                     # excluded
        _e(7, params={"lookback": 189}, provider="demo"),                    # excluded
    ]
    pt = project_trials(exps)
    assert (pt.configurations, pt.families, pt.experiments) == (4, 2, 5)
    assert pt.by_strategy == {"xs_momentum": 3, "xs_reversal": 1}
    assert project_trials(exps, upto_id=2).configurations == 1


def test_reports_show_both_counts_and_leave_stored_trials_alone():
    from quantedge.reports import build_reports
    m = {"net_return": 0.1, "benchmark_return": 0.05, "excess_return": 0.05, "sharpe": 0.5, "trials": 2.0,
         "deflated_sharpe": 0.7, "universe_return": 0.08, "excess_vs_universe": 0.02,
         "universe_monthly_return": 0.09, "excess_vs_universe_monthly": 0.01}
    rec = dict(_e(10, syms=("XLK", "XLE")), status="completed", metrics=dict(m), benchmark="SPY", config={},
               bias_notes=["old note"], start_date="2020-01-01", end_date="2021-01-01", created_at="2026-10-28")
    other = dict(_e(3, syms=("AAPL", "MSFT")), status="completed", metrics={})
    r = build_reports([rec], all_experiments=[rec, other])[0]
    titles = {f.title: f.detail for f in r.findings}
    assert "2 distinct configurations" in titles["Project-wide search (context, not a DSR input)"]
    assert "uses 2 tries on these symbols" in titles["Project-wide search (context, not a DSR input)"]
    assert "Secondary baseline (exploratory)" in titles
    assert rec["metrics"]["trials"] == 2.0 and r.universe_kind == "etf"
    assert r.caveats[0] == "old note" and len(r.caveats) == 2          # stored note kept, type note added


# ── 5. monthly equal-weight baseline ───────────────────────────────────────

def test_monthly_baseline_rebalances_to_equal_weight():
    idx = pd.DatetimeIndex(["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02"])
    up = pd.DataFrame({"close": [100.0, 200.0, 200.0, 400.0]}, index=idx)
    flat = pd.DataFrame({"close": [100.0] * 4}, index=idx)
    cfg = BacktestConfig(costs=ZERO_COSTS, initial_capital=1000.0)
    c = universe_monthly_curve({"UP": up, "FLAT": flat}, idx, cfg, "US")
    # Jan 31: 500 + 1000 = 1500. Feb 1 rebalances to 750/750. Feb 2: UP doubles → 1500 + 750 = 2250.
    assert list(c.round(6)) == [1000.0, 1500.0, 1500.0, 2250.0]
    # Buy-and-hold would end at 2000 + 500 = 2500: the two baselines differ, as they should.
    costly = universe_monthly_curve({"UP": up, "FLAT": flat}, idx, BacktestConfig(initial_capital=1000.0), "US")
    assert costly.iloc[-1] < c.iloc[-1]
