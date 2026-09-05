"""
Plain-English explanation engine.

Turns a ResearchScore into sentences a beginner can read without knowing what
P/E, ROE or RSI mean. This is the product's main differentiator: competitors
show a wall of ratios and leave the reader to interpret it.

RULES
-----
1. Never state anything the data does not show. Every sentence is generated
   from a metric that was actually available.
2. Never predict. Describe what the figures show and what would change them.
3. Never instruct. No buy, sell, entry, exit, sizing or timing language --
   see the LANGUAGE POLICY note in screener/dashboard.py.
4. Always surface the other side. A company with strengths also has risks, and
   both are shown.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from services.scoring import ResearchScore

__all__ = ["GLOSSARY", "explain", "format_value", "MetricHelp"]


class MetricHelp(dict):
    """A metric's beginner explanation: what it is, why it matters, direction."""


# Every metric shown to a user must appear here. The dashboard renders these as
# tooltips so no number is presented without an explanation.
GLOSSARY: dict[str, MetricHelp] = {
    "returnOnEquity": MetricHelp(
        label="Return on equity",
        means="How much profit the company makes from the money shareholders have put in.",
        matters="A consistently high figure suggests the company uses its funding efficiently. "
                "It can also be flattered by heavy borrowing, so it is read alongside debt.",
        better="higher",
        unit="%",
    ),
    "profitMargins": MetricHelp(
        label="Net profit margin",
        means="How much of each £1 of sales is left as profit after all costs.",
        matters="Higher margins give a company more room to absorb rising costs or price competition.",
        better="higher",
        unit="%",
    ),
    "operatingMargins": MetricHelp(
        label="Operating margin",
        means="Profit from the core business, before interest and tax.",
        matters="Shows whether the main operation is profitable, separately from how it is financed.",
        better="higher",
        unit="%",
    ),
    "revenueGrowth": MetricHelp(
        label="Revenue growth",
        means="How fast sales are growing compared with the same period last year.",
        matters="Sales growth is usually what drives long-term profit growth. Falling revenue is "
                "harder to offset than a temporary dip in profit.",
        better="higher",
        unit="%",
    ),
    "earningsGrowth": MetricHelp(
        label="Earnings growth",
        means="How fast profits are growing compared with the same period last year.",
        matters="Growing profits can support a higher share price over time, though a single "
                "year's figure can be distorted by one-off items.",
        better="higher",
        unit="%",
    ),
    "trailingPE": MetricHelp(
        label="P/E ratio",
        means="The share price divided by the profit per share over the last year. A P/E of 20 "
              "means you pay £20 for every £1 of annual profit.",
        matters="A higher P/E can reflect strong growth expectations, but it also means investors "
                "are paying more for today's earnings, leaving less room for disappointment.",
        better="lower",
        unit="×",
    ),
    "forwardPE": MetricHelp(
        label="Forward P/E",
        means="The same calculation using analysts' forecast profits for next year.",
        matters="If it is lower than the current P/E, analysts expect profits to grow. Those "
                "forecasts are estimates and are often revised.",
        better="lower",
        unit="×",
    ),
    "priceToSalesTrailing12Months": MetricHelp(
        label="Price to sales",
        means="The company's market value divided by its annual sales.",
        matters="Useful for companies that are not yet profitable, where P/E cannot be calculated. "
                "Normal levels differ sharply between industries.",
        better="lower",
        unit="×",
    ),
    "debtToEquity": MetricHelp(
        label="Debt to equity",
        means="How much the company has borrowed compared with shareholders' money in the business.",
        matters="More debt magnifies both gains and losses, and interest must be paid whatever "
                "happens to profits. What counts as normal varies by industry.",
        better="lower",
        unit="%",
    ),
    "currentRatio": MetricHelp(
        label="Current ratio",
        means="Short-term assets divided by short-term bills. Above 1 means it can cover the "
              "next year's obligations from assets it already holds.",
        matters="A low figure can signal difficulty paying bills; a very high one can mean cash "
                "is sitting idle.",
        better="higher",
        unit="×",
    ),
    "freeCashflow": MetricHelp(
        label="Free cash flow",
        means="Cash left over after running the business and paying for equipment and property.",
        matters="Cash is harder to flatter with accounting choices than reported profit, so it is "
                "often a better guide to financial health.",
        better="higher",
        unit="",
    ),
    "chg_1w": MetricHelp(
        label="1-week price change",
        means="How much the share price has moved over the past week.",
        matters="Short-term moves are mostly noise and say little about the business.",
        better="higher",
        unit="%",
    ),
    "chg_1m": MetricHelp(
        label="1-month price change",
        means="How much the share price has moved over the past month.",
        matters="Useful for spotting a recent change in direction, but a month is still a short "
                "period for judging a company.",
        better="higher",
        unit="%",
    ),
    "chg_3m": MetricHelp(
        label="3-month price change",
        means="How much the share price has moved over the past three months.",
        matters="Long enough to show a trend, short enough that it can reverse quickly.",
        better="higher",
        unit="%",
    ),
    "vs_sma50": MetricHelp(
        label="Price vs 50-day average",
        means="How far the price sits above or below its average over the last 50 trading days.",
        matters="A common way to describe whether a stock is in an up or down trend. It describes "
                "the past and does not indicate what happens next.",
        better="higher",
        unit="%",
    ),
    "beta": MetricHelp(
        label="Beta",
        means="How much the shares have moved relative to the wider market. A beta of 1.5 means "
              "they have typically moved 1.5% for every 1% market move.",
        matters="Higher beta means bigger swings in both directions, not higher expected returns.",
        better="lower",
        unit="",
    ),
}

