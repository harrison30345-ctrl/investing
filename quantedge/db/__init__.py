"""
Database engine and sessions.

`QE_DATABASE_URL` picks the backend: SQLite by default, Postgres in the cloud.
Tables are created on first use. Alembic migrations get added before the
first cloud deployment.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import models
from .models import Base


@lru_cache(maxsize=4)
def get_engine(url: Optional[str] = None) -> Engine:
    if url is None:
        from ..config import get_settings
        url = get_settings().database_url
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        Path(url.replace("sqlite:///", "")).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(conn, _):  # WAL lets the dashboard read while a worker writes
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    return engine


def _add_missing_columns(engine: Engine) -> None:
    """
    Minimal forward migration: `create_all` never alters existing tables, so new
    *nullable* columns are added here. Anything more involved (renames, type
    changes, non-null columns) needs Alembic, planned before cloud deployment.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have and col.nullable:
                    ddl = col.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}'))


@contextmanager
def session_scope(url: Optional[str] = None) -> Iterator[Session]:
    factory = sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["get_engine", "session_scope", "models"]
