from __future__ import annotations

"""
Autonomous scheduler — runs the whole roster on a fixed interval so the agents
trade on their own. This is the "leave it running" mode.

Examples:
    # Every hour, forever (Ctrl-C to stop):
    python3 -m trading.schedule --interval 1h

    # Every 30 minutes, but stop after 10 rounds:
    python3 -m trading.schedule --interval 30m --rounds 10

    # Fast interval for testing:
    python3 -m trading.schedule --interval 30s --rounds 3

For true background operation across reboots, wire the one-shot CLI into cron
instead, e.g. run every weekday at market close:
    30 16 * * 1-5  cd /Users/harrisontallon/investing && python3 -m trading.run
"""

import argparse
import logging
import re
import time
from datetime import datetime

from .config import load_config
from .engine import Engine
from .roster import build_roster

logger = logging.getLogger(__name__)

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_interval(text: str) -> int:
    """Parse '30s' / '15m' / '1h' / '2d' into seconds."""
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", text.lower())
    if not m:
        raise ValueError(f"bad interval {text!r} — use e.g. 30s, 15m, 1h, 2d")
    return int(m.group(1)) * _UNITS[m.group(2)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous trading scheduler")
    parser.add_argument("--interval", default="1h",
                        help="how often to run a tick (e.g. 30s, 15m, 1h, 1d)")
    parser.add_argument("--rounds", type=int, default=0,
                        help="stop after N rounds (0 = run forever)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s", datefmt="%H:%M:%S",
    )

    interval = parse_interval(args.interval)
    config = load_config()
    config.assert_live_allowed()
    agents = build_roster(config)
    engine = Engine(config, agents)

    print(f"\n🎮 Trading Arena scheduler — {config.mode.upper()} mode")
    print(f"   Agents : {', '.join(a.name for a in agents)}")
    print(f"   Every  : {args.interval}"
          + (f"  ·  {args.rounds} rounds" if args.rounds else "  ·  forever")
          + "   (Ctrl-C to stop)\n")

    round_num = 0
    try:
        while True:
            round_num += 1
            print(f"\n── Round {round_num} · {datetime.now():%Y-%m-%d %H:%M:%S} ──")
            engine.run_tick()
            engine.print_leaderboard()

            if args.rounds and round_num >= args.rounds:
                print("\nReached round limit — stopping.")
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\nStopped by user. Final standings:")
        engine.print_leaderboard()


if __name__ == "__main__":
    main()
