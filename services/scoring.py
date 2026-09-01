"""
Research scoring engine.

Turns a company's raw fundamentals into five category scores, an overall score,
and a confidence rating — plus a record of every metric that was unavailable and
why. Pure functions over plain dicts: no Streamlit, no network, no globals, so
it can be unit-tested and reused by the backtester.

THE MISSING-DATA RULE
---------------------
The single most important property of this module: absent data is never
substituted with a value.

The previous implementation coerced missing fields with `or 0`, so a company
with no debt-to-equity figure was scored as having zero debt and received a
perfect financial-health score. Here, a metric that is missing is *excluded*,
the remaining weights in its category are renormalised, and the category's
confidence falls to reflect how much of it was actually measured. Below
MIN_COVERAGE the score is withheld entirely rather than published from a
minority of inputs.

One honest limitation, stated rather than hidden: excluding an unknown metric
cannot reproduce the score the company would have received had the value been
known — no method can, because the value is unknown. What this module
guarantees is narrower and testable: missing data never substitutes a
favourable value, never produces a maximum score, and always lowers confidence.
`test_missing_data.py` enforces exactly that.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

try:
    from config.scoring_config import (
        CATEGORY_LABELS, CATEGORY_WEIGHTS, CONFIDENCE_BANDS, METRICS,
        MIN_COVERAGE, NOT_COMPARABLE, SCORING_VERSION, SECTOR_OVERRIDES, band_for,
    )
except ImportError:  # running from inside the package directory
    from scoring_config import (  # type: ignore
        CATEGORY_LABELS, CATEGORY_WEIGHTS, CONFIDENCE_BANDS, METRICS,
        MIN_COVERAGE, NOT_COMPARABLE, SCORING_VERSION, SECTOR_OVERRIDES, band_for,
    )

__all__ = [
    "MetricResult", "CategoryScore", "ResearchScore",
    "score_company", "SCORING_VERSION",
]


# ── Reasons a metric may be unavailable ──────────────────────────────────────
MISSING_ABSENT = "not reported"
MISSING_NAN = "value not a number"
MISSING_SECTOR = "not comparable for this sector"


@dataclass(frozen=True)
class MetricResult:
    """One metric's contribution, or the reason it has none."""
    field: str
    label: str
    raw: float | None
    score: float | None
    weight: float
    available: bool
    reason: str = ""

    @property
    def display(self) -> str:
        return "Unavailable" if not self.available else f"{self.score:.0f}/100"


@dataclass(frozen=True)
class CategoryScore:
    """A category score plus the evidence behind it."""
    key: str
    label: str
    score: float | None          # None = withheld, insufficient data
    coverage: float              # 0..1 share of category weight available
    confidence: str              # "high" | "moderate" | "low"
    metrics: list[MetricResult] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.score is not None

    @property
    def missing(self) -> list[MetricResult]:
        return [m for m in self.metrics if not m.available]


@dataclass(frozen=True)
class ResearchScore:
    """Full result for one company."""
    ticker: str
    sector: str | None
    overall: float | None
    coverage: float
    confidence: str
    categories: dict[str, CategoryScore]
    version: str = SCORING_VERSION

    @property
    def available(self) -> bool:
        return self.overall is not None

    def as_dict(self) -> dict[str, Any]:
        """Flat, JSON-safe form — the shape persisted for score history."""
        return {
            "ticker": self.ticker,
            "sector": self.sector,
            "overall": self.overall,
            "coverage": round(self.coverage, 4),
            "confidence": self.confidence,
            "version": self.version,
            "categories": {
                k: {
                    "score": c.score,
                    "coverage": round(c.coverage, 4),
                    "confidence": c.confidence,
                    "missing": [m.field for m in c.missing],
                }
                for k, c in self.categories.items()
            },
        }


# ── Internals ────────────────────────────────────────────────────────────────

def _clean(value: Any) -> float | None:
    """Return a usable float, or None. Never returns a substitute value.

    Rejects None, non-numerics, NaN and infinities. Booleans are rejected
    explicitly: `isinstance(True, int)` is True in Python, and a stray boolean
    silently scoring as 1.0 is exactly the class of bug this module exists to
    prevent.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _spec_for(sector: str | None, spec: dict) -> dict:
    """Apply any documented sector override to a metric's bounds."""
    if not sector:
        return spec
    override = SECTOR_OVERRIDES.get(sector, {}).get(spec["field"])
    if not override:
        return spec
    return {**spec, **override}


