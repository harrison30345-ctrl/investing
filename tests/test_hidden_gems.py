"""
Hidden Gems methodology.

The defect this replaces: the previous implementation blended "hiddenness" into
a weighted average, so a company followed by no analysts scored maximum points
for obscurity and could reach the results on that alone. These tests enforce
that low coverage is a gate, never a substitute for a sound business.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.hidden_gems import (  # noqa: E402
    GEM_GATES, MAX_ANALYSTS, MAX_MARKET_CAP, MIN_QUALITY, assess_gem,
)

# A sound, reasonably priced, thinly covered company: the intended positive.
GEM = {
    "returnOnEquity": 0.24, "profitMargins": 0.18, "operatingMargins": 0.22,
    "revenueGrowth": 0.14, "earningsGrowth": 0.16,
    "trailingPE": 13.0, "forwardPE": 11.0, "priceToSalesTrailing12Months": 1.8,
    "debtToEquity": 30.0, "currentRatio": 2.1, "freeCashflow": 400_000_000.0,
    "chg_1w": 1.0, "chg_1m": 3.0, "chg_3m": 6.0, "vs_sma50": 2.0,
    "numberOfAnalystOpinions": 4, "marketCap": 3_000_000_000.0,
}


def _assess(overrides=None, sector=None, name="Test Co"):
    data = {**GEM, **(overrides or {})}
    return assess_gem("TEST", name, data, sector)


# ── The core fix ─────────────────────────────────────────────────────────────

def test_obscurity_alone_does_not_qualify():
    """A poor business followed by nobody must NOT be a Hidden Gem."""
    junk = _assess({
        "returnOnEquity": 0.005, "profitMargins": 0.002, "operatingMargins": 0.004,
        "revenueGrowth": -0.25, "earningsGrowth": -0.35,
        "trailingPE": 39.0, "forwardPE": 38.0, "priceToSalesTrailing12Months": 14.5,
        "debtToEquity": 195.0, "currentRatio": 0.55, "freeCashflow": 1_000.0,
        "numberOfAnalystOpinions": 0, "marketCap": 50_000_000.0,
    })
    assert not junk.qualifies
    # It should still pass the attention gate -- that is precisely the point:
    # being overlooked is true of it, and counts for nothing on its own.
    assert any(label == "Overlooked" for label, _ in junk.passed)
    assert junk.failed, "a weak business must fail at least one gate"


def test_every_gate_is_necessary():
    """Breaking any single gate must disqualify an otherwise perfect company."""
    assert _assess().qualifies, "baseline company should qualify"
    breakers = {
        "quality": {"returnOnEquity": 0.001, "profitMargins": 0.001, "operatingMargins": 0.001},
        "valuation": {"trailingPE": 39.0, "forwardPE": 38.0,
                      "priceToSalesTrailing12Months": 14.8},
        "growth": {"revenueGrowth": -0.30, "earningsGrowth": -0.40},
        "financial_health": {"debtToEquity": 199.0, "currentRatio": 0.51,
                             "freeCashflow": 1.0},
        "attention": {"numberOfAnalystOpinions": 40, "marketCap": 900_000_000_000.0},
    }
    for gate_key, override in breakers.items():
        result = _assess(override)
        assert not result.qualifies, f"breaking {gate_key} should disqualify"


def test_ranking_uses_research_score_not_obscurity():
    """Between two qualifying companies, the better business ranks higher."""
    # Both must clear every gate; they differ only in business quality and in
    # how obscure they are. The weaker one is deliberately the more obscure.
    better = _assess({"returnOnEquity": 0.34, "profitMargins": 0.26,
                      "numberOfAnalystOpinions": 12})
    worse = _assess({"returnOnEquity": 0.22, "profitMargins": 0.16,
                     "numberOfAnalystOpinions": 0})   # far more obscure
    assert better.qualifies and worse.qualifies
    assert better.rank_score > worse.rank_score, \
        "the more obscure company must not outrank the better business"


# ── Unknown is not the same as passed ────────────────────────────────────────

def test_missing_data_does_not_pass_a_gate():
    """A gate that cannot be evaluated counts as unknown, never as passed."""
    result = _assess({k: None for k in ("debtToEquity", "currentRatio", "freeCashflow")})
    labels_passed = [label for label, _ in result.passed]
    assert "Not distressed" not in labels_passed
    assert not result.qualifies


def test_absent_coverage_data_is_not_evidence_of_low_coverage():
    data = {k: v for k, v in GEM.items()
            if k not in ("numberOfAnalystOpinions", "marketCap")}
    result = assess_gem("TEST", "Test Co", data)
    assert any(label == "Overlooked" for label, _ in result.unknown)
    assert not result.qualifies


def test_sector_withheld_category_does_not_pass_health_gate():
    """A bank's health category is withheld; that is unknown, not healthy."""
    result = _assess(sector="Financial Services")
    assert "Not distressed" not in [label for label, _ in result.passed]
    assert not result.qualifies


