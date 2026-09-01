"""
Central configuration for the research scoring engine.

Everything the engine needs to turn raw company data into category scores lives
here: the category weights, the metric definitions, and the sector rules. No
weights or thresholds should be hardcoded anywhere else in the codebase.

Bumping SCORING_VERSION invalidates stored score history — a score is only
comparable to another score produced by the same methodology version.
"""
from __future__ import annotations

SCORING_VERSION = "1.0.0"

# ── Category weights (must sum to 1.0) ───────────────────────────────────────
CATEGORY_WEIGHTS: dict[str, float] = {
    "quality": 0.25,
    "growth": 0.20,
    "valuation": 0.20,
    "financial_health": 0.20,
    "momentum": 0.15,
}

CATEGORY_LABELS: dict[str, str] = {
    "quality": "Quality",
    "growth": "Growth",
    "valuation": "Valuation",
    "financial_health": "Financial Health",
    "momentum": "Momentum",
}

# ── Confidence thresholds ────────────────────────────────────────────────────
# Confidence is the share of a category's weight that was actually available.
# Below MIN_COVERAGE we refuse to publish a score at all rather than present a
# number derived from a minority of its inputs.
MIN_COVERAGE = 0.50
CONFIDENCE_BANDS = (
    (0.85, "high"),
    (0.60, "moderate"),
    (0.00, "low"),
)


# ── Metric definitions ───────────────────────────────────────────────────────
# Each metric declares:
#   field      — key in the fundamentals dict
#   weight     — weight within its category
#   direction  — "higher" if a larger raw value is better, else "lower"
#   floor/ceil — raw values mapped onto 0..100 between these bounds
#   scale      — multiplier applied to the raw value before scoring
#                (yfinance returns ratios like 0.23 for 23%)
#   label      — beginner-facing name
#
# IMPORTANT: there is deliberately no "default" key. A metric that is absent is
# absent. It is never substituted with a neutral or favourable stand-in.
METRICS: dict[str, list[dict]] = {
    "quality": [
        {"field": "returnOnEquity", "weight": 0.35, "direction": "higher",
         "floor": 0.0, "ceil": 40.0, "scale": 100, "label": "Return on equity"},
        {"field": "profitMargins", "weight": 0.35, "direction": "higher",
         "floor": 0.0, "ceil": 30.0, "scale": 100, "label": "Net profit margin"},
        {"field": "operatingMargins", "weight": 0.30, "direction": "higher",
         "floor": 0.0, "ceil": 35.0, "scale": 100, "label": "Operating margin"},
    ],
    "growth": [
        {"field": "revenueGrowth", "weight": 0.50, "direction": "higher",
         "floor": -5.0, "ceil": 50.0, "scale": 100, "label": "Revenue growth"},
        {"field": "earningsGrowth", "weight": 0.50, "direction": "higher",
         "floor": -10.0, "ceil": 50.0, "scale": 100, "label": "Earnings growth"},
    ],
    "valuation": [
        {"field": "trailingPE", "weight": 0.40, "direction": "lower",
         "floor": 5.0, "ceil": 40.0, "scale": 1, "label": "P/E ratio"},
        {"field": "forwardPE", "weight": 0.30, "direction": "lower",
         "floor": 5.0, "ceil": 35.0, "scale": 1, "label": "Forward P/E"},
        {"field": "priceToSalesTrailing12Months", "weight": 0.30, "direction": "lower",
         "floor": 0.5, "ceil": 15.0, "scale": 1, "label": "Price to sales"},
    ],
    "financial_health": [
        {"field": "debtToEquity", "weight": 0.45, "direction": "lower",
         "floor": 0.0, "ceil": 200.0, "scale": 1, "label": "Debt to equity"},
        {"field": "currentRatio", "weight": 0.30, "direction": "higher",
         "floor": 0.5, "ceil": 3.0, "scale": 1, "label": "Current ratio"},
        {"field": "freeCashflow", "weight": 0.25, "direction": "higher",
         "floor": 0.0, "ceil": 5_000_000_000.0, "scale": 1, "label": "Free cash flow"},
    ],
    "momentum": [
        {"field": "chg_1w", "weight": 0.20, "direction": "higher",
         "floor": -10.0, "ceil": 10.0, "scale": 1, "label": "1-week price change"},
        {"field": "chg_1m", "weight": 0.30, "direction": "higher",
         "floor": -15.0, "ceil": 20.0, "scale": 1, "label": "1-month price change"},
        {"field": "chg_3m", "weight": 0.30, "direction": "higher",
         "floor": -25.0, "ceil": 35.0, "scale": 1, "label": "3-month price change"},
        {"field": "vs_sma50", "weight": 0.20, "direction": "higher",
         "floor": -15.0, "ceil": 15.0, "scale": 1, "label": "Price vs 50-day average"},
    ],
}


# ── Sector rules ─────────────────────────────────────────────────────────────
# Deliberately conservative. We do NOT invent sector benchmarks we cannot
# justify. Where a metric is structurally not comparable for a sector, it is
# marked NOT COMPARABLE and excluded from scoring, with the reason surfaced to
# the user — which is honest, where a fabricated threshold would not be.
#
# Rationale for each exclusion:
#   Banks/insurers  — leverage is the business model; debt-to-equity and current
#                     ratio do not carry their usual meaning.
#   REITs           — depreciation makes reported earnings and margins
#                     misleading; the sector is analysed on FFO, which is not
#                     available from the current data source.
#   Utilities       — high regulated leverage is structural, not distress.
#   Biotech         — pre-revenue companies have no meaningful margins, and
#                     earnings growth off a negative base is not interpretable.
NOT_COMPARABLE: dict[str, dict[str, str]] = {
    "Financial Services": {
        "debtToEquity": "Leverage is intrinsic to how banks and insurers operate.",
        "currentRatio": "Current ratio is not meaningful for financial firms.",
    },
    "Real Estate": {
        "profitMargins": "Depreciation distorts reported margins for REITs.",
        "operatingMargins": "Depreciation distorts reported margins for REITs.",
    },
    "Utilities": {
        "debtToEquity": "High regulated leverage is structural for utilities.",
    },
    "Biotechnology": {
        "profitMargins": "Pre-revenue biotech has no meaningful profit margin.",
        "operatingMargins": "Pre-revenue biotech has no meaningful operating margin.",
        "earningsGrowth": "Earnings growth from a negative base is not interpretable.",
    },
}

# Sectors where we hold a documented view that a threshold should differ.
# Empty by default — populate only with thresholds that can be justified, never
# to make the model look more sophisticated than the evidence supports.
SECTOR_OVERRIDES: dict[str, dict[str, dict]] = {
    "Technology": {
        # Software carries structurally higher sales multiples; scoring it on the
        # market-wide P/S band would mark the whole sector expensive.
        "priceToSalesTrailing12Months": {"floor": 1.0, "ceil": 25.0},
    },
    "Utilities": {
        # Utilities are stable, low-growth by design; the market-wide growth band
        # would score the entire sector near zero.
        "revenueGrowth": {"floor": -5.0, "ceil": 15.0},
    },
}


def band_for(coverage: float) -> str:
    """Map a 0..1 coverage ratio onto a confidence band label."""
    for threshold, label in CONFIDENCE_BANDS:
        if coverage >= threshold:
            return label
    return "low"
