from __future__ import annotations

"""
Live Ace — the sports-betting agent running on REAL odds and REAL results.

Each round:
  1. Settle: fetch real outcomes for the sports we have open bets in, and settle
     any bet whose game has finished, against the actual winner.
  2. Bet: fetch upcoming games, and for each one place a value bet where the best
     available price beats the vig-free market consensus (positive EV vs market),
     sized by fractional Kelly.

Stakes are PAPER — this tracks a simulated bankroll against real games. It never
places a real wager. The picks it logs are exactly the bets a person could choose
to place themselves.
"""

import logging

from .agent import AceAgent   # reuse the Kelly helper
from .book import SportsBook
from .live_odds import TheOddsAPIFeed

logger = logging.getLogger(__name__)


class LiveAceAgent:
    is_betting = True
    rounds_per_cycle = 1   # one real slate per cycle — conserves API requests

    def __init__(self, book: SportsBook, feed: TheOddsAPIFeed, *,
                 name: str = "Ace",
                 kelly_fraction: float = 0.15,
                 edge_threshold: float = 0.03,   # need >3% EV vs the market
                 edge_cap: float = 0.12,          # >12% "edge" = stale/bad data, skip
                 max_stake_frac: float = 0.05,
                 games_per_round: int = 12):
        self.name = name
        self.broker = book
        self.feed = feed
        self.room = "The Sportsbook"
        self.avatar = "🎲"
        self.personality = ("A sharp betting REAL games — hunts prices that beat "
                            "the market consensus, sizes by Kelly.")
        self.kelly_fraction = kelly_fraction
        self.edge_threshold = edge_threshold
        self.edge_cap = edge_cap
        self.max_stake_frac = max_stake_frac
        self.games_per_round = games_per_round

    def settle_finished(self) -> list[str]:
        """Settle any open bet whose real game has completed."""
        open_bets = self.broker.open_bets()
        if not open_bets:
            return []
        sports = {b.get("sport_key", "") for b in open_bets if b.get("sport_key")}
        winners: dict[str, str] = {}
        for sport in sports:
            winners.update(self.feed.winners(sport))

        logs = []
        for bet in open_bets:
            winner = winners.get(bet.get("event_id"))
            if winner is None:
                continue  # game not finished yet
            won = winner == bet.get("team")
            pnl = self.broker.settle_bet(bet, won)
            logs.append(f"{bet['matchup']}: {bet['team']} "
                        f"{'WON' if won else 'lost'} ({pnl:+.0f})")
        return logs

    def place_value_bets(self) -> list[str]:
        slate = self.feed.next_slate(self.games_per_round)
        open_ids = {b.get("event_id") for b in self.broker.open_bets()}
        logs = []
        for fx in slate:
            if fx.event_id in open_ids:
                continue  # already have a bet on this game

            # Evaluate both sides, then bet AT MOST the single best-value one.
            sides = [
                ("home", fx.home, fx.home_best_odds, fx.home_fair_prob, fx.home_book),
                ("away", fx.away, fx.away_best_odds, fx.away_fair_prob, fx.away_book),
            ]
            side, team, odds, fair, book = max(
                sides, key=lambda s: s[3] * s[2] - 1.0)
            ev = fair * odds - 1.0

            # Below threshold = no edge; above the cap = almost certainly stale
            # or bad data rather than a real 12%+ edge. Skip both.
            if ev < self.edge_threshold or ev > self.edge_cap:
                continue

            bankroll = self.broker.get_account().cash
            if bankroll <= 0:
                return logs
            kelly = AceAgent._kelly(fair, odds) * self.kelly_fraction
            stake = round(bankroll * min(kelly, self.max_stake_frac), 2)
            if stake < 1:
                continue
            ok = self.broker.place_bet({
                "event_id": fx.event_id,
                "sport_key": fx.sport_key,
                "matchup": f"{fx.home} v {fx.away}",
                "side": side,
                "team": team,
                "stake": stake,
                "odds": odds,
                "est_prob": round(fair, 3),
                "commence_time": fx.commence_time,
            })
            if ok:
                logs.append(f"{fx.home} v {fx.away}: {team} @ {odds} "
                            f"({book}, edge {ev*100:.0f}%, ${stake:.0f})")
        return logs

    def run_round(self) -> list[str]:
        logs = self.settle_finished()
        logs += self.place_value_bets()
        if not logs:
            logs = ["no settled games and no value found this round"]
        return logs