# ── Attention gate behaviour ─────────────────────────────────────────────────

@pytest.mark.parametrize("analysts,cap,expected", [
    (2, 500_000_000.0, True),                      # thin coverage, small
    (MAX_ANALYSTS, 900_000_000_000.0, True),       # thin coverage, huge
    (45, 1_000_000_000.0, True),                   # well covered but small
    (45, 900_000_000_000.0, False),                # well covered and huge
])
def test_attention_gate(analysts, cap, expected):
    result = _assess({"numberOfAnalystOpinions": analysts, "marketCap": cap})
    passed = any(label == "Overlooked" for label, _ in result.passed)
    assert passed is expected


def test_mega_cap_with_thin_coverage_still_needs_sound_business():
    result = _assess({"numberOfAnalystOpinions": 3, "marketCap": 800_000_000_000.0,
                      "returnOnEquity": 0.001, "profitMargins": 0.001,
                      "operatingMargins": 0.001})
    assert not result.qualifies


# ── Transparency ─────────────────────────────────────────────────────────────

def test_qualifying_company_explains_every_gate_it_passed():
    result = _assess()
    assert result.qualifies
    assert len(result.passed) == len(GEM_GATES)
    for line in result.why_lines:
        assert "—" in line and len(line) > 12
    assert any("analyst" in line.lower() for line in result.why_lines)


def test_evidence_cites_actual_numbers():
    result = _assess()
    joined = " ".join(result.why_lines)
    assert "/100" in joined, "gate evidence should cite the scores it used"
    assert "4 analysts covering" in joined


def test_failed_gates_are_reported_with_reasons():
    result = _assess({"trailingPE": 39.0, "forwardPE": 38.0,
                      "priceToSalesTrailing12Months": 14.8})
    assert result.failed
    for label, evidence in result.failed:
        assert label and evidence


def test_every_gate_documents_why_it_exists():
    for gate in GEM_GATES:
        assert gate.why and len(gate.why) > 25, f"{gate.key} needs a stated rationale"
        assert gate.label


def test_methodology_does_not_claim_undervaluation():
    """Gate labels must not assert a company is undervalued."""
    text = " ".join(f"{g.label} {g.why}" for g in GEM_GATES).lower()
    for banned in ("undervalued", "bargain", "cheap stock", "will rise", "guaranteed"):
        assert banned not in text, f"methodology claims {banned!r}"


# ── The provider->engine mapper must not resurrect the coercion bug ──────────

def test_mapper_omits_missing_values_rather_than_coercing():
    """The seam where `or 0` caused the original defect. Absent stays absent.

    The mapper itself lives in dashboard.py, which executes Streamlit calls on
    import and so cannot be imported here. What is asserted instead is the
    contract the mapper must uphold: a field the provider did not return must
    reach the engine as absent, and absence must not flatter the score.
    """
    from services.scoring import score_company

    # A provider dict where debt is absent entirely.
    info_missing = {"returnOnEquity": 0.3, "profitMargins": 0.2, "operatingMargins": 0.25,
                    "revenueGrowth": 0.2, "earningsGrowth": 0.2,
                    "trailingPE": 20.0, "forwardPE": 18.0,
                    "priceToSalesTrailing12Months": 5.0,
                    "currentRatio": 2.0, "freeCashflow": 1e9}
    result = score_company("T", info_missing)
    health = result.categories["financial_health"]
    assert health.score != 100.0, "absent debt must not produce a perfect health score"
    assert "debtToEquity" in [m.field for m in health.missing]


@pytest.mark.parametrize("junk", [None, float("nan"), "N/A", True, [], {}])
def test_junk_provider_values_never_qualify_a_company(junk):
    """Malformed provider data must not let a company through a gate."""
    result = _assess({"debtToEquity": junk, "currentRatio": junk, "freeCashflow": junk})
    assert "Not distressed" not in [label for label, _ in result.passed]
    assert not result.qualifies


def test_loosening_coverage_cannot_rescue_a_weak_business():
    """The coverage slider must only affect the attention gate."""
    weak = {"returnOnEquity": 0.001, "profitMargins": 0.001, "operatingMargins": 0.001,
            "numberOfAnalystOpinions": 2, "marketCap": 1e9}
    for max_analysts in (3, 15, 30):
        result = assess_gem("T", "Test", {**GEM, **weak}, max_analysts=max_analysts)
        assert not result.qualifies, f"weak business qualified at max_analysts={max_analysts}"


def test_tightening_coverage_can_exclude_an_otherwise_good_company():
    data = {**GEM, "numberOfAnalystOpinions": 12, "marketCap": 500_000_000_000.0}
    assert assess_gem("T", "Test", data, max_analysts=15).qualifies
    assert not assess_gem("T", "Test", data, max_analysts=5).qualifies
