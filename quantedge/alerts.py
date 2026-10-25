"""
Alerts: raised by health checks and failed jobs, shown on the dashboard, and
optionally pushed to a webhook (QE_ALERT_WEBHOOK_URL).

One open alert per key. Raising it again only refreshes `last_seen`; a later
passing check resolves it. Notifications go out once, when an alert opens.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import requests
from sqlalchemy import select

from .config import alert_webhook_url
from .db import session_scope
from .db.models import Alert, utcnow

log = logging.getLogger(__name__)


def raise_alert(key: str, level: str, message: str) -> int:
    with session_scope() as s:
        a = s.scalars(select(Alert).where(Alert.key == key, Alert.resolved_at.is_(None))).first()
        if a is not None:
            a.last_seen, a.message, a.level = utcnow(), message, level
            return a.id
        a = Alert(key=key, level=level, message=message)
        s.add(a)
        s.flush()
        alert_id = a.id
    log.warning("ALERT [%s] %s: %s", level, key, message)
    if _notify(level, key, message):
        with session_scope() as s:
            s.get(Alert, alert_id).notified = True
    return alert_id


def resolve(key: str) -> bool:
    with session_scope() as s:
        a = s.scalars(select(Alert).where(Alert.key == key, Alert.resolved_at.is_(None))).first()
        if a is None:
            return False
        a.resolved_at = utcnow()
        return True


def open_alerts() -> List[Alert]:
    with session_scope() as s:
        rows = list(s.scalars(select(Alert).where(Alert.resolved_at.is_(None)).order_by(Alert.opened_at.desc())))
        for r in rows:
            s.expunge(r)
        return rows


def _notify(level: str, key: str, message: str, url: Optional[str] = None) -> bool:
    url = url or alert_webhook_url()
    if not url:
        return False
    try:
        r = requests.post(url, json={"text": f"QuantEdge {level.upper()}: {message}", "level": level, "key": key},
                          timeout=5)
        return r.ok
    except requests.RequestException as exc:   # alerting must never take the worker down
        log.error("Alert webhook failed: %s", exc)
        return False
