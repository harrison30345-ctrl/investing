"""
Score history.

Records what a company scored on the day it was scored, so that later the
product can show how a score has moved and why.

WHY THIS IS APPEND-ONLY AND STARTS FROM TODAY
---------------------------------------------
A past score cannot be reconstructed. The data source returns only today's
fundamentals -- today's revenue growth, today's P/E, today's margins. Scoring a
past date with them would produce a number that looks like history but is
actually today's company wearing an old date. That is look-ahead bias, and a
"score 1 year ago" built that way would be fabricated.

So history only exists from the first day a snapshot is written. There is no
backfill, and none should ever be added. A gap in the record is shown as a gap.

COMPARING ACROSS METHODOLOGY VERSIONS
-------------------------------------
Every snapshot stores the scoring version that produced it. If the methodology
changes, old and new scores are not comparable -- a score moving from 74 to 87
because the formula changed is not the company improving. Comparisons across
versions are flagged rather than silently presented as a trend.

STORAGE
-------
SQLite, because it is in the standard library, gives real queries, and maps
cleanly onto a hosted database later. The schema is deliberately flat so it can
be lifted into Postgres with a user_id column added when accounts arrive.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from services.scoring import ResearchScore

__all__ = [
    "ScoreSnapshot", "ScoreHistory", "DEFAULT_DB_PATH",
    "describe_change", "CATEGORY_ORDER",
]

DEFAULT_DB_PATH = Path.home() / ".screener" / "score_history.db"

CATEGORY_ORDER = ["quality", "growth", "valuation", "financial_health", "momentum"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS score_snapshots (
    ticker        TEXT    NOT NULL,
    taken_on      TEXT    NOT NULL,          -- ISO date, one row per ticker per day
    overall       REAL,                      -- NULL when the score was withheld
    coverage      REAL    NOT NULL,
    confidence    TEXT    NOT NULL,
    version       TEXT    NOT NULL,          -- methodology that produced it
    sector        TEXT,
    categories    TEXT    NOT NULL,          -- JSON: {key: score or null}
    recorded_at   TEXT    NOT NULL,
    PRIMARY KEY (ticker, taken_on)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON score_snapshots(ticker, taken_on);
"""


@dataclass(frozen=True)
class ScoreSnapshot:
    ticker: str
    taken_on: date
    overall: float | None
    coverage: float
    confidence: str
    version: str
    sector: str | None
    categories: dict[str, float | None]

    @property
    def age_days(self) -> int:
        return (date.today() - self.taken_on).days


