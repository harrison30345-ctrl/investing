from __future__ import annotations

"""
Paper sportsbook — tracks a betting bankroll, open (pending) bets, and settled
history, persisted to JSON so P&L survives restarts. Deliberately mirrors the
trading PaperSimBroker interface (get_account / starting_cash / recent history)
so Ace slots onto the same leaderboard as the trading agents.

Real money is never involved: this is simulated bankroll only, and there is no
adapter that places real bets.
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_BANKROLL = 10_000.0


class _Account:
    """Minimal account view compatible with the leaderboard/hub."""
    def __init__(self, cash: float, equity: float):
        self.cash = cash
        self.equity = equity
        self.buying_power = cash

    @property
    def positions_value(self) -> float:
        return self.equity - self.cash


class SportsBook:
    def __init__(self, account_id: str, state_dir: Path,
                 starting_bankroll: float = DEFAULT_BANKROLL):
        self.name = f"paper-book:{account_id}"
        self.account_id = account_id
        self._state_path = state_dir / f"bets_{account_id}.json"
        self._starting = starting_bankroll
        self._state = self._load()

    def _load(self) -> dict:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text())
            except Exception as e:  # noqa: BLE001
                logger.warning("could not read book state: %s", e)
        return {
            "cash": self._starting,
            "starting_cash": self._starting,
            "open_bets": [],      # bets awaiting settlement
            "settled": [],        # append-only settled history
        }

    def _save(self) -> None:
        self._state_path.write_text(json.dumps(self._state, indent=2))

    # ---- leaderboard/hub compatibility ----------------------------------
    def get_account(self) -> _Account:
        open_stakes = sum(b["stake"] for b in self._state["open_bets"])
        cash = self._state["cash"]
        return _Account(cash=cash, equity=cash + open_stakes)

    def get_positions(self) -> list:
        """Open bets stand in for 'positions' (used for the count on the board)."""
        return list(self._state["open_bets"])

    @property
    def starting_cash(self) -> float:
        return self._state.get("starting_cash", self._starting)

    # ---- betting ---------------------------------------------------------
    def place_bet(self, bet: dict) -> bool:
        """
        bet must include: game_id, matchup, side, stake, odds, est_prob,
        true_prob_home. Deducts the stake from cash and records the open bet.
        """
        stake = bet["stake"]
        if stake <= 0 or stake > self._state["cash"] + 1e-9:
            return False
        self._state["cash"] -= stake
        bet = {**bet, "ts": time.time()}
        self._state["open_bets"].append(bet)
        self._save()
        return True

    def settle_bet(self, bet: dict, won: bool) -> float:
        """Settle one open bet. Returns realized P&L (payout - stake)."""
        stake = bet["stake"]
        if won:
            payout = stake * bet["odds"]
            self._state["cash"] += payout
            pnl = payout - stake
        else:
            pnl = -stake  # stake was already deducted when placed
        record = {**bet, "won": won, "pnl": pnl, "settled_ts": time.time()}
        self._state["settled"].append(record)
        if bet in self._state["open_bets"]:
            self._state["open_bets"].remove(bet)
        self._save()
        return pnl

    def open_bets(self) -> list[dict]:
        return list(self._state["open_bets"])

    def recent_settled(self, limit: int = 8) -> list[dict]:
        return list(reversed(self._state["settled"]))[:limit]

    def settled_pnls(self) -> list[float]:
        return [b["pnl"] for b in self._state["settled"]]

    def reset(self) -> None:
        self._state = {
            "cash": self._starting, "starting_cash": self._starting,
            "open_bets": [], "settled": [],
        }
        self._save()
