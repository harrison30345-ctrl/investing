"""
Headline attribution.

The test that matters most here is that a story is only filed under a company
when it genuinely names it. The first working version of this module stamped
whichever ticker had been searched for onto every result, which filed a UPS
earnings story under NVDA. Yahoo's own `relatedTickers` field turned out to be
no better -- it lists NVDA as related to that same UPS story.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services import news


def _item(title, uuid="u1", publisher="Reuters", link="https://example.com/a",
          when=None, related=None):
    return {
        "uuid": uuid, "title": title, "publisher": publisher, "link": link,
        "providerPublishTime": (when or datetime.now(timezone.utc)).timestamp(),
        "relatedTickers": related or [],
    }


# ── Attribution ──────────────────────────────────────────────

def test_ticker_in_headline_counts_as_a_mention():
    assert news._mentions("NVDA jumps on earnings beat", "NVDA")


def test_company_name_in_headline_counts_as_a_mention():
    assert news._mentions("Apple's $2,000 iPhone shrugs off screen issues",
                          "AAPL", "Apple Inc.")


def test_name_match_ignores_corporate_suffixes():
    """'Lloyds Banking Group plc' must match on 'Lloyds', not on 'Group'."""
    assert news._mentions("Lloyds raises guidance", "LLOY", "Lloyds Banking Group plc")
    assert not news._mentions("Group earnings season begins", "LLOY",
                              "Lloyds Banking Group plc")


def test_unrelated_headline_is_not_a_mention():
    assert not news._mentions("Is UPS a Buy After Its Latest Earnings Report?", "NVDA")


def test_ticker_substring_does_not_match():
    """'GD' must not match 'GOOD' or 'guidance'."""
    assert not news._mentions("A GOOD quarter for industrials", "GD")
    assert not news._mentions("Raised guidance across the sector", "GD")


@pytest.mark.parametrize("word,ticker", [
    ("ALL eyes on the Fed this week", "ALL"),
    ("Rates are ON hold until December", "ON"),
    ("IT spending slows in the fourth quarter", "IT"),
])
def test_tickers_that_are_common_words_are_not_matched(word, ticker):
    """Matching these as company mentions would tag nearly every headline."""
    assert not news._mentions(word, ticker)


def test_exchange_suffix_does_not_block_a_match():
    assert news._mentions("BP cuts capital spending", "BP.L")


def test_related_tickers_alone_do_not_attribute_a_story(monkeypatch):
    """Yahoo lists NVDA against a UPS story. Trusting that field misfiles it."""
    monkeypatch.setattr(
        news, "_search",
        lambda query, count: [news._parse(_item(
            "Is UPS a Buy After Its Latest Earnings Report?",
            related=["UPS", "NVDA"],
        ))],
    )
    assert news.for_tickers(["NVDA"]) == []


def test_a_genuine_mention_is_returned_and_narrowed(monkeypatch):
    monkeypatch.setattr(
        news, "_search",
        lambda query, count: [news._parse(_item(
            "Nvidia partner Vertiv soars after earnings",
            related=["NVDA", "VRT", "MSFT"],
        ))],
    )
    out = news.for_tickers(["NVDA", "AAPL"], names={"NVDA": "NVIDIA Corporation"})
    assert len(out) == 1
    # Narrowed to the companies asked about that the headline actually names --
    # MSFT came from the provider's related field and must not appear.
    assert out[0].tickers == ("NVDA",)


# ── Parsing and housekeeping ─────────────────────────────────

def test_item_without_a_title_or_link_is_dropped():
    assert news._parse(_item("", link="https://example.com")) is None
    assert news._parse({"title": "Something happened", "link": ""}) is None


def test_unparseable_timestamp_leaves_the_date_unknown():
    item = _item("Markets move")
    item["providerPublishTime"] = "not-a-timestamp"
    parsed = news._parse(item)
    assert parsed.published is None
    assert parsed.age == "Date unknown"
    assert parsed.fresh is False       # unknown is never treated as recent


def test_duplicate_story_under_a_second_ticker_keeps_both_companies(monkeypatch):
    """One wire story fetched twice should appear once, naming both companies."""
    shared = "Apple and Microsoft both cut data centre spending"
    monkeypatch.setattr(
        news, "_search",
        lambda query, count: [news._parse(_item(shared, uuid="same-uuid"))],
    )
    out = news.for_tickers(["AAPL", "MSFT"],
                           names={"AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation"})
    assert len(out) == 1
    assert set(out[0].tickers) == {"AAPL", "MSFT"}


def test_near_identical_headlines_are_deduplicated():
    a = news._parse(_item("Fed holds rates steady", uuid="a"))
    b = news._parse(_item("Fed holds rates steady!", uuid="b",
                          link="https://example.com/b"))
    assert len(news._merge([[a], [b]])) == 1


def test_headlines_are_newest_first():
    now = datetime.now(timezone.utc)
    old = news._parse(_item("Older story", uuid="o", when=now - timedelta(hours=5)))
    new = news._parse(_item("Newer story", uuid="n", when=now - timedelta(hours=1),
                            link="https://example.com/n"))
    assert [h.title for h in news._merge([[old], [new]])] == ["Newer story", "Older story"]


def test_stale_stories_are_dropped():
    now = datetime.now(timezone.utc)
    fresh = news._parse(_item("Today", uuid="f", when=now))
    stale = news._parse(_item("Months ago", uuid="s", link="https://example.com/s",
                              when=now - timedelta(days=90)))
    kept = news._recent([fresh, stale], news.MAX_AGE_DAYS)
    assert [h.title for h in kept] == ["Today"]


def test_provider_failure_yields_no_headlines_not_an_error(monkeypatch):
    def boom(query, count):
        raise RuntimeError("provider down")
    monkeypatch.setattr(news.yf, "Search", lambda *a, **k: (_ for _ in ()).throw(boom))
    assert news.for_tickers(["AAPL"]) == []
    assert news.for_queries(["stock market"]) == []


def test_no_tickers_makes_no_requests(monkeypatch):
    monkeypatch.setattr(news, "_search",
                        lambda *a, **k: pytest.fail("should not fetch"))
    assert news.for_tickers([]) == []
    assert news.for_queries([]) == []


def test_age_reads_in_plain_words():
    now = datetime.now(timezone.utc)
    def age(delta):
        return news._parse(_item("t", when=now - delta)).age
    assert age(timedelta(minutes=30)).endswith("m ago")
    assert age(timedelta(hours=5)) == "5h ago"
    assert age(timedelta(days=1, hours=2)) == "Yesterday"
    assert age(timedelta(days=4)) == "4d ago"