def _normalise(raw: float, spec: dict) -> float:
    """Map a raw value onto 0..100 using the metric's bounds and direction."""
    value = raw * spec.get("scale", 1)
    lo, hi = float(spec["floor"]), float(spec["ceil"])
    if hi == lo:
        return 50.0
    pct = (value - lo) / (hi - lo)
    if spec["direction"] == "lower":
        pct = 1.0 - pct
    return max(0.0, min(100.0, pct * 100.0))


def _score_metric(data: dict, spec: dict, sector: str | None) -> MetricResult:
    fld, label, weight = spec["field"], spec["label"], spec["weight"]

    blocked = NOT_COMPARABLE.get(sector or "", {}).get(fld)
    if blocked:
        return MetricResult(fld, label, None, None, weight, False, blocked)

    if fld not in data:
        return MetricResult(fld, label, None, None, weight, False, MISSING_ABSENT)

    raw = _clean(data.get(fld))
    if raw is None:
        reason = MISSING_NAN if data.get(fld) is not None else MISSING_ABSENT
        return MetricResult(fld, label, None, None, weight, False, reason)

    resolved = _spec_for(sector, spec)
    return MetricResult(fld, label, raw, _normalise(raw, resolved), weight, True)


def _score_category(key: str, data: dict, sector: str | None) -> CategoryScore:
    results = [_score_metric(data, spec, sector) for spec in METRICS[key]]

    total_weight = sum(m.weight for m in results)
    have_weight = sum(m.weight for m in results if m.available)
    coverage = have_weight / total_weight if total_weight else 0.0

    # Withhold rather than publish a score built on a minority of its inputs.
    if coverage < MIN_COVERAGE or have_weight == 0:
        return CategoryScore(key, CATEGORY_LABELS[key], None, coverage,
                             band_for(coverage), results)

    # Renormalise across available metrics only. A missing metric contributes
    # nothing — it is never filled in with a neutral or favourable stand-in.
    score = sum(m.score * m.weight for m in results if m.available) / have_weight
    return CategoryScore(key, CATEGORY_LABELS[key], round(score, 1), coverage,
                         band_for(coverage), results)


_BAND_ORDER = {"low": 0, "moderate": 1, "high": 2}


def _overall_confidence(coverage: float, categories: dict[str, CategoryScore]) -> str:
    """Overall confidence is held down by the weakest category.

    Averaging coverage alone is misleading: a company missing every
    financial-health input still averages ~91% coverage across five categories
    and would report "high confidence" while one fifth of the analysis is
    unknown. Confidence is therefore capped by the least-well-covered category,
    and a category withheld entirely caps it at "low".
    """
    weakest = min(
        (_BAND_ORDER[c.confidence] for c in categories.values()),
        default=_BAND_ORDER["low"],
    )
    if any(not c.available for c in categories.values()):
        weakest = min(weakest, _BAND_ORDER["low"])
    capped = min(_BAND_ORDER[band_for(coverage)], weakest)
    return next(name for name, rank in _BAND_ORDER.items() if rank == capped)


def score_company(
    ticker: str,
    fundamentals: dict[str, Any],
    sector: str | None = None,
) -> ResearchScore:
    """Score one company.

    `fundamentals` is a plain dict of raw provider fields (yfinance naming today,
    but the engine never imports a provider — swapping providers is a mapping
    change in services/market_data.py, not a change here).

    Absent keys, None, NaN and non-numerics are all treated as unavailable.
    """
    categories = {k: _score_category(k, fundamentals, sector) for k in CATEGORY_WEIGHTS}

    # Overall is weighted across categories that produced a score, renormalised
    # over their weights. Coverage is weighted by each category's own coverage,
    # so a company scored on half its inputs cannot claim high confidence.
    avail = {k: c for k, c in categories.items() if c.available}
    total_w = sum(CATEGORY_WEIGHTS[k] for k in categories)
    have_w = sum(CATEGORY_WEIGHTS[k] for k in avail)

    coverage = sum(
        CATEGORY_WEIGHTS[k] * c.coverage for k, c in categories.items()
    ) / total_w if total_w else 0.0

    if not avail or coverage < MIN_COVERAGE:
        return ResearchScore(ticker, sector, None, coverage, band_for(coverage), categories)

    overall = sum(categories[k].score * CATEGORY_WEIGHTS[k] for k in avail) / have_w
    return ResearchScore(ticker, sector, round(overall, 1), coverage,
                         _overall_confidence(coverage, categories), categories)
