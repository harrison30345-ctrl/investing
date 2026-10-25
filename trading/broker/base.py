from __future__ import annotations

"""
Broker interface. Any execution backend (the built-in paper simulator, Alpaca
paper, Alpaca live, or a future broker) implements this same interface, so the
agents and engine never know or care whether money is real.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price

    @property
    def cost_basis(self) -> float:
        return self.qty * self.avg_entry_price

    @property
    def unrealized_pl(self) -> float:
        return self.market_value - self.cost_basis

    @property
    def unrealized_pl_pct(self) -> float:
        if self.cost_basis == 0:
            return 0.0
        return self.unrealized_pl / self.cost_basis * 100


@dataclass
class Account:
    cash: float
    equity: float          # cash + market value of positions
    buying_power: float

    @property
    def positions_value(self) -> float:
        return self.equity - self.cash


@dataclass
class Order:
    symbol: str
    side: str              # "buy" or "sell"
    qty: float
    filled_price: float
    status: str            # "filled", "rejected", "pending_approval"
    note: str = ""


class BrokerBase(ABC):
    """Execution backend interface."""

    #: Human-readable label, e.g. "paper-sim" or "alpaca-paper".
    name: str = "broker"

    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_price(self, symbol: str) -> float | None: ...

    @abstractmethod
    def submit_order(self, symbol: str, side: str, qty: float) -> Order: ...

    def get_position(self, symbol: str) -> Position | None:
        for p in self.get_positions():
            if p.symbol == symbol:
                return p
        return None
