"""
Watchlist and recently-viewed companies.

Local, single-user storage for now, in the same SQLite database as score
history. The schema carries an `owner` column that is currently always "local":
when authenticated accounts arrive it becomes the user id and the queries below
do not change shape. That is the whole reason it exists this early -- retrofitting
ownership onto a table later means migrating everyone's data.

Nothing here is investment-related logic. It records which companies someone
chose to keep an eye on, and does not interpret that choice.
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

__all__ = ["WatchlistEntry", "Watchlist", "LOCAL_OWNER", "MAX_RECENT"]

LOCAL_OWNER = "local"
MAX_RECENT = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    owner      TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    name       TEXT,
    added_at   TEXT NOT NULL,          -- microsecond precision: entries added
                                       -- in the same second must still order
    note       TEXT,
    PRIMARY KEY (owner, ticker)
);
CREATE TABLE IF NOT EXISTS recently_viewed (
    owner      TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    name       TEXT,
    viewed_at  TEXT NOT NULL,
    PRIMARY KEY (owner, ticker)
);
"""


@dataclass(frozen=True)
class WatchlistEntry:
    ticker: str
    name: str | None
    added_at: datetime
    note: str | None = None


class Watchlist:
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

    # ── Watchlist ────────────────────────────────────────────

    def add(self, ticker: str, name: str | None = None, note: str | None = None) -> bool:
        """Add a company. Idempotent — re-adding refreshes the name, not the date."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO watchlist (owner, ticker, name, added_at, note)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(owner, ticker) DO UPDATE SET
                       name = COALESCE(excluded.name, watchlist.name),
                       note = COALESCE(excluded.note, watchlist.note)""",
                (self.owner, ticker.upper(), name, datetime.now().isoformat(), note),
            )
        return True

    def remove(self, ticker: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM watchlist WHERE owner = ? AND ticker = ?",
                               (self.owner, ticker.upper()))
        return cur.rowcount > 0

    def contains(self, ticker: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM watchlist WHERE owner = ? AND ticker = ?",
                (self.owner, ticker.upper()),
            ).fetchone()
        return row is not None

    def all(self) -> list[WatchlistEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM watchlist WHERE owner = ? ORDER BY added_at DESC",
                (self.owner,),
            ).fetchall()
        return [
            WatchlistEntry(r["ticker"], r["name"],
                           datetime.fromisoformat(r["added_at"]), r["note"])
            for r in rows
        ]

    def tickers(self) -> list[str]:
        return [e.ticker for e in self.all()]

    # ── Recently viewed ──────────────────────────────────────

    def record_view(self, ticker: str, name: str | None = None) -> None:
        """Note that a company was looked at. Most recent view wins."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO recently_viewed (owner, ticker, name, viewed_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(owner, ticker) DO UPDATE SET
                       name = COALESCE(excluded.name, recently_viewed.name),
                       viewed_at = excluded.viewed_at""",
                (self.owner, ticker.upper(), name, datetime.now().isoformat()),
            )
            # Keep the list short rather than letting it grow without limit.
            conn.execute(
                """DELETE FROM recently_viewed
                   WHERE owner = ? AND ticker NOT IN (
                       SELECT ticker FROM recently_viewed WHERE owner = ?
                       ORDER BY viewed_at DESC LIMIT ?
                   )""",
                (self.owner, self.owner, MAX_RECENT),
            )

    def recent(self, limit: int = MAX_RECENT) -> list[WatchlistEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recently_viewed WHERE owner = ? "
                "ORDER BY viewed_at DESC LIMIT ?", (self.owner, limit),
            ).fetchall()
        return [
            WatchlistEntry(r["ticker"], r["name"], datetime.fromisoformat(r["viewed_at"]))
            for r in rows
        ]
