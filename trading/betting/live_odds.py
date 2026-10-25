from __future__ import annotations

"""
Real sports odds via The Odds API (https://the-odds-api.com) — free tier gives
~500 requests/month, enough for a few daemon cycles a day.

Ace's edge here is line-shopping value: for each game we de-vig every book's
prices into a fair probability, take the CONSENSUS across books as the market's
best estimate of the true probability, then look for a book offering odds better
than that consensus implies (positive expected value versus the market). This
doesn't require predicting games better than everyone — just spotting the sharpest
available price.

The API key is read from the ODDS_API_KEY env var, or a ~/.trading/odds_api_key
file (so you never have to paste it into a prompt, and launchd can read it too).
"""

import logging
from dataclasses import dataclass
from statistics import mean

import requests

from ..config import STATE_DIR

logger = logging.getLogger(__name__)

BASE = "https://api.the-odds-api.com/v4"
DEFAULT_SPORT = "upcoming"   # next games across all in-season sports


def get_api_key() -> str | None:
    import os
    key = os.environ.get("ODDS_API_KEY")
    if key:
        return key.strip()
    path = STATE_DIR / "odds_api_key"
    if path.exists():
        val = path.read_text().strip()
        return val or None
    return None


@dataclass
class LiveFixture:
    event_id: str
    sport_key: str
    commence_time: str
    home: str
    away: str
    home_best_odds: float
    away_best_odds: float
    home_fair_prob: float   # de-vigged consensus across books
    away_fair_prob: float
    home_book: str
    away_book: str


class TheOddsAPIFeed:
    def __init__(self, sport: str = DEFAULT_SPORT, api_key: str | None = None,
                 regions: str = "us", min_books: int = 3):
        self.sport = sport
        self.regions = regions
        self.api_key = api_key or get_api_key()
        # Line-shopping needs several real books; thin/1-book markets are skipped.
        self.min_books = min_books

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    # ---- HTTP ------------------------------------------------------------
    def _get(self, path: str, **params):
        params["apiKey"] = self.api_key
        r = requests.get(f"{BASE}{path}", params=params, timeout=20)
        r.raise_for_status()
        remaining = r.headers.get("x-requests-remaining")
        if remaining is not None:
            logger.info("Odds API requests remaining: %s", remaining)
        return r.json()

    # ---- odds ------------------------------------------------------------
    def next_slate(self, n_games: int = 12) -> list[LiveFixture]:
        """Fetch upcoming games and reduce each to consensus + best price."""
        if not self.available:
            return []
        try:
            events = self._get(f"/sports/{self.sport}/odds",
                               regions=self.regions, markets="h2h",
                               oddsFormat="decimal")
        except Exception as e:  # noqa: BLE001
            logger.warning("odds fetch failed: %s", e)
            return []

        fixtures = []
        for ev in events[:n_games]:
            fx = self._parse_event(ev, self.min_books)
            if fx is not None:
                fixtures.append(fx)
        return fixtures

    @staticmethod
    def _parse_event(ev: dict, min_books: int = 3) -> "LiveFixture | None":
        home, away = ev.get("home_team"), ev.get("away_team")
        if not home or not away:
            return None

        fair_home, best_home, best_away = [], (0.0, ""), (0.0, "")
        for bm in ev.get("bookmakers", []):
            price_home = price_away = None
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                for out in mkt.get("outcomes", []):
                    if out["name"] == home:
                        price_home = out["price"]
                    elif out["name"] == away:
                        price_away = out["price"]
            if not price_home or not price_away:
                continue
            # Skip suspended/placeholder lines (a near-1.0 price = "certain").
            if min(price_home, price_away) <= 1.02:
                continue
            # De-vig this book into a fair home probability.
            ih, ia = 1 / price_home, 1 / price_away
            fair_home.append(ih / (ih + ia))
            if price_home > best_home[0]:
                best_home = (price_home, bm.get("title", "?"))
            if price_away > best_away[0]:
                best_away = (price_away, bm.get("title", "?"))

        # Need enough real books to have a trustworthy consensus to shop against.
        if len(fair_home) < min_books or best_home[0] == 0 or best_away[0] == 0:
            return None

        consensus_home = mean(fair_home)
        return LiveFixture(
            event_id=ev["id"], sport_key=ev.get("sport_key", ""),
            commence_time=ev.get("commence_time", ""),
            home=home, away=away,
            home_best_odds=best_home[0], away_best_odds=best_away[0],
            home_fair_prob=consensus_home, away_fair_prob=1 - consensus_home,
            home_book=best_home[1], away_book=best_away[1],
        )

    # ---- results ---------------------------------------------------------
    def winners(self, sport_key: str, days_from: int = 3) -> dict[str, str]:
        """
        Return {event_id: winning_team_name} for completed games in `sport_key`.
        Used to settle open bets against real outcomes.
        """
        if not self.available:
            return {}
        try:
            events = self._get(f"/sports/{sport_key}/scores", daysFrom=days_from)
        except Exception as e:  # noqa: BLE001
            logger.warning("scores fetch failed for %s: %s", sport_key, e)
            return {}

        out = {}
        for ev in events:
            if not ev.get("completed"):
                continue
            scores = ev.get("scores") or []
            best = None
            for s in scores:
                try:
                    val = float(s["score"])
                except (TypeError, ValueError):
                    continue
                if best is None or val > best[0]:
                    best = (val, s["name"])
            if best is not None:
                out[ev["id"]] = best[1]
        return out
