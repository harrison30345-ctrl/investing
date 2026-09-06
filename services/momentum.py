"""
Price momentum, kept strictly separate from business quality.

WHY THESE ARE NEVER COMBINED
----------------------------
The previous implementation produced a single "hot score" from weekly price
change, volume surge, RSI, distance from the moving average, and analyst
sentiment. No measure of the underlying business appeared in it at all. A
company with collapsing revenue and no profits could top the list purely
because its price had moved, and nothing on screen distinguished that from a
strong business whose price had also moved.

Those are two different situations and a reader needs to tell them apart, so
this module computes them separately and never averages them together. A
momentum score describes what a share price has done. It carries no information
about whether the company behind it is any good.

WHAT MOMENTUM IS NOT
--------------------
Momentum is a description of past price movement. It is not a prediction, and a
high momentum score is not a reason to buy anything. Strong momentum
historically persists sometimes and reverses sometimes; this module makes no
claim about which.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from services.scoring import ResearchScore

__all__ = [
    "MOMENTUM_VERSION", "MomentumResult", "assess_momentum",
    "MOMENTUM_COMPONENTS", "PROFILES",
]

MOMENTUM_VERSION = "1.0.0"

# Pure price and volume inputs. Deliberately no fundamentals and no analyst
# opinion: this score answers "what has the price done", nothing else.
MOMENTUM_COMPONENTS = [
    {"key": "chg_1w", "label": "1-week price change", "weight": 0.30,
     "floor": -10.0, "ceil": 12.0},
    {"key": "chg_1m", "label": "1-month price change", "weight": 0.30,
     "floor": -15.0, "ceil": 25.0},
    {"key": "vs_sma20", "label": "Price vs 20-day average", "weight": 0.20,
     "floor": -8.0, "ceil": 12.0},
    {"key": "vol_surge", "label": "Volume vs normal", "weight": 0.20,
     "floor": 0.6, "ceil": 2.5},
]

# Thresholds for describing, not ranking.
STRONG_MOMENTUM = 65.0
WEAK_FUNDAMENTALS = 45.0
STRONG_FUNDAMENTALS = 60.0

PROFILES = {
    "momentum_and_quality": (
        "Momentum with strong fundamentals",
        "The share price has risen and the underlying business also scores well.",
    ),
    "momentum_only": (
        "Price momentum only",
        "The share price has risen, but the business scores poorly on our measures. "
        "Price movement alone says nothing about the quality of a company.",
    ),
    "momentum_unknown": (
        "Price momentum, business unknown",
        "The share price has risen, but too little financial data was available to "
        "assess the business. The momentum figure describes the price only.",
    ),
    "quality_no_momentum": (
        "Strong business, quiet price",
        "The business scores well but the share price has not moved much recently.",
    ),
    "neither": (
        "Neither notable",
        "Neither the price movement nor the business measures stand out.",
    ),
}


def _normalise(value: float, floor: float, ceil: float) -> float:
    if ceil == floor:
        return 50.0
    return max(0.0, min(100.0, (value - floor) / (ceil - floor) * 100.0))


@dataclass(frozen=True)
class MomentumResult:
    ticker: str
    name: str
    momentum: float | None
    momentum_coverage: float
    fundamentals: float | None        # overall research score, or None
    fundamentals_confidence: str
    profile_key: str
    components: list[dict[str, Any]]
    version: str = MOMENTUM_VERSION

    @property
    def profile_label(self) -> str:
        return PROFILES[self.profile_key][0]

    @property
    def profile_note(self) -> str:
        return PROFILES[self.profile_key][1]

    @property
    def fundamentals_display(self) -> str:
        return "Not assessed" if self.fundamentals is None else f"{self.fundamentals:.0f}/100"


def _classify(momentum: float | None, fundamentals: float | None) -> str:
    strong_mom = momentum is not None and momentum >= STRONG_MOMENTUM
    if fundamentals is None:
        return "momentum_unknown" if strong_mom else "neither"
    strong_fun = fundamentals >= STRONG_FUNDAMENTALS
    weak_fun = fundamentals < WEAK_FUNDAMENTALS
    if strong_mom and strong_fun:
        return "momentum_and_quality"
    if strong_mom and weak_fun:
        return "momentum_only"
    if strong_mom:
        return "momentum_and_quality" if fundamentals >= WEAK_FUNDAMENTALS else "momentum_only"
    if strong_fun:
        return "quality_no_momentum"
    return "neither"


def assess_momentum(
    ticker: str,
    name: str,
    price_metrics: dict[str, Any],
    research: "ResearchScore | None" = None,
) -> MomentumResult:
    """Score price momentum, and report business quality alongside it separately.

    `price_metrics` carries only price and volume figures. `research` is the
    company's research score, reported next to the momentum figure and never
    blended into it. A company whose fundamentals could not be assessed reports
    that plainly rather than being scored as if it had none.
    """
    components, have, total = [], 0.0, 0.0
    for spec in MOMENTUM_COMPONENTS:
        total += spec["weight"]
        raw = price_metrics.get(spec["key"])
        if raw is None or isinstance(raw, bool):
            components.append({"label": spec["label"], "score": None, "raw": None})
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            components.append({"label": spec["label"], "score": None, "raw": None})
            continue
        if value != value:  # NaN
            components.append({"label": spec["label"], "score": None, "raw": None})
            continue
        score = _normalise(value, spec["floor"], spec["ceil"])
        components.append({"label": spec["label"], "score": score, "raw": value})
        have += spec["weight"]

    if have == 0:
        momentum, coverage = None, 0.0
    else:
        momentum = sum(
            c["score"] * s["weight"]
            for c, s in zip(components, MOMENTUM_COMPONENTS) if c["score"] is not None
        ) / have
        coverage = have / total

    fundamentals = research.overall if research is not None else None
    confidence = research.confidence if research is not None else "low"

    return MomentumResult(
        ticker=ticker,
        name=name,
        momentum=round(momentum, 1) if momentum is not None else None,
        momentum_coverage=coverage,
        fundamentals=fundamentals,
        fundamentals_confidence=confidence,
        profile_key=_classify(momentum, fundamentals),
        components=components,
    )
