"""
Learning progress: explicit, honest, and scoped to its owner.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from content.lessons import LESSONS  # noqa: E402
from services.progress import LearningProgress, Progress  # noqa: E402


@pytest.fixture
def prog(tmp_path):
    return LearningProgress(tmp_path / "p.db")


def test_nothing_done_to_begin_with(prog):
    assert prog.completed() == set()
    assert not prog.is_done("pe_ratio")


def test_mark_and_unmark(prog):
    prog.mark_done("pe_ratio")
    assert prog.is_done("pe_ratio")
    assert prog.mark_undone("pe_ratio") is True
    assert not prog.is_done("pe_ratio")


def test_marking_twice_is_idempotent(prog):
    prog.mark_done("pe_ratio")
    first = prog.completed_at("pe_ratio")
    prog.mark_done("pe_ratio")
    assert prog.completed() == {"pe_ratio"}
    assert prog.completed_at("pe_ratio") == first, "re-marking must not reset the date"


def test_toggle_returns_resulting_state(prog):
    assert prog.toggle("debt") is True
    assert prog.toggle("debt") is False


def test_unmarking_something_absent_reports_false(prog):
    assert prog.mark_undone("never_marked") is False


def test_progress_counts_only_requested_keys(prog):
    """A retired topic must not inflate the total or the count."""
    prog.mark_done("a"); prog.mark_done("retired")
    p = prog.progress_for(["a", "b", "c"])
    assert (p.done, p.total) == (1, 3)


def test_progress_arithmetic():
    p = Progress(done=3, total=10)
    assert p.remaining == 7
    assert abs(p.fraction - 0.3) < 1e-9
    assert not p.complete
    assert str(p) == "3 of 10"


def test_progress_complete_when_all_done():
    assert Progress(done=4, total=4).complete
    assert not Progress(done=0, total=0).complete, "no topics is not completion"


def test_empty_progress_does_not_divide_by_zero():
    assert Progress(done=0, total=0).fraction == 0.0


def test_next_unread_follows_the_given_order(prog):
    keys = ["one", "two", "three"]
    assert prog.next_unread(keys) == "one"
    prog.mark_done("one")
    assert prog.next_unread(keys) == "two"


def test_next_unread_is_none_when_finished(prog):
    prog.mark_done("one"); prog.mark_done("two")
    assert prog.next_unread(["one", "two"]) is None


def test_completed_at_is_none_when_not_done(prog):
    assert prog.completed_at("pe_ratio") is None


def test_reset_clears_everything(prog):
    prog.mark_done("a"); prog.mark_done("b")
    prog.reset()
    assert prog.completed() == set()


def test_owners_do_not_see_each_other(tmp_path):
    path = tmp_path / "shared.db"
    a = LearningProgress(path, owner="user_a")
    b = LearningProgress(path, owner="user_b")
    a.mark_done("pe_ratio")
    assert a.is_done("pe_ratio")
    assert not b.is_done("pe_ratio"), "progress must be scoped to its owner"


def test_survives_reopening(tmp_path):
    path = tmp_path / "p.db"
    LearningProgress(path).mark_done("pe_ratio")
    assert LearningProgress(path).is_done("pe_ratio")


def test_works_against_the_real_lesson_keys(prog):
    keys = [l.key for l in LESSONS]
    assert prog.progress_for(keys).total == len(LESSONS)
    prog.mark_done(keys[0])
    assert prog.progress_for(keys).done == 1


def test_store_has_no_auto_complete_path():
    """Opening a page is not the same as having read it."""
    for forbidden in ("record_view", "mark_viewed", "auto_complete", "on_open"):
        assert not hasattr(LearningProgress, forbidden), \
            f"LearningProgress must not offer {forbidden}()"
