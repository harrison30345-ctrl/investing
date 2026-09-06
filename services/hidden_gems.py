"""
Hidden Gems methodology.

WHAT "HIDDEN GEM" MEANS HERE
----------------------------
A company that looks like a sound business on its reported figures, is
reasonably priced relative to those figures, and is followed by relatively few
analysts.

The important word is *and*. Every criterion below is a GATE: a company must
pass all of them to appear. This is deliberately different from the previous
implementation, which blended "hiddenness" into a weighted average alongside
quality and valuation. Under a weighted average, a company followed by no
analysts scored maximum points for obscurity, which could carry it into the
results despite mediocre fundamentals -- so the list filled up with companies
whose main qualification was that nobody was looking at them.

Low analyst coverage does not make a company undervalued. Most companies are
under-covered because they are small or unremarkable. Coverage is therefore
used only to answer "is this overlooked?" *after* the business has already
passed the quality, health, valuation and growth gates.

Ranking among qualifying companies is by research score, not by how obscure
they are. Being less known is a qualifying condition, never a merit.

WHAT THIS CANNOT TELL YOU
-------------------------
Passing these gates does not mean a company is undervalued. It means its
reported figures look sound and its valuation multiples are not stretched
relative to the range we score against. A low valuation score can equally
reflect a market view of risk that the figures do not yet show.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

try:
    from services.scoring import ResearchScore, score_company
except ImportError:  # pragma: no cover
    from scoring import ResearchScore, score_company  # type: ignore

__all__ = ["GEM_GATES", "GemResult", "assess_gem", "METHODOLOGY_VERSION"]

METHODOLOGY_VERSION = "1.0.0"

# Thresholds. Kept here so the methodology is inspectable and adjustable in one
# place rather than scattered through UI code.
MIN_QUALITY = 55.0        # a genuinely profitable business
MIN_HEALTH = 45.0         # not financially distressed
MIN_VALUATION = 50.0      # not paying a stretched multiple
MIN_GROWTH = 20.0         # not visibly shrinking -- calibrated below
MAX_ANALYSTS = 15         # "overlooked" -- see note below
MAX_MARKET_CAP = 20_000_000_000.0   # £/$20bn: mid-cap and below

# MIN_GROWTH is calibrated against what the growth category actually returns,
# rather than picked as a round number. Measured reference points:
#
#   revenue -20%, earnings -25%  (clearly shrinking)  ->   0/100
#   revenue  -5%, earnings  -5%  (mildly shrinking)   ->   4/100
#   revenue   0%, earnings   0%  (flat)               ->  13/100
#   revenue  +5%, earnings  +5%  (modest growth)      ->  22/100
#   revenue +14%, earnings +16%  (solid growth)       ->  39/100
#
# A threshold of 20 admits companies growing at roughly 5% or better and
# excludes flat or declining ones, which is what "not shrinking" should mean.
# An earlier value of 40 silently required ~15% growth -- far stricter than the
# gate's own label claimed, and it would have excluded most sound, steady
# businesses of exactly the kind this screen exists to surface.

# MAX_ANALYSTS is a judgement, not a discovered constant. Large caps are
# typically covered by 25-50 analysts; under ~15 is genuinely thin coverage for
# a listed company. It is set conservatively because the cost of a false
# positive here (surfacing a company as "overlooked" when it is simply small
# and unremarkable) is worse than missing one.


@dataclass(frozen=True)
class Gate:
    key: str
    label: str
    why: str
    test: Callable[[ResearchScore, dict[str, Any]], bool | None]
    evidence: Callable[[ResearchScore, dict[str, Any]], str]


def _cat(score: ResearchScore, key: str) -> float | None:
    cat = score.categories.get(key)
    return cat.score if cat and cat.available else None


def _quality_test(s: ResearchScore, f: dict) -> bool | None:
    v = _cat(s, "quality")
    return None if v is None else v >= MIN_QUALITY


def _health_test(s: ResearchScore, f: dict) -> bool | None:
    v = _cat(s, "financial_health")
    # A category withheld because it is not comparable for the sector (banks,
    # utilities) must not silently pass this gate. It is unknown, not fine.
    return None if v is None else v >= MIN_HEALTH


def _valuation_test(s: ResearchScore, f: dict) -> bool | None:
    v = _cat(s, "valuation")
    return None if v is None else v >= MIN_VALUATION


def _growth_test(s: ResearchScore, f: dict) -> bool | None:
    v = _cat(s, "growth")
    return None if v is None else v >= MIN_GROWTH


def _attention_test(s: ResearchScore, f: dict, max_analysts: int = MAX_ANALYSTS) -> bool | None:
    """Overlooked = thin analyst coverage, or small enough to be off the radar.

    Returns None when neither figure is available: absence of coverage data is
    not evidence of low coverage.
    """
    analysts = f.get("numberOfAnalystOpinions")
    cap = f.get("marketCap")
    if analysts is None and cap is None:
        return None
    if analysts is not None and analysts <= max_analysts:
        return True
    if cap is not None and cap <= MAX_MARKET_CAP:
        return True
    return False


def _ev_quality(s, f):
    v = _cat(s, "quality")
    return f"Quality {v:.0f}/100" if v is not None else "Quality unavailable"


def _ev_health(s, f):
    v = _cat(s, "financial_health")
    return f"Financial health {v:.0f}/100" if v is not None else "Financial health unavailable"


def _ev_valuation(s, f):
    v = _cat(s, "valuation")
    return f"Valuation {v:.0f}/100" if v is not None else "Valuation unavailable"


def _ev_growth(s, f):
    v = _cat(s, "growth")
    return f"Growth {v:.0f}/100" if v is not None else "Growth unavailable"


def _ev_attention(s, f):
    analysts, cap = f.get("numberOfAnalystOpinions"), f.get("marketCap")
    bits = []
    if analysts is not None:
        bits.append(f"{int(analysts)} analyst{'s' if analysts != 1 else ''} covering")
    if cap is not None:
        bits.append(f"{cap / 1e9:.1f}bn market value")
    return ", ".join(bits) if bits else "Coverage data unavailable"


GEM_GATES: list[Gate] = [
    Gate("quality", "Sound business",
         "Profitable and efficient enough to be worth a closer look.",
         _quality_test, _ev_quality),
    Gate("financial_health", "Not distressed",
         "Able to service its debts from what it owns and earns.",
         _health_test, _ev_health),
    Gate("valuation", "Reasonably priced",
         "Not trading on a stretched multiple relative to what it earns.",
         _valuation_test, _ev_valuation),
    Gate("growth", "Not shrinking",
         "Sales and profits are not in visible decline.",
         _growth_test, _ev_growth),
    Gate("attention", "Overlooked",
         "Followed by relatively few analysts, or small enough to be off the radar. "
         "This is a qualifying condition, not a merit -- it is checked last and "
         "never compensates for a weak business.",
         _attention_test, _ev_attention),
]


@dataclass(frozen=True)
class GemResult:
    ticker: str
    name: str
    qualifies: bool
    score: ResearchScore
    passed: list[tuple[str, str]]        # (label, evidence)
    failed: list[tuple[str, str]]
    unknown: list[tuple[str, str]]

    @property
    def rank_score(self) -> float:
        """Qualifying companies rank on research score, never on obscurity."""
        return self.score.overall if self.score.overall is not None else -1.0

    @property
    def why_lines(self) -> list[str]:
        return [f"{label} — {evidence}" for label, evidence in self.passed]


def assess_gem(
    ticker: str,
    name: str,
    fundamentals: dict[str, Any],
    sector: str | None = None,
    score: ResearchScore | None = None,
    max_analysts: int = MAX_ANALYSTS,
) -> GemResult:
    """Run every gate and report exactly which ones a company passed.

    A gate that cannot be evaluated counts as unknown, not as passed. A company
    is only a Hidden Gem if every gate returns True.

    `max_analysts` loosens or tightens only the attention gate. It cannot make a
    weak business qualify -- the other four gates are unaffected by it.
    """
    score = score or score_company(ticker, fundamentals, sector)

    passed, failed, unknown = [], [], []
    for gate in GEM_GATES:
        if gate.key == "attention":
            outcome = _attention_test(score, fundamentals, max_analysts)
        else:
            outcome = gate.test(score, fundamentals)
        entry = (gate.label, gate.evidence(score, fundamentals))
        if outcome is True:
            passed.append(entry)
        elif outcome is False:
            failed.append(entry)
        else:
            unknown.append(entry)

    return GemResult(
        ticker=ticker,
        name=name,
        qualifies=len(passed) == len(GEM_GATES),
        score=score,
        passed=passed,
        failed=failed,
        unknown=unknown,
    )
