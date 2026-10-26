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
    source: Mapped[str] = mapped_column(String(32), default="human")   # human | cli | dashboard | agent:<name>
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    batch: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)      # parameter sweep group
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)     # run this one re-tests (e.g. validation of a train run)


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
    universe: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # equal-weight B&H of the symbols


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


# ─────────────────────────────────────────────────────────────────────────────
# AI agents (Phase 6). Agents never write experiments or metrics directly: they
# write tasks (what they were asked and shown), proposals (what they want done)
# and LLM call records (what it cost). The backtester stays the source of truth.
# ─────────────────────────────────────────────────────────────────────────────

class AgentTask(Base):
    __tablename__ = "agent_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    cycle_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    trigger: Mapped[str] = mapped_column(String(16))          # manual | cli | schedule | event
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    # queued | running | completed | failed | skipped | budget_blocked | refused
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    evidence_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    evidence: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)       # exactly what the model was shown
    output: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)         # parsed structured output
    verification: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)   # cited numbers checked against the DB
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(48))
    served_model: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)   # differs if a fallback ran
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))   # ok | refusal | max_tokens | error | budget_blocked
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Proposal(Base):
    """
    Something an agent wants done. kind:
      experiment         — a backtest (train/validation only). May auto-run within caps.
      strategy_change    — new or modified rules. Always needs human approval;
                           approval means "implement this", which is done in code, not by the agent.
      risk_limit_change  — sizing/exposure limits. Always needs human approval.
      promote_to_paper   — move a configuration to paper trading. Always needs human approval.
    """
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    cycle_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    agent: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(24), index=True)
    title: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    hypothesis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(8), default="medium")
    status: Mapped[str] = mapped_column(String(20), index=True)
    # proposed | invalid | duplicate | selected | not_selected | running | completed | failed
    # | awaiting_approval | approved | declined
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    experiment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    director_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ─────────────────────────────────────────────────────────────────────────────
# Paper trading (Phase 7). Separate tables from backtests on purpose: a paper
# account only ever moves forward from the moment a human approved it, on bars
# that arrive after that moment. No real orders, no broker.
# ─────────────────────────────────────────────────────────────────────────────

class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(12), index=True)        # active | paused | closed
    strategy_key: Mapped[str] = mapped_column(String(48))
    params: Mapped[dict] = mapped_column(JSON)
    universe: Mapped[list] = mapped_column(JSON)
    market: Mapped[str] = mapped_column(String(4))
    timeframe: Mapped[str] = mapped_column(String(8))                  # daily | intraday
    config: Mapped[dict] = mapped_column(JSON)
    data_provider: Mapped[str] = mapped_column(String(16))
    starting_capital: Mapped[float] = mapped_column(Float)
    source_experiment_id: Mapped[int] = mapped_column(Integer)
    proposal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(48))
    approval_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_after: Mapped[datetime] = mapped_column(DateTime)          # only bars after this are traded
    last_bar_ts: Mapped[datetime] = mapped_column(DateTime)
    last_step_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)            # SignalPortfolio.to_state()
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PaperEvent(Base):
    __tablename__ = "paper_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ts: Mapped[datetime] = mapped_column(DateTime)                     # bar the event belongs to
    type: Mapped[str] = mapped_column(String(12))                      # order | fill | note
    side: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    shares: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fees: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
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


class PaperEquity(Base):
    __tablename__ = "paper_equity"
    account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    exposure: Mapped[float] = mapped_column(Float)


class Alert(Base):
    """Health problems. One open alert per key; resolved automatically when the check passes again."""
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    level: Mapped[str] = mapped_column(String(8))                      # warning | error
    message: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
