"""
Persistence for market data: bar upserts, reads, fetch log, freshness.

The rest of the app reads prices only through `load_bars`. That keeps the
"which provider?" filter in one place, so demo rows never leak into a real-data view.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_engine, session_scope
from ..db.models import FetchLog, Instrument, PriceBar, QualityReport, utcnow
from ..universe import get as catalogue_get
from .providers.base import BAR_COLUMNS


def _insert(session: Session):
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def upsert_bars(session: Session, provider: str, symbol: str, interval: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    now = utcnow()
    rows = [
        {"provider": provider, "symbol": symbol, "interval": interval, "ts": ts.to_pydatetime(),
         **{c: (None if pd.isna(r[c]) else float(r[c])) for c in BAR_COLUMNS}, "fetched_at": now}
        for ts, r in df.iterrows()
    ]
    insert = _insert(session)
    for i in range(0, len(rows), 500):   # SQLite caps bound parameters per statement
        stmt = insert(PriceBar).values(rows[i:i + 500])
        update = {c: getattr(stmt.excluded, c) for c in BAR_COLUMNS + ["fetched_at"]}
        session.execute(stmt.on_conflict_do_update(
            index_elements=["provider", "symbol", "interval", "ts"], set_=update))
    return len(rows)


def ensure_instrument(session: Session, symbol: str) -> None:
    if session.get(Instrument, symbol) is None:
        i = catalogue_get(symbol)
        session.add(Instrument(symbol=i.symbol, name=i.name, market=i.market, kind=i.kind, currency=i.currency))


def delete_bars(session: Session, provider: str, symbol: str, interval: str) -> None:
    session.query(PriceBar).filter_by(provider=provider, symbol=symbol, interval=interval).delete()


def load_bars(provider: str, symbol: str, interval: str = "1d",
              start: Optional[datetime] = None, end: Optional[datetime] = None) -> pd.DataFrame:
    q = select(PriceBar.ts, *[getattr(PriceBar, c) for c in BAR_COLUMNS]).where(
        PriceBar.provider == provider, PriceBar.symbol == symbol, PriceBar.interval == interval)
    if start is not None:
        q = q.where(PriceBar.ts >= start)
    if end is not None:
        q = q.where(PriceBar.ts <= end)
    with get_engine().connect() as conn:
        df = pd.read_sql(q.order_by(PriceBar.ts), conn, parse_dates=["ts"])
    return df.set_index("ts").astype(float)


def load_many(provider: str, symbols: Iterable[str], interval: str = "1d", **kw) -> Dict[str, pd.DataFrame]:
    return {s: load_bars(provider, s, interval, **kw) for s in symbols}


def last_bar_ts(session: Session, provider: str, symbol: str, interval: str = "1d") -> Optional[datetime]:
    return session.scalar(select(func.max(PriceBar.ts)).where(
        PriceBar.provider == provider, PriceBar.symbol == symbol, PriceBar.interval == interval))


def first_bar_ts(session: Session, provider: str, symbol: str, interval: str = "1d") -> Optional[datetime]:
    return session.scalar(select(func.min(PriceBar.ts)).where(
        PriceBar.provider == provider, PriceBar.symbol == symbol, PriceBar.interval == interval))


def adj_ratio_at(session: Session, provider: str, symbol: str, ts: datetime) -> Optional[float]:
    bar = session.get(PriceBar, (provider, symbol, "1d", ts))
    if bar is None or not bar.adj_close or not bar.close:
        return None
    return bar.adj_close / bar.close


def log_fetch(session: Session, provider: str, symbol: str, interval: str, status: str,
              rows: int = 0, message: Optional[str] = None, duration_ms: int = 0) -> None:
    session.add(FetchLog(provider=provider, symbol=symbol, interval=interval, status=status,
                         rows=rows, message=message, duration_ms=duration_ms))


def save_quality(session: Session, provider: str, interval: str, result) -> None:
    session.add(QualityReport(
        provider=provider, symbol=result.symbol, interval=interval, rows=result.rows,
        first_ts=result.first.to_pydatetime() if result.first is not None else None,
        last_ts=result.last.to_pydatetime() if result.last is not None else None,
        ok=result.ok, issues=result.issues_as_dicts()))


@dataclass
class SymbolFreshness:
    symbol: str
    market: str
    last_bar: Optional[datetime]
    last_fetch: Optional[datetime]
    last_status: Optional[str]
    quality_ok: Optional[bool]
    issue_count: int


def freshness(provider: str, symbols: Optional[List[str]] = None, interval: str = "1d") -> List[SymbolFreshness]:
    """Per-symbol last bar / last fetch / latest quality verdict, straight from the DB."""
    with session_scope() as s:
        bars = dict(s.execute(
            select(PriceBar.symbol, func.max(PriceBar.ts))
            .where(PriceBar.provider == provider, PriceBar.interval == interval)
            .group_by(PriceBar.symbol)).all())
        last_fetch_id = (select(FetchLog.symbol, func.max(FetchLog.id).label("mid"))
                         .where(FetchLog.provider == provider, FetchLog.interval == interval)
                         .group_by(FetchLog.symbol).subquery())
        fetches = {f.symbol: f for f in s.scalars(
            select(FetchLog).join(last_fetch_id, FetchLog.id == last_fetch_id.c.mid))}
        last_q_id = (select(QualityReport.symbol, func.max(QualityReport.id).label("mid"))
                     .where(QualityReport.provider == provider, QualityReport.interval == interval)
                     .group_by(QualityReport.symbol).subquery())
        quality = {q.symbol: q for q in s.scalars(
            select(QualityReport).join(last_q_id, QualityReport.id == last_q_id.c.mid))}

    names = symbols if symbols is not None else sorted(set(bars) | set(fetches))
    out = []
    for sym in names:
        f, q = fetches.get(sym), quality.get(sym)
        out.append(SymbolFreshness(
            symbol=sym, market=catalogue_get(sym).market, last_bar=bars.get(sym),
            last_fetch=f.started_at if f else None, last_status=f.status if f else None,
            quality_ok=bool(q.ok) if q else None, issue_count=len(q.issues) if q else 0))
    return out


def latest_quality(provider: str, symbol: str, interval: str = "1d") -> Optional[QualityReport]:
    with session_scope() as s:
        return s.scalars(select(QualityReport).where(
            QualityReport.provider == provider, QualityReport.symbol == symbol,
            QualityReport.interval == interval).order_by(QualityReport.id.desc()).limit(1)).first()


def recent_fetch_errors(provider: str, limit: int = 10) -> List[FetchLog]:
    with session_scope() as s:
        return list(s.scalars(select(FetchLog).where(
            FetchLog.provider == provider, FetchLog.status != "ok").order_by(FetchLog.id.desc()).limit(limit)))
