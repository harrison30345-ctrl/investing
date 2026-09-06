"""
Score history: record honestly, never fabricate, never mislead about age.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.score_history import (  # noqa: E402
    ScoreHistory, describe_change,
)
from services.scoring import score_company  # noqa: E402

STRONG = {
    "returnOnEquity": 0.32, "profitMargins": 0.24, "operatingMargins": 0.29,
    "revenueGrowth": 0.28, "earningsGrowth": 0.33,
    "trailingPE": 19.0, "forwardPE": 16.0, "priceToSalesTrailing12Months": 4.0,
    "debtToEquity": 35.0, "currentRatio": 2.2, "freeCashflow": 2_500_000_000.0,
    "chg_1w": 1.5, "chg_1m": 5.0, "chg_3m": 11.0, "vs_sma50": 4.0,
}
WEAKER = {**STRONG, "revenueGrowth": 0.02, "earningsGrowth": -0.05,
          "returnOnEquity": 0.12, "profitMargins": 0.08}


@pytest.fixture
def store(tmp_path):
    return ScoreHistory(tmp_path / "history.db")


# ── Recording ────────────────────────────────────────────────────────────────

def test_records_and_reads_back(store):
    store.record(score_company("AAPL", STRONG))
    hist = store.history("AAPL")
    assert len(hist) == 1
    assert hist[0].ticker == "AAPL"
    assert hist[0].taken_on == date.today()
    assert hist[0].overall is not None


def test_one_row_per_ticker_per_day(store):
    """Refreshing a page repeatedly must not distort the record."""
    for _ in range(5):
        store.record(score_company("AAPL", STRONG))
    assert len(store.history("AAPL")) == 1


def test_same_day_rerecord_overwrites_with_latest(store):
    store.record(score_company("AAPL", STRONG))
    store.record(score_company("AAPL", WEAKER))
    hist = store.history("AAPL")
    assert len(hist) == 1
    assert hist[0].overall == score_company("AAPL", WEAKER).overall


def test_stores_methodology_version_and_confidence(store):
    scored = score_company("AAPL", STRONG)
    store.record(scored)
    snap = store.latest("AAPL")
    assert snap.version == scored.version
    assert snap.confidence == scored.confidence
    assert 0.0 <= snap.coverage <= 1.0


def test_stores_every_category(store):
    store.record(score_company("AAPL", STRONG))
    cats = store.latest("AAPL").categories
    assert set(cats) == {"quality", "growth", "valuation", "financial_health", "momentum"}


def test_withheld_score_is_stored_as_null_not_zero(store):
    """An unscoreable company must not be recorded as scoring zero."""
    store.record(score_company("SPARSE", {"returnOnEquity": 0.3}))
    snap = store.latest("SPARSE")
    assert snap.overall is None, "a withheld score must persist as unknown"


def test_tickers_are_normalised(store):
    store.record(score_company("aapl", STRONG))
    assert store.history("AAPL")


def test_history_is_ordered_oldest_first(store):
    for n in (30, 10, 20):
        store.record(score_company("X", STRONG), date.today() - timedelta(days=n))
    days = [s.taken_on for s in store.history("X")]
    assert days == sorted(days)


# ── Never fabricate a comparison ─────────────────────────────────────────────

def test_no_history_returns_nothing(store):
    assert store.history("NEW") == []
    assert store.latest("NEW") is None
    assert store.nearest("NEW", 30) is None


def test_nearest_refuses_a_snapshot_outside_tolerance(store):
    """A 200-day-old score must not be shown under a '30 days ago' heading."""
    store.record(score_company("X", STRONG), date.today() - timedelta(days=200))
    assert store.nearest("X", 30, tolerance_days=14) is None


def test_nearest_finds_a_snapshot_inside_tolerance(store):
    store.record(score_company("X", STRONG), date.today() - timedelta(days=33))
    snap = store.nearest("X", 30, tolerance_days=14)
    assert snap is not None
    assert snap.age_days == 33, "the snapshot reports its real age, not the age requested"


def test_nearest_picks_the_closest_of_several(store):
    for n in (20, 28, 45):
        store.record(score_company("X", STRONG), date.today() - timedelta(days=n))
    assert store.nearest("X", 30, tolerance_days=20).age_days == 28


def test_snapshot_reports_its_true_age(store):
    store.record(score_company("X", STRONG), date.today() - timedelta(days=47))
    assert store.nearest("X", 90, tolerance_days=60).age_days == 47


# ── Explaining change ────────────────────────────────────────────────────────

def _pair(store, ticker, then_data, now_data, days=30):
    store.record(score_company(ticker, then_data), date.today() - timedelta(days=days))
    store.record(score_company(ticker, now_data))
    return store.nearest(ticker, days, tolerance_days=5), store.latest(ticker)


def test_change_is_attributed_to_the_category_that_moved(store):
    then, now = _pair(store, "X", STRONG, WEAKER)
    result = describe_change(now, then)
    assert result["comparable"]
    assert result["delta"] < 0
    assert "fell" in result["summary"]
    assert result["movers"], "a real move must name the categories responsible"
    assert result["movers"][0]["category"] in {"Growth", "Quality"}


def test_unchanged_score_says_so(store):
    then, now = _pair(store, "X", STRONG, STRONG)
    result = describe_change(now, then)
    assert "unchanged" in result["summary"].lower()


def test_cross_version_comparison_is_refused(store):
    """A formula change is not the company improving."""
    from dataclasses import replace
    store.record(score_company("X", STRONG), date.today() - timedelta(days=30))
    store.record(score_company("X", WEAKER))
    then, now = store.nearest("X", 30, tolerance_days=5), store.latest("X")
    then = replace(then, version="0.9.0")
    result = describe_change(now, then)
    assert not result["comparable"]
    assert "methodology" in result["summary"].lower()
    assert result["movers"] == []


def test_withheld_score_cannot_be_compared(store):
    store.record(score_company("X", {"returnOnEquity": 0.3}), date.today() - timedelta(days=30))
    store.record(score_company("X", STRONG))
    then, now = store.nearest("X", 30, tolerance_days=5), store.latest("X")
    result = describe_change(now, then)
    assert not result["comparable"]
    assert "withheld" in result["summary"].lower()


def test_summary_never_predicts(store):
    then, now = _pair(store, "X", WEAKER, STRONG)
    text = describe_change(now, then)["summary"].lower()
    for banned in ("will rise", "will fall", "expect", "should buy", "recommend", "undervalued"):
        assert banned not in text


# ── There is no backfill, by design ──────────────────────────────────────────

def test_store_exposes_no_backfill_method():
    """Reconstructing past scores from today's data would be fabrication."""
    for forbidden in ("backfill", "reconstruct", "synthesize", "synthesise", "estimate_past"):
        assert not hasattr(ScoreHistory, forbidden), \
            f"ScoreHistory must not offer {forbidden}()"


def test_coverage_summary_reports_real_extent(store):
    assert store.coverage_summary()["rows"] == 0
    store.record(score_company("A", STRONG), date.today() - timedelta(days=10))
    store.record(score_company("B", STRONG))
    summary = store.coverage_summary()
    assert summary["rows"] == 2 and summary["tickers"] == 2
    assert summary["days_of_history"] == 10


def test_store_survives_reopening(tmp_path):
    path = tmp_path / "h.db"
    ScoreHistory(path).record(score_company("A", STRONG))
    assert len(ScoreHistory(path).history("A")) == 1
