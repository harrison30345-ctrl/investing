"""
Strategy interfaces.

Two shapes, because they really are different problems:

SignalStrategy (trend following, intraday)
    Per-symbol rules. `generate(bars, params)` returns, for each bar t, whether
    an entry or exit condition is true *at t's close*, plus the stop distance
    and target multiple. The backtester acts on these at t+1's open.

RebalanceStrategy (cross-sectional momentum)
    Portfolio rules. `target_weights(closes, t, params)` ranks the universe
    using closes up to t and returns weights to hold from t+1.

Strategies are deterministic and pure: no I/O, no randomness, no LLM.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    kind: str                 # "int" | "float" | "bool" | "choice"
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    choices: tuple = ()
    help: str = ""


class Strategy(ABC):
    key: str
    name: str
    family: str               # "trend" | "momentum" | "intraday"
    timeframe: str = "daily"
    description: str = ""
    params: List[Param] = []

    def defaults(self) -> Dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def resolve(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Merge overrides into defaults, rejecting unknown or out-of-range values."""
        merged = self.defaults()
        for k, v in (overrides or {}).items():
            spec = next((p for p in self.params if p.name == k), None)
            if spec is None:
                raise ValueError(f"{self.key}: unknown parameter {k!r}")
            if spec.kind == "int":
                v = int(v)
            elif spec.kind == "float":
                v = float(v)
            elif spec.kind == "bool":
                v = bool(v)
            elif spec.kind == "choice":
                v = type(spec.default)(v)
                if v not in spec.choices:
                    raise ValueError(f"{self.key}: {k} must be one of {spec.choices}")
            if spec.min is not None and v < spec.min or spec.max is not None and v > spec.max:
                raise ValueError(f"{self.key}: {k}={v} outside [{spec.min}, {spec.max}]")
            merged[k] = v
        return merged

    @abstractmethod
    def entry_rule(self, p: Dict[str, Any]) -> str: ...

    @abstractmethod
    def exit_rule(self, p: Dict[str, Any]) -> str: ...

    def warmup_bars(self, p: Dict[str, Any]) -> int:
        return 260

    def describe(self) -> dict:
        return {"key": self.key, "name": self.name, "family": self.family, "timeframe": self.timeframe,
                "description": self.description, "params": [asdict(p) for p in self.params]}


class SignalStrategy(Strategy):
    @abstractmethod
    def generate(self, bars: pd.DataFrame, p: Dict[str, Any]) -> pd.DataFrame:
        """
        Input: adjusted daily bars (open, high, low, close, volume).
        Output: same index, columns
            entry       bool   — enter long at next open
            exit        bool   — exit at next open
            stop_dist   float  — stop distance in price units (set where entry)
            target_r    float  — profit target as a multiple of stop_dist (NaN = none)
            max_bars    float  — time stop in bars (NaN = none)
            reason      str    — plain-English explanation where entry/exit
            stop_price  float  — optional: an exact stop level; when set it overrides stop_dist
        plus any indicator columns useful as context.
        """


class RebalanceStrategy(Strategy):
    rebalance_every: int = 21

    @abstractmethod
    def target_weights(self, closes: pd.DataFrame, t: int, p: Dict[str, Any],
                       regime: Optional[pd.Series] = None) -> Dict[str, float]:
        """Weights (summing to <= 1) to hold after bar index t, using closes.iloc[:t+1] only."""

    def ranking(self, closes: pd.DataFrame, t: int, p: Dict[str, Any]) -> pd.Series:
        return pd.Series(dtype=float)


@dataclass
class SignalExplanation:
    reason: str
    context: Dict[str, Any] = field(default_factory=dict)
