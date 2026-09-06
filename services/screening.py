"""
Screening companies against score thresholds.

Applies preset or custom filters to a set of research scores and reports how
many companies each filter removed, so a reader can see why a screen returned
what it returned rather than being handed an unexplained list.

A company whose category could not be scored does NOT pass a filter on that
category. Unknown is not a pass -- the same rule the scoring engine and the
Hidden Gems gates follow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

try:
    from config.screener_presets import FILTERABLE, PRESETS_BY_KEY, Preset
except ImportError:  # pragma: no cover
    from screener_presets import FILTERABLE, PRESETS_BY_KEY, Preset  # type: ignore

if TYPE_CHECKING:  # pragma: no cover
    from services.scoring import ResearchScore

__all__ = ["ScreenResult", "screen", "apply_preset", "CONFIDENCE_RANK"]

CONFIDENCE_RANK = {"low": 0, "moderate": 1, "high": 2}


@dataclass(frozen=True)
class ScreenResult:
    passed: list["ResearchScore"]
    removed_by: dict[str, int] = field(default_factory=dict)
    unscorable: int = 0
    total: int = 0

    @property
    def pass_rate(self) -> float:
        return len(self.passed) / self.total if self.total else 0.0


def _value_for(score: "ResearchScore", key: str) -> float | None:
    if key == "overall":
        return score.overall
    category = score.categories.get(key)
    return category.score if category and category.available else None


def screen(
    scores: list["ResearchScore"],
    limits: dict[str, tuple[float, float]],
    min_confidence: str = "low",
) -> ScreenResult:
    """Filter scores against per-category (low, high) bounds.

    Companies are removed one filter at a time so the caller can report where
    they dropped out. A company that cannot be scored on a filtered category is
    removed by that filter -- it has not demonstrated it meets the threshold.
    """
    total = len(scores)
    unscorable = sum(1 for s in scores if s.overall is None)
    remaining = [s for s in scores if s.overall is not None]
    removed: dict[str, int] = {}

    floor = CONFIDENCE_RANK.get(min_confidence, 0)
    if floor > 0:
        before = len(remaining)
        remaining = [s for s in remaining
                     if CONFIDENCE_RANK.get(s.confidence, 0) >= floor]
        if before - len(remaining):
            removed["Data confidence"] = before - len(remaining)

    for key, (low, high) in limits.items():
        if key not in FILTERABLE:
            raise ValueError(f"'{key}' is not a filterable measure")
        before = len(remaining)
        kept = []
        for s in remaining:
            value = _value_for(s, key)
            if value is None:      # unknown never passes
                continue
            if low <= value <= high:
                kept.append(s)
        remaining = kept
        if before - len(remaining):
            removed[FILTERABLE[key]] = before - len(remaining)

    remaining.sort(key=lambda s: s.overall or 0.0, reverse=True)
    return ScreenResult(passed=remaining, removed_by=removed,
                        unscorable=unscorable, total=total)


def apply_preset(scores: list["ResearchScore"], preset_key: str) -> tuple[ScreenResult, Preset]:
    preset = PRESETS_BY_KEY.get(preset_key)
    if preset is None:
        raise ValueError(f"unknown preset: {preset_key}")
    return screen(scores, preset.limits, preset.min_confidence), preset
