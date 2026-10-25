"""
Data-leakage tests: a signal at bar t must not change when future bars are removed.

If any indicator used centred windows, negative shifts or full-series
statistics, the truncated and full computations would disagree.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import random_walk
from quantedge.strategies import REGISTRY, SignalStrategy
from quantedge.strategies.indicators import atr, ema, prior_high, prior_low, sma, total_return

SIGNAL_STRATEGIES = [s for s in REGISTRY.values() if isinstance(s, SignalStrategy)]
COLS = ["entry", "exit", "stop_dist"]


@pytest.mark.parametrize("strategy", SIGNAL_STRATEGIES, ids=lambda s: s.key)
def test_signals_do_not_depend_on_future_bars(strategy):
    bars = random_walk(500, seed=7)
    p = strategy.defaults()
    full = strategy.generate(bars, p)
    for cut in (150, 260, 333, 499):
        part = strategy.generate(bars.iloc[:cut], p)
        pd.testing.assert_frame_equal(part[COLS], full[COLS].iloc[:cut], check_dtype=False)


@pytest.mark.parametrize("fn", [lambda s, d: ema(s, 20), lambda s, d: sma(s, 50), lambda s, d: atr(d, 14),
                                lambda s, d: prior_high(s, 20), lambda s, d: prior_low(s, 20),
                                lambda s, d: total_return(s, 126, 21)])
def test_indicators_are_causal(fn):
    bars = random_walk(400, seed=3)
    full = fn(bars["close"], bars)
    part = fn(bars["close"].iloc[:250], bars.iloc[:250])
    np.testing.assert_allclose(part.values, full.iloc[:250].values, equal_nan=True)


def test_prior_high_excludes_today():
    s = pd.Series([1, 2, 3, 10.0])
    assert prior_high(s, 3).iloc[-1] == 3.0


def test_momentum_ranking_ignores_future():
    from quantedge.strategies.momentum import CrossSectionalMomentum
    strat = CrossSectionalMomentum()
    p = strat.resolve({"lookback": 63, "skip": 5})
    closes = pd.DataFrame({f"S{i}": random_walk(300, seed=i)["close"] for i in range(6)})
    r_full = strat.ranking(closes, 200, p)
    closes_cut = closes.iloc[:201].copy()
    r_cut = strat.ranking(closes_cut, 200, p)
    pd.testing.assert_series_equal(r_full, r_cut)
    # Corrupt the future: ranking at t=200 must be unaffected.
    closes.iloc[201:] = closes.iloc[201:] * 50
    pd.testing.assert_series_equal(strat.ranking(closes, 200, p), r_cut)
