"""Every dashboard page renders without an exception (headless, demo data, fresh database)."""
from __future__ import annotations

from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "quantedge" / "app" / "main.py"
SCREENS = APP.parent / "screens"
# Each page is rendered in a single run. Streamlit 1.50's AppTest mishandles single-select
# segmented controls when it re-runs with widget state (it splits "All" into letters), so
# multi-step navigation inside AppTest isn't reliable. The full shell (top bar, navigation) is
# covered by test_app_shell_renders.
PAGES = ["home", "markets", "watchlists", "signals", "strategy_lab", "backtests", "paper", "ai_research",
         "reports", "settings"]


@pytest.fixture()
def populated(isolated_db):
    from quantedge.backtest.runner import run_experiment
    from quantedge.data.service import refresh_daily
    from quantedge.paper import create_account
    from quantedge.signals import scan_signals
    from quantedge.universe import BENCHMARKS, INDICES, DEFAULT_WATCHLIST
    syms = ["AAPL", "MSFT", "NVDA", "JPM"]
    refresh_daily(syms + [i.symbol for i in INDICES + BENCHMARKS] + DEFAULT_WATCHLIST, years=5)
    scan_signals(syms, lookback_sessions=60)
    run_experiment("ema_pullback", symbols=syms, period="train")
    val = run_experiment("ema_pullback", symbols=syms, period="validation")
    create_account(val, "Page test", "human (test)")


def _render(page: str):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(SCREENS / f"{page}.py"), default_timeout=60)
    at.run()
    return at


def test_app_shell_renders(populated):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert not at.exception, [e.message for e in at.exception]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(populated, page):
    at = _render(page)
    assert not at.exception, [e.message for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def test_pages_render_on_empty_database(isolated_db):
    for page in PAGES:
        at = _render(page)
        assert not at.exception, (page, [e.message for e in at.exception])
