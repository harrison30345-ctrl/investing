"""Paper trading: approval gate, forward-only stepping, parity with the backtester, completed bars only."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

SYMS = ["AAPL", "MSFT", "NVDA", "JPM"]
FUTURE = pd.Timestamp("2100-01-01")


@pytest.fixture()
def demo_data(isolated_db):
    from quantedge.data.service import refresh_daily
    refresh_daily(SYMS + ["SPY"], years=6)


def _validated(strategy="ema_pullback"):
    from quantedge.backtest.runner import run_experiment
    run_experiment(strategy, symbols=SYMS, period="train")
    return run_experiment(strategy, symbols=SYMS, period="validation")


def _rewind(account_id: int, to: pd.Timestamp):
    """Test helper: pretend the account was approved at an earlier date, so there are bars to trade."""
    from quantedge.db import session_scope
    from quantedge.db.models import PaperAccount
    with session_scope() as s:
        a = s.get(PaperAccount, account_id)
        a.started_after = a.last_bar_ts = to.to_pydatetime()


# ── approval gate ──────────────────────────────────────────────────────────

def test_requires_human_approval_and_validated_run(demo_data):
    from quantedge.backtest.runner import run_experiment
    from quantedge.paper import PaperError, create_account
    train_id = run_experiment("ema_pullback", symbols=SYMS, period="train")
    with pytest.raises(PaperError, match="validation"):
        create_account(train_id, "x", "human (test)")
    val_id = run_experiment("ema_pullback", symbols=SYMS, period="validation")
    with pytest.raises(PaperError, match="human"):
        create_account(val_id, "x", "agent:risk")
    with pytest.raises(PaperError, match="human"):
        create_account(val_id, "x", "")
    mom = run_experiment("xs_momentum", symbols=SYMS, period="validation")
    with pytest.raises(PaperError, match="rebalancing"):
        create_account(mom, "x", "human (test)")
    assert create_account(val_id, "ok", "human (test)") > 0


def test_agent_code_cannot_create_paper_accounts():
    from pathlib import Path
    import quantedge.agents as agents_pkg
    src = " ".join(p.read_text() for p in Path(agents_pkg.__file__).parent.glob("*.py"))
    assert "create_account" not in src and "quantedge.paper" not in src and "from ..paper" not in src


def test_new_account_is_forward_only(demo_data):
    from quantedge.paper import create_account, step_account, summary
    acct = create_account(_validated(), "fwd", "human (test)")
    assert step_account(acct, now=FUTURE) == 0          # nothing after the approval point yet
    assert summary(acct)["trades"] == 0


# ── parity with the backtester ─────────────────────────────────────────────

def test_paper_matches_backtest_and_survives_restarts(demo_data):
    from quantedge.backtest.engine import BacktestConfig, run_signal_backtest
    from quantedge.backtest.runner import _prepare
    from quantedge.data import repository as repo
    from quantedge.db import session_scope
    from quantedge.db.models import PaperEquity, PaperTrade
    from quantedge.paper import create_account, step_account
    from quantedge.strategies import get_strategy

    exp = _validated()
    a1 = create_account(exp, "one-shot", "human (test)")
    a2 = create_account(exp, "stepwise", "human (test)")
    bars = {s: _prepare(repo.load_bars("demo", s), s) for s in SYMS}
    dates = sorted(set().union(*[set(b.index) for b in bars.values()]))
    t0, mid = pd.Timestamp(dates[-400]), pd.Timestamp(dates[-200])
    _rewind(a1, t0)
    _rewind(a2, t0)

    n1 = step_account(a1, now=FUTURE)
    step_account(a2, now=mid + pd.Timedelta(days=1))    # restart in the middle: state goes via the DB
    step_account(a2, now=FUTURE)
    assert n1 == 399

    strat = get_strategy("ema_pullback")
    with session_scope() as s:
        from quantedge.db.models import Experiment
        e = s.get(Experiment, exp)
        cfg = BacktestConfig.from_dict(e.config)
    bt = run_signal_backtest(strat, strat.resolve({}), bars, "US", cfg, start=dates[-399])
    bt_closed = bt.trades[bt.trades["exit_reason"] != "end_of_period"]

    with session_scope() as s:
        for acct in (a1, a2):
            pt = s.query(PaperTrade).filter_by(account_id=acct).order_by(PaperTrade.exit_ts, PaperTrade.symbol).all()
            assert len(pt) == len(bt_closed)
            assert sum(t.net_pnl for t in pt) == pytest.approx(bt_closed["net_pnl"].sum(), rel=1e-9)
        eq1 = s.query(PaperEquity).filter_by(account_id=a1).order_by(PaperEquity.ts.desc()).first().equity
        eq2 = s.query(PaperEquity).filter_by(account_id=a2).order_by(PaperEquity.ts.desc()).first().equity
    assert eq1 == pytest.approx(eq2, rel=1e-12)
    # Backtest's last equity point is after its forced liquidation; the one before is the marked book.
    assert eq1 == pytest.approx(bt.equity.iloc[-2] if bt.trades["exit_reason"].eq("end_of_period").any()
                                else bt.equity.iloc[-1], rel=1e-6)


def test_only_completed_daily_bars_are_traded(demo_data):
    from quantedge.data import repository as repo
    from quantedge.paper import create_account, step_account
    acct = create_account(_validated(), "partial", "human (test)")
    dates = repo.load_bars("demo", "AAPL").index
    _rewind(acct, dates[-6])
    # It's 15:00 New York on the latest stored date: that day's bar is unfinished.
    now = pd.Timestamp(dates[-1]) + pd.Timedelta(hours=19)
    assert step_account(acct, now=now) == 4
    assert step_account(acct, now=FUTURE) == 1


def test_close_liquidates_and_locks(demo_data):
    from quantedge.paper import PaperError, create_account, set_status, step_account, summary
    acct = create_account(_validated(), "close", "human (test)")
    from quantedge.data import repository as repo
    _rewind(acct, repo.load_bars("demo", "AAPL").index[-300])
    step_account(acct, now=FUTURE)
    set_status(acct, "closed", "human (test)")
    s = summary(acct)
    assert s["positions"] == {} and s["equity"] == pytest.approx(s["cash"])
    with pytest.raises(PaperError):
        set_status(acct, "active", "human (test)")
    assert step_account(acct, now=FUTURE) == 0


# ── intraday ───────────────────────────────────────────────────────────────

def test_intraday_paper_is_flat_overnight_and_skips_unfinished_bars(isolated_db):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_intraday import random_sessions
    from quantedge.backtest.runner import run_experiment
    from quantedge.data import repository as repo
    from quantedge.db import session_scope
    from quantedge.db.models import PaperTrade
    from quantedge.markets import sessions
    from quantedge.paper import create_account, step_account, summary

    days = [d.strftime("%Y-%m-%d") for d in sessions("US", datetime(2026, 6, 1), datetime(2026, 8, 14))
            if d != pd.Timestamp("2026-07-03")]
    for sym, seed in (("AAPL", 1), ("SPY", 2)):
        with session_scope() as s:
            repo.upsert_bars(s, "demo", sym, "5m", random_sessions(days, seed=seed))
    exp = run_experiment("vwap_continuation", symbols=["AAPL"], period="validation")
    acct = create_account(exp, "intra", "human (test)")
    bars = repo.load_bars("demo", "AAPL", "5m")
    _rewind(acct, bars.index[-78 * 3 - 1])          # three sessions to trade
    mid_session = bars.index[-40] + pd.Timedelta(minutes=2)   # inside an unfinished 5-minute bar
    n = step_account(acct, now=mid_session)
    assert n == 78 * 2 + 38                          # two full sessions + the 38 finished bars of the third
    step_account(acct, now=FUTURE)
    ny = lambda t: pd.Timestamp(t).tz_localize("UTC").tz_convert("America/New_York").date()  # noqa: E731
    with session_scope() as s:
        for t in s.query(PaperTrade).filter_by(account_id=acct):
            assert ny(t.entry_ts) == ny(t.exit_ts)
    assert summary(acct)["positions"] == {}