# Wording for a category score, keyed by band.
_BAND_WORDS = [
    (80, "a clear strength"),
    (65, "solid"),
    (50, "middling"),
    (35, "a weak point"),
    (0, "a significant weakness"),
]

_CATEGORY_PLAIN = {
    "quality": "how profitable and efficient the business is",
    "growth": "how fast sales and profits are growing",
    "valuation": "how much you pay for what the company earns",
    "financial_health": "how comfortably it can pay its debts",
    "momentum": "how the share price has moved recently",
}

# What a measurable improvement in each category would actually look like.
# Phrased as concrete, checkable developments rather than restating the label.
_CATEGORY_IMPROVES = {
    "quality": "margins or return on equity rise in the next set of results",
    "growth": "revenue or earnings growth accelerates",
    "valuation": "profits grow faster than the share price, or the price falls",
    "financial_health": "debt is reduced, or cash generation improves",
    "momentum": "the share price strengthens relative to its recent average",
}
_CATEGORY_WORSENS = {
    "quality": "margins or return on equity decline",
    "growth": "revenue or earnings growth slows",
    "valuation": "the share price rises faster than profits",
    "financial_health": "debt rises, or cash generation weakens",
    "momentum": "the share price weakens relative to its recent average",
}


def _band_word(score: float) -> str:
    for threshold, word in _BAND_WORDS:
        if score >= threshold:
            return word
    return "a significant weakness"


def format_value(field: str, raw: float) -> str:
    """Format a raw provider value for display, applying the metric's unit."""
    help_ = GLOSSARY.get(field, {})
    unit = help_.get("unit", "")
    if field in ("returnOnEquity", "profitMargins", "operatingMargins",
                 "revenueGrowth", "earningsGrowth"):
        return f"{raw * 100:.1f}%"      # provider returns ratios
    if field == "freeCashflow":
        for div, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
            if abs(raw) >= div:
                return f"{raw / div:,.1f}{suffix}"
        return f"{raw:,.0f}"
    return f"{raw:,.1f}{unit}"


def explain(score: "ResearchScore", name: str) -> dict[str, object]:
    """Build the plain-English sections for one company.

    Returns keys: summary, strengths, concerns, could_change, unavailable.
    Every item is derived from a metric that was actually available.
    """
    cats = score.categories

    # ── Summary ──────────────────────────────────────────────
    if score.overall is None:
        summary = (
            f"There is not enough reliable data to score {name}. "
            f"Only {score.coverage:.0%} of the figures needed were available from the data "
            f"provider, so no overall score is shown rather than presenting a misleading one."
        )
        return {"summary": summary, "strengths": [], "concerns": [],
                "could_change": [], "unavailable": _unavailable(score)}

    scored = {k: c for k, c in cats.items() if c.available}
    best = max(scored.items(), key=lambda kv: kv[1].score) if scored else None
    worst = min(scored.items(), key=lambda kv: kv[1].score) if scored else None

    summary = (
        f"{name} scores {score.overall:.0f} out of 100 overall, based on {score.coverage:.0%} "
        f"of the figures we look for. "
    )
    if best and worst and best[0] != worst[0]:
        summary += (
            f"Its strongest area is {best[1].label.lower()} at {best[1].score:.0f} — "
            f"{_CATEGORY_PLAIN[best[0]]}. Its weakest is {worst[1].label.lower()} at "
            f"{worst[1].score:.0f}. "
        )
    if score.confidence != "high":
        summary += (
            f"Confidence is {score.confidence} because some figures were unavailable, so treat "
            f"this score as a rough guide rather than a precise measure. "
        )
    summary += "A score describes what the reported figures show today; it is not a forecast."

    # ── Strengths and concerns, drawn from actual metrics ────
    strengths, concerns = [], []
    for key, cat in cats.items():
        if not cat.available:
            continue
        for metric in cat.metrics:
            if not metric.available or metric.score is None:
                continue
            help_ = GLOSSARY.get(metric.field)
            if not help_:
                continue
            value = format_value(metric.field, metric.raw)
            if metric.score >= 75:
                strengths.append(
                    f"**{help_['label']} of {value}** — {help_['means']} "
                    f"This is strong relative to the range we score against."
                )
            elif metric.score <= 30:
                concerns.append(
                    f"**{help_['label']} of {value}** — {help_['means']} "
                    f"This is weak relative to the range we score against."
                )

    # Missing data is itself a concern worth stating plainly.
    for key, cat in cats.items():
        if not cat.available:
            concerns.append(
                f"**{cat.label} could not be assessed** — too many of the underlying figures "
                f"were unavailable from the data provider, so this part of the picture is unknown."
            )

    strengths.sort(key=len)
    concerns.sort(key=len)

    # ── What could change the score ──────────────────────────
    could_change = []
    for key, cat in sorted(scored.items(), key=lambda kv: kv[1].score):
        if cat.score < 50:
            could_change.append(
                f"**{cat.label} ({cat.score:.0f}/100)** would rise if {_CATEGORY_IMPROVES[key]}."
            )
        elif cat.score > 75:
            could_change.append(
                f"**{cat.label} ({cat.score:.0f}/100)** is currently a strength; it would fall if "
                f"{_CATEGORY_WORSENS[key]}."
            )
    if score.confidence != "high":
        could_change.append(
            "**Confidence** would rise if the missing figures became available — the score itself "
            "may move up or down once they do."
        )

    return {
        "summary": summary,
        "strengths": strengths[:5],
        "concerns": concerns[:5],
        "could_change": could_change[:5],
        "unavailable": _unavailable(score),
    }


def _unavailable(score: "ResearchScore") -> list[str]:
    out = []
    for cat in score.categories.values():
        for metric in cat.missing:
            out.append(f"{metric.label} — {metric.reason}")
    return out
