from __future__ import annotations

"""
Automated daily cycle — the entrypoint the scheduler (launchd) runs unattended.

One cycle:
  * Each trend trader (Vega, Nova) re-evaluates its signal and rebalances if the
    trend changed. Daily cadence suits the 200-day SMA — checking intraday would
    just add whipsaw the backtest showed hurts.
  * Ace plays several betting rounds so its thin edge accrues volume over time.
  * Every agent's equity is snapshotted to the history log for the month-long
    race chart.

Run manually:  python3 -m trading.daemon
Scheduled:     launchd runs `python3 -m trading.daemon` on an interval.
"""

import argparse
import logging

from .config import load_config
from .engine import Engine
from .history import snapshot
from .roster import build_roster

logger = logging.getLogger(__name__)


def run_cycle(ace_rounds: int = 10) -> None:
    config = load_config()
    config.assert_live_allowed()
    agents = build_roster(config)
    engine = Engine(config, agents)

    for agent in agents:
        if getattr(agent, "is_betting", False):
            # Sim Ace plays many rounds per cycle; live Ace plays one real slate.
            rounds = getattr(agent, "rounds_per_cycle", ace_rounds)
            for _ in range(rounds):
                for line in agent.run_round():
                    logger.info("%s: %s", agent.name, line)
        else:
            engine.run_agent(agent)

    snapshot(agents)
    engine.print_leaderboard()


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated daily trading cycle")
    parser.add_argument("--ace-rounds", type=int, default=10,
                        help="betting rounds Ace plays per cycle")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("=== daily cycle start ===")
    try:
        run_cycle(ace_rounds=args.ace_rounds)
    except Exception:
        logger.exception("daily cycle failed")
        raise
    logger.info("=== daily cycle done ===")


if __name__ == "__main__":
    main()
