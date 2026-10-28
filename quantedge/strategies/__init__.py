"""Strategy registry. Add a new strategy by importing it here."""
from __future__ import annotations

from typing import Dict

from .base import Param, RebalanceStrategy, SignalStrategy, Strategy
from .intraday import OpeningRangeBreakout, VwapContinuation
from .momentum import CrossSectionalMomentum
from .reversal import CrossSectionalReversal
from .trend import DonchianBreakout, EmaPullback
from .trend_filter import SmaTrendFilter

REGISTRY: Dict[str, Strategy] = {s.key: s for s in (EmaPullback(), DonchianBreakout(), CrossSectionalMomentum(),
                                                    OpeningRangeBreakout(), VwapContinuation(),
                                                    CrossSectionalReversal(), SmaTrendFilter())}


def get_strategy(key: str) -> Strategy:
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"Unknown strategy {key!r}. Available: {', '.join(REGISTRY)}") from None


__all__ = ["REGISTRY", "get_strategy", "Strategy", "SignalStrategy", "RebalanceStrategy", "Param"]
