"""
Comparison: name the trade-offs, never pick a winner for the reader.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.comparison import (  # noqa: E402
    MAX_COMPANIES, MIN_MEANINGFUL_GAP, compare,
)
from services.scoring import score_company  # noqa: E402

PROFITABLE = {
    "returnOnEquity": 0.34, "profitMargins": 0.26, "operatingMargins": 0.31,
    "revenueGrowth": 0.06, "earningsGrowth": 0.05,
    "trailingPE": 14.0, "forwardPE": 12.0, "priceToSalesTrailing12Months": 2.5,
    "debtToEquity": 25.0, "currentRatio": 2.5, "freeCashflow": 3_000_000_000.0,
    "chg_1w": 1.0, "chg_1m": 2.0, "chg_3m": 4.0, "vs_sma50": 1.0,
}
FAST_GROWING = {
    "returnOnEquity": 0.10, "profitMargins": 0.06, "operatingMargins": 0.08,
    "revenueGrowth": 0.45, "earningsGrowth": 0.48,
    "trailingPE": 38.0, "forwardPE": 33.0, "priceToSalesTrailing12Months": 13.0,
    "debtToEquity": 90.0, "currentRatio": 1.4, "freeCashflow": 400_000_000.0,
    "chg_1w": 6.0, "chg_1m": 15.0, "chg_3m": 28.0, "vs_sma50": 9.0,
}


def _cmp(*datasets, sectors=None):
    sectors = sectors or [None] * len(datasets)
    scores = [score_company(f"T{i}", d, s)
              for i, (d, s) in enumerate(zip(datasets, sectors))]
    return compare(scores)


# ── Shape ────────────────────────────────────────────────────────────────────

def test_compares_two_companies():
    result = _cmp(PROFITABLE, FAST_GROWING)
    assert len(result.tickers) == 2
    assert len(result.rows) == 5


@pytest.mark.parametrize("n", [2, 3, 4])
def test_accepts_two_to_four(n):
    assert len(_cmp(*([PROFITABLE] * n)).tickers) == n


@pytest.mark.parametrize("n", [1, 5])
def test_rejects_out_of_range(n):
    with pytest.raises(ValueError):
        _cmp(*([PROFITABLE] * n))


def test_every_category_appears():
    keys = {r.key for r in _cmp(PROFITABLE, FAST_GROWING).rows}
    assert keys == {"quality", "growth", "valuation", "financial_health", "momentum"}


# ── It names trade-offs, not winners ─────────────────────────────────────────

def test_identifies_each_company_s_strength():
    result = _cmp(PROFITABLE, FAST_GROWING)
    by_key = {r.key: r for r in result.rows}
    assert by_key["quality"].leader == "T0", "the profitable company leads on quality"
    assert by_key["growth"].leader == "T1", "the fast grower leads on growth"


def test_summary_describes_the_trade_off():
    summary = _cmp(PROFITABLE, FAST_GROWING).summary.lower()
    assert "more profitable" in summary
    assert "growing faster" in summary
    assert "depends on what you are looking for" in summary


def test_summary_never_recommends():
    summary = _cmp(PROFITABLE, FAST_GROWING).summary.lower()
    for banned in (r"\bbuy\b", r"\bsell\b", r"\bbetter\b", r"\bbest\b", r"\bwinner\b",
                   r"\bchoose\b", r"\bprefer\b", r"\bwe recommend\b", r"\byou should\b"):
        assert not re.search(banned, summary), f"summary contains {banned!r}"


def test_close_scores_are_not_separated():
    """A small gap must not be presented as one company leading."""
    result = _cmp(PROFITABLE, PROFITABLE)
    for row in result.rows:
        assert row.leader is None, "identical companies must have no leader"
        assert "too close" in row.note.lower()


def test_gap_must_exceed_the_noise_threshold():
    result = _cmp(PROFITABLE, PROFITABLE)
    assert all(r.spread is None or r.spread < MIN_MEANINGFUL_GAP for r in result.rows)


def test_identical_companies_say_so():
    assert "score similarly" in _cmp(PROFITABLE, PROFITABLE).summary.lower()


# ── Missing data is reported, not papered over ───────────────────────────────

def test_unscoreable_category_is_marked_not_comparable():
    thin = {k: v for k, v in PROFITABLE.items()
            if k not in ("debtToEquity", "currentRatio", "freeCashflow")}
    result = _cmp(PROFITABLE, thin)
    health = next(r for r in result.rows if r.key == "financial_health")
    assert not health.comparable
    assert health.leader is None
    assert "not comparable" in health.note.lower()


def test_low_confidence_is_surfaced_as_a_caveat():
    thin = {k: v for k, v in PROFITABLE.items() if k != "debtToEquity"}
    caveats = " ".join(_cmp(PROFITABLE, thin).caveats).lower()
    assert "confidence" in caveats


def test_withheld_category_is_named_in_caveats():
    thin = {k: v for k, v in PROFITABLE.items()
            if k not in ("debtToEquity", "currentRatio", "freeCashflow")}
    caveats = " ".join(_cmp(PROFITABLE, thin).caveats).lower()
    assert "could not be assessed" in caveats


def test_cross_sector_comparison_is_flagged():
    caveats = " ".join(
        _cmp(PROFITABLE, FAST_GROWING, sectors=["Utilities", "Technology"]).caveats
    ).lower()
    assert "different sectors" in caveats
    assert "not directly comparable" in caveats


def test_same_sector_is_not_flagged():
    caveats = " ".join(
        _cmp(PROFITABLE, FAST_GROWING, sectors=["Technology", "Technology"]).caveats
    ).lower()
    assert "different sectors" not in caveats


def test_unscoreable_company_is_reported():
    scores = [score_company("A", PROFITABLE), score_company("B", {"returnOnEquity": 0.3})]
    result = compare(scores)
    assert result.overall["B"] is None
    assert any("could not be scored overall" in c.lower() for c in result.caveats)


# ── Determinism ──────────────────────────────────────────────────────────────

def test_comparison_is_deterministic():
    a, b = _cmp(PROFITABLE, FAST_GROWING), _cmp(PROFITABLE, FAST_GROWING)
    assert a.summary == b.summary
    assert [r.leader for r in a.rows] == [r.leader for r in b.rows]


def test_order_does_not_change_the_findings():
    forward = _cmp(PROFITABLE, FAST_GROWING)
    reverse = _cmp(FAST_GROWING, PROFITABLE)
    fwd = {r.key: r.spread for r in forward.rows}
    rev = {r.key: r.spread for r in reverse.rows}
    for key in fwd:
        if fwd[key] is None or rev[key] is None:
            continue
        assert abs(fwd[key] - rev[key]) < 1e-9
