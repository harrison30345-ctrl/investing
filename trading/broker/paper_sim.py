from __future__ import annotations

"""
Built-in paper-trading simulator. Zero external setup — no broker account, no
API keys. Fills market orders instantly at the current yfinance price and
persists cash/positions/trade-history to a JSON file so P&L survives restarts.

Each agent gets its own isolated sim account (its own state file), which is what
makes the cross-agent leaderboard work.
"""

import json
import logging
import time
from pathlib import Path

from ..prices import get_price
from .base import Account, BrokerBase, Order, Position

logger = logging.getLogger(__name__)

DEFAULT_STARTING_CASH = 10_000.0


class PaperSimBroker(BrokerBase):
    def __init__(self, account_id: str, state_dir: Path,
                 starting_cash: float = DEFAULT_STARTING_CASH):
        self.name = f"paper-sim:{account_id}"
        self.account_id = account_id
        self._state_path = state_dir / f"sim_{account_id}.json"
        self._starting_cash = starting_cash
        self._state = self._load()

    # ---- persistence -----------------------------------------------------
    def _load(self) -> dict:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text())
            except Exception as e:  # noqa: BLE001
                logger.warning("could not read sim state %s: %s", self._state_path, e)
        return {
            "cash": self._starting_cash,
            "starting_cash": self._starting_cash,
            # symbol -> {"qty": float, "avg_entry_price": float}
            "positions": {},
            "trades": [],  # append-only history
        }

    def _save(self) -> None:
        self._state_path.write_text(json.dumps(self._state, indent=2))

    # ---- broker interface ------------------------------------------------
    def get_price(self, symbol: str) -> float | None:
        return get_price(symbol)

    def get_positions(self) -> list[Position]:
        out = []
        for symbol, pos in self._state["positions"].items():
            if pos["qty"] == 0:
                continue
            price = get_price(symbol) or pos["avg_entry_price"]
            out.append(Position(
                symbol=symbol,
                qty=pos["qty"],
                avg_entry_price=pos["avg_entry_price"],
                current_price=price,
            ))
        return out

    def get_account(self) -> Account:
        positions_value = sum(p.market_value for p in self.get_positions())
        cash = self._state["cash"]
        equity = cash + positions_value
        return Account(cash=cash, equity=equity, buying_power=cash)

    def submit_order(self, symbol: str, side: str, qty: float) -> Order:
        side = side.lower()
        if qty <= 0:
            return Order(symbol, side, 0, 0.0, "rejected", "qty must be > 0")

        price = get_price(symbol)
        if price is None:
            return Order(symbol, side, qty, 0.0, "rejected", "no price available")

        positions = self._state["positions"]
        pos = positions.get(symbol, {"qty": 0.0, "avg_entry_price": 0.0})

        if side == "buy":
            cost = price * qty
            if cost > self._state["cash"] + 1e-9:
                return Order(symbol, side, qty, price, "rejected",
                             f"insufficient cash (need ${cost:,.2f}, "
                             f"have ${self._state['cash']:,.2f})")
            # Weighted-average entry price.
            new_qty = pos["qty"] + qty
            pos["avg_entry_price"] = (
                (pos["avg_entry_price"] * pos["qty"] + price * qty) / new_qty
            )
            pos["qty"] = new_qty
            self._state["cash"] -= cost

        elif side == "sell":
            if qty > pos["qty"] + 1e-9:
                return Order(symbol, side, qty, price, "rejected",
                             f"cannot sell {qty}, only hold {pos['qty']}")
            pos["qty"] -= qty
            self._state["cash"] += price * qty
            if pos["qty"] <= 1e-9:
                pos = {"qty": 0.0, "avg_entry_price": 0.0}
        else:
            return Order(symbol, side, qty, price, "rejected", f"bad side {side!r}")

        positions[symbol] = pos
        self._state["trades"].append({
            "ts": time.time(),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
        })
        self._save()
        return Order(symbol, side, qty, price, "filled")

    # ---- helpers ---------------------------------------------------------
    def reset(self) -> None:
        """Wipe this sim account back to starting cash."""
        self._state = {
            "cash": self._starting_cash,
            "starting_cash": self._starting_cash,
            "positions": {},
            "trades": [],
        }
        self._save()

    @property
    def starting_cash(self) -> float:
        return self._state.get("starting_cash", self._starting_cash)

    def recent_trades(self, limit: int = 10) -> list[dict]:
        """Most-recent trades first, for display in the game hub."""
        return list(reversed(self._state.get("trades", [])))[:limit]
