"""
Health checks. Run by the worker every few minutes, and on demand by
`python3 -m quantedge.cli health` (whose exit code container health checks can use).

Each check returns a result. A failing check opens (or refreshes) an alert and a
passing one resolves it, so the dashboard always shows current problems, not history.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pandas as pd
from sqlalchemy import select

from . import alerts
from .config import get_settings
from .data import repository as repo
from .db import session_scope
from .db.models import Heartbeat, PaperAccount, utcnow
from .markets import MARKETS, last_completed_session, schedule, session_status

WORKER_STALE = timedelta(minutes=3)
PROVIDER_CHECK_EVERY = timedelta(minutes=30)
DAILY_GRACE = timedelta(hours=3)          # after a session closes, how long before missing bars count as stale
INTRADAY_STALE = timedelta(minutes=20)


@dataclass
class Check:
    key: str
    ok: bool
    level: str          # warning | error
    message: str


def beat(service: str, status: str = "ok", detail: Optional[str] = None) -> None:
    with session_scope() as s:
        hb = s.get(Heartbeat, service)
        if hb is None:
            s.add(Heartbeat(service=service, last_seen=utcnow(), status=status, detail=detail))
        else:
            hb.last_seen, hb.status, hb.detail = utcnow(), status, detail


def heartbeat(service: str) -> Optional[Heartbeat]:
    with session_scope() as s:
        hb = s.get(Heartbeat, service)
        if hb is not None:
            s.expunge(hb)
        return hb


def _utcnow(now: Optional[datetime]) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)


def check_daily_freshness(now: Optional[datetime] = None) -> List[Check]:
    pkey = get_settings().data_provider
    rows = [r for r in repo.freshness(pkey) if r.last_bar is not None]
    out = []
    n = _utcnow(now)
    for m in MARKETS:
        mine = [r for r in rows if r.market == m]
        if not mine:
            continue
        expected = last_completed_session(m, now)
        latest = max(r.last_bar for r in mine)
        stale = expected is not None and latest.date() < expected
        # Give the scheduled refresh time to run after the close before calling it stale.
        if stale:
            # `expected` is the last *completed* session; its bar is due once the refresh window has passed.
            done_close = schedule(m, expected, expected)["market_close"].iloc[0].tz_convert("UTC").tz_localize(None)
            if n < done_close + DAILY_GRACE:
                stale = False
        out.append(Check(f"freshness:{m}", not stale, "error" if stale else "warning",
                         f"{m} daily bars end {latest:%d %b}; last completed session {expected:%d %b}" if stale else
                         f"{m} daily bars current ({latest:%d %b})"))
    return out


def check_intraday_freshness(now: Optional[datetime] = None) -> List[Check]:
    pkey = get_settings().data_provider
    rows = [r for r in repo.freshness(pkey, interval="5m") if r.last_bar is not None]
    if not rows or not session_status("US", now).is_open:
        return [Check("freshness:intraday", True, "warning", "Intraday checks only apply during the US session")]
    latest = max(r.last_bar for r in rows)
    lag = _utcnow(now) - latest
    ok = lag <= INTRADAY_STALE
    return [Check("freshness:intraday", ok, "warning",
                  f"Latest 5-minute bar is {int(lag.total_seconds() // 60)} min old" if not ok else "Intraday current")]


def check_provider(now: Optional[datetime] = None, force: bool = False) -> List[Check]:
    """Network check, rate-limited: at most every 30 minutes unless forced."""
    from .data.providers import get_provider
    pkey = get_settings().data_provider
    svc = f"provider:{pkey}"
    hb = heartbeat(svc)
    n = _utcnow(now)
    if not force and hb is not None and n - hb.last_seen < PROVIDER_CHECK_EVERY:
        return [Check("provider", hb.status == "ok", "error", hb.detail or "")]
    try:
        ok, msg = get_provider().healthcheck()
    except Exception as exc:
        ok, msg = False, str(exc)
    beat(svc, "ok" if ok else "error", msg)
    return [Check("provider", ok, "error", f"{pkey}: {msg}")]


def check_worker(now: Optional[datetime] = None) -> List[Check]:
    hb = heartbeat("worker")
    if hb is None:
        return [Check("worker", False, "warning", "Background worker has never run")]
    age = _utcnow(now) - hb.last_seen
    ok = age <= WORKER_STALE
    return [Check("worker", ok, "error", f"Worker heartbeat {int(age.total_seconds())}s old" if not ok else
                  "Worker alive")]


def check_budget() -> List[Check]:
    s = get_settings()
    if not s.llm_configured:
        return []
    from .agents.budget import month_to_date
    used = month_to_date()
    ok = used < 0.8 * s.llm_monthly_budget_usd
    return [Check("llm_budget", ok, "warning", f"AI spend ${used:.2f} of ${s.llm_monthly_budget_usd:.2f} this month")]


def check_paper() -> List[Check]:
    with session_scope() as s:
        out = []
        for a in s.scalars(select(PaperAccount).where(PaperAccount.status == "active")):
            failed = (a.status_note or "").startswith("Last step failed")
            out.append(Check(f"paper:{a.id}", not failed, "warning",
                             f"Paper account '{a.name}': {a.status_note}" if failed else "ok"))
        return out


def run_checks(now: Optional[datetime] = None, include_worker: bool = False,
               force_provider: bool = False) -> List[Check]:
    checks = (check_daily_freshness(now) + check_intraday_freshness(now) + check_provider(now, force_provider)
              + check_budget() + check_paper())
    if include_worker:
        checks += check_worker(now)
    for c in checks:
        if c.ok:
            alerts.resolve(c.key)
        else:
            alerts.raise_alert(c.key, c.level, c.message)
    return checks
