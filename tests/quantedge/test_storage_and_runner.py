"""End-to-end over a throwaway database using the demo provider (no network)."""
from __future__ import annotations

import pytest


def test_refresh_store_validate(isolated_db):
    from quantedge.data import repository as repo
    from quantedge.data.service import refresh_daily
    res = refresh_daily(["AAPL", "SHEL.L"], years=2)
    assert all(r.status == "ok" for r in res)
    df = repo.load_bars("demo", "AAPL")
    assert len(df) > 400 and df.index.is_unique
    # Real-data views never see demo rows.
    assert repo.load_bars("yahoo", "AAPL").empty
    again = refresh_daily(["AAPL"], years=2)
    assert again[0].rows < 20                              # incremental overlap only
    assert len(repo.load_bars("demo", "AAPL")) == len(df)  # upsert, no duplicates


def test_experiment_recorded_and_test_set_locked(isolated_db):
    from quantedge.backtest.runner import TestSetLocked, run_experiment
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import EquityPoint, Experiment
    refresh_daily(["AAPL", "MSFT", "NVDA", "SPY"], years=6)
    exp_id = run_experiment("ema_pullback", symbols=["AAPL", "MSFT", "NVDA"], period="train")
    with session_scope() as s:
        e = s.get(Experiment, exp_id)
        assert e.status == "completed" and e.data_provider == "demo"
        assert any("DEMO" in n for n in e.bias_notes)
        assert s.query(EquityPoint).filter_by(experiment_id=exp_id).count() > 100
    with pytest.raises(TestSetLocked):
        run_experiment("ema_pullback", symbols=["AAPL"], period="test")


def test_failed_experiment_is_kept(isolated_db):
    from quantedge.backtest.runner import run_experiment
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    with pytest.raises(RuntimeError):
        run_experiment("ema_pullback", symbols=["AAPL"], period="train")  # nothing stored yet
    with session_scope() as s:
        e = s.query(Experiment).one()
        assert e.status == "failed" and "No stored 1d price data" in e.error


def test_signal_scan_writes_labelled_observations(isolated_db):
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import Signal
    from quantedge.signals import scan_signals
    syms = ["AAPL", "MSFT", "NVDA", "JPM", "SHEL.L", "AZN.L"]
    refresh_daily(syms, years=3)
    scan_signals(syms, lookback_sessions=250)
    scan_signals(syms, lookback_sessions=250)   # idempotent
    with session_scope() as s:
        rows = s.query(Signal).all()
        assert rows and all(r.kind == "observation" and r.provider == "demo" for r in rows)
        assert len({(r.symbol, r.strategy_key, r.ts) for r in rows}) == len(rows)
        assert all(r.entry_rule and r.exit_rule and r.reason for r in rows)


def test_default_watchlist_seeded(isolated_db):
    from quantedge.watchlists import get_or_create_default, list_symbols
    wid = get_or_create_default()
    assert "AAPL" in list_symbols(wid)
