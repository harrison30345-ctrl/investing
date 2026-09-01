"""
Scoring engine behaviour: weighting, sectors, versioning, determinism.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.scoring_config import CATEGORY_WEIGHTS, NOT_COMPARABLE, SCORING_VERSION  # noqa: E402
from services.scoring import score_company  # noqa: E402

STRONG = {
    "returnOnEquity": 0.35, "profitMargins": 0.25, "operatingMargins": 0.30,
    "revenueGrowth": 0.35, "earningsGrowth": 0.40,
    "trailingPE": 15.0, "forwardPE": 12.0, "priceToSalesTrailing12Months": 3.0,
    "debtToEquity": 20.0, "currentRatio": 2.8, "freeCashflow": 4_000_000_000.0,
    "chg_1w": 3.0, "chg_1m": 8.0, "chg_3m": 18.0, "vs_sma50": 7.0,
}
WEAK = {
    "returnOnEquity": 0.02, "profitMargins": 0.01, "operatingMargins": 0.02,
    "revenueGrowth": -0.04, "earningsGrowth": -0.08,
    "trailingPE": 38.0, "forwardPE": 34.0, "priceToSalesTrailing12Months": 14.0,
    "debtToEquity": 190.0, "currentRatio": 0.6, "freeCashflow": 10_000_000.0,
    "chg_1w": -8.0, "chg_1m": -13.0, "chg_3m": -22.0, "vs_sma50": -12.0,
}


# ── Basic correctness ────────────────────────────────────────────────────────

def test_weights_sum_to_one():
    assert abs(sum(CATEGORY_WEIGHTS.values()) - 1.0) < 1e-9


def test_strong_company_outscores_weak():
    assert score_company("S", STRONG).overall > score_company("W", WEAK).overall


def test_scores_are_bounded():
    for data in (STRONG, WEAK):
        result = score_company("T", data)
        assert 0.0 <= result.overall <= 100.0
        for cat in result.categories.values():
            assert cat.score is None or 0.0 <= cat.score <= 100.0


def test_all_five_categories_present():
    cats = score_company("T", STRONG).categories
    assert set(cats) == {"quality", "growth", "valuation", "financial_health", "momentum"}


def test_direction_lower_is_better_for_valuation():
    """A lower P/E must score higher than a high one."""
    cheap = score_company("C", {**STRONG, "trailingPE": 8.0})
    dear = score_company("D", {**STRONG, "trailingPE": 38.0})
    assert cheap.categories["valuation"].score > dear.categories["valuation"].score


def test_direction_higher_is_better_for_quality():
    good = score_company("G", {**STRONG, "returnOnEquity": 0.38})
    poor = score_company("P", {**STRONG, "returnOnEquity": 0.01})
    assert good.categories["quality"].score > poor.categories["quality"].score


# ── Determinism and versioning ───────────────────────────────────────────────

def test_same_inputs_produce_same_outputs():
    a, b = score_company("T", STRONG), score_company("T", STRONG)
    assert a.overall == b.overall
    assert a.as_dict() == b.as_dict()


def test_score_carries_methodology_version():
    result = score_company("T", STRONG)
    assert result.version == SCORING_VERSION
    assert result.as_dict()["version"] == SCORING_VERSION


def test_version_is_semver_shaped():
    parts = SCORING_VERSION.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def test_persisted_shape_is_json_safe():
    import json
    payload = score_company("T", STRONG).as_dict()
    assert json.loads(json.dumps(payload)) == payload
    for key in ("ticker", "overall", "coverage", "confidence", "version", "categories"):
        assert key in payload


# ── Sector awareness ─────────────────────────────────────────────────────────

def test_bank_debt_is_excluded_not_penalised():
    """Leverage is intrinsic to banks — it must be excluded, with a reason."""
    bank = score_company("BANK", STRONG, sector="Financial Services")
    health = bank.categories["financial_health"]
    debt = next(m for m in health.metrics if m.field == "debtToEquity")
    assert not debt.available
    assert debt.reason == NOT_COMPARABLE["Financial Services"]["debtToEquity"]


def test_identical_company_scores_differently_by_sector():
    """Sector must actually affect the outcome, not just be displayed."""
    generic = score_company("X", STRONG, sector=None)
    utility = score_company("X", STRONG, sector="Utilities")
    assert generic.categories["financial_health"].coverage != \
           utility.categories["financial_health"].coverage


def test_sector_override_changes_valuation_band():
    """Tech's wider price-to-sales band must score a high multiple differently."""
    data = {**STRONG, "priceToSalesTrailing12Months": 12.0}
    generic = score_company("X", data, sector=None)
    tech = score_company("X", data, sector="Technology")
    assert tech.categories["valuation"].score > generic.categories["valuation"].score


def test_unknown_sector_falls_back_to_generic():
    known = score_company("X", STRONG, sector=None)
    unknown = score_company("X", STRONG, sector="Nonexistent Sector")
    assert unknown.overall == known.overall


# ── Confidence ───────────────────────────────────────────────────────────────

def test_full_data_gives_high_confidence():
    assert score_company("T", STRONG).confidence == "high"


def test_confidence_degrades_with_data_loss():
    seen = []
    for drop in ([], ["debtToEquity"], ["debtToEquity", "currentRatio", "forwardPE"]):
        data = {k: v for k, v in STRONG.items() if k not in drop}
        seen.append(score_company("T", data).coverage)
    assert seen == sorted(seen, reverse=True), "coverage must fall as data is removed"


def test_confidence_band_values_are_known():
    assert score_company("T", STRONG).confidence in {"high", "moderate", "low"}


# ── Category-level reporting ─────────────────────────────────────────────────

def test_missing_metrics_are_reported_by_name():
    data = {k: v for k, v in STRONG.items() if k != "currentRatio"}
    health = score_company("T", data).categories["financial_health"]
    assert "currentRatio" in [m.field for m in health.missing]
    assert all(m.reason for m in health.missing), "every gap needs a stated reason"


def test_metric_display_is_human_readable():
    health = score_company("T", STRONG).categories["financial_health"]
    for metric in health.metrics:
        assert metric.display.endswith("/100") or metric.display == "Unavailable"


@pytest.mark.parametrize("category", list(CATEGORY_WEIGHTS))
def test_each_category_can_be_scored_independently(category):
    result = score_company("T", STRONG)
    assert result.categories[category].score is not None


# ── Overall confidence must be held down by the weakest category ─────────────

def test_withheld_category_caps_overall_confidence():
    """A company missing an entire category cannot claim high confidence."""
    data = {k: v for k, v in STRONG.items()
            if k not in ("debtToEquity", "currentRatio", "freeCashflow")}
    result = score_company("T", data)
    assert result.categories["financial_health"].score is None
    assert result.overall is not None, "other categories still score"
    assert result.confidence == "low", "withheld category must cap confidence"


def test_weak_category_coverage_caps_overall_confidence():
    """High average coverage must not mask one poorly-covered category."""
    data = {k: v for k, v in STRONG.items() if k != "debtToEquity"}
    result = score_company("T", data)
    assert result.coverage > 0.85, "average coverage is still high"
    assert result.confidence != "high", "but one weak category must cap it"
