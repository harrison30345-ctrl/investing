from __future__ import annotations

"""
Weekly digest — summarizes the race: each agent's return over the past week and
since the start, ranked. Writes a readable summary to ~/.trading/weekly_summary.txt
and fires a macOS notification so you actually notice it. Scheduled weekly via
launchd (com.tradingarena.weekly).

Run manually:  python3 -m trading.weekly
"""

import logging
import subprocess
import time
from datetime import datetime

from .config import STATE_DIR
from .history import load_history

logger = logging.getLogger(__name__)

SUMMARY_PATH = STATE_DIR / "weekly_summary.txt"
WEEK_SECONDS = 7 * 86400


def _latest_per_agent(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        out[r["agent"]] = r  # rows are oldest-first, so last wins
    return out


def _closest_before(rows: list[dict], agent: str, target_ts: float) -> dict | None:
    """The agent's snapshot nearest to `target_ts` (its earliest if none older)."""
    agent_rows = [r for r in rows if r["agent"] == agent]
    if not agent_rows:
        return None
    older = [r for r in agent_rows if r["ts"] <= target_ts]
    return older[-1] if older else agent_rows[0]


def build_summary() -> str:
    rows = load_history()
    if not rows:
        return "No race history yet — the daemon hasn't logged any snapshots."

    now = time.time()
    latest = _latest_per_agent(rows)

    standings = []
    for agent, cur in latest.items():
        wk_ago = _closest_before(rows, agent, now - WEEK_SECONDS)
        wk_start_eq = wk_ago["equity"] if wk_ago else cur["equity"]
        week_change = ((cur["equity"] - wk_start_eq) / wk_start_eq * 100
                       if wk_start_eq else 0.0)
        standings.append({
            "agent": agent,
            "room": cur["room"],
            "equity": cur["equity"],
            "total_ret": cur["return_pct"],
            "week_ret": week_change,
        })
    standings.sort(key=lambda s: s["total_ret"], reverse=True)

    span_days = (now - rows[0]["ts"]) / 86400
    lines = [
        "🏁 TRADING ARENA — WEEKLY DIGEST",
        f"   {datetime.now():%Y-%m-%d %H:%M}  ·  day {span_days:.0f} of ~30",
        "",
        f"   {'RANK':<6}{'AGENT':<8}{'EQUITY':>12}{'THIS WEEK':>12}{'TOTAL':>10}",
        "   " + "-" * 48,
    ]
    medals = ["🥇", "🥈", "🥉"] + ["  "] * 9
    for i, s in enumerate(standings):
        lines.append(
            f"   {medals[i]:<4}{s['agent']:<8}${s['equity']:>10,.0f}"
            f"{s['week_ret']:>+11.1f}%{s['total_ret']:>+9.1f}%")
    leader = standings[0]
    lines += ["", f"   Leader: {leader['agent']} ({leader['room']}) "
                  f"at {leader['total_ret']:+.1f}% overall."]
    return "\n".join(lines)


def _notify(leader: str, ret: float) -> None:
    """Best-effort macOS notification; silently no-ops if it can't run."""
    try:
        msg = f"Leader: {leader} at {ret:+.1f}%. See weekly_summary.txt"
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{msg}" with title "🏁 Trading Arena — Weekly"'],
            check=False, timeout=10)
    except Exception as e:  # noqa: BLE001
        logger.debug("notification failed: %s", e)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    summary = build_summary()
    SUMMARY_PATH.write_text(summary + "\n")
    print(summary)

    rows = load_history()
    if rows:
        latest = _latest_per_agent(rows)
        best = max(latest.values(), key=lambda r: r["return_pct"])
        _notify(best["agent"], best["return_pct"])


if __name__ == "__main__":
    main()
