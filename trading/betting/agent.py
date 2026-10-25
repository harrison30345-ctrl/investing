from __future__ import annotations

"""
Ace — the sports-betting agent. Lives in The Sportsbook.

Strategy: value betting sized by fractional Kelly — the genuine high-probability
approach to betting. Ace only bets when its estimated win probability implies
positive expected value against the offered odds, and stakes each bet by a
fraction of the Kelly-optimal amount to control variance.

The edge comes from Ace's probability model being better than the book's price.
In this simulation Ace's estimate is the (hidden) true probability plus noise —
i.e. a skilled-but-imperfect handicapper. `skill` controls that noise: high skill
= a real edge that compounds; zero skill = no edge and the vig grinds you down.
This mirrors reality — your betting results are only as good as your model.
"""

import logging
import random

from .book import SportsBook
from .odds import SimOddsFeed

logger = logging.getLogger(__name__)


class AceAgent:
    is_betting = True   # lets the engine/hub treat betting agents differently
    rounds_per_cycle = 10   # sim can play many rounds per daemon cycle

    def __init__(self, book: SportsBook, feed: SimOddsFeed | None = None, *,
                 name: str = "Ace",
                 skill: float = 0.65,         # 0..1; higher = sharper estimates
                 kelly_fraction: float = 0.15,  # fractional Kelly for safety
                 edge_threshold: float = 0.04,  # need >4% EV edge to bet
                 max_stake_frac: float = 0.05,  # never risk >5% of bankroll on one bet
                 games_per_round: int = 6,
                 seed: int | None = None):
        self.name = name
        self.broker = book                     # named 'broker' for leaderboard compat
        self.room = "The Sportsbook"
        self.avatar = "🎲"
        self.personality = "A sharp who only bets value and sizes by the Kelly criterion."
        self.feed = feed or SimOddsFeed(seed=seed)
        self.skill = max(0.0, min(1.0, skill))
        self.kelly_fraction = kelly_fraction
        self.edge_threshold = edge_threshold
        self.max_stake_frac = max_stake_frac
        self.games_per_round = games_per_round
        self._rng = random.Random(seed)

    def _estimate(self, true_prob: float) -> float:
        """Ace's imperfect read on the true probability (noisier at low skill)."""
        noise_sd = (1 - self.skill) * 0.12
        est = true_prob + self._rng.gauss(0, noise_sd)
        return max(0.02, min(0.98, est))

    @staticmethod
    def _kelly(prob: float, odds: float) -> float:
        """Full-Kelly fraction for decimal odds; 0 if no edge."""
        b = odds - 1.0
        if b <= 0:
            return 0.0
        q = 1 - prob
        f = (b * prob - q) / b
        return max(0.0, f)

    def settle_open_bets(self) -> list[str]:
        """Play out and settle everything currently pending."""
        logs = []
        for bet in self.broker.open_bets():
            outcome = "home" if self._rng.random() < bet["true_prob_home"] else "away"
            won = outcome == bet["side"]
            pnl = self.broker.settle_bet(bet, won)
            logs.append(f"{bet['matchup']}: bet {bet['side']} "
                        f"{'WON' if won else 'lost'} ({pnl:+.0f})")
        return logs

    def place_value_bets(self) -> list[str]:
        """Scan a fresh slate and place value bets sized by fractional Kelly."""
        logs = []
        slate = self.feed.next_slate(self.games_per_round)
        for fx in slate:
            bankroll = self.broker.get_account().cash
            if bankroll <= 0:
                break
            for side in ("home", "away"):
                odds = fx.home_odds if side == "home" else fx.away_odds
                true_p = fx.true_prob_home if side == "home" else 1 - fx.true_prob_home
                est = self._estimate(true_p)

                ev = est * odds - 1.0            # expected value per $1 staked
                if ev < self.edge_threshold:
                    continue

                kelly = self._kelly(est, odds) * self.kelly_fraction
                stake_frac = min(kelly, self.max_stake_frac)
                stake = round(bankroll * stake_frac, 2)
                if stake < 1:
                    continue

                ok = self.broker.place_bet({
                    "game_id": fx.game_id,
                    "matchup": f"{fx.home} v {fx.away}",
                    "side": side,
                    "stake": stake,
                    "odds": odds,
                    "est_prob": round(est, 3),
                    "true_prob_home": fx.true_prob_home,
                })
                if ok:
                    logs.append(f"{fx.home} v {fx.away}: {side} @ {odds} "
                                f"(edge {ev*100:.0f}%, ${stake:.0f})")
        return logs

    def run_round(self) -> list[str]:
        """One betting cycle: settle last round's games, then bet the new slate."""
        logs = self.settle_open_bets()
        logs += self.place_value_bets()
        return logs
