"""
Deterministic descriptions derived from scores.

Every phrase here is produced by a lookup against a score band, not generated.
The same inputs always produce the same words, the vocabulary is fixed and
inspectable, and nothing is invented — which is why this is a module of tables
rather than a text generator.

Two things are produced:

`rank_reason`  — the one-line reason a company sits where it does in a ranked
                 list, built from its strongest and weakest categories.
`situation`    — a controlled-vocabulary reading of each category, so
                 "Valuation: Expensive" always means the same score band.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from services.scoring import ResearchScore

__all__ = ["rank_reason", "situation", "SITUATION_WORDS", "CATEGORY_PHRASES"]


# Controlled vocabulary. Bands are (minimum score, word).
# Valuation reads in the opposite direction to the others: a high valuation
# score means the shares are priced modestly, which is worth saying in words
# rather than leaving the reader to invert a number.
SITUATION_WORDS: dict[str, list] = {
    "quality": [(80, "Very strong"), (65, "Strong"), (50, "Moderate"),
                (35, "Weak"), (0, "Very weak")],
    "growth": [(80, "Very strong"), (65, "Strong"), (50, "Moderate"),
               (35, "Slow"), (0, "Declining")],
    "valuation": [(80, "Inexpensive"), (65, "Reasonable"), (50, "Moderate"),
                  (35, "Demanding"), (0, "Expensive")],
    "financial_health": [(80, "Very strong"), (65, "Strong"), (50, "Adequate"),
                         (35, "Stretched"), (0, "Strained")],
    "momentum": [(80, "Strong"), (65, "Positive"), (50, "Steady"),
                 (35, "Soft"), (0, "Negative")],
}

# Short phrases used to build a ranking reason. Index 0 is the strength
# reading, index 1 the weakness reading.
CATEGORY_PHRASES: dict[str, tuple] = {
    "quality": ("strong profitability", "thin profitability"),
    "growth": ("strong growth", "slow growth"),
    "valuation": ("undemanding valuation", "expensive valuation"),
    "financial_health": ("healthy balance sheet", "stretched balance sheet"),
    "momentum": ("positive price momentum", "weak price momentum"),
}

STRONG = 70.0
WEAK = 45.0


def _word(key: str, score: float) -> str:
    for threshold, word in SITUATION_WORDS[key]:
        if score >= threshold:
            return word
    return SITUATION_WORDS[key][-1][1]


def situation(score: "ResearchScore") -> list:
    """Each category as (label, word, score). Unscored categories are named."""
    out = []
    for key, cat in score.categories.items():
        if cat.available:
            out.append((cat.label, _word(key, cat.score), cat.score))
        else:
            out.append((cat.label, "Not assessed", None))
    return out


def rank_reason(score: "ResearchScore") -> str:
    """One short line explaining where a company sits and why.

    Built from the highest and lowest scoring categories. Roughly eight to
    twelve words, and it names a weakness whenever there is a real one, so a
    ranked list cannot read as a list of endorsements.
    """
    scored = {k: c.score for k, c in score.categories.items() if c.available}
    if not scored:
        return "Too little data to assess"

    best_key = max(scored, key=lambda k: scored[k])
    worst_key = min(scored, key=lambda k: scored[k])
    best, worst = scored[best_key], scored[worst_key]

    strength = CATEGORY_PHRASES[best_key][0]
    weakness = CATEGORY_PHRASES[worst_key][1]

    # Everything weak: say so plainly rather than dressing up the least-bad.
    if best < WEAK:
        return f"Weak across most measures, {weakness} in particular"

    # Everything strong and nothing dragging: no manufactured caveat.
    if worst >= STRONG:
        second = sorted(scored, key=lambda k: scored[k], reverse=True)[1]
        return f"{CATEGORY_PHRASES[best_key][0].capitalize()} and " \
               f"{CATEGORY_PHRASES[second][0]}"

    if best_key == worst_key:            # only one category available
        return f"{strength.capitalize()}, limited data elsewhere"

    if worst < WEAK:
        return f"{strength.capitalize()}, but {weakness}"
    return f"{strength.capitalize()} with {CATEGORY_PHRASES[worst_key][0]}"


def score_change_reason(delta: float, category: str) -> str:
    """Plain phrasing for a score move, used on the watchlist."""
    direction = "improved" if delta > 0 else "weakened"
    return f"{category} {direction}"
