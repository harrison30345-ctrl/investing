"""Scheduler timing, worker execution and recovery, alerts (dedupe + webhook), health checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quantedge.scheduler import JOBS, is_due, prev_run

JOB = {j.key: j for j in JOBS}
UTC = timezone.utc


def at(*a):
    return datetime(*a, tzinfo=UTC)


# ── scheduler ──────────────────────────────────────────────────────────────

def test_eod_refresh_due_after_close_and_only_once():
    j = JOB["us_eod_refresh"]                         # 16:30 New York on trading days
    before = at(2026, 10, 26, 20, 0)                  # Mon 16:00 NY
    after = at(2026, 10, 26, 20, 45)                  # Mon 16:45 NY
    assert prev_run(j, after) == at(2026, 10, 26, 20, 30)
    assert is_due(j, datetime(2026, 10, 23, 21, 0), after)        # last ran Friday
    assert not is_due(j, datetime(2026, 10, 26, 20, 31), after)   # already ran today
    assert not is_due(j, datetime(2026, 10, 23, 20, 31), before)  # Friday's run done; Monday's not yet due


def test_no_runs_on_weekends_or_holidays():
    j = JOB["uk_eod_refresh"]
    sat = at(2026, 10, 24, 18, 0)
    assert prev_run(j, sat) == at(2026, 10, 23, 16, 15)          # Friday 17:15 London (BST)
    assert not is_due(j, datetime(2026, 10, 23, 16, 20), sat)
    xmas = at(2026, 12, 25, 18, 0)
    assert prev_run(j, xmas).date() == datetime(2026, 12, 24).date()   # LSE shut on Christmas Day


def test_missed_refresh_is_caught_up_after_downtime():
    j = JOB["us_eod_refresh"]
    monday_morning = at(2026, 10, 26, 13, 0)
    assert is_due(j, datetime(2026, 10, 22, 20, 31), monday_morning)   # Friday's run was missed


def test_research_cycle_not_caught_up_when_old():
    j = JOB["research_cycle"]                           # weekdays 23:00 London, 18h catch-up
    assert is_due(j, None, at(2026, 10, 27, 7, 0))                     # Mon 23:00 run, 8h ago, never ran
    assert not is_due(j, datetime(2026, 10, 20), at(2026, 10, 25, 12, 0))   # Fri 23:00 run is >18h old


def test_intraday_poll_only_in_session_window():
    j = JOB["us_intraday_poll"]
    assert is_due(j, None, at(2026, 10, 26, 15, 0))                     # 11:00 NY
    assert not is_due(j, datetime(2026, 10, 26, 14, 58), at(2026, 10, 26, 15, 0))   # ran 2 min ago
    assert is_due(j, None, at(2026, 10, 26, 20, 5))                     # 16:05 NY: grace poll for last bar
    assert not is_due(j, None, at(2026, 10, 26, 21, 0))                 # 17:00 NY
    assert not is_due(j, None, at(2026, 10, 25, 15, 0))                 # Sunday


# ── worker ─────────────────────────────────────────────────────────────────

def test_run_job_records_success_and_failure_with_single_alert(isolated_db, monkeypatch):
    from quantedge import alerts
    from quantedge.db import session_scope
    from quantedge.db.models import Alert, JobRun
    from quantedge.worker import run_job
    sent = []
    monkeypatch.setenv("QE_ALERT_WEBHOOK_URL", "https://hooks.example.test/x")

    class R:
        ok = True
    monkeypatch.setattr(alerts.requests, "post", lambda url, json, timeout: sent.append(json) or R())

    def boom():
        raise RuntimeError("provider down")
    j = JOB["us_eod_refresh"]
    assert run_job(j, handler=boom).status == "failed"
    assert run_job(j, handler=boom).status == "failed"
    with session_scope() as s:
        open_ = s.query(Alert).filter(Alert.resolved_at.is_(None)).all()
        assert len(open_) == 1 and open_[0].notified
    assert len(sent) == 1 and "provider down" in sent[0]["text"]          # notified once, not every failure
    assert run_job(j, handler=lambda: "fine").status == "ok"
    with session_scope() as s:
        assert s.query(Alert).filter(Alert.resolved_at.is_(None)).count() == 0
        assert [r.status for r in s.query(JobRun).order_by(JobRun.id)] == ["failed", "failed", "ok"]


def test_webhook_failure_never_raises(isolated_db, monkeypatch):
    from quantedge import alerts
    monkeypatch.setenv("QE_ALERT_WEBHOOK_URL", "https://hooks.example.test/x")

    def fail(*a, **k):
        raise alerts.requests.ConnectionError("no route")
    monkeypatch.setattr(alerts.requests, "post", fail)
    assert alerts.raise_alert("k", "error", "m") > 0


def test_tick_runs_due_jobs_skips_llm_and_beats(isolated_db):
    from quantedge.health import heartbeat
    from quantedge.worker import tick
    calls = []
    handlers = {k: (lambda k=k: calls.append(k) or "ok") for k in JOB}
    ran = tick(now=at(2026, 10, 26, 15, 0), handlers=handlers)
    assert "research_cycle" not in ran                  # AI disabled in tests
    assert "us_intraday_poll" in ran and "health_check" in ran
    assert heartbeat("worker").status == "ok"
    ran2 = tick(now=at(2026, 10, 26, 15, 0, 30), handlers=handlers)
    assert "us_intraday_poll" not in ran2 and "health_check" not in ran2   # intervals respected


def test_recover_marks_abandoned_runs(isolated_db):
    from quantedge.db import session_scope
    from quantedge.db.models import JobRun
    from quantedge.worker import recover
    with session_scope() as s:
        s.add(JobRun(job="us_eod_refresh", status="running", started_at=datetime.utcnow() - timedelta(hours=5)))
        s.add(JobRun(job="health_check", status="running"))
    assert recover() == 1


def test_second_worker_refused_while_first_alive(isolated_db):
    from quantedge import health
    from quantedge.worker import another_worker_alive
    health.beat("worker", "ok", "otherhost:1234 tick")
    assert another_worker_alive() == "otherhost:1234"
    health.beat("worker", "stopped", "otherhost:1234 stopped")
    assert another_worker_alive() is None


# ── health ─────────────────────────────────────────────────────────────────

def test_stale_daily_data_raises_then_resolves(isolated_db, monkeypatch):
    from quantedge import alerts, health
    from quantedge.data.service import refresh_daily
    refresh_daily(["AAPL"], years=1)
    monkeypatch.setattr(health, "check_provider", lambda now=None, force=False: [])
    far_future = at(2026, 12, 1, 12, 0)
    checks = {c.key: c for c in health.run_checks(now=far_future)}
    assert not checks["freshness:US"].ok
    assert any(a.key == "freshness:US" for a in alerts.open_alerts())
    checks = {c.key: c for c in health.run_checks()}
    assert checks["freshness:US"].ok and not alerts.open_alerts()


def test_provider_check_is_rate_limited(isolated_db, monkeypatch):
    from quantedge import health
    from quantedge.data.providers.demo import DemoProvider
    calls = []
    monkeypatch.setattr(DemoProvider, "healthcheck", lambda self: calls.append(1) or (True, "ok"))
    health.check_provider()
    health.check_provider()
    assert len(calls) == 1
    health.check_provider(force=True)
    assert len(calls) == 2


def test_worker_check_flags_dead_worker(isolated_db):
    from quantedge import health
    assert not health.check_worker()[0].ok
    health.beat("worker", "ok", "h:1 tick")
    assert health.check_worker()[0].ok
    assert not health.check_worker(now=datetime.now(UTC) + timedelta(minutes=10))[0].ok
