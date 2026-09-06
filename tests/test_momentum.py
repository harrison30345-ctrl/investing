"""
Momentum must stay separate from business quality.

The defect this replaces: a single "hot score" blended price change, volume,
RSI and analyst sentiment, with no measure of the business in it at all. A
company with collapsing revenue could top the list purely on price movement,
and nothing distinguished it from a strong business that had also risen.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.momentum import (  # noqa: E402
    MOMENTUM_COMPONENTS, PROFILES, MomentumResult, assess_momentum,
)
from services.scoring import score_company  # noqa: E402

RISING = {"chg_1w": 8.0, "chg_1m": 20.0, "vs_sma20": 10.0, "vol_surge": 2.2}
FLAT = {"chg_1w": 0.0, "chg_1m": 1.0, "vs_sma20": 0.0, "vol_surge": 1.0}

GOOD_BUSINESS = {
    "returnOnEquity": 0.32, "profitMargins": 0.24, "operatingMargins": 0.29,
    "revenueGrowth": 0.28, "earningsGrowth": 0.33,
    "trailingPE": 17.0, "forwardPE": 14.0, "priceToSalesTrailing12Months": 3.5,
    "debtToEquity": 30.0, "currentRatio": 2.3, "freeCashflow": 2_000_000_000.0,
    "chg_1w": 2.0, "chg_1m": 5.0, "chg_3m": 9.0, "vs_sma50": 3.0,
}
BAD_BUSINESS = {
    "returnOnEquity": 0.005, "profitMargins": 0.002, "operatingMargins": 0.004,
    "revenueGrowth": -0.28, "earningsGrowth": -0.35,
    "trailingPE": 39.0, "forwardPE": 38.0, "priceToSalesTrailing12Months": 14.5,
    "debtToEquity": 195.0, "currentRatio": 0.55, "freeCashflow": 5_000.0,
    "chg_1w": 8.0, "chg_1m": 20.0, "chg_3m": 30.0, "vs_sma50": 12.0,
}


# ── The core separation ──────────────────────────────────────────────────────

def test_momentum_ignores_fundamentals_entirely():
    """The same price action must score identically regardless of the business."""
    good = assess_momentum("A", "Good", RISING, score_company("A", GOOD_BUSINESS))
    bad = assess_momentum("B", "Bad", RISING, score_company("B", BAD_BUSINESS))
    assert good.momentum == bad.momentum, "fundamentals leaked into the momentum score"


def test_fundamentals_are_reported_not_blended():
    good = assess_momentum("A", "Good", RISING, score_company("A", GOOD_BUSINESS))
    bad = assess_momentum("B", "Bad", RISING, score_company("B", BAD_BUSINESS))
    assert good.fundamentals > bad.fundamentals, "business quality must still be shown"
    assert good.momentum == bad.momentum


def test_rising_price_on_a_bad_business_is_labelled_as_such():
    """The situation the old score hid: price up, business poor."""
    result = assess_momentum("B", "Bad", RISING, score_company("B", BAD_BUSINESS))
    assert result.profile_key == "momentum_only"
    assert "says nothing about the quality" in result.profile_note


def test_rising_price_on_a_good_business_is_distinguished():
    result = assess_momentum("A", "Good", RISING, score_company("A", GOOD_BUSINESS))
    assert result.profile_key == "momentum_and_quality"
    assert result.profile_label != PROFILES["momentum_only"][0]


def test_the_two_profiles_are_never_confused():
    good = assess_momentum("A", "Good", RISING, score_company("A", GOOD_BUSINESS))
    bad = assess_momentum("B", "Bad", RISING, score_company("B", BAD_BUSINESS))
    assert good.profile_key != bad.profile_key


def test_strong_business_quiet_price_is_its_own_profile():
    result = assess_momentum("A", "Good", FLAT, score_company("A", GOOD_BUSINESS))
    assert result.profile_key == "quality_no_momentum"


def test_no_analyst_opinion_in_momentum():
    """Analyst sentiment was 10% of the old score; it must not be an input."""
    keys = {c["key"] for c in MOMENTUM_COMPONENTS}
    for banned in ("recommendationMean", "analyst_score", "numberOfAnalystOpinions",
                   "targetMeanPrice"):
        assert banned not in keys


def test_no_fundamental_field_in_momentum():
    keys = {c["key"] for c in MOMENTUM_COMPONENTS}
    for banned in ("returnOnEquity", "profitMargins", "revenueGrowth",
                   "trailingPE", "debtToEquity"):
        assert banned not in keys


# ── Unknown fundamentals are stated, not assumed ─────────────────────────────

def test_missing_research_reports_business_as_unknown():
    result = assess_momentum("X", "Unknown", RISING, research=None)
    assert result.fundamentals is None
    assert result.fundamentals_display == "Not assessed"
    assert result.profile_key == "momentum_unknown"
    assert "too little financial data" in result.profile_note


def test_unscoreable_company_is_not_treated_as_weak():
    """No data is not the same as bad data."""
    sparse = score_company("S", {"returnOnEquity": 0.3})
    result = assess_momentum("S", "Sparse", RISING, sparse)
    assert result.fundamentals is None
    assert result.profile_key == "momentum_unknown"
    assert result.profile_key != "momentum_only"


# ── Momentum's own missing data ──────────────────────────────────────────────

@pytest.mark.parametrize("junk", [None, float("nan"), "N/A", True, [], {}])
def test_junk_price_inputs_are_excluded(junk):
    result = assess_momentum("X", "X", {**RISING, "vol_surge": junk})
    assert result.momentum_coverage < 1.0
    unavailable = [c for c in result.components if c["score"] is None]
    assert any(c["label"] == "Volume vs normal" for c in unavailable)


def test_no_price_data_yields_no_momentum_score():
    result = assess_momentum("X", "X", {})
    assert result.momentum is None
    assert result.momentum_coverage == 0.0


def test_partial_price_data_still_scores_within_known_range():
    full = assess_momentum("X", "X", RISING).momentum
    partial = assess_momentum("X", "X", {"chg_1w": 8.0, "chg_1m": 20.0}).momentum
    assert partial is not None and 0.0 <= partial <= 100.0
    assert abs(partial - full) < 40.0


def test_falling_price_scores_lower_than_rising():
    up = assess_momentum("X", "X", RISING).momentum
    down = assess_momentum("X", "X", {"chg_1w": -9.0, "chg_1m": -14.0,
                                      "vs_sma20": -7.0, "vol_surge": 0.7}).momentum
    assert down < up


def test_scores_are_bounded():
    extreme = assess_momentum("X", "X", {"chg_1w": 1e6, "chg_1m": 1e6,
                                         "vs_sma20": 1e6, "vol_surge": 1e6})
    assert 0.0 <= extreme.momentum <= 100.0


# ── Language ─────────────────────────────────────────────────────────────────

def test_profile_text_never_instructs_or_predicts():
    text = " ".join(f"{label} {note}" for label, note in PROFILES.values()).lower()
    for banned in (r"\bbuy\b", r"\bsell\b", r"\bwill rise\b", r"\bwill continue\b",
                   r"\bexpect\b", r"\bopportunity\b", r"\bundervalued\b"):
        assert not re.search(banned, text), f"profile text contains {banned!r}"


def test_module_states_momentum_is_not_a_prediction():
    import services.momentum as m
    doc = (m.__doc__ or "").lower()
    assert "not a prediction" in doc
    assert "not a reason to buy" in doc


def test_every_profile_has_a_label_and_an_explanation():
    for key, (label, note) in PROFILES.items():
        assert label and len(note) > 30, f"{key} needs a real explanation"


def test_result_exposes_version():
    assert assess_momentum("X", "X", RISING).version
