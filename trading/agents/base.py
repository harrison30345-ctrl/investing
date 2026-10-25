from __future__ import annotations

"""
Agent interface. An agent has a personality (for the game theme), an execution
broker, and a strategy expressed as decide() — which inspects the market and its
own portfolio and returns a list of intended trades. The engine executes them.

Agents never place orders themselves; they only *propose* Decisions. This keeps
the "what to trade" logic (agent) separate from the "actually trade it" logic
(engine + broker + safety gate).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..broker.base import BrokerBase


@dataclass
class Decision:
    symbol: str
    side: str          # "buy" or "sell"
    qty: float
    reason: str        # human-readable why, shown in logs / the game UI


class BaseAgent(ABC):
    def __init__(self, name: str, broker: BrokerBase,
                 personality: str = "", room: str = "", avatar: str = "🤖"):
        self.name = name
        self.broker = broker
        self.personality = personality  # flavor text for the game hub
        self.room = room                # which "location" in the game world
        self.avatar = avatar            # emoji shown in the game hub

    @abstractmethod
    def decide(self) -> list[Decision]:
        """Inspect market + portfolio, return proposed trades (may be empty)."""
        ...
