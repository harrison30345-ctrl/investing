"""
Ranking reasons and situation words: deterministic, honest, never invented.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.narrative import (  # noqa: E402
    CATEGORY_PHRASES, SITUATION_WORDS, rank_reason, situation,
)
from services.scoring import score_company  # noqa: E402

BASE = {
    "returnOnEquity": 0.30, "profitMargins": 0.22, "operatingMargins": 0.27,
    "revenueGrowth": 0.24, "earningsGrowth": 0.28,
    "trailingPE": 20.0, "forwardPE": 17.0, "priceToSalesTrailing12Months": 5.0,
    "debtToEquity": 40.0, "currentRatio": 2.2, "freeCashflow": 2_000_000_000.0,
    "chg_1w": 2.0, "chg_1m": 6.0, "chg_3m": 12.0, "vs_sma50": 4.0,
}


def _score(**over):
    return score_company("T", {**BASE, **over})


# ── Determinism ──────────────────────────────────────────────────────────────

def test_same_inputs_give_same_words():
    a, b = _score(), _score()
    assert rank_reason(a) == rank_reason(b)
    assert situation(a) == situation(b)


def test_vocabulary_is_closed():
    """Every word shown must come from the table, never generated."""
    allowed = {w for bands in SITUATION_WORDS.values() for _, w in bands} | {"Not assessed"}
    for _label, word, _value in situation(_score()):
        assert word in allowed, f"{word!r} is outside the controlled vocabulary"


@pytest.mark.parametrize("key", list(SITUATION_WORDS))
def test_every_category_has_a_full_band_table(key):
    bands = SITUATION_WORDS[key]
    assert bands[-1][0] == 0, f"{key} has no catch-all band"
    assert bands == sorted(bands, key=lambda b: -b[0]), f"{key} bands out of order"


# ── Reasons are honest ───────────────────────────────────────────────────────

def test_reason_is_short():
    for over in ({}, {"trailingPE": 39.0, "forwardPE": 38.0},
                 {"revenueGrowth": -0.3, "earningsGrowth": -0.3}):
        words = len(rank_reason(_score(**over)).split())
        assert 3 <= words <= 13, f"{words} words is not a one-line reason"


def test_weak_company_is_described_as_weak():
    weak = _score(returnOnEquity=0.005, profitMargins=0.002, operatingMargins=0.004,
                  revenueGrowth=-0.3, earningsGrowth=-0.35, trailingPE=39.0,
                  forwardPE=38.0, priceToSalesTrailing12Months=14.5,
                  debtToEquity=195.0, currentRatio=0.55, freeCashflow=1000.0,
                  chg_1w=-8.0, chg_1m=-14.0, chg_3m=-25.0, vs_sma50=-12.0)
    assert "weak" in rank_reason(weak).lower()


def test_expensive_valuation_is_named_as_a_weakness():
    dear = _score(trailingPE=39.0, forwardPE=38.0, priceToSalesTrailing12Months=14.8)
    assert "expensive valuation" in rank_reason(dear).lower()


def test_reason_names_a_weakness_when_one_exists():
    """A ranked list must not read as a list of endorsements."""
    mixed = _score(trailingPE=38.0, forwardPE=37.0, priceToSalesTrailing12Months=14.0)
    reason = rank_reason(mixed).lower()
    assert any(w in reason for w in ("but", "with", "expensive", "weak", "slow"))


def test_unscoreable_company_says_so():
    assert "too little data" in rank_reason(score_company("X", {})).lower()


# ── No hype, no instruction ──────────────────────────────────────────────────

@pytest.mark.parametrize("over", [{}, {"trailingPE": 8.0}, {"revenueGrowth": 0.6},
                                  {"revenueGrowth": -0.4}])
def test_no_instruction_or_hype(over):
    text = rank_reason(_score(**over)).lower()
    for banned in (r"\bbuy\b", r"\bsell\b", r"\bopportunity\b", r"\bbargain\b",
                   r"\bwill\b", r"\bexcellent\b", r"\bamazing\b", r"\bundervalued\b"):
        assert not re.search(banned, text), f"reason contains {banned!r}"


def test_situation_words_are_not_directional_advice():
    text = " ".join(w for bands in SITUATION_WORDS.values() for _, w in bands).lower()
    for banned in ("buy", "sell", "cheap", "overvalued", "undervalued"):
        assert banned not in text


def test_valuation_vocabulary_reads_the_right_way_round():
    """A high valuation score means priced modestly, not expensive."""
    cheap = _score(trailingPE=7.0, forwardPE=6.0, priceToSalesTrailing12Months=0.6)
    dear = _score(trailingPE=39.0, forwardPE=38.0, priceToSalesTrailing12Months=14.8)
    word_cheap = next(w for l, w, _ in situation(cheap) if l == "Valuation")
    word_dear = next(w for l, w, _ in situation(dear) if l == "Valuation")
    assert word_cheap in ("Inexpensive", "Reasonable")
    assert word_dear == "Expensive"


def test_unavailable_category_is_named_not_guessed():
    thin = score_company("T", {k: v for k, v in BASE.items()
                               if k not in ("debtToEquity", "currentRatio", "freeCashflow")})
    words = dict((l, w) for l, w, _ in situation(thin))
    assert words["Financial Health"] == "Not assessed"


def test_every_category_has_a_phrase_pair():
    for key in SITUATION_WORDS:
        assert key in CATEGORY_PHRASES
        assert len(CATEGORY_PHRASES[key]) == 2
