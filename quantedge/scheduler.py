"""
Scheduled job definitions, aware of exchange sessions.

This module describes the schedule and decides when each job is due. The
worker process (quantedge/worker.py) asks `is_due` every tick and executes.
The dashboard uses `next_run` to show upcoming jobs.

Timing rationale
  * UK EOD refresh 17:15 London: LSE closes 16:30 and the closing auction ends
    ~16:35; vendors publish final daily bars a little later.
  * US EOD refresh 16:30 New York: NYSE closes 16:00.
  * Intraday polling only while the US session is open.
  * The AI research cycle runs overnight, after both markets' data is in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import List, Optional, Tuple

import pandas as pd

from .markets import MARKETS, schedule


@dataclass(frozen=True)
class Job:
    key: str
    name: str
    description: str
    cadence: str                  # "trading_days" | "weekdays" | "interval" | "session_interval"
    market: Optional[str] = None
    at: Optional[str] = None      # "HH:MM" in the market's (or London's) local time
    every_minutes: int = 0
    needs_llm: bool = False
    catch_up_hours: float = 18.0  # after downtime, a missed daily run is made up if it's at most this old


JOBS: List[Job] = [
    Job("uk_eod_refresh", "UK daily bars", "Fetch + validate LSE daily bars, scan signals, step UK paper accounts",
        "trading_days", "UK", "17:15", catch_up_hours=120),
    Job("us_eod_refresh", "US daily bars", "Fetch + validate US daily bars, scan signals, step US paper accounts",
        "trading_days", "US", "16:30", catch_up_hours=120),
    Job("us_intraday_poll", "US intraday bars", "5-minute bars during the US session; steps intraday paper accounts",
        "session_interval", "US", every_minutes=5),
    Job("health_check", "Health & freshness", "Data freshness, provider reachability, budgets, paper accounts",
        "interval", every_minutes=5),
    Job("research_cycle", "AI research cycle", "Agents review new evidence; skipped when nothing changed", "weekdays",
        "UK", "23:00", needs_llm=True),
]


def _now(now: Optional[datetime]) -> pd.Timestamp:
    ts = pd.Timestamp(now or datetime.now(timezone.utc))
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def next_run(job: Job, now: Optional[datetime] = None) -> Optional[pd.Timestamp]:
    """Next execution time in UTC, or None if it can't be determined."""
    now_ts = _now(now)
    if job.cadence == "interval":
        return now_ts.ceil(f"{job.every_minutes}min")
    tz = MARKETS[job.market or "UK"].tz
    if job.cadence == "weekdays":
        hh, mm = map(int, job.at.split(":"))
        local = now_ts.tz_convert(tz)
        for d in range(0, 8):
            day = (local + timedelta(days=d)).date()
            if day.weekday() < 5:
                cand = pd.Timestamp(datetime.combine(day, time(hh, mm))).tz_localize(tz).tz_convert("UTC")
                if cand > now_ts:
                    return cand
        return None
    sched = schedule(job.market, now_ts.date() - timedelta(days=1), now_ts.date() + timedelta(days=14))
    if job.cadence == "trading_days":
        hh, mm = map(int, job.at.split(":"))
        for day in sched.index:
            cand = pd.Timestamp(datetime.combine(day.date(), time(hh, mm))).tz_localize(tz).tz_convert("UTC")
            if cand > now_ts:
                return cand
        return None
    if job.cadence == "session_interval":
        for _, row in sched.iterrows():
            if row["market_close"] <= now_ts:
                continue
            start = max(row["market_open"], now_ts)
            return start.ceil(f"{job.every_minutes}min")
    return None


def upcoming(now: Optional[datetime] = None) -> List[Tuple[Job, Optional[pd.Timestamp]]]:
    rows = [(j, next_run(j, now)) for j in JOBS]
    return sorted(rows, key=lambda r: (r[1] is None, r[1] if r[1] is not None else pd.Timestamp.max.tz_localize("UTC")))


AFTER_CLOSE_GRACE = timedelta(minutes=10)   # one more intraday poll just after the close picks up the final bar


def prev_run(job: Job, now: Optional[datetime] = None) -> Optional[pd.Timestamp]:
    """Most recent scheduled time at or before now (daily-style jobs), in UTC."""
    now_ts = _now(now)
    tz = MARKETS[job.market or "UK"].tz
    hh, mm = map(int, job.at.split(":"))
    if job.cadence == "weekdays":
        local = now_ts.tz_convert(tz)
        for d in range(0, 8):
            day = (local - timedelta(days=d)).date()
            if day.weekday() < 5:
                cand = pd.Timestamp(datetime.combine(day, time(hh, mm))).tz_localize(tz).tz_convert("UTC")
                if cand <= now_ts:
                    return cand
        return None
    sched = schedule(job.market, now_ts.date() - timedelta(days=14), now_ts.date() + timedelta(days=1))
    for day in reversed(list(sched.index)):
        cand = pd.Timestamp(datetime.combine(day.date(), time(hh, mm))).tz_localize(tz).tz_convert("UTC")
        if cand <= now_ts:
            return cand
    return None


def is_due(job: Job, last_started: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """`last_started` is naive UTC (database convention) or None if the job has never run."""
    now_ts = _now(now)
    last = pd.Timestamp(last_started).tz_localize("UTC") if last_started is not None else None
    if job.cadence == "interval":
        return last is None or now_ts - last >= timedelta(minutes=job.every_minutes)
    if job.cadence == "session_interval":
        sched = schedule(job.market, now_ts.date() - timedelta(days=1), now_ts.date())
        in_window = any(row["market_open"] <= now_ts <= row["market_close"] + AFTER_CLOSE_GRACE
                        for _, row in sched.iterrows())
        return in_window and (last is None or now_ts - last >= timedelta(minutes=job.every_minutes))
    p = prev_run(job, now)
    if p is None or now_ts - p > timedelta(hours=job.catch_up_hours):
        return False
    return last is None or last < p
