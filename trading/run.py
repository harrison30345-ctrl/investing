from __future__ import annotations

"""
CLI entrypoint — runs the whole roster (the game world) for one tick.

Examples:
    # Run every agent once on the built-in paper simulator (no account needed):
    python3 -m trading.run

    # Just show current standings without trading:
    python3 -m trading.run --leaderboard-only

The web hub (`streamlit run trading/hub.py`) is the visual front-end for the same
agents and state; this CLI is handy for scripted/scheduled runs.
"""

import argparse
import logging

from .config import load_config
from .engine import Engine
from .roster import build_roster


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-agent trading engine")
    parser.add_argument("--leaderboard-only", action="store_true",
                        help="show standings without running a trading tick")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    config = load_config()
    agents = build_roster(config)
    engine = Engine(config, agents)

    print(f"\nMode: {config.mode.upper()}  |  Agents: "
          f"{', '.join(a.name for a in agents)}")

    if not args.leaderboard_only:
        engine.run_tick()

    engine.print_leaderboard()


if __name__ == "__main__":
    main()
