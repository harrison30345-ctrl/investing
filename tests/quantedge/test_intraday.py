"""Intraday: session rules, no overnight holding, no look-ahead, VWAP reset, data checks, runner splits."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantedge.backtest.costs import ZERO_COSTS
from quantedge.backtest.engine import BacktestConfig, run_signal_backtest
from quantedge.strategies.intraday import OpeningRangeBreakout, VwapContinuation, session_frame

SUMMER_DAYS = ["2026-07-06", "2026-07-07", "2026-07-08"]     # NYSE sessions in EDT (09:30 ET = 13:30 UTC)


def session_bars(day: str, closes, volume=1000.0) -> pd.DataFrame:
    idx = pd.date_range(f"{day} 13:30", periods=len(closes), freq="5min")
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05, "close": c,
                         "adj_close": c, "volume": volume}, index=idx)


def random_sessions(days=SUMMER_DAYS, seed=0):
    rng = np.random.default_rng(seed)
    frames, px = [], 100.0
    for d in days:
        c = px * np.exp(np.cumsum(rng.normal(0, 0.002, 78)))
        frames.append(session_bars(d, c, volume=rng.integers(500, 5000, 78).astype(float)))
        px = c[-1]
    return pd.concat(frames)


def cfg():
    return BacktestConfig(initial_capital=100_000, risk_per_trade=0.01, max_position_pct=1.0, costs=ZERO_COSTS)


def test_session_frame_minutes_from_open():
    f = session_frame(session_bars(SUMMER_DAYS[0], [100] * 78))
    assert f["minute"].iloc[0] == 0 and f["minute"].iloc[-1] == 385


def test_orb_waits_for_range_and_fires_once_per_day():
    closes = [100.5, 100.0, 100.8] + [100.9] * 3 + [101.5, 101.6, 101.0, 101.7] + [101.2] * 68
    bars = session_bars(SUMMER_DAYS[0], closes)
    s = OpeningRangeBreakout()
    sig = s.generate(bars, s.resolve({"or_minutes": 15}))
    first_bars = sig.iloc[:3]
    assert not first_bars["entry"].any() and first_bars["or_high"].isna().all()   # range not known yet
    assert sig["entry"].sum() == 1
    hit = sig.index[sig["entry"]][0]
    assert bars.loc[hit, "close"] > sig.loc[hit, "or_high"]


def test_orb_respects_entry_cutoff():
    closes = [100.0] * 3 + [100.0] * 30 + [102.0] * 45      # breakout 165 min after the open
    s = OpeningRangeBreakout()
    sig = s.generate(session_bars(SUMMER_DAYS[0], closes), s.resolve({"entry_cutoff": 120}))
    assert not sig["entry"].any()


def test_positions_never_held_overnight():
    bars = random_sessions(seed=3)
    s = VwapContinuation()
    r = run_signal_backtest(s, s.resolve({"target_r": 0.0}), {"X": bars}, "US", cfg(), intraday=True)
    assert len(r.trades) > 0
    ny = lambda t: pd.Timestamp(t).tz_localize("UTC").tz_convert("America/New_York").date()  # noqa: E731
    for t in r.trades.itertuples():
        assert ny(t.entry_ts) == ny(t.exit_ts)
    assert {"session_close", "exit_signal", "stop"} >= set(r.trades["exit_reason"]) - {"end_of_period"}
    assert len(r.equity) == len(SUMMER_DAYS)          # metrics on end-of-day equity


def test_session_close_exit_is_at_last_bar_close():
    closes = [100.0, 100.2, 100.1] + [101.0] + [101.2] * 74
    bars = session_bars(SUMMER_DAYS[0], closes)
    s = OpeningRangeBreakout()
    r = run_signal_backtest(s, s.resolve({"target_r": 0.0, "exit_on_failure": False}), {"X": bars}, "US", cfg(),
                            intraday=True)
    t = r.trades.iloc[0]
    assert t.exit_reason == "session_close" and t.exit_ts == bars.index[-1]
    assert t.exit_price == pytest.approx(bars["close"].iloc[-1])


@pytest.mark.parametrize("strategy", [OpeningRangeBreakout(), VwapContinuation()], ids=lambda s: s.key)
def test_intraday_signals_have_no_lookahead(strategy):
    bars = random_sessions(seed=11)
    p = strategy.defaults()
    full = strategy.generate(bars, p)
    for cut in (40, 78, 120, 200):
        part = strategy.generate(bars.iloc[:cut], p)
        pd.testing.assert_frame_equal(part[["entry", "exit", "stop_dist"]], full[["entry", "exit", "stop_dist"]].iloc[:cut],
                                      check_dtype=False)


def test_vwap_resets_each_session():
    bars = random_sessions(seed=5)
    s = VwapContinuation()
    sig = s.generate(bars, s.defaults())
    first_day2 = bars.index[78]
    typical = (bars.loc[first_day2, ["high", "low", "close"]].sum()) / 3
    assert sig.loc[first_day2, "vwap"] == pytest.approx(typical)


def test_intraday_validation_flags_gaps_and_out_of_hours():
    from quantedge.data.validation import validate_intraday
    bars = random_sessions(seed=1)
    bad = bars.drop(bars.index[100:110])
    extra = session_bars("2026-07-06", [100.0]).rename(index=lambda t: t - pd.Timedelta(hours=2))  # 07:30 ET
    q = validate_intraday(pd.concat([bad, extra]).sort_index(), "X", "US")
    codes = {i.code: i for i in q.issues}
    assert codes["missing_bars"].count == 10 and "outside_session" in codes


def test_runner_splits_by_whole_sessions(isolated_db):
    from datetime import date

    from quantedge.backtest.runner import run_experiment
    from quantedge.data import repository as repo
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    from quantedge.markets import sessions
    days = [d.strftime("%Y-%m-%d") for d in sessions("US", date(2026, 6, 1), date(2026, 8, 14))
            if d.month in (6, 7, 8)]
    days = [d for d in days if pd.Timestamp(d) not in (pd.Timestamp("2026-07-03"),)]   # half day
    for sym, seed in (("AAPL", 1), ("SPY", 2)):
        with session_scope() as s:
            repo.upsert_bars(s, "demo", sym, "5m", random_sessions(days, seed=seed))
    exp_id = run_experiment("orb", symbols=["AAPL"], period="train")
    with session_scope() as s:
        e = s.get(Experiment, exp_id)
        assert e.status == "completed"
        assert pd.Timestamp(e.start_date).strftime("%H:%M") == "13:30"     # first bar of a session
        assert pd.Timestamp(e.end_date).strftime("%H:%M") == "19:55"       # last bar of a session
        assert e.metrics["sessions"] == int(len(days) * 0.6)
        assert e.metrics.get("benchmark_return") is not None
        assert any("Short sample" in n for n in e.bias_notes)


def test_intraday_strategy_rejects_uk(isolated_db):
    from quantedge.backtest.runner import run_experiment
    with pytest.raises(ValueError):
        run_experiment("orb", symbols=["SHEL.L"], period="train")
