"""
Database schema.

Conventions
-----------
* Timestamps are stored as naive UTC (`utcnow()`), except daily bar `ts`,
  which is the exchange-local trading *date* at midnight. That's how vendors
  publish daily bars, and it avoids pretending a daily bar has a time.
* `provider` is part of every price row's key, so demo data and real data
  can never be confused or merged.
* JSON columns hold structured detail (parameters, metrics, issues) that we
  want to keep verbatim for the experiment record.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    market: Mapped[str] = mapped_column(String(4))
    kind: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8))


class PriceBar(Base):
    __tablename__ = "price_bars"
    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)   # "1d", "5m", ...
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    adj_close: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FetchLog(Base):
    """One row per provider request. This is what drives 'connection health'."""
    __tablename__ = "fetch_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    interval: Mapped[str] = mapped_column(String(8))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))          # ok | empty | not_found | error
    rows: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class QualityReport(Base):
    __tablename__ = "quality_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    interval: Mapped[str] = mapped_column(String(8))
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    rows: Mapped[int] = mapped_column(Integer)
    first_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean)
    issues: Mapped[list] = mapped_column(JSON, default=list)


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Experiment(Base):
    """Every backtest run, including failures. Nothing gets deleted."""
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    strategy_key: Mapped[str] = mapped_column(String(48), index=True)
    params: Mapped[dict] = mapped_column(JSON)
    universe: Mapped[list] = mapped_column(JSON)
    market: Mapped[str] = mapped_column(String(4))
    period: Mapped[str] = mapped_column(String(16))          # train | validation | test | full
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    used_test_set: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON)               # capital, sizing, costs
    data_provider: Mapped[str] = mapped_column(String(16))
    benchmark: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16))          # running | completed | failed
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    bias_notes: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(32), default="human")   # human | cli | agent:<name>
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    entry_ts: Mapped[datetime] = mapped_column(DateTime)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_ts: Mapped[datetime] = mapped_column(DateTime)
    exit_price: Mapped[float] = mapped_column(Float)
    shares: Mapped[float] = mapped_column(Float)
    costs: Mapped[float] = mapped_column(Float)
    net_pnl: Mapped[float] = mapped_column(Float)
    return_pct: Mapped[float] = mapped_column(Float)
    bars_held: Mapped[int] = mapped_column(Integer)
    entry_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(32))


class EquityPoint(Base):
    __tablename__ = "equity_points"
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    equity: Mapped[float] = mapped_column(Float)
    benchmark: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exposure: Mapped[float] = mapped_column(Float, default=0.0)


class Signal(Base):
    """
    A rule firing on a bar. `kind` says what it is, and the UI always shows it:
      observation — fired on the latest stored (delayed) real data; not a trade
      paper       — taken by the paper-trading account (Phase 7)
      backtest    — produced inside a historical simulation
    """
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("provider", "symbol", "strategy_key", "ts", "kind", name="uq_signal"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    provider: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(4))
    strategy_key: Mapped[str] = mapped_column(String(48))
    timeframe: Mapped[str] = mapped_column(String(8))         # daily | intraday
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))         # long | exit
    price: Mapped[float] = mapped_column(Float)
    entry_rule: Mapped[str] = mapped_column(Text)
    exit_rule: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    risk: Mapped[dict] = mapped_column(JSON, default=dict)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    data_as_of: Mapped[datetime] = mapped_column(DateTime)


class JobRun(Base):
    """Background or CLI job executions: data refreshes, signal scans, research."""
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(48), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16))           # running | ok | partial | failed
    trigger: Mapped[str] = mapped_column(String(16), default="manual")   # manual | cli | schedule | event
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Heartbeat(Base):
    """Long-running services write here; the dashboard reads it to show liveness."""
    __tablename__ = "heartbeats"
    service: Mapped[str] = mapped_column(String(48), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16))
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


Index("ix_bars_lookup", PriceBar.provider, PriceBar.symbol, PriceBar.interval, PriceBar.ts)
