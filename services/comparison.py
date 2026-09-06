"""
Side-by-side company comparison.

Describes how two to four companies differ on the measures the platform scores,
in plain English.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not say which company to buy, which is "better", or which anyone should
prefer. Those depend on what an individual is trying to achieve, their time
horizon and their attitude to risk -- none of which this platform knows, and
recommending one on the reader's behalf would be advice rather than research.

What it does instead is name the trade-offs: which company scores higher on
what, and what each one gives up in exchange. A company with faster growth and
a more demanding valuation is described as exactly that, and the reader decides
what to make of it.

Comparisons are only drawn between categories both companies could be scored
on. Where one company's category was withheld for lack of data, the difference
is reported as not comparable rather than being presented as a gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

try:
    from config.scoring_config import CATEGORY_LABELS, CATEGORY_WEIGHTS
except ImportError:  # pragma: no cover
    from scoring_config import CATEGORY_LABELS, CATEGORY_WEIGHTS  # type: ignore

if TYPE_CHECKING:  # pragma: no cover
    from services.scoring import ResearchScore

__all__ = ["ComparisonRow", "Comparison", "compare", "MIN_MEANINGFUL_GAP", "MAX_COMPANIES"]

# Below this many points, two scores are treated as effectively the same. The
# inputs carry more measurement noise than a 5-point gap represents, so calling
# a 3-point difference a "lead" would overstate what the data supports.
MIN_MEANINGFUL_GAP = 5.0

MAX_COMPANIES = 4

_TRADE_OFF = {
    "quality": ("more profitable", "less profitable"),
    "growth": ("growing faster", "growing more slowly"),
    "valuation": ("priced more modestly", "priced more richly"),
    "financial_health": ("carrying less balance-sheet strain", "carrying more balance-sheet strain"),
    "momentum": ("stronger recent price movement", "weaker recent price movement"),
}


@dataclass(frozen=True)
class ComparisonRow:
    """One category across all companies being compared."""
    key: str
    label: str
    scores: dict[str, float | None]     # ticker -> score or None
    leader: str | None                  # ticker, or None if too close / not comparable
    comparable: bool
    note: str = ""

    @property
    def spread(self) -> float | None:
        known = [v for v in self.scores.values() if v is not None]
        return max(known) - min(known) if len(known) >= 2 else None


@dataclass(frozen=True)
class Comparison:
    tickers: list[str]
    names: dict[str, str]
    overall: dict[str, float | None]
    confidence: dict[str, str]
    rows: list[ComparisonRow]
    summary: str
    caveats: list[str]


def _leader(scores: dict[str, float | None]) -> tuple[str | None, bool, str]:
    known = {t: v for t, v in scores.items() if v is not None}
    if len(known) < 2:
        missing = [t for t, v in scores.items() if v is None]
        return None, False, (
            f"Not comparable — no score for {', '.join(missing)}."
        )
    best = max(known, key=lambda t: known[t])
    worst = min(known, key=lambda t: known[t])
    if known[best] - known[worst] < MIN_MEANINGFUL_GAP:
        return None, True, "Too close to separate."
    note = ""
    if len(known) < len(scores):
        absent = [t for t, v in scores.items() if v is None]
        note = f"{', '.join(absent)} could not be scored on this measure."
    return best, True, note


def compare(scores: list["ResearchScore"], names: dict[str, str] | None = None) -> Comparison:
    """Compare 2-4 companies category by category.

    Raises ValueError outside that range: one company is not a comparison, and
    beyond four the table stops being readable on a phone.
    """
    if not 2 <= len(scores) <= MAX_COMPANIES:
        raise ValueError(f"compare() takes 2 to {MAX_COMPANIES} companies, got {len(scores)}")

    tickers = [s.ticker for s in scores]
    names = names or {t: t for t in tickers}
    by_ticker = {s.ticker: s for s in scores}

    rows = []
    for key in CATEGORY_WEIGHTS:
        per_ticker = {
            t: (by_ticker[t].categories[key].score
                if by_ticker[t].categories[key].available else None)
            for t in tickers
        }
        leader, comparable, note = _leader(per_ticker)
        rows.append(ComparisonRow(key, CATEGORY_LABELS[key], per_ticker,
                                  leader, comparable, note))

    overall = {t: by_ticker[t].overall for t in tickers}
    confidence = {t: by_ticker[t].confidence for t in tickers}

    return Comparison(
        tickers=tickers,
        names=names,
        overall=overall,
        confidence=confidence,
        rows=rows,
        summary=_summarise(tickers, names, rows, overall),
        caveats=_caveats(tickers, names, by_ticker),
    )


def _summarise(tickers, names, rows, overall) -> str:
    """Name the trade-offs. Never names a winner."""
    strengths: dict[str, list[str]] = {t: [] for t in tickers}
    for row in rows:
        if row.leader and row.comparable:
            strengths[row.leader].append(_TRADE_OFF[row.key][0])

    parts = []
    for t in tickers:
        if strengths[t]:
            items = strengths[t]
            joined = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
            parts.append(f"**{names.get(t, t)}** is {joined}")

    if not parts:
        return (
            "These companies score similarly across every measure, with no difference "
            "large enough to separate them on the figures available."
        )

    sentence = ". ".join(parts) + "."
    scored = {t: v for t, v in overall.items() if v is not None}
    if len(scored) >= 2:
        spread = max(scored.values()) - min(scored.values())
        if spread < MIN_MEANINGFUL_GAP:
            sentence += (" Their overall scores are close enough to be treated as "
                         "equivalent on the measures used here.")
    sentence += (" Which of these matters more depends on what you are looking for — "
                 "this comparison describes the differences rather than ranking them.")
    return sentence


def _caveats(tickers, names, by_ticker) -> list[str]:
    out = []
    for t in tickers:
        score = by_ticker[t]
        if score.overall is None:
            out.append(f"{names.get(t, t)} could not be scored overall — too little data.")
            continue
        if score.confidence != "high":
            out.append(
                f"{names.get(t, t)} is scored with {score.confidence} confidence "
                f"({score.coverage:.0%} of figures available), so its numbers are less reliable."
            )
        withheld = [c.label for c in score.categories.values() if not c.available]
        if withheld:
            out.append(
                f"{names.get(t, t)}: {', '.join(withheld)} could not be assessed."
            )
    sectors = {by_ticker[t].sector for t in tickers if by_ticker[t].sector}
    if len(sectors) > 1:
        out.append(
            "These companies are in different sectors. Valuation and balance-sheet "
            "measures are not directly comparable across industries — a normal debt "
            "level for a utility is not normal for a software company."
        )
    return out
