"""
Postgres compatibility, checked without a Postgres server: every table's DDL and
the bar upsert must compile for the PostgreSQL dialect, and metric JSON must be strictly
valid (Postgres rejects NaN/Infinity, which SQLite tolerates).
"""
from __future__ import annotations

import json
import math

import pandas as pd
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from quantedge.db.models import Base, PriceBar


def test_all_tables_compile_for_postgres():
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert table.name in ddl
        for idx in table.indexes:
            str(CreateIndex(idx).compile(dialect=dialect))


def test_bar_upsert_compiles_for_postgres():
    from sqlalchemy.dialects.postgresql import insert
    stmt = insert(PriceBar).values([{"provider": "yahoo", "symbol": "AAPL", "interval": "1d",
                                     "ts": pd.Timestamp("2026-01-02").to_pydatetime(), "open": 1.0, "high": 1.0,
                                     "low": 1.0, "close": 1.0, "adj_close": 1.0, "volume": 1.0}])
    stmt = stmt.on_conflict_do_update(index_elements=["provider", "symbol", "interval", "ts"],
                                      set_={"close": stmt.excluded.close})
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (provider, symbol, interval, ts) DO UPDATE" in sql


def test_repository_picks_postgres_insert():
    from quantedge.data.repository import _insert

    class Bind:
        class dialect:
            name = "postgresql"

    class FakeSession:
        bind = Bind()
    assert _insert(FakeSession()).__module__.startswith("sqlalchemy.dialects.postgresql")


def test_metrics_are_strict_json(isolated_db):
    """allow_nan=False mimics Postgres: NaN/inf in stored metrics would fail there."""
    from quantedge.backtest.runner import run_experiment
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    refresh_daily(["AAPL", "MSFT", "SPY"], years=4)
    for key in ("ema_pullback", "donchian_breakout", "xs_momentum"):
        exp_id = run_experiment(key, symbols=["AAPL", "MSFT"], period="train")
        with session_scope() as s:
            m = s.get(Experiment, exp_id).metrics
        json.dumps(m, allow_nan=False)
        assert not any(isinstance(v, float) and not math.isfinite(v) for v in m.values())