class ScoreHistory:
    """Append-only store of scores, one row per ticker per day."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Writing ──────────────────────────────────────────────

    def record(self, score: "ResearchScore", taken_on: date | None = None) -> bool:
        """Store one score. Idempotent per ticker per day.

        Re-recording the same ticker on the same day overwrites that day's row
        rather than creating a second one, so a page that is refreshed several
        times does not distort the record. Returns True if a row was written.
        """
        taken_on = taken_on or date.today()
        categories = {k: c.score for k, c in score.categories.items()}
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO score_snapshots
                   (ticker, taken_on, overall, coverage, confidence, version,
                    sector, categories, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ticker, taken_on) DO UPDATE SET
                       overall=excluded.overall, coverage=excluded.coverage,
                       confidence=excluded.confidence, version=excluded.version,
                       sector=excluded.sector, categories=excluded.categories,
                       recorded_at=excluded.recorded_at""",
                (score.ticker.upper(), taken_on.isoformat(), score.overall,
                 score.coverage, score.confidence, score.version, score.sector,
                 json.dumps(categories), datetime.now().isoformat(timespec="seconds")),
            )
        return True

    def record_many(self, scores: list["ResearchScore"], taken_on: date | None = None) -> int:
        for s in scores:
            self.record(s, taken_on)
        return len(scores)

    # ── Reading ──────────────────────────────────────────────

    @staticmethod
    def _row(row: sqlite3.Row) -> ScoreSnapshot:
        return ScoreSnapshot(
            ticker=row["ticker"],
            taken_on=date.fromisoformat(row["taken_on"]),
            overall=row["overall"],
            coverage=row["coverage"],
            confidence=row["confidence"],
            version=row["version"],
            sector=row["sector"],
            categories=json.loads(row["categories"]),
        )

    def history(self, ticker: str, limit: int = 400) -> list[ScoreSnapshot]:
        """Every snapshot for a ticker, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM score_snapshots WHERE ticker = ? "
                "ORDER BY taken_on ASC LIMIT ?",
                (ticker.upper(), limit),
            ).fetchall()
        return [self._row(r) for r in rows]

    def latest(self, ticker: str) -> ScoreSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM score_snapshots WHERE ticker = ? "
                "ORDER BY taken_on DESC LIMIT 1", (ticker.upper(),),
            ).fetchone()
        return self._row(row) if row else None

    def nearest(
        self, ticker: str, days_ago: int, tolerance_days: int = 14,
    ) -> ScoreSnapshot | None:
        """The snapshot closest to `days_ago`, or None if none is close enough.

        Returns None rather than the oldest available row. Showing a 200-day-old
        score under a "90 days ago" heading would misrepresent the record, so
        when nothing falls inside the tolerance the caller is expected to say
        the comparison is not available yet.

        The snapshot's own `taken_on` is the truth: callers should display the
        actual age, not the age that was asked for.
        """
        target = date.today() - timedelta(days=days_ago)
        low = (target - timedelta(days=tolerance_days)).isoformat()
        high = (target + timedelta(days=tolerance_days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM score_snapshots WHERE ticker = ? "
                "AND taken_on BETWEEN ? AND ?", (ticker.upper(), low, high),
            ).fetchall()
        if not rows:
            return None
        snaps = [self._row(r) for r in rows]
        return min(snaps, key=lambda s: abs((s.taken_on - target).days))

    def coverage_summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers, "
                "MIN(taken_on) AS first_day, MAX(taken_on) AS last_day "
                "FROM score_snapshots"
            ).fetchone()
        first = row["first_day"]
        return {
            "rows": row["rows"],
            "tickers": row["tickers"],
            "first_day": date.fromisoformat(first) if first else None,
            "last_day": date.fromisoformat(row["last_day"]) if row["last_day"] else None,
            "days_of_history": (date.today() - date.fromisoformat(first)).days if first else 0,
        }


# ── Explaining a change ──────────────────────────────────────────────────────

def describe_change(now: ScoreSnapshot, then: ScoreSnapshot) -> dict[str, Any]:
    """Explain how a score moved, attributing it to the categories that shifted.

    Refuses to describe a change across methodology versions as company
    performance: if the formula changed, the difference is not evidence about
    the business.
    """
    if now.version != then.version:
        return {
            "comparable": False,
            "summary": (
                f"These scores were produced by different methodology versions "
                f"({then.version} then, {now.version} now), so the difference does not "
                f"reflect a change in the company and is not shown as a trend."
            ),
            "movers": [],
        }

    if now.overall is None or then.overall is None:
        return {
            "comparable": False,
            "summary": (
                "One of these scores was withheld because too little data was available, "
                "so the two cannot be compared."
            ),
            "movers": [],
        }

    delta = now.overall - then.overall
    movers = []
    for key in CATEGORY_ORDER:
        a, b = now.categories.get(key), then.categories.get(key)
        if a is None or b is None:
            continue
        if abs(a - b) >= 1.0:
            movers.append((key, a - b, b, a))
    movers.sort(key=lambda m: abs(m[1]), reverse=True)

    label = {"quality": "Quality", "growth": "Growth", "valuation": "Valuation",
             "financial_health": "Financial health", "momentum": "Momentum"}

    days = (now.taken_on - then.taken_on).days
    if abs(delta) < 0.5:
        summary = f"The score is essentially unchanged over the last {days} days."
    else:
        direction = "rose" if delta > 0 else "fell"
        summary = (f"The score {direction} from {then.overall:.0f} to {now.overall:.0f} "
                   f"over {days} days.")
        if movers:
            top = movers[0]
            summary += (f" The largest move was {label[top[0]].lower()}, "
                        f"{'up' if top[1] > 0 else 'down'} "
                        f"{abs(top[1]):.0f} points ({top[2]:.0f} to {top[3]:.0f}).")

    return {
        "comparable": True,
        "delta": delta,
        "days": days,
        "summary": summary,
        "movers": [
            {"category": label[k], "delta": d, "then": b, "now": a}
            for k, d, b, a in movers
        ],
    }
