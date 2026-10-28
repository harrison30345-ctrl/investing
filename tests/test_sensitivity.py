"""
Measured factor sensitivity.

The property worth protecting is restraint: a slope fitted through noise still
returns a confident-looking number, so a relationship that explains nothing must
be reported as no relationship rather than as a small one.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services import sensitivity as sv


@pytest.fixture
def dates():
    return pd.bdate_range("2025-01-01", periods=300)


def _walk(dates, returns, start=100.0):
    """Turn a series of daily percentage returns into a price path."""
    return pd.Series(start * np.cumprod(1 + np.asarray(returns) / 100), index=dates)


def test_a_known_beta_is_recovered(dates):
    """A stock built as exactly 2x the index must measure as beta 2."""
    rng = np.random.default_rng(7)
    index_returns = rng.normal(0, 1, len(dates))
    stock_returns = 2 * index_returns

    prices = pd.DataFrame({"TWOX": _walk(dates, stock_returns)})
    factors = pd.DataFrame({"^GSPC": _walk(dates, index_returns)})

    row = next(r for r in sv.sensitivities(prices, factors)["TWOX"]
               if r.factor == "^GSPC")
    assert row.beta == pytest.approx(2.0, abs=0.05)
    assert row.r2 > 0.99
    assert row.measurable


def test_an_unrelated_stock_is_reported_as_not_measurable(dates):
    """Independent noise must not be dressed up as a weak relationship."""
    rng = np.random.default_rng(11)
    prices = pd.DataFrame({"NOISE": _walk(dates, rng.normal(0, 1.5, len(dates)))})
    factors = pd.DataFrame({"^GSPC": _walk(dates, rng.normal(0, 1, len(dates)))})

    row = next(r for r in sv.sensitivities(prices, factors)["NOISE"]
               if r.factor == "^GSPC")
    assert not row.measurable
    assert "No measurable relationship" in sv.describe(row)


def test_negative_relationships_are_described_as_falling_with(dates):
    rng = np.random.default_rng(3)
    index_returns = rng.normal(0, 1, len(dates))
    prices = pd.DataFrame({"SHORT": _walk(dates, -1.5 * index_returns)})
    factors = pd.DataFrame({"^GSPC": _walk(dates, index_returns)})

    row = next(r for r in sv.sensitivities(prices, factors)["SHORT"]
               if r.factor == "^GSPC")
    assert row.beta < 0
    assert row.direction == "falls with"
    assert "falls with" in sv.describe(row)


def test_too_little_history_is_skipped_rather_than_estimated():
    short = pd.bdate_range("2025-01-01", periods=40)
    rng = np.random.default_rng(5)
    prices = pd.DataFrame({"NEW": _walk(short, rng.normal(0, 1, len(short)))})
    factors = pd.DataFrame({"^GSPC": _walk(short, rng.normal(0, 1, len(short)))})
    assert sv.sensitivities(prices, factors) == {}


def test_a_flat_factor_produces_no_row(dates):
    """A factor that never moves has no slope -- it must not report beta 0."""
    rng = np.random.default_rng(13)
    prices = pd.DataFrame({"ANY": _walk(dates, rng.normal(0, 1, len(dates)))})
    factors = pd.DataFrame({"^GSPC": pd.Series(100.0, index=dates)})
    assert sv.sensitivities(prices, factors).get("ANY") in (None, [])


def test_yields_are_measured_in_points_not_percent(dates):
    """A percentage change of a percentage is not an interpretable quantity."""
    rng = np.random.default_rng(17)
    yields = pd.Series(4 + np.cumsum(rng.normal(0, 0.03, len(dates))), index=dates)
    prices = pd.DataFrame({"RATE": _walk(dates, rng.normal(0, 1, len(dates)))})

    rows = sv.sensitivities(prices, pd.DataFrame({"^TNX": yields}))["RATE"]
    row = next(r for r in rows if r.factor == "^TNX")
    assert row.mode == "level"
    assert row.unit == "per 1pp"


def test_rows_are_ordered_by_how_well_they_fit(dates):
    rng = np.random.default_rng(23)
    index_returns = rng.normal(0, 1, len(dates))
    oil_returns = rng.normal(0, 2, len(dates))
    # Driven mostly by the index, barely by oil.
    stock = 1.5 * index_returns + 0.05 * oil_returns

    prices = pd.DataFrame({"MIX": _walk(dates, stock)})
    factors = pd.DataFrame({"^GSPC": _walk(dates, index_returns),
                            "CL=F": _walk(dates, oil_returns)})

    rows = sv.sensitivities(prices, factors)["MIX"]
    assert [r.factor for r in rows][0] == "^GSPC"
    assert rows == sorted(rows, key=lambda r: r.r2, reverse=True)


def test_empty_input_returns_nothing():
    assert sv.sensitivities(pd.DataFrame(), pd.DataFrame()) == {}
    assert sv.sensitivities(None, None) == {}


def test_only_overlapping_dates_are_used():
    """A factor covering half the window must report the overlap, not the whole."""
    rng = np.random.default_rng(29)
    stock_dates = pd.bdate_range("2025-01-01", periods=300)
    factor_dates = stock_dates[-200:]

    index_returns = rng.normal(0, 1, len(stock_dates))
    prices = pd.DataFrame({"PART": _walk(stock_dates, 2 * index_returns)})
    factors = pd.DataFrame({"^GSPC": _walk(factor_dates, index_returns[-200:])})

    row = next(r for r in sv.sensitivities(prices, factors)["PART"]
               if r.factor == "^GSPC")
    assert row.observations <= 200


def test_describe_hedges_in_proportion_to_the_fit():
    """Wording must strengthen with R-squared, and always disclaim mechanism."""
    loose = sv.FactorBeta("X", "^GSPC", "the S&P 500", 1.0, 0.08, 250, "price")
    close = sv.FactorBeta("X", "^GSPC", "the S&P 500", 1.0, 0.50, 250, "price")
    assert "loosely" in sv.describe(loose)
    assert "closely" in sv.describe(close)
    for text in (sv.describe(loose), sv.describe(close)):
        assert "not a mechanism" in text
