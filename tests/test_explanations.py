"""
Explanation engine: never fabricate, never predict, never instruct.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.scoring_config import METRICS  # noqa: E402
from services.explanations import GLOSSARY, explain, format_value  # noqa: E402
from services.scoring import score_company  # noqa: E402

COMPLETE = {
    "returnOnEquity": 0.32, "profitMargins": 0.24, "operatingMargins": 0.29,
    "revenueGrowth": 0.28, "earningsGrowth": 0.33,
    "trailingPE": 19.0, "forwardPE": 16.0, "priceToSalesTrailing12Months": 4.0,
    "debtToEquity": 35.0, "currentRatio": 2.2, "freeCashflow": 2_500_000_000.0,
    "chg_1w": 1.5, "chg_1m": 5.0, "chg_3m": 11.0, "vs_sma50": 4.0,
}


def _explain(data, sector=None, name="Test Company"):
    return explain(score_company("TEST", data, sector), name)


# ── Coverage of the glossary ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "field", sorted({s["field"] for specs in METRICS.values() for s in specs})
)
def test_every_scored_metric_has_a_beginner_explanation(field):
    """No number may be shown to a user without an explanation."""
    assert field in GLOSSARY, f"{field} is scored but has no glossary entry"
    entry = GLOSSARY[field]
    for key in ("label", "means", "matters", "better"):
        assert entry.get(key), f"{field} glossary missing {key!r}"
    assert entry["better"] in ("higher", "lower")


def test_glossary_explanations_are_substantive():
    """An explanation must say more than the label already does."""
    for field, entry in GLOSSARY.items():
        assert len(entry["means"]) > len(entry["label"]) + 25, \
            f"{field}: 'means' is too thin to explain anything"
        assert len(entry["matters"]) > 40, f"{field}: 'matters' is too thin"
        assert entry["means"].strip().endswith("."), f"{field}: 'means' should be a sentence"


# ── Never fabricate ──────────────────────────────────────────────────────────

def test_explanations_only_mention_available_metrics():
    """A metric that was unavailable must not appear as a strength or concern."""
    data = {k: v for k, v in COMPLETE.items() if k != "debtToEquity"}
    result = _explain(data)
    body = " ".join(result["strengths"] + result["concerns"])
    assert "Debt to equity of" not in body, "an unavailable metric was described with a value"


def test_missing_category_is_reported_as_a_concern():
    data = {k: v for k, v in COMPLETE.items()
            if k not in ("debtToEquity", "currentRatio", "freeCashflow")}
    result = _explain(data)
    assert any("could not be assessed" in c for c in result["concerns"])


def test_unscoreable_company_says_so_plainly():
    result = _explain({"returnOnEquity": 0.3})
    assert "not enough reliable data" in result["summary"].lower()
    assert result["strengths"] == [] and result["concerns"] == []


def test_low_confidence_is_stated_in_the_summary():
    data = {k: v for k, v in COMPLETE.items() if k != "debtToEquity"}
    result = _explain(data)
    assert "confidence is" in result["summary"].lower()


def test_summary_states_a_score_is_not_a_forecast():
    assert "not a forecast" in _explain(COMPLETE)["summary"].lower()


# ── Never instruct, never predict ────────────────────────────────────────────

BANNED = [
    r"\bbuy\b", r"\bsell\b", r"\bstop loss\b", r"\bposition siz", r"\bentry\b",
    r"\btarget price\b", r"\bwill rise\b", r"\bwill fall\b", r"\bshould rise\b",
    r"\bwe expect\b", r"\bguaranteed\b", r"\bwill outperform\b",
]


@pytest.mark.parametrize("pattern", BANNED)
@pytest.mark.parametrize("sector", [None, "Financial Services", "Utilities"])
def test_no_instruction_or_prediction_language(pattern, sector):
    result = _explain(COMPLETE, sector=sector)
    text = " ".join(
        [result["summary"]] + result["strengths"] + result["concerns"] + result["could_change"]
    )
    assert not re.search(pattern, text, re.IGNORECASE), \
        f"explanation contains {pattern!r}: {text[:200]}"


def test_glossary_contains_no_instruction_language():
    text = " ".join(f"{e['means']} {e['matters']}" for e in GLOSSARY.values())
    for pattern in (r"\byou should\b", r"\bwe recommend\b", r"\bbuy when\b", r"\bsell when\b"):
        assert not re.search(pattern, text, re.IGNORECASE), f"glossary contains {pattern!r}"


# ── Balance: strengths and risks both surface ────────────────────────────────

def test_strong_company_still_shows_what_could_change():
    result = _explain(COMPLETE)
    assert result["could_change"], "even a strong company must show what would change the score"


def test_weak_company_produces_concerns():
    weak = {**COMPLETE, "returnOnEquity": 0.005, "profitMargins": 0.002,
            "operatingMargins": 0.004, "trailingPE": 39.0}
    assert _explain(weak)["concerns"]


def test_sections_are_capped_for_readability():
    result = _explain(COMPLETE)
    for key in ("strengths", "concerns", "could_change"):
        assert len(result[key]) <= 5, f"{key} should be capped at 5 items"


# ── Value formatting ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,raw,expected", [
    ("profitMargins", 0.276, "27.6%"),      # provider ratio -> percent
    ("returnOnEquity", 1.488, "148.8%"),
    ("trailingPE", 36.6, "36.6×"),
    ("freeCashflow", 107_700_000_000.0, "107.7bn"),
    ("freeCashflow", 2_500_000.0, "2.5m"),
    ("beta", 1.46, "1.5"),
])
def test_values_format_for_a_lay_reader(field, raw, expected):
    assert format_value(field, raw) == expected


def test_negative_values_format_without_error():
    assert format_value("revenueGrowth", -0.12).startswith("-12.0")
    assert "bn" in format_value("freeCashflow", -3_000_000_000.0)
