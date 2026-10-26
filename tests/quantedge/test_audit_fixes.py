"""
Audit fixes 1–3 (docs/quantedge/AUDIT-2026-10-26.md):
  F1 equal-weight universe benchmark, F2 neutral tie-break, F3 uncertainty statistics.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from conftest import make_bars, random_walk
from quantedge.backtest import stats as S
from quantedge.backtest.costs import ZERO_COSTS, CostModel
from quantedge.backtest.engine import BacktestConfig, run_signal_backtest, universe_curve
from quantedge.backtest.portfolio import SignalPortfolio
from test_engine import Scripted, flat_then


# ── F1: universe benchmark ─────────────────────────────────────────────────

def test_universe_curve_is_equal_weight_buy_and_hold():
    up = make_bars(np.linspace(100, 200, 50))
    flat = make_bars([100.0] * 50)
    cfg = BacktestConfig(initial_capital=1000, costs=ZERO_COSTS)
    u = universe_curve({"UP": up, "FLAT": flat}, up.index, cfg, "US")
    assert u.iloc[0] == pytest.approx(1000) and u.iloc[-1] == pytest.approx(1500)   # (2.0 + 1.0) / 2


def test_universe_curve_pays_costs_and_skips_late_listings():
    a = make_bars([100.0] * 30)
    late = make_bars([50.0] * 10, start="2024-02-01")          # no price on the first day of the window
    cfg = BacktestConfig(initial_capital=1000, costs=CostModel(10, 5, 0, 0, 50))
    u_us = universe_curve({"A": a, "LATE": late}, a.index, cfg, "US")
    u_uk = universe_curve({"A": a}, a.index, cfg, "UK")
    assert u_us.iloc[-1] < 1000                                 # spread + slippage both ways
    assert u_uk.iloc[-1] < u_us.iloc[-1]                        # plus stamp duty on the purchase
    assert u_us.iloc[0] == pytest.approx(1000 * 100 / (100 * (1 + 10e-4)))   # only A held


def test_backtests_report_excess_over_universe():
    bars = {f"S{i}": random_walk(400, seed=i) for i in range(4)}
    from quantedge.strategies.trend import EmaPullback
    s = EmaPullback()
    r = run_signal_backtest(s, s.defaults(), bars, "US", BacktestConfig())
    m = r.metrics
    assert m["excess_vs_universe"] == pytest.approx(m["net_return"] - m["universe_return"])
    assert r.universe is not None and len(r.universe) == len(r.equity)


# ── F2: neutral tie-break ──────────────────────────────────────────────────

def _contest(seed: int, names=("AAA", "ZZZ")):
    bars = {n: flat_then([100] * 6) for n in names}
    cfg = BacktestConfig(initial_capital=100_000, max_positions=1, max_position_pct=1.0, costs=ZERO_COSTS,
                         tie_break_seed=seed)
    r = run_signal_backtest(Scripted(entries=[5]), {}, bars, "US", cfg)
    return r.trades["symbol"].iloc[0], r.metrics["entries_skipped_capacity"]


def test_tie_break_is_not_alphabetical_and_is_reproducible():
    winners = [_contest(seed)[0] for seed in range(20)]
    assert set(winners) == {"AAA", "ZZZ"}                       # alphabetical order would always pick AAA
    assert [_contest(seed)[0] for seed in range(20)] == winners  # same seed, same answer
    assert _contest(0)[1] == 1                                   # the loser is counted as a capacity skip


def test_priority_survives_paper_state_round_trip():
    cfg = BacktestConfig(tie_break_seed=7)
    pf = SignalPortfolio(cfg, "US")
    pf.skipped_capacity = 3
    back = SignalPortfolio.from_state(cfg, "US", pf.to_state())
    assert back.skipped_capacity == 3
    assert back.priority("AAPL", "2026-01-02") == pf.priority("AAPL", "2026-01-02")


# ── F3: uncertainty statistics ─────────────────────────────────────────────

def test_sharpe_ci_brackets_estimate_and_is_deterministic():
    rng = np.random.default_rng(1)
    r = rng.normal(0.0005, 0.01, 1500)
    est = r.mean() / r.std(ddof=1) * math.sqrt(252)
    lo, hi = S.sharpe_ci(r)
    assert lo < est < hi and S.sharpe_ci(r) == (lo, hi)
    assert hi - lo > 0.5                                         # ~6 years of daily data is still noisy


def test_short_samples_get_a_real_interval():
    rng = np.random.default_rng(4)
    r = rng.normal(-0.002, 0.01, 24)                             # ~24 intraday sessions
    est = r.mean() / r.std(ddof=1) * math.sqrt(252)
    lo, hi = S.sharpe_ci(r)
    assert lo < est < hi and hi - lo > 2                         # wide, and the estimate sits inside


def test_flat_series_gives_no_interval_instead_of_crashing():
    assert all(math.isnan(v) for v in S.sharpe_ci(np.zeros(30)))


def test_excess_ci_detects_a_real_gap_and_not_a_fake_one():
    rng = np.random.default_rng(2)
    b = rng.normal(0.0004, 0.01, 1500)
    lo, _ = S.excess_ci(b + 0.0008, b)                           # +20%/yr with no extra noise
    assert lo > 0
    lo, hi = S.excess_ci(b + rng.normal(0, 0.01, 1500), b)       # pure noise on top
    assert lo < 0 < hi


def test_psr_and_dsr():
    rng = np.random.default_rng(3)
    good = rng.normal(0.001, 0.01, 1500)
    zero = rng.normal(0.0, 0.01, 1500)
    zero -= zero.mean()
    assert S.probabilistic_sharpe(good) > 0.99
    assert S.probabilistic_sharpe(zero) == pytest.approx(0.5, abs=0.01)
    d1, d10, d100 = (S.deflated_sharpe(good, n) for n in (1, 10, 100))
    assert d1 >= d10 > d100                                       # more tries ⇒ higher bar
    assert S.deflated_sharpe(good, 1) == pytest.approx(S.probabilistic_sharpe(good))


def test_trade_tstat():
    r = np.array([0.02, -0.01, 0.03, 0.01, 0.0, 0.02, -0.02, 0.01, 0.02, 0.01])
    t = S.trade_stats(r)["trade_tstat"]
    assert t == pytest.approx(r.mean() / (r.std(ddof=1) / math.sqrt(len(r))))


# ── runner integration ─────────────────────────────────────────────────────

def test_runner_records_trials_tiebreak_and_universe(isolated_db):
    from quantedge.backtest.runner import run_experiment
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import EquityPoint, Experiment
    syms = ["AAPL", "MSFT", "NVDA", "JPM", "V", "KO", "PG"]
    refresh_daily(syms + ["SPY"], years=6)
    cfg = BacktestConfig(max_positions=2)                         # force slot contention
    first = run_experiment("ema_pullback", symbols=syms, period="train", config=cfg)
    second = run_experiment("ema_pullback", {"fast": 10}, symbols=syms, period="train", config=cfg)
    with session_scope() as s:
        m1, m2 = s.get(Experiment, first).metrics, s.get(Experiment, second).metrics
        assert m1["trials"] == 1 and m2["trials"] == 2
        assert "deflated_sharpe" in m2 and "sharpe_ci_low" in m2 and "universe_return" in m2
        assert m1["entries_skipped_capacity"] > 0 and m1["tiebreak_seeds"] == 8
        assert m1["tiebreak_net_min"] <= m1["net_return"] <= m1["tiebreak_net_max"]
        assert s.query(EquityPoint).filter(EquityPoint.experiment_id == first,
                                           EquityPoint.universe.isnot(None)).count() > 100
