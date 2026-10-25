"""
Transaction cost model.

Every fill pays half the bid-ask spread plus slippage, both in basis points
of price, and these go into the fill price itself. Commission and UK stamp duty
are charged separately as cash fees, so they show up in the cost breakdown.

UK stamp duty (SDRT) is 0.5% on *purchases* of most UK-listed shares. It
doesn't apply to sales, and it doesn't apply to ETFs (exempt) or AIM shares;
this model applies it to all UK equity buys, which is slightly conservative.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CostModel:
    spread_bps: float = 5.0          # full quoted spread; half is paid per side
    slippage_bps: float = 5.0
    commission_bps: float = 0.0
    commission_min: float = 0.0      # per trade, in account currency
    uk_stamp_duty_bps: float = 50.0

    def buy_price(self, price: float) -> float:
        return price * (1 + (self.spread_bps / 2 + self.slippage_bps) / 1e4)

    def sell_price(self, price: float) -> float:
        return price * (1 - (self.spread_bps / 2 + self.slippage_bps) / 1e4)

    def fees(self, notional: float, side: str, market: str) -> float:
        fee = max(self.commission_min, notional * self.commission_bps / 1e4) if notional else 0.0
        if side == "buy" and market == "UK":
            fee += notional * self.uk_stamp_duty_bps / 1e4
        return fee

    def to_dict(self) -> dict:
        return asdict(self)


ZERO_COSTS = CostModel(0, 0, 0, 0, 0)
