"""
Watchlist and recently-viewed storage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.watchlist import MAX_RECENT, Watchlist  # noqa: E402


@pytest.fixture
def wl(tmp_path):
    return Watchlist(tmp_path / "w.db")


# ── Watchlist ────────────────────────────────────────────────────────────────

def test_add_and_list(wl):
    wl.add("AAPL", "Apple Inc.")
    assert wl.tickers() == ["AAPL"]
    assert wl.all()[0].name == "Apple Inc."


def test_add_is_idempotent(wl):
    for _ in range(4):
        wl.add("AAPL", "Apple Inc.")
    assert wl.tickers() == ["AAPL"]


def test_readding_does_not_reset_the_added_date(wl):
    wl.add("AAPL", "Apple Inc.")
    first = wl.all()[0].added_at
    wl.add("AAPL", "Apple Inc.")
    assert wl.all()[0].added_at == first


def test_remove(wl):
    wl.add("AAPL")
    assert wl.remove("AAPL") is True
    assert wl.tickers() == []


def test_remove_absent_reports_false(wl):
    assert wl.remove("NOPE") is False


def test_contains(wl):
    wl.add("AAPL")
    assert wl.contains("AAPL") and wl.contains("aapl")
    assert not wl.contains("MSFT")


def test_tickers_are_normalised(wl):
    wl.add("aapl")
    assert wl.tickers() == ["AAPL"]
    assert wl.remove("AaPl") is True


def test_newest_first(wl):
    for t in ("A", "B", "C"):
        wl.add(t)
    assert wl.tickers()[0] == "C"


def test_note_is_kept(wl):
    wl.add("AAPL", "Apple", note="watching margins")
    assert wl.all()[0].note == "watching margins"


def test_readding_without_a_name_keeps_the_existing_one(wl):
    wl.add("AAPL", "Apple Inc.")
    wl.add("AAPL")
    assert wl.all()[0].name == "Apple Inc."


# ── Recently viewed ──────────────────────────────────────────────────────────

def test_records_views_newest_first(wl):
    for t in ("A", "B", "C"):
        wl.record_view(t)
    assert [e.ticker for e in wl.recent()][0] == "C"


def test_reviewing_moves_a_company_to_the_front(wl):
    for t in ("A", "B", "C"):
        wl.record_view(t)
    wl.record_view("A")
    assert [e.ticker for e in wl.recent()][0] == "A"


def test_recent_list_does_not_grow_without_limit(wl):
    for i in range(MAX_RECENT + 8):
        wl.record_view(f"T{i}")
    assert len(wl.recent(limit=100)) == MAX_RECENT


def test_recent_and_watchlist_are_independent(wl):
    wl.record_view("AAPL")
    assert wl.tickers() == []
    wl.add("MSFT")
    assert [e.ticker for e in wl.recent()] == ["AAPL"]


# ── Ownership column is ready for accounts ───────────────────────────────────

def test_owners_do_not_see_each_other(tmp_path):
    path = tmp_path / "shared.db"
    a, b = Watchlist(path, owner="user_a"), Watchlist(path, owner="user_b")
    a.add("AAPL")
    assert a.tickers() == ["AAPL"]
    assert b.tickers() == [], "watchlists must be scoped to their owner"


def test_recent_is_also_scoped_by_owner(tmp_path):
    path = tmp_path / "shared.db"
    a, b = Watchlist(path, owner="user_a"), Watchlist(path, owner="user_b")
    a.record_view("AAPL")
    assert b.recent() == []


def test_survives_reopening(tmp_path):
    path = tmp_path / "w.db"
    Watchlist(path).add("AAPL", "Apple Inc.")
    assert Watchlist(path).tickers() == ["AAPL"]


def test_shares_the_database_with_score_history(tmp_path):
    """Both stores must coexist in one file without clobbering each other."""
    from datetime import date
    from services.score_history import ScoreHistory
    from services.scoring import score_company
    path = tmp_path / "shared.db"
    hist = ScoreHistory(path)
    wl = Watchlist(path)
    hist.record(score_company("AAPL", {"returnOnEquity": 0.3, "profitMargins": 0.2,
                                       "operatingMargins": 0.25, "revenueGrowth": 0.2,
                                       "earningsGrowth": 0.2, "trailingPE": 20.0,
                                       "forwardPE": 18.0,
                                       "priceToSalesTrailing12Months": 5.0,
                                       "debtToEquity": 40.0, "currentRatio": 2.0,
                                       "freeCashflow": 1e9}), date.today())
    wl.add("AAPL", "Apple Inc.")
    assert wl.tickers() == ["AAPL"]
    assert len(hist.history("AAPL")) == 1
