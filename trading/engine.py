from __future__ import annotations

"""
The engine: runs a tick for each agent — collect proposed Decisions, apply the
safety gate, execute via the broker, and report P&L. Also builds the cross-agent
leaderboard that the game hub renders.
"""

import logging

from .agents.base import BaseAgent, Decision
from .broker.base import Order
from .config import TradingConfig

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, config: TradingConfig, agents: list[BaseAgent]):
        self.config = config
        self.agents = agents

    def _approve(self, agent: BaseAgent, d: Decision) -> bool:
        """
        Safety gate. Paper trades auto-approve. Live trades require explicit
        human confirmation unless the operator has opted out via env flag.
        """
        if not self.config.is_live:
            return True
        if not self.config.require_approval:
            return True
        price = agent.broker.get_price(d.symbol)
        est = f"~${price * d.qty:,.2f}" if price else "unknown value"
        prompt = (
            f"\n>>> LIVE ORDER APPROVAL <<<\n"
            f"    Agent : {agent.name}\n"
            f"    Order : {d.side.upper()} {d.qty} {d.symbol} ({est})\n"
            f"    Reason: {d.reason}\n"
            f"    Type 'yes' to place this REAL order: "
        )
        try:
            return input(prompt).strip().lower() == "yes"
        except EOFError:
            # No interactive terminal → never place a live order unattended.
            logger.warning("no TTY for live approval; skipping order")
            return False

    def run_agent(self, agent: BaseAgent) -> list[Order]:
        logger.info("--- %s (%s) thinking… ---", agent.name, agent.broker.name)

        # Betting agents (Ace) don't place market orders — they settle and bet.
        if getattr(agent, "is_betting", False):
            for line in agent.run_round():
                logger.info("%s: %s", agent.name, line)
            return []

        decisions = agent.decide()
        if not decisions:
            logger.info("%s: no action this tick", agent.name)
            return []

        orders: list[Order] = []
        for d in decisions:
            if not self._approve(agent, d):
                logger.info("%s: skipped %s %s %s (not approved)",
                            agent.name, d.side, d.qty, d.symbol)
                continue
            order = agent.broker.submit_order(d.symbol, d.side, d.qty)
            orders.append(order)
            if order.status == "filled":
                logger.info("%s: %s %s %s @ $%.2f — %s",
                            agent.name, d.side.upper(), d.qty, d.symbol,
                            order.filled_price, d.reason)
            else:
                logger.warning("%s: order %s %s %s %s — %s",
                               agent.name, d.side, d.qty, d.symbol,
                               order.status, order.note)
        return orders

    def run_tick(self) -> None:
        self.config.assert_live_allowed()
        for agent in self.agents:
            self.run_agent(agent)

    # ---- reporting -------------------------------------------------------
    def leaderboard(self) -> list[dict]:
        """Return per-agent standings sorted by total return, best first."""
        rows = []
        for agent in self.agents:
            acct = agent.broker.get_account()
            start = getattr(agent.broker, "starting_cash", acct.equity)
            ret_pct = (acct.equity - start) / start * 100 if start else 0.0
            rows.append({
                "agent": agent.name,
                "room": agent.room,
                "equity": acct.equity,
                "cash": acct.cash,
                "return_pct": ret_pct,
                "positions": len(agent.broker.get_positions()),
            })
        rows.sort(key=lambda r: r["return_pct"], reverse=True)
        return rows

    def print_leaderboard(self) -> None:
        rows = self.leaderboard()
        print("\n" + "=" * 60)
        print(f"{'AGENT':<12}{'ROOM':<18}{'EQUITY':>12}{'RETURN':>10}")
        print("-" * 60)
        for r in rows:
            print(f"{r['agent']:<12}{r['room']:<18}"
                  f"${r['equity']:>10,.0f}{r['return_pct']:>9.2f}%")
        print("=" * 60)
