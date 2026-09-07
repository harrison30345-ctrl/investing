"""
Learn content: short, factual, and honest about limits.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from content.lessons import (  # noqa: E402
    CATEGORIES, CATEGORY_INTROS, LESSONS, LESSONS_BY_KEY, lesson_for_metric,
)
from services.explanations import GLOSSARY  # noqa: E402


def test_lessons_exist():
    assert len(LESSONS) >= 15


def test_keys_and_titles_are_unique():
    assert len({l.key for l in LESSONS}) == len(LESSONS)
    assert len({l.title for l in LESSONS}) == len(LESSONS)


@pytest.mark.parametrize("lesson", LESSONS, ids=lambda l: l.key)
def test_lesson_is_short_and_complete(lesson):
    words = len(lesson.body.split())
    assert 90 < words < 420, f"{lesson.key}: {words} words is not a short lesson"
    assert lesson.category in CATEGORIES
    assert 1 <= lesson.minutes <= 6
    assert lesson.summary.endswith("."), "summary should be a sentence"


@pytest.mark.parametrize("lesson", LESSONS, ids=lambda l: l.key)
def test_every_lesson_states_what_it_does_not_tell_you(lesson):
    """The caveat is the part beginners most need and most guides omit."""
    lowered = lesson.body.lower()
    assert ("does not tell you" in lowered or "what to watch" in lowered), \
        f"{lesson.key} never says what the idea does not cover"


@pytest.mark.parametrize("lesson", LESSONS, ids=lambda l: l.key)
def test_no_hype_or_promises(lesson):
    text = f"{lesson.title} {lesson.summary} {lesson.body}".lower()
    # Denials are fine and often necessary -- "no guarantee of anything" is
    # exactly the sort of sentence this content should contain. Only promises
    # are banned, so strip negated forms before matching.
    text = re.sub(r"\b(no|not|never|without)\s+\w{0,12}\s*(guarantee\w*|promise\w*)", " ", text)
    for banned in (r"\bguarantee", r"\bwill rise\b", r"\bwill fall\b", r"\bbeat the market\b",
                   r"\bamazing\b", r"\bexciting\b", r"\bhuge\b", r"\bmust buy\b",
                   r"\byou should buy\b", r"\bwe love\b", r"\bred flags you need\b",
                   r"\bsecret\b", r"\bhack\b", r"\beasy money\b"):
        assert not re.search(banned, text), f"{lesson.key} contains {banned!r}"


@pytest.mark.parametrize("lesson", LESSONS, ids=lambda l: l.key)
def test_no_emoji_in_learn_content(lesson):
    emoji = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
    assert not emoji.search(lesson.title + lesson.body)


def test_metric_links_point_at_real_glossary_entries():
    for lesson in LESSONS:
        if lesson.metric:
            assert lesson.metric in GLOSSARY, f"{lesson.key} links to unknown metric"


def test_lesson_lookup_by_metric():
    assert lesson_for_metric("trailingPE").key == "pe_ratio"
    assert lesson_for_metric("debtToEquity").key == "debt"
    assert lesson_for_metric("nonexistent") is None


def test_every_category_has_content():
    used = {l.category for l in LESSONS}
    for category in CATEGORIES:
        assert category in used, f"{category} has no lessons"


def test_core_beginner_topics_are_covered():
    for key in ("what_is_a_share", "pe_ratio", "isa", "diversification",
                "risk", "how_to_research", "market_cap", "dividends"):
        assert key in LESSONS_BY_KEY


# ── Each group explains what it is for ───────────────────────────────────────

@pytest.mark.parametrize("category", CATEGORIES)
def test_every_category_has_an_introduction(category):
    """A list of titles tells a beginner nothing about which group they need."""
    intro = CATEGORY_INTROS.get(category)
    assert intro, f"{category} has no introduction"
    assert len(intro) > 80, f"{category} introduction is too thin to orient anyone"
    assert intro.strip().endswith(".")


def test_category_intros_do_not_promise_outcomes():
    text = " ".join(CATEGORY_INTROS.values()).lower()
    for banned in ("guarantee", "beat the market", "get rich", "you should buy",
                   "best stocks", "make money"):
        assert banned not in text


def test_no_orphan_intros():
    for key in CATEGORY_INTROS:
        assert key in CATEGORIES, f"{key} has an intro but no category"
