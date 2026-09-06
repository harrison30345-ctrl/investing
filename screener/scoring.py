from __future__ import annotations

"""
Scoring module — produces a composite 0–100 score per stock based on weighted metrics.

Missing data gets the category average (neutral treatment).
"""

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default category weights (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "profitability": 0.30,
    "financial_health": 0.25,
    "valuation": 0.20,
    "growth": 0.15,
    "cash_flow": 0.10,
}

# Metrics in each category and how to normalize them.
# "higher_better": True means higher values → higher score
# "range": (low, high) defines the 0–100 normalization range
METRIC_DEFINITIONS = {
    "profitability": {
        "roe":              {"higher_better": True,  "range": (0, 40)},
        "roa":              {"higher_better": True,  "range": (0, 20)},
        "roic":             {"higher_better": True,  "range": (0, 30)},
        "gross_margin":     {"higher_better": True,  "range": (0, 80)},
        "operating_margin": {"higher_better": True,  "range": (0, 40)},
        "net_margin":       {"higher_better": True,  "range": (0, 30)},
    },
    "financial_health": {
        "debt_to_equity":    {"higher_better": False, "range": (0, 3)},
        "current_ratio":     {"higher_better": True,  "range": (0, 3)},
        "quick_ratio":       {"higher_better": True,  "range": (0, 3)},
        "interest_coverage": {"higher_better": True,  "range": (0, 20)},
        "net_debt_to_ebitda": {"higher_better": False, "range": (-1, 5)},
    },
    "valuation": {
        "pe_trailing": {"higher_better": False, "range": (5, 40)},
        "pe_forward":  {"higher_better": False, "range": (5, 35)},
        "pb":          {"higher_better": False, "range": (0.5, 10)},
        "ps":          {"higher_better": False, "range": (0.5, 15)},
        "ev_ebitda":   {"higher_better": False, "range": (3, 25)},
        "peg":         {"higher_better": False, "range": (0, 3)},
    },
    "growth": {
        "revenue_growth_1y":      {"higher_better": True, "range": (-10, 50)},
        "revenue_growth_3y_cagr": {"higher_better": True, "range": (-5, 30)},
        "revenue_growth_5y_cagr": {"higher_better": True, "range": (-5, 25)},
        "eps_growth_1y":          {"higher_better": True, "range": (-20, 60)},
        "eps_growth_3y_cagr":     {"higher_better": True, "range": (-10, 30)},
        "fcf_growth_1y":          {"higher_better": True, "range": (-20, 50)},
    },
    "cash_flow": {
        "fcf_yield":  {"higher_better": True, "range": (0, 12)},
        "fcf_margin": {"higher_better": True, "range": (0, 30)},
    },
}


def _normalize(value: float, low: float, high: float, higher_better: bool) -> float:
    """Normalize a value to 0–100 scale."""
    if high == low:
        return 50.0
    clamped = max(low, min(high, value))
    score = (clamped - low) / (high - low) * 100
    if not higher_better:
        score = 100 - score
    return score


def score_stock(metrics: dict, config: dict | None = None) -> dict:
    """
    Compute composite score for a single stock.

    Returns dict with:
        - composite_score: float 0–100
        - category_scores: {category: float}
        - metric_scores: {metric: float}  (individual normalized scores)
    """
    weights = DEFAULT_WEIGHTS.copy()
    metric_overrides = {}

    if config:
        if "scoring" in config:
            scoring_config = config["scoring"]
            if "weights" in scoring_config:
                for cat, w in scoring_config["weights"].items():
                    if cat in weights:
                        weights[cat] = w
            if "metric_ranges" in scoring_config:
                metric_overrides = scoring_config["metric_ranges"]

    # Normalize weights to sum to 1.0
    total_weight = sum(weights.values())
    if total_weight > 0:
        weights = {k: v / total_weight for k, v in weights.items()}

    category_scores = {}
    metric_scores = {}

    for category, metric_defs in METRIC_DEFINITIONS.items():
        scores_in_category = []

        for metric_name, defn in metric_defs.items():
            value = metrics.get(metric_name)

            # Apply config overrides for ranges
            if metric_name in metric_overrides:
                override = metric_overrides[metric_name]
                low = override.get("low", defn["range"][0])
                high = override.get("high", defn["range"][1])
                higher_better = override.get("higher_better", defn["higher_better"])
            else:
                low, high = defn["range"]
                higher_better = defn["higher_better"]

            if value is not None:
                score = _normalize(value, low, high, higher_better)
                metric_scores[metric_name] = round(score, 1)
                scores_in_category.append(score)
            else:
                metric_scores[metric_name] = None  # Will be filled with average later

        # Calculate category average (excluding None)
        valid_scores = [s for s in scores_in_category if s is not None]
        if valid_scores:
            cat_avg = sum(valid_scores) / len(valid_scores)
            # Fill missing metrics with category average (neutral treatment)
            for metric_name in metric_defs:
                if metric_scores.get(metric_name) is None:
                    metric_scores[metric_name] = round(cat_avg, 1)
                    scores_in_category.append(cat_avg)
            category_scores[category] = round(cat_avg, 1)
        else:
            category_scores[category] = 50.0  # Complete data gap: neutral

    # Composite score
    composite = sum(
        category_scores.get(cat, 50.0) * weight
        for cat, weight in weights.items()
    )

    return {
        "composite_score": round(composite, 1),
        "category_scores": category_scores,
        "metric_scores": metric_scores,
    }


def score_batch(all_metrics: list[dict], config: dict | None = None) -> list[dict]:
    """
    Score a batch of stocks and merge scores into metrics dicts.
    Returns list sorted by composite_score descending.
    """
    results = []
    for metrics in all_metrics:
        score_data = score_stock(metrics, config)
        merged = {**metrics}
        merged["composite_score"] = score_data["composite_score"]
        for cat, score in score_data["category_scores"].items():
            merged[f"score_{cat}"] = score
        results.append(merged)

    results.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    return results
