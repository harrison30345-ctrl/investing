from __future__ import annotations

"""
Sportsbook odds feed. A real book would come from a paid odds API (e.g. The Odds
API); this is a self-contained SIMULATED feed so Ace runs with zero setup, and it
implements a clean interface you can later swap for a live source.

Each fixture carries a hidden `true_prob` (the real chance the home side wins) and
a bookmaker `home_odds` / `away_odds` in decimal format. The book adds a margin
("vig") so the implied probabilities sum to > 1 — the house edge Ace must overcome
by finding genuinely mispriced games.
"""

import random
from dataclasses import dataclass


@dataclass
class Fixture:
    game_id: str
    home: str
    away: str
    home_odds: float       # decimal odds offered on the home side
    away_odds: float       # decimal odds offered on the away side
    true_prob_home: float  # hidden true probability home wins (for settlement)

    def implied_prob(self, side: str) -> float:
        odds = self.home_odds if side == "home" else self.away_odds
        return 1.0 / odds


TEAMS = [
    "Sharks", "Wolves", "Falcons", "Titans", "Rovers", "Kings",
    "Comets", "Raptors", "Bulls", "Storm", "Vipers", "Giants",
]


class SimOddsFeed:
    """
    Generates a slate of fixtures with a realistic bookmaker margin.

    Crucially, the book is IMPERFECT: it prices each game off a noisy estimate of
    the true probability (`book_noise`), then adds vig. That mispricing is what a
    sharper bettor can exploit — without it, no edge is possible against the vig.
    """

    def __init__(self, margin: float = 0.05, book_noise: float = 0.07,
                 seed: int | None = None):
        self.margin = margin           # ~5% overround, typical for major sports
        self.book_noise = book_noise    # how wrong the book's price can be
        self._rng = random.Random(seed)
        self._counter = 0

    def _odds(self, book_prob: float) -> float:
        """Decimal odds for the book's (vig-inflated) view of a probability."""
        implied = min(max(book_prob, 0.02), 0.98) * (1 + self.margin)
        return round(1.0 / min(implied, 0.99), 2)

    def next_slate(self, n_games: int = 6) -> list[Fixture]:
        """Return `n_games` fresh fixtures for this round."""
        fixtures = []
        teams = self._rng.sample(TEAMS, min(2 * n_games, len(TEAMS)))
        for i in range(n_games):
            home = teams[(2 * i) % len(teams)]
            away = teams[(2 * i + 1) % len(teams)]
            true_p = self._rng.uniform(0.30, 0.70)
            # The book's noisy read on the home side; away is its complement.
            book_p = min(max(true_p + self._rng.gauss(0, self.book_noise), 0.05), 0.95)
            self._counter += 1
            fixtures.append(Fixture(
                game_id=f"G{self._counter}",
                home=home, away=away,
                home_odds=self._odds(book_p),
                away_odds=self._odds(1 - book_p),
                true_prob_home=true_p,
            ))
        return fixtures

    def settle(self, fixture: Fixture) -> str:
        """Play the game out from its true probability. Returns 'home' or 'away'."""
        return "home" if self._rng.random() < fixture.true_prob_home else "away"
