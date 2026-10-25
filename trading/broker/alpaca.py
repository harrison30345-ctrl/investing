from __future__ import annotations

"""
Alpaca broker adapter (paper + live). Uses plain `requests` against Alpaca's
REST API — no extra dependency. Paper and live are the SAME code path; the only
difference is the base URL and which keys you supply, both driven by config.

This is the drop-in replacement for PaperSimBroker once you have an Alpaca
account. Get free paper keys at https://alpaca.markets → Paper Trading.
"""

import logging

import requests

from ..config import ALPACA_DATA_URL, TradingConfig
from .base import Account, BrokerBase, Order, Position

logger = logging.getLogger(__name__)


class AlpacaBroker(BrokerBase):
    def __init__(self, config: TradingConfig):
        if not (config.alpaca_key and config.alpaca_secret):
            raise RuntimeError(
                "Alpaca keys missing. Set ALPACA_API_KEY and ALPACA_API_SECRET."
            )
        # Refuse to touch a live endpoint unless the full opt-in is satisfied.
        config.assert_live_allowed()
        self.config = config
        self.base_url = config.alpaca_base_url
        self.name = "alpaca-live" if config.is_live else "alpaca-paper"
        self._headers = {
            "APCA-API-KEY-ID": config.alpaca_key,
            "APCA-API-SECRET-KEY": config.alpaca_secret,
        }

    def _get(self, url: str, **kwargs):
        r = requests.get(url, headers=self._headers, timeout=15, **kwargs)
        r.raise_for_status()
        return r.json()

    def get_account(self) -> Account:
        data = self._get(f"{self.base_url}/v2/account")
        return Account(
            cash=float(data["cash"]),
            equity=float(data["equity"]),
            buying_power=float(data["buying_power"]),
        )

    def get_price(self, symbol: str) -> float | None:
        try:
            data = self._get(
                f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/trades/latest")
            return float(data["trade"]["p"])
        except Exception as e:  # noqa: BLE001
            logger.warning("alpaca price fetch failed for %s: %s", symbol, e)
            return None

    def get_positions(self) -> list[Position]:
        data = self._get(f"{self.base_url}/v2/positions")
        out = []
        for p in data:
            out.append(Position(
                symbol=p["symbol"],
                qty=float(p["qty"]),
                avg_entry_price=float(p["avg_entry_price"]),
                current_price=float(p.get("current_price") or p["avg_entry_price"]),
            ))
        return out

    def submit_order(self, symbol: str, side: str, qty: float) -> Order:
        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side.lower(),
            "type": "market",
            "time_in_force": "day",
        }
        try:
            r = requests.post(f"{self.base_url}/v2/orders",
                              headers=self._headers, json=payload, timeout=15)
            r.raise_for_status()
            data = r.json()
        except requests.HTTPError as e:
            body = e.response.text if e.response is not None else str(e)
            return Order(symbol, side, qty, 0.0, "rejected", body[:200])
        except Exception as e:  # noqa: BLE001
            return Order(symbol, side, qty, 0.0, "rejected", str(e)[:200])

        filled = data.get("filled_avg_price")
        return Order(
            symbol=symbol,
            side=side,
            qty=qty,
            filled_price=float(filled) if filled else (self.get_price(symbol) or 0.0),
            status="filled" if data.get("status") in ("filled", "accepted", "new")
                   else data.get("status", "unknown"),
            note=f"alpaca order id {data.get('id', '?')}",
        )
