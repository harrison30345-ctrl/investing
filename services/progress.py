"""
Learning progress.

Records which topics someone has completed, so the Learn section can show what
has been covered and what has not. Same SQLite file as the watchlist and score
history, and the same owner column, so it moves to real accounts without a
migration.

Completion is explicit. Opening a page is not the same as having read and
understood it, and a tracker that ticks topics off because a page loaded would
tell the reader something untrue about their own progress.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from services.score_history import DEFAULT_DB_PATH
except ImportError:  # pragma: no cover
    from score_history import DEFAULT_DB_PATH  # type: ignore

__all__ = ["LearningProgress", "Progress", "LOCAL_OWNER"]

LOCAL_OWNER = "local"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lesson_progress (
    owner        TEXT NOT NULL,
    lesson_key   TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (owner, lesson_key)
);
"""


@dataclass(frozen=True)
class Progress:
    """How far through a set of topics someone is."""
    done: int
    total: int

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.done)

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.done >= self.total

    def __str__(self) -> str:
        return f"{self.done} of {self.total}"


class LearningProgress:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH, owner: str = LOCAL_OWNER):
        self.path = Path(path)
        self.owner = owner
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def mark_done(self, lesson_key: str) -> None:
        """Idempotent: marking a topic twice does not move its completion date."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO lesson_progress (owner, lesson_key, completed_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(owner, lesson_key) DO NOTHING""",
                (self.owner, lesson_key, datetime.now().isoformat(timespec="seconds")),
            )

    def mark_undone(self, lesson_key: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM lesson_progress WHERE owner = ? AND lesson_key = ?",
                (self.owner, lesson_key),
            )
        return cur.rowcount > 0

    def toggle(self, lesson_key: str) -> bool:
        """Flip completion. Returns the state afterwards."""
        if self.is_done(lesson_key):
            self.mark_undone(lesson_key)
            return False
        self.mark_done(lesson_key)
        return True

    def is_done(self, lesson_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM lesson_progress WHERE owner = ? AND lesson_key = ?",
                (self.owner, lesson_key),
            ).fetchone()
        return row is not None

    def completed(self) -> set:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT lesson_key FROM lesson_progress WHERE owner = ?", (self.owner,),
            ).fetchall()
        return {r["lesson_key"] for r in rows}

    def completed_at(self, lesson_key: str) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT completed_at FROM lesson_progress "
                "WHERE owner = ? AND lesson_key = ?", (self.owner, lesson_key),
            ).fetchone()
        return datetime.fromisoformat(row["completed_at"]) if row else None

    def progress_for(self, lesson_keys) -> Progress:
        """How many of these topics are done. Counts only keys that were asked
        about, so a retired topic cannot inflate the total."""
        keys = list(lesson_keys)
        done = self.completed()
        return Progress(done=sum(1 for k in keys if k in done), total=len(keys))

    def next_unread(self, lesson_keys) -> str | None:
        """The first topic in the given order that has not been completed."""
        done = self.completed()
        for key in lesson_keys:
            if key not in done:
                return key
        return None

    def reset(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM lesson_progress WHERE owner = ?", (self.owner,))
