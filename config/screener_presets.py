"""
Screener presets.

Every preset is a set of explicit thresholds against the scoring engine's
categories, stated in full so a reader can see exactly what "High Quality"
means rather than trusting a label.

RULES FOR ADDING A PRESET
-------------------------
1. It must be expressible as thresholds on scored categories or reported
   metrics. If it cannot be written down, it does not go in.
2. Its name must describe what it filters for, not what it promises. "Quality
   at a reasonable price" describes a filter; "Best buys" describes an outcome
   nobody can deliver.
3. It must state what it does NOT tell you. A preset surfaces companies whose
   figures match a pattern; it never establishes that they are good
   investments.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Preset", "PRESETS", "PRESET_VERSION", "FILTERABLE"]

PRESET_VERSION = "1.0.0"

# Categories and metrics a user can filter on. Anything not listed here cannot
# be filtered, so the UI and the methodology cannot drift apart.
FILTERABLE = {
    "overall": "Research score",
    "quality": "Quality",
    "growth": "Growth",
    "valuation": "Valuation",
    "financial_health": "Financial health",
    "momentum": "Momentum",
}


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    limits: dict[str, tuple[float, float]] = field(default_factory=dict)
    caveat: str = ""
    min_confidence: str = "low"

    def describe(self) -> str:
        parts = []
        for name, (low, high) in self.limits.items():
            label = FILTERABLE.get(name, name)
            if low > 0 and high < 100:
                parts.append(f"{label} between {low:.0f} and {high:.0f}")
            elif low > 0:
                parts.append(f"{label} of at least {low:.0f}")
            elif high < 100:
                parts.append(f"{label} of at most {high:.0f}")
        return "; ".join(parts) if parts else "no thresholds applied"


PRESETS: list[Preset] = [
    Preset(
        key="high_quality",
        label="High quality",
        description=(
            "Companies that are strongly profitable and financially sound, without any "
            "requirement on growth or price."
        ),
        limits={"quality": (70, 100), "financial_health": (60, 100)},
        caveat=(
            "A high-quality business can still be an expensive share. This filter says "
            "nothing about what you would be paying for it."
        ),
    ),
    Preset(
        key="high_growth",
        label="High growth",
        description="Companies whose reported revenue and earnings are growing quickly.",
        limits={"growth": (70, 100)},
        caveat=(
            "Fast growth is often already reflected in the share price, and growth rates "
            "frequently slow. This filter applies no valuation or quality test."
        ),
    ),
    Preset(
        key="quality_reasonable_price",
        label="Quality at a reasonable price",
        description=(
            "Profitable, sound businesses that are not trading on stretched multiples."
        ),
        limits={"quality": (65, 100), "valuation": (55, 100), "financial_health": (55, 100)},
        caveat=(
            "A modest valuation can reflect a risk the reported figures do not yet show. "
            "Cheap relative to earnings is not the same as underpriced."
        ),
    ),
    Preset(
        key="potentially_undervalued",
        label="Modestly valued",
        description=(
            "Companies trading on low multiples relative to what they earn, that are "
            "still profitable and not financially strained."
        ),
        limits={"valuation": (70, 100), "quality": (45, 100), "financial_health": (45, 100)},
        caveat=(
            "This does NOT identify undervalued companies. It identifies low multiples. "
            "Markets often price a company modestly for reasons that are entirely sound, "
            "and establishing that something is genuinely worth more than its price "
            "requires judgement this platform does not make."
        ),
    ),
    Preset(
        key="strong_momentum",
        label="Strong momentum",
        description="Companies whose share price has risen strongly in recent months.",
        limits={"momentum": (70, 100)},
        caveat=(
            "Momentum describes past price movement only. It carries no information about "
            "the quality of the business, and it reverses as well as persists."
        ),
    ),
    Preset(
        key="defensive",
        label="Defensive",
        description=(
            "Financially robust, consistently profitable companies, without a requirement "
            "for fast growth."
        ),
        limits={"financial_health": (70, 100), "quality": (60, 100)},
        caveat=(
            "Financial strength reduces the risk of distress. It does not protect a share "
            "price from falling."
        ),
    ),
    Preset(
        key="all_round",
        label="Strong across the board",
        description="Companies that score at least moderately on every measure.",
        limits={"quality": (55, 100), "growth": (45, 100), "valuation": (45, 100),
                "financial_health": (55, 100)},
        caveat=(
            "Scoring consistently is not the same as scoring highly. This filter is strict "
            "about breadth, not about excellence."
        ),
        min_confidence="moderate",
    ),
]

PRESETS_BY_KEY = {p.key: p for p in PRESETS}
