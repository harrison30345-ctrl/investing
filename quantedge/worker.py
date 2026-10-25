"""
Background worker: the always-on process that runs QuantEdge's scheduled jobs.

    python3 -m quantedge.worker            # run forever (Ctrl-C or SIGTERM to stop)
    python3 -m quantedge.worker --once     # one tick, then exit (testing, cron)
    python3 -m quantedge.worker --run uk_eod_refresh   # run one job now

Every tick (QE_WORKER_TICK_S, default 30 s):
  1. write a heartbeat (the dashboard's "Worker online" pill reads this)
  2. for each job in scheduler.JOBS, run it if `is_due` says so

Jobs and their market awareness are defined in scheduler.py. Each run is a
JobRun row. A failure raises an alert and doesn't stop the loop; the next
success resolves the alert. On start-up, runs left "running" by a crashed
process are marked failed, and a second worker refuses to start while the
first is alive.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from sqlalchemy import select

from . import alerts, health
from .config import get_settings, worker_tick_seconds
from .db import session_scope
from .db.models import JobRun, PaperAccount, utcnow
from .scheduler import JOBS, Job, is_due

log = logging.getLogger("quantedge.worker")
STALE_RUN = timedelta(hours=2)
_stop = False


# ── job handlers ────────────────────────────────────────────────────────────

def _daily_symbols(market: str) -> List[str]:
    from .universe import BENCHMARKS, INDICES, UNIVERSES, get
    from .watchlists import all_watched_symbols
    syms = [i.symbol for i in INDICES + BENCHMARKS if i.market == market]
    syms += UNIVERSES["uk_large" if market == "UK" else "us_large"]
    syms += [s for s in all_watched_symbols() if get(s).market == market]
    with session_scope() as s:
        for a in s.scalars(select(PaperAccount).where(PaperAccount.status == "active",
                                                      PaperAccount.timeframe == "daily",
                                                      PaperAccount.market == market)):
            syms += list(a.universe)
    return list(dict.fromkeys(syms))


def _eod(market: str) -> str:
    from . import paper
    from .data.service import refresh_daily
    from .signals import scan_signals
    res = refresh_daily(_daily_symbols(market), trigger="schedule")
    ok = [r.symbol for r in res if r.status == "ok"]
    n_sig = scan_signals(ok, trigger="schedule")
    stepped = paper.step_all("daily")
    bad = [r.symbol for r in res if r.status != "ok"]
    if len(bad) > len(res) / 2:
        raise RuntimeError(f"{len(bad)}/{len(res)} {market} symbols failed to refresh: {', '.join(bad[:8])}")
    return (f"{len(ok)}/{len(res)} symbols refreshed, {n_sig} new signals, "
            f"{sum(v for v in stepped.values() if v > 0)} paper bars processed")


def _intraday() -> str:
    from . import paper
    from .data.service import refresh_intraday
    from .universe import UNIVERSES
    syms = list(UNIVERSES["us_intraday"])
    with session_scope() as s:
        for a in s.scalars(select(PaperAccount).where(PaperAccount.status == "active",
                                                      PaperAccount.timeframe == "intraday")):
            syms += list(a.universe)
    res = refresh_intraday(list(dict.fromkeys(syms)), trigger="schedule")
    stepped = paper.step_all("intraday")
    return f"{sum(r.status == 'ok' for r in res)}/{len(res)} symbols, " \
           f"{sum(v for v in stepped.values() if v > 0)} paper bars processed"


def _health() -> str:
    checks = health.run_checks()
    failing = [c for c in checks if not c.ok]
    return f"{len(checks) - len(failing)}/{len(checks)} checks ok" + \
           (": " + "; ".join(c.message for c in failing) if failing else "")


def _research() -> str:
    from .agents.orchestrator import run_cycle
    r = run_cycle("schedule")
    return f"{r.status}: {r.detail}"


HANDLERS: Dict[str, Callable[[], str]] = {
    "uk_eod_refresh": lambda: _eod("UK"),
    "us_eod_refresh": lambda: _eod("US"),
    "us_intraday_poll": _intraday,
    "health_check": _health,
    "research_cycle": _research,
}


# ── execution ───────────────────────────────────────────────────────────────

def last_started(job_key: str) -> Optional[datetime]:
    with session_scope() as s:
        return s.scalar(select(JobRun.started_at).where(JobRun.job == job_key).order_by(JobRun.id.desc()).limit(1))


def run_job(job: Job, trigger: str = "schedule", handler: Optional[Callable[[], str]] = None,
            started_at: Optional[datetime] = None) -> JobRun:
    handler = handler or HANDLERS[job.key]
    with session_scope() as s:
        run = JobRun(job=job.key, status="running", trigger=trigger, started_at=started_at or utcnow())
        s.add(run)
        s.flush()
        run_id = run.id
    t0 = time.monotonic()
    try:
        detail, status = handler(), "ok"
        alerts.resolve(f"job:{job.key}")
    except Exception as exc:
        detail, status = f"{type(exc).__name__}: {exc}", "failed"
        log.error("job %s failed: %s\n%s", job.key, exc, traceback.format_exc(limit=6))
        alerts.raise_alert(f"job:{job.key}", "error", f"{job.name} failed: {exc}")
    with session_scope() as s:
        run = s.get(JobRun, run_id)
        run.status, run.finished_at = status, utcnow()
        run.detail = f"{detail} ({time.monotonic() - t0:.1f}s)"
        s.flush()          # write before detaching, or the update is lost
        s.expunge(run)
        return run


def recover() -> int:
    """Mark runs left 'running' by a dead process as failed."""
    n = 0
    with session_scope() as s:
        for r in s.scalars(select(JobRun).where(JobRun.status == "running")):
            if utcnow() - r.started_at > STALE_RUN:
                r.status, r.finished_at, r.detail = "failed", utcnow(), "Abandoned: worker stopped mid-run"
                n += 1
    return n


def identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def another_worker_alive() -> Optional[str]:
    hb = health.heartbeat("worker")
    if hb is None or hb.status == "stopped" or utcnow() - hb.last_seen > health.WORKER_STALE:
        return None
    holder = (hb.detail or "").split(" ")[0]
    return holder if holder and holder != identity() else None


def tick(now: Optional[datetime] = None, handlers: Optional[Dict[str, Callable[[], str]]] = None) -> List[str]:
    """One pass: heartbeat, then run whatever is due. Returns the job keys that ran."""
    s = get_settings()
    health.beat("worker", "ok", f"{identity()} tick")
    ran = []
    for job in JOBS:
        if job.needs_llm and not s.llm_configured:
            continue
        if is_due(job, last_started(job.key), now):
            start = now.astimezone(timezone.utc).replace(tzinfo=None) if now else None
            run_job(job, "schedule", (handlers or {}).get(job.key), started_at=start)
            ran.append(job.key)
            health.beat("worker", "ok", f"{identity()} ran {job.key}")
    return ran


def _handle_stop(signum, frame):  # noqa: ARG001
    global _stop
    _stop = True
    log.info("Stop requested (signal %s); finishing current job", signum)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quantedge.worker")
    ap.add_argument("--once", action="store_true", help="single tick then exit")
    ap.add_argument("--run", choices=sorted(HANDLERS), help="run one job immediately and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.run:
        job = next(j for j in JOBS if j.key == args.run)
        r = run_job(job, "manual")
        print(f"{job.key}: {r.status}: {r.detail}")
        return 0 if r.status == "ok" else 1
    other = another_worker_alive()
    if other:
        log.error("Another worker (%s) is running; refusing to start a second one.", other)
        return 2
    n = recover()
    if n:
        log.warning("Recovered %d abandoned job runs", n)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    log.info("Worker %s started; tick every %.0fs; jobs: %s", identity(), worker_tick_seconds(),
             ", ".join(j.key for j in JOBS))
    try:
        while not _stop:
            try:
                ran = tick()
                if ran:
                    log.info("ran: %s", ", ".join(ran))
            except Exception:       # never let one bad tick kill the process
                log.exception("tick failed")
            if args.once:
                break
            for _ in range(int(worker_tick_seconds())):
                if _stop:
                    break
                time.sleep(1)
    finally:
        health.beat("worker", "stopped", f"{identity()} stopped")
        log.info("Worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
