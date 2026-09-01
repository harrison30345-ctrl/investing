"""
Missing-data guarantees.

These tests exist because of a real defect: the previous implementation coerced
a missing debt-to-equity figure to zero via `or 0`, so a company that simply had
not reported its debt received a PERFECT financial-health score — better than a
company with genuinely low debt.

The property under test is the one users are entitled to rely on: absent data
must never flatter a company.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.scoring_config import METRICS, MIN_COVERAGE  # noqa: E402
from services.scoring import score_company  # noqa: E402

# A complete, healthy company used as the baseline for comparisons.
COMPLETE = {
    "returnOnEquity": 0.30, "profitMargins": 0.22, "operatingMargins": 0.28,
    "revenueGrowth": 0.25, "earningsGrowth": 0.30,
    "trailingPE": 22.0, "forwardPE": 18.0, "priceToSalesTrailing12Months": 6.0,
    "debtToEquity": 40.0, "currentRatio": 2.4, "freeCashflow": 3_000_000_000.0,
    "chg_1w": 2.0, "chg_1m": 6.0, "chg_3m": 12.0, "vs_sma50": 5.0,
}

ALL_FIELDS = [spec["field"] for specs in METRICS.values() for spec in specs]


# ── The original defect ──────────────────────────────────────────────────────

def test_missing_debt_does_not_produce_perfect_health():
    """The exact regression: no debt data must not mean 'no debt'."""
    data = {k: v for k, v in COMPLETE.items() if k != "debtToEquity"}
    health = score_company("TEST", data).categories["financial_health"]
    assert health.score != 100.0, "missing debt data scored a perfect health score"
    assert health.coverage < 1.0
    assert "debtToEquity" in [m.field for m in health.missing]


def test_missing_debt_scores_no_better_than_zero_debt():
    """Unknown debt must never beat the best possible known debt."""
    best = score_company("A", {**COMPLETE, "debtToEquity": 0.0})
    unknown = score_company("B", {k: v for k, v in COMPLETE.items() if k != "debtToEquity"})
    assert unknown.categories["financial_health"].score is not None
    assert unknown.categories["financial_health"].score <= best.categories["financial_health"].score


# ── The general property, across every metric ────────────────────────────────

@pytest.mark.parametrize("field_name", ALL_FIELDS)
def test_removing_any_metric_never_beats_its_best_case(field_name):
    """For every metric: score(missing) <= score(that metric at its best)."""
    spec = next(s for specs in METRICS.values() for s in specs if s["field"] == field_name)
    best_raw = (spec["ceil"] if spec["direction"] == "higher" else spec["floor"])
    best_raw /= spec.get("scale", 1)

    with_best = score_company("A", {**COMPLETE, field_name: best_raw})
    without = score_company("B", {k: v for k, v in COMPLETE.items() if k != field_name})

    if without.overall is None:
        return  # withheld entirely — cannot flatter anyone
    assert without.overall <= with_best.overall + 1e-9, (
        f"removing {field_name} scored higher than its best possible value"
    )


@pytest.mark.parametrize("field_name", ALL_FIELDS)
def test_removing_any_metric_lowers_confidence(field_name):
    """Missing data must always cost coverage, for every metric."""
    full = score_company("A", COMPLETE)
    without = score_company("B", {k: v for k, v in COMPLETE.items() if k != field_name})
    assert without.coverage < full.coverage


# ── Junk values are treated as missing, not as numbers ───────────────────────

@pytest.mark.parametrize("junk", [None, float("nan"), float("inf"), float("-inf"),
                                  "N/A", "", [], {}, True, False])
def test_junk_values_are_unavailable_not_favourable(junk):
    data = {**COMPLETE, "debtToEquity": junk}
    health = score_company("TEST", data).categories["financial_health"]
    assert health.score != 100.0
    metric = next(m for m in health.metrics if m.field == "debtToEquity")
    assert not metric.available, f"{junk!r} was treated as a usable value"


def test_all_data_missing_yields_no_score():
    result = score_company("EMPTY", {})
    assert result.overall is None
    assert result.confidence == "low"
    assert not result.available


def test_sparse_data_is_withheld_not_guessed():
    """Below the coverage floor we publish nothing rather than a weak number."""
    result = score_company("SPARSE", {"returnOnEquity": 0.4})
    assert result.overall is None
    assert result.coverage < MIN_COVERAGE


# ── Zero and negative values are real data, not missing ──────────────────────

def test_zero_is_a_real_value_not_missing():
    """A genuine zero must be scored, unlike an absent field."""
    data = {**COMPLETE, "revenueGrowth": 0.0}
    growth = score_company("TEST", data).categories["growth"]
    metric = next(m for m in growth.metrics if m.field == "revenueGrowth")
    assert metric.available
    assert metric.raw == 0.0


def test_negative_growth_scores_worse_than_positive():
    weak = score_company("W", {**COMPLETE, "revenueGrowth": -0.20})
    strong = score_company("S", {**COMPLETE, "revenueGrowth": 0.40})
    assert weak.categories["growth"].score < strong.categories["growth"].score


def test_extreme_values_are_clamped_not_overflowing():
    for extreme in (1e12, -1e12):
        result = score_company("X", {**COMPLETE, "returnOnEquity": extreme})
        score = result.categories["quality"].score
        assert score is not None and 0.0 <= score <= 100.0
        assert not math.isnan(score)


# ── A complete company must beat an incomplete one, all else equal ───────────

def test_complete_data_beats_partial_data_on_confidence():
    full = score_company("FULL", COMPLETE)
    partial = score_company("PART", {k: v for k, v in COMPLETE.items()
                                     if k not in ("debtToEquity", "currentRatio")})
    assert full.confidence == "high"
    assert partial.coverage < full.coverage
