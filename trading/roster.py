from __future__ import annotations

"""
The game world's cast of agents. Both the CLI (`run.py`) and the game hub
(`hub.py`) build their agents from here, so there is one source of truth for who
lives in the world and which room they're in.

Both trading agents run the validated trend-following strategy (price above its
200-day moving average, checked periodically): hold names in an uptrend, sit in
cash when the trend breaks. Same brain, different markets. Each agent runs on its
own isolated paper-sim account, making the leaderboard a real competition.
"""

import os

from .agents.momentum import momentum_score
from .agents.signal_agent import PriceSignalAgent
from .agents.trend import price_vs_sma_score
from .betting.agent import AceAgent
from .betting.book import SportsBook
from .betting.live_agent import LiveAceAgent
from .betting.live_odds import TheOddsAPIFeed, get_api_key
from .broker.paper_sim import PaperSimBroker
from .config import TradingConfig

STOCK_UNIVERSE = [
    "AAPL", "MSFT", "GOOG", "NVDA", "AMZN",
    "META", "JPM", "V", "UNH", "COST",
]

CRYPTO_UNIVERSE = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
    "ADA-USD", "DOGE-USD", "AVAX-USD", "LINK-USD", "DOT-USD",
]


def build_roster(config: TradingConfig) -> list:
    """Construct every agent in the world, each with its own sim account."""
    agents = []

    # --- The Trading Floor: trend-following stock trader ---
    agents.append(PriceSignalAgent(
        broker=PaperSimBroker(account_id="vega", state_dir=config.state_dir),
        universe=STOCK_UNIVERSE,
        score_fn=price_vs_sma_score,
        name="Vega",
        room="The Trading Floor",
        avatar="📈",
        personality="A disciplined trend-follower — rides uptrends, steps aside in downtrends.",
        fractional=False,
    ))

    # --- The Crypto Casino: trend-following crypto trader ---
    agents.append(PriceSignalAgent(
        broker=PaperSimBroker(account_id="nova", state_dir=config.state_dir),
        universe=CRYPTO_UNIVERSE,
        score_fn=price_vs_sma_score,
        name="Nova",
        room="The Crypto Casino",
        avatar="🚀",
        personality="Rides crypto uptrends and bails to cash when the trend cracks.",
        fractional=True,
    ))

    # --- The Degen Den: always-active momentum crypto trader ---
    # Same universe as Nova, but momentum-rotation with a zero buy threshold, so
    # it ALWAYS holds the 3 strongest coins — even in a downtrend. The risky,
    # always-in counterpart to Nova's disciplined trend-following.
    agents.append(PriceSignalAgent(
        broker=PaperSimBroker(account_id="blitz", state_dir=config.state_dir),
        universe=CRYPTO_UNIVERSE,
        score_fn=momentum_score,
        name="Blitz",
        room="The Degen Den",
        avatar="⚡",
        personality="Always in — chases the 3 hottest coins, downtrend or not.",
        buy_threshold=0.0,      # top-ranked names always qualify
        sell_threshold=0.0,     # only rotates out when a coin leaves the top 3
        max_positions=3,
        stop_loss_pct=100.0,    # no stop — pure momentum rotation
        fractional=True,
        history_period="6mo",
    ))

    # --- The Sportsbook: value-betting sports agent ---
    # Real odds if an API key is configured, otherwise the built-in simulation.
    ace_book = SportsBook(account_id="ace", state_dir=config.state_dir)
    if get_api_key():
        feed = TheOddsAPIFeed(sport=os.environ.get("ODDS_SPORT", "upcoming"))
        agents.append(LiveAceAgent(ace_book, feed))
    else:
        agents.append(AceAgent(book=ace_book, name="Ace"))

    return agents
