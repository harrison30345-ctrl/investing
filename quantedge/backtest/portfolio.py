"""
SignalPortfolio: the trading rules for signal strategies, one bar at a time.

Both the backtester and paper trading drive this class, so a paper account
follows exactly the rules its backtest was judged on. The backtester runs it
over history in one go. Paper trading runs it over each new bar as it arrives
and saves the state to the database between runs (`to_state` / `from_state`).

Per bar (see engine.py for the reasoning):
  1. Open      — fill queued exits, then queued entries, at this bar's open.
  2. Intrabar  — stops and targets against high/low (gap ⇒ fill at open; both ⇒ stop).
  3. Close     — count bars held, queue time-stop / exit-signal exits, queue new
                 entries from signals evaluated on this bar's close.
  4. Session end (intraday only) — anything still open is sold at the close and
                 queued entries are cancelled, so nothing is held overnight.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .costs import CostModel


def _ts(x) -> pd.Timestamp:
    return pd.Timestamp(x)


class SignalPortfolio:
    def __init__(self, config, market: str):
        self.cfg = config
        self.cm: CostModel = config.costs
        self.market = market
        self.cash: float = float(config.initial_capital)
        self.positions: Dict[str, dict] = {}
        self.pending_entries: List[dict] = []
        self.pending_exits: Dict[str, str] = {}
        self.last_close: Dict[str, float] = {}
        self.trades: List[dict] = []          # closed round trips
        self.events: List[dict] = []          # fills and queued orders, for audit (paper trading)
        self.total_costs = 0.0
        self.notional_traded = 0.0

    # ── valuation ──────────────────────────────────────────────────────────
    def mark(self) -> float:
        return self.cash + sum(p["shares"] * self.last_close.get(s, p["entry_price"]) for s, p in self.positions.items())

    def exposure(self) -> float:
        eq = self.mark()
        return (eq - self.cash) / eq if eq > 0 else 0.0

    # ── fills ──────────────────────────────────────────────────────────────
    def _close(self, sym: str, raw_price: float, ts, reason: str) -> None:
        pos = self.positions.pop(sym)
        fill = self.cm.sell_price(raw_price)
        proceeds = pos["shares"] * fill
        fee = self.cm.fees(proceeds, "sell", self.market)
        self.cash += proceeds - fee
        implicit = pos["shares"] * (raw_price - fill)
        self.total_costs += fee + implicit
        self.notional_traded += proceeds
        cost_basis = pos["shares"] * pos["entry_price"] + pos["entry_fee"]
        net = proceeds - fee - cost_basis
        self.trades.append({
            "symbol": sym, "entry_ts": pos["entry_ts"], "entry_price": pos["entry_price"], "exit_ts": ts,
            "exit_price": fill, "shares": pos["shares"],
            "costs": pos["entry_fee"] + pos["entry_implicit"] + fee + implicit,
            "net_pnl": net, "return_pct": net / cost_basis, "bars_held": pos["bars_held"],
            "entry_reason": pos["reason"], "exit_reason": reason,
        })
        self.events.append({"type": "fill", "side": "sell", "symbol": sym, "ts": ts, "shares": pos["shares"],
                            "price": fill, "reason": reason, "fees": fee})

    def _open(self, order: dict, raw: float, ts, equity_at_open: float) -> None:
        cfg, cm = self.cfg, self.cm
        sym = order["symbol"]
        fill = cm.buy_price(raw)
        stop_dist = order["stop_dist"]
        if not stop_dist or not np.isfinite(stop_dist) or stop_dist <= 0 or fill - stop_dist <= 0:
            return
        shares = equity_at_open * cfg.risk_per_trade / stop_dist
        shares = min(shares, equity_at_open * cfg.max_position_pct / fill)
        fee_rate = (cm.uk_stamp_duty_bps if self.market == "UK" else 0) / 1e4 + cm.commission_bps / 1e4
        shares = min(shares, max(self.cash - cm.commission_min, 0) / (fill * (1 + fee_rate)))
        shares = math.floor(shares)
        if shares < 1:
            return
        notional = shares * fill
        fee = cm.fees(notional, "buy", self.market)
        self.cash -= notional + fee
        implicit = shares * (fill - raw)
        self.total_costs += fee + implicit
        self.notional_traded += notional
        tr = order["target_r"]
        self.positions[sym] = {
            "shares": shares, "entry_price": fill, "entry_ts": ts, "entry_fee": fee, "entry_implicit": implicit,
            "stop": fill - stop_dist, "target": fill + tr * stop_dist if tr and np.isfinite(tr) else None,
            "max_bars": order["max_bars"], "bars_held": 0, "reason": order["reason"], "entered": ts,
        }
        self.events.append({"type": "fill", "side": "buy", "symbol": sym, "ts": ts, "shares": shares, "price": fill,
                            "reason": order["reason"], "fees": fee, "stop": fill - stop_dist,
                            "target": self.positions[sym]["target"]})

    # ── one bar ────────────────────────────────────────────────────────────
    def step(self, ts, today: Dict[str, dict], signals: Dict[str, dict], session_end: bool = False) -> None:
        ts = _ts(ts)
        # 1. Open: exits first, so their cash is available for entries.
        for sym in list(self.pending_exits):
            if sym in self.positions and sym in today:
                self._close(sym, float(today[sym]["open"]), ts, self.pending_exits.pop(sym))
            elif sym not in self.positions:
                self.pending_exits.pop(sym)
        equity_at_open = self.mark()
        for order in sorted(self.pending_entries, key=lambda o: o["symbol"]):
            sym = order["symbol"]
            if sym in self.positions or sym not in today or len(self.positions) >= self.cfg.max_positions:
                continue
            self._open(order, float(today[sym]["open"]), ts, equity_at_open)
        self.pending_entries = []

        # 2. Intrabar: stops and targets.
        for sym in list(self.positions):
            if sym not in today:
                continue
            pos, bar = self.positions[sym], today[sym]
            o, h, lo = float(bar["open"]), float(bar["high"]), float(bar["low"])
            stop, target = pos["stop"], pos["target"]
            fresh = _ts(pos["entered"]) == ts
            if not fresh and o <= stop:
                self._close(sym, o, ts, "stop_gap")
            elif not fresh and target is not None and o >= target:
                self._close(sym, o, ts, "target_gap")
            elif lo <= stop:
                self._close(sym, stop, ts, "stop")
            elif target is not None and h >= target:
                self._close(sym, target, ts, "target")

        # 3. Close: bookkeeping, then queue the next bar's orders.
        for sym, bar in today.items():
            self.last_close[sym] = float(bar["close"])
        for sym, pos in self.positions.items():
            if sym not in today:
                continue
            pos["bars_held"] += 1
            sig = signals.get(sym) or {}
            mb = pos["max_bars"]
            if mb and np.isfinite(mb) and pos["bars_held"] >= mb:
                self.pending_exits[sym] = "time_stop"
            elif bool(sig.get("exit")):
                self.pending_exits[sym] = "exit_signal"
        if session_end:
            # 4. Intraday: never hold overnight; nothing queued carries into the next session.
            for sym in list(self.positions):
                if sym in today:
                    self._close(sym, float(today[sym]["close"]), ts, "session_close")
            self.pending_exits = {}
            return
        for sym in today:
            sig = signals.get(sym) or {}
            if bool(sig.get("entry")) and sym not in self.positions:
                order = {"symbol": sym, "stop_dist": float(sig["stop_dist"]), "target_r": float(sig["target_r"]),
                         "max_bars": float(sig["max_bars"]), "reason": sig.get("reason") or ""}
                self.pending_entries.append(order)
                self.events.append({"type": "order", "side": "buy", "symbol": sym, "ts": ts,
                                    "reason": order["reason"], "stop_dist": order["stop_dist"]})
        for sym, why in self.pending_exits.items():
            self.events.append({"type": "order", "side": "sell", "symbol": sym, "ts": ts, "reason": why})

    def liquidate(self, ts, reason: str = "end_of_period") -> None:
        for sym in list(self.positions):
            self._close(sym, self.last_close.get(sym, self.positions[sym]["entry_price"]), _ts(ts), reason)
        self.pending_entries, self.pending_exits = [], {}

    # ── persistence (paper trading) ────────────────────────────────────────
    def to_state(self) -> Dict[str, Any]:
        def enc(v):
            if isinstance(v, (pd.Timestamp,)):
                return {"__ts__": v.isoformat()}
            if isinstance(v, float) and not np.isfinite(v):
                return {"__nan__": True}
            if isinstance(v, dict):
                return {k: enc(x) for k, x in v.items()}
            if isinstance(v, list):
                return [enc(x) for x in v]
            return v
        return enc({"cash": self.cash, "positions": self.positions, "pending_entries": self.pending_entries,
                    "pending_exits": self.pending_exits, "last_close": self.last_close,
                    "total_costs": self.total_costs, "notional_traded": self.notional_traded})

    @classmethod
    def from_state(cls, config, market: str, state: Optional[Dict[str, Any]]) -> "SignalPortfolio":
        pf = cls(config, market)
        if not state:
            return pf

        def dec(v):
            if isinstance(v, dict):
                if "__ts__" in v:
                    return pd.Timestamp(v["__ts__"])
                if "__nan__" in v:
                    return float("nan")
                return {k: dec(x) for k, x in v.items()}
            if isinstance(v, list):
                return [dec(x) for x in v]
            return v
        st = dec(state)
        pf.cash = float(st["cash"])
        pf.positions = st["positions"]
        pf.pending_entries = st["pending_entries"]
        pf.pending_exits = st["pending_exits"]
        pf.last_close = st["last_close"]
        pf.total_costs = float(st.get("total_costs", 0.0))
        pf.notional_traded = float(st.get("notional_traded", 0.0))
        return pf
