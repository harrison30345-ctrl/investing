"""
Cached read models for the dashboard.

Pages call these functions instead of querying the database directly, so
caching (60 s) and the "active provider only" rule live in one place.
Call `clear()` after anything writes new data.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional

import pandas as pd
import streamlit as st
from sqlalchemy import select

from quantedge.config import get_settings
from quantedge.data import repository as repo
from quantedge.db import session_scope
from quantedge.db.models import EquityPoint, Experiment, Heartbeat, JobRun, Signal, Trade
from quantedge.markets import MARKETS, last_completed_session
from quantedge.universe import get as catalogue_get

TTL = 60


def provider() -> str:
    return get_settings().data_provider


def clear() -> None:
    st.cache_data.clear()


@st.cache_data(ttl=TTL, show_spinner=False)
def history(symbol: str, pkey: str, days: Optional[int] = None) -> pd.DataFrame:
    start = datetime.now() - timedelta(days=days) if days else None
    return repo.load_bars(pkey, symbol, start=start)


@st.cache_data(ttl=TTL, show_spinner=False)
def snapshot(symbols: tuple, pkey: str, spark_days: int = 30) -> pd.DataFrame:
    """Latest close, previous close, change and a short trend for each symbol."""
    rows = []
    for sym in symbols:
        inst = catalogue_get(sym)
        df = repo.load_bars(pkey, sym, start=datetime.now() - timedelta(days=400))
        base = {"symbol": sym, "name": inst.name, "market": inst.market, "kind": inst.kind, "currency": inst.currency}
        if len(df) < 2:
            rows.append({**base, "last": None, "prev": None, "change": None, "change_pct": None,
                         "as_of": df.index[-1] if len(df) else None, "trend": [], "high_52w": None, "low_52w": None})
            continue
        c = df["close"]
        rows.append({**base, "last": float(c.iloc[-1]), "prev": float(c.iloc[-2]),
                     "change": float(c.iloc[-1] - c.iloc[-2]), "change_pct": float(c.iloc[-1] / c.iloc[-2] - 1),
                     "as_of": df.index[-1], "trend": [float(x) for x in c.iloc[-spark_days:]],
                     "high_52w": float(df["high"].iloc[-252:].max()), "low_52w": float(df["low"].iloc[-252:].min()),
                     "ret_1y": float(c.iloc[-1] / c.iloc[-min(252, len(c))] - 1)})
    return pd.DataFrame(rows)


@st.cache_data(ttl=TTL, show_spinner=False)
def market_freshness(pkey: str) -> dict:
    """Per market: newest stored bar vs last completed session, and the latest fetch."""
    rows = repo.freshness(pkey)
    out = {}
    for m in MARKETS:
        mine = [r for r in rows if r.market == m and r.last_bar is not None]
        expected = last_completed_session(m)
        latest = max((r.last_bar for r in mine), default=None)
        fetched = max((r.last_fetch for r in rows if r.market == m and r.last_fetch), default=None)
        if latest is None:
            status = "none"
        elif expected and latest.date() >= expected:
            status = "current"
        else:
            status = "stale"
        out[m] = {"latest_bar": latest, "expected": expected, "last_fetch": fetched, "status": status,
                  "symbols": len(mine), "issues": sum(1 for r in mine if r.quality_ok is False),
                  "errors": sum(1 for r in rows if r.market == m and r.last_status not in (None, "ok"))}
    return out


@st.cache_data(ttl=TTL, show_spinner=False)
def freshness_table(pkey: str) -> pd.DataFrame:
    return pd.DataFrame([r.__dict__ for r in repo.freshness(pkey)])


@st.cache_data(ttl=TTL, show_spinner=False)
def signals(pkey: str, limit: int = 500) -> pd.DataFrame:
    with session_scope() as s:
        rows = s.scalars(select(Signal).where(Signal.provider == pkey).order_by(Signal.ts.desc(), Signal.id.desc())
                         .limit(limit)).all()
        data = [{c.name: getattr(r, c.name) for c in Signal.__table__.columns} for r in rows]
    return pd.DataFrame(data)


@st.cache_data(ttl=TTL, show_spinner=False)
def experiments(limit: int = 200) -> pd.DataFrame:
    with session_scope() as s:
        rows = s.scalars(select(Experiment).order_by(Experiment.id.desc()).limit(limit)).all()
        data = [{c.name: getattr(r, c.name) for c in Experiment.__table__.columns} for r in rows]
    return pd.DataFrame(data)


@st.cache_data(ttl=TTL, show_spinner=False)
def experiment_curve(exp_id: int) -> pd.DataFrame:
    with session_scope() as s:
        rows = s.execute(select(EquityPoint.ts, EquityPoint.equity, EquityPoint.benchmark, EquityPoint.exposure)
                         .where(EquityPoint.experiment_id == exp_id).order_by(EquityPoint.ts)).all()
    return pd.DataFrame(rows, columns=["ts", "equity", "benchmark", "exposure"]).set_index("ts")


@st.cache_data(ttl=TTL, show_spinner=False)
def experiment_trades(exp_id: int) -> pd.DataFrame:
    with session_scope() as s:
        rows = s.scalars(select(Trade).where(Trade.experiment_id == exp_id).order_by(Trade.entry_ts)).all()
        data = [{c.name: getattr(r, c.name) for c in Trade.__table__.columns} for r in rows]
    return pd.DataFrame(data)


@st.cache_data(ttl=TTL, show_spinner=False)
def job_runs(limit: int = 20) -> pd.DataFrame:
    with session_scope() as s:
        rows = s.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(limit)).all()
        data = [{c.name: getattr(r, c.name) for c in JobRun.__table__.columns} for r in rows]
    return pd.DataFrame(data)


@st.cache_data(ttl=TTL, show_spinner=False)
def heartbeats() -> List[dict]:
    with session_scope() as s:
        return [{"service": h.service, "last_seen": h.last_seen, "status": h.status, "detail": h.detail}
                for h in s.scalars(select(Heartbeat))]


@st.cache_data(ttl=TTL, show_spinner=False)
def stored_symbols(pkey: str) -> List[str]:
    return [r.symbol for r in repo.freshness(pkey) if r.last_bar is not None]


# ── AI agents ───────────────────────────────────────────────────────────────

def _rows(model, q) -> pd.DataFrame:
    with session_scope() as s:
        rows = s.scalars(q).all()
        return pd.DataFrame([{c.name: getattr(r, c.name) for c in model.__table__.columns} for r in rows])


@st.cache_data(ttl=TTL, show_spinner=False)
def agent_tasks(limit: int = 300) -> pd.DataFrame:
    from quantedge.db.models import AgentTask
    return _rows(AgentTask, select(AgentTask).order_by(AgentTask.id.desc()).limit(limit))


@st.cache_data(ttl=TTL, show_spinner=False)
def proposals(limit: int = 500) -> pd.DataFrame:
    from quantedge.db.models import Proposal
    return _rows(Proposal, select(Proposal).order_by(Proposal.id.desc()).limit(limit))


@st.cache_data(ttl=TTL, show_spinner=False)
def llm_calls(limit: int = 500) -> pd.DataFrame:
    from quantedge.db.models import LLMCall
    return _rows(LLMCall, select(LLMCall).order_by(LLMCall.id.desc()).limit(limit))
