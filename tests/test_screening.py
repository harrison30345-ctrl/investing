"""
Screener presets and filtering.

Every preset must be a documented set of thresholds, must not promise an
outcome, and must state what it cannot tell you. Unknown data never passes a
filter.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.screener_presets import FILTERABLE, PRESETS, PRESETS_BY_KEY  # noqa: E402
from services.screening import apply_preset, screen  # noqa: E402
from services.scoring import score_company  # noqa: E402

EXCELLENT = {
    "returnOnEquity": 0.35, "profitMargins": 0.27, "operatingMargins": 0.32,
    "revenueGrowth": 0.40, "earningsGrowth": 0.45,
    "trailingPE": 12.0, "forwardPE": 10.0, "priceToSalesTrailing12Months": 2.0,
    "debtToEquity": 15.0, "currentRatio": 2.9, "freeCashflow": 5_000_000_000.0,
    "chg_1w": 4.0, "chg_1m": 12.0, "chg_3m": 25.0, "vs_sma50": 8.0,
}
POOR = {
    "returnOnEquity": 0.01, "profitMargins": 0.005, "operatingMargins": 0.01,
    "revenueGrowth": -0.20, "earningsGrowth": -0.30,
    "trailingPE": 39.0, "forwardPE": 37.0, "priceToSalesTrailing12Months": 14.5,
    "debtToEquity": 195.0, "currentRatio": 0.6, "freeCashflow": 1_000.0,
    "chg_1w": -7.0, "chg_1m": -14.0, "chg_3m": -24.0, "vs_sma50": -11.0,
}


def _scores():
    return [score_company("GOOD", EXCELLENT), score_company("BAD", POOR)]


# ── Preset integrity ─────────────────────────────────────────────────────────

def test_every_preset_documents_its_thresholds():
    for p in PRESETS:
        assert p.limits, f"{p.key} has no thresholds"
        assert p.describe() != "no thresholds applied"
        for key in p.limits:
            assert key in FILTERABLE, f"{p.key} filters on unknown measure {key}"


def test_every_preset_states_what_it_cannot_tell_you():
    for p in PRESETS:
        assert len(p.caveat) > 40, f"{p.key} needs a real caveat"
        assert len(p.description) > 30


def test_preset_names_do_not_promise_outcomes():
    text = " ".join(f"{p.label} {p.description}" for p in PRESETS).lower()
    for banned in (r"\bbest\b", r"\bwinner", r"\bbuy\b", r"\bguarantee",
                   r"\bwill rise\b", r"\bbeat the market\b", r"\btop pick"):
        assert not re.search(banned, text), f"preset text contains {banned!r}"


def test_undervalued_preset_does_not_claim_undervaluation():
    """A low multiple is not the same as being underpriced."""
    preset = PRESETS_BY_KEY["potentially_undervalued"]
    assert "does not identify undervalued" in preset.caveat.lower()
    assert "undervalued" not in preset.label.lower()


def test_momentum_preset_warns_it_says_nothing_about_the_business():
    caveat = PRESETS_BY_KEY["strong_momentum"].caveat.lower()
    assert "no information about the quality of the business" in caveat


def test_quality_preset_warns_about_price():
    assert "expensive" in PRESETS_BY_KEY["high_quality"].caveat.lower()


def test_preset_keys_are_unique():
    keys = [p.key for p in PRESETS]
    assert len(keys) == len(set(keys))


# ── Filtering ────────────────────────────────────────────────────────────────

def test_strong_company_passes_and_weak_one_does_not():
    result = screen(_scores(), {"quality": (70, 100)})
    assert [s.ticker for s in result.passed] == ["GOOD"]


def test_reports_which_filter_removed_what():
    result = screen(_scores(), {"quality": (70, 100), "growth": (70, 100)})
    assert result.removed_by
    assert sum(result.removed_by.values()) + len(result.passed) + result.unscorable == result.total


def test_results_are_ordered_by_score():
    scores = [score_company("A", POOR), score_company("B", EXCELLENT)]
    passed = screen(scores, {}).passed
    assert [s.ticker for s in passed] == ["B", "A"]


def test_unknown_category_never_passes_a_filter():
    """A company that could not be scored on debt has not shown it is sound."""
    thin = {k: v for k, v in EXCELLENT.items()
            if k not in ("debtToEquity", "currentRatio", "freeCashflow")}
    scores = [score_company("THIN", thin)]
    assert scores[0].categories["financial_health"].score is None
    result = screen(scores, {"financial_health": (0, 100)})
    assert result.passed == [], "an unscored category must not pass, even on a 0-100 range"


def test_unscoreable_company_is_counted_separately():
    scores = [score_company("EMPTY", {}), score_company("GOOD", EXCELLENT)]
    result = screen(scores, {})
    assert result.unscorable == 1
    assert [s.ticker for s in result.passed] == ["GOOD"]


def test_confidence_floor_removes_low_confidence_companies():
    thin = {k: v for k, v in EXCELLENT.items() if k != "debtToEquity"}
    scores = [score_company("THIN", thin)]
    assert screen(scores, {}, min_confidence="low").passed
    assert screen(scores, {}, min_confidence="high").passed == []


def test_unknown_measure_is_rejected():
    with pytest.raises(ValueError):
        screen(_scores(), {"not_a_measure": (0, 100)})


def test_empty_filter_returns_everything_scoreable():
    result = screen(_scores(), {})
    assert len(result.passed) == 2


def test_pass_rate_is_reported():
    result = screen(_scores(), {"quality": (70, 100)})
    assert result.pass_rate == 0.5


# ── Presets end to end ───────────────────────────────────────────────────────

@pytest.mark.parametrize("key", [p.key for p in PRESETS])
def test_every_preset_runs(key):
    result, preset = apply_preset(_scores(), key)
    assert preset.key == key
    assert result.total == 2
    assert all(s.ticker != "BAD" for s in result.passed), \
        "the deliberately poor company should not pass any preset"


def test_unknown_preset_raises():
    with pytest.raises(ValueError):
        apply_preset(_scores(), "nonexistent")


def test_high_quality_preset_ignores_growth():
    """Its description promises no growth requirement; check that holds."""
    slow = {**EXCELLENT, "revenueGrowth": 0.01, "earningsGrowth": 0.0}
    result, _ = apply_preset([score_company("SLOW", slow)], "high_quality")
    assert [s.ticker for s in result.passed] == ["SLOW"]
