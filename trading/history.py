from __future__ import annotations

"""
Equity history — an append-only log of each agent's equity over time, so the
month-long race can be charted and a winner declared. One JSON line per agent
per snapshot, written to ~/.trading/history.jsonl.
"""

import json
import time
from datetime import datetime
from pathlib import Path

from .config import STATE_DIR

HISTORY_PATH = STATE_DIR / "history.jsonl"


def snapshot(agents) -> None:
    """Append a timestamped equity reading for every agent."""
    now = time.time()
    stamp = datetime.now().isoformat(timespec="seconds")
    lines = []
    for agent in agents:
        acct = agent.broker.get_account()
        start = getattr(agent.broker, "starting_cash", acct.equity)
        ret = (acct.equity - start) / start * 100 if start else 0.0
        lines.append(json.dumps({
            "ts": now,
            "time": stamp,
            "agent": agent.name,
            "room": agent.room,
            "equity": round(acct.equity, 2),
            "cash": round(acct.cash, 2),
            "starting": start,
            "return_pct": round(ret, 3),
        }))
    with HISTORY_PATH.open("a") as f:
        f.write("\n".join(lines) + "\n")


def load_history() -> list[dict]:
    """Read the full equity history, oldest first."""
    if not HISTORY_PATH.exists():
        return []
    rows = []
    for line in HISTORY_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows
