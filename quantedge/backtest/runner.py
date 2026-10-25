"""
Run a backtest against stored data and record it as an experiment.

Every call writes an `experiments` row *before* running, so crashes and
failed attempts are kept as well as successes. The stored row holds
everything needed to reproduce the run: strategy, parameters, universe, date
window, costs, sizing, data provider, and known biases.
"""
from __future__ import annotations

import logging
import traceback
from typing import Any, Dict, List, Optional

import pandas as pd

from ..config import get_settings
from ..data import repository as repo
from ..db import session_scope
from ..db.models import EquityPoint, Experiment, Trade, utcnow
from ..markets import MARKETS
from ..strategies import get_strategy
from ..strategies.indicators import adjusted_ohlc
from ..universe import UNIVERSES
from ..universe import get as catalogue_get
from .engine import BacktestConfig, BacktestResult, run_backtest
from .splits import PERIODS, chronological_split

log = logging.getLogger(__name__)


class TestSetLocked(PermissionError):
    """Raised when the untouched test period is requested without confirmation."""


def _prepare(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Adjusted OHLC, converted to major currency units (pence → pounds)."""
    out = adjusted_ohlc(df)
    if catalogue_get(symbol).currency == "GBX":
        for c in ("open", "high", "low", "close", "adj_close"):
            out[c] = out[c] / 100.0
    return out.dropna(subset=["open", "high", "low", "close"])


def bias_notes(universe_name: Optional[str], provider: str) -> List[str]:
    notes = []
    if universe_name in UNIVERSES or universe_name is None:
        notes.append("Survivorship bias: universe is today's large caps; delisted or demoted companies are missing.")
    if provider == "yahoo":
        notes.append("Yahoo data is not point-in-time; adjusted prices are revised after each dividend or split.")
    if provider == "demo":
        notes.append("DEMO DATA: synthetic prices. Results are meaningless for decisions.")
    notes.append("Past performance in a backtest is not evidence of future profitability.")
    return notes


def run_experiment(strategy_key: str, params: Optional[Dict[str, Any]] = None, *,
                   symbols: Optional[List[str]] = None, universe: Optional[str] = None,
                   period: str = "train", config: Optional[BacktestConfig] = None,
                   provider: Optional[str] = None, confirm_test: bool = False,
                   source: str = "cli", notes: Optional[str] = None) -> int:
    """Returns the experiment id. Failures are recorded and re-raised."""
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    if period in ("test", "full") and not confirm_test:
        raise TestSetLocked("The test period is locked. Pass confirm_test=True (CLI: --confirm-test) "
                            "only for a final evaluation; the run will be flagged.")
    strategy = get_strategy(strategy_key)
    p = strategy.resolve(params)
    cfg = config or BacktestConfig()
    pkey = provider or get_settings().data_provider
    syms = [s.upper() for s in (symbols or UNIVERSES.get(universe or "", []))]
    if not syms:
        raise ValueError("No symbols: pass symbols=[...] or a known universe")
    markets = {catalogue_get(s).market for s in syms}
    if len(markets) != 1:
        raise ValueError("A backtest must use one market (one currency). Split UK and US runs.")
    market = markets.pop()
    bench_sym = MARKETS[market].benchmark

    with session_scope() as s:
        exp = Experiment(strategy_key=strategy.key, params=p, universe=syms, market=market, period=period,
                         used_test_set=period in ("test", "full"), config=cfg.to_dict(), data_provider=pkey,
                         benchmark=bench_sym, status="running", bias_notes=bias_notes(universe, pkey),
                         source=source, notes=notes)
        s.add(exp)
        s.flush()
        exp_id = exp.id

    try:
        bars = {}
        for sym in syms:
            raw = repo.load_bars(pkey, sym)
            if raw.empty:
                log.warning("%s: no stored data for provider %s; excluded", sym, pkey)
                continue
            bars[sym] = _prepare(raw, sym)
        if not bars:
            raise RuntimeError(f"No stored price data for any symbol under provider {pkey!r}. Run a refresh first.")
        bench_raw = repo.load_bars(pkey, bench_sym)
        benchmark = _prepare(bench_raw, bench_sym)["close"] if not bench_raw.empty else None

        all_dates = pd.DatetimeIndex(sorted(set().union(*[set(b.index) for b in bars.values()])))
        warm = strategy.warmup_bars(p)
        if len(all_dates) <= warm + 30:
            raise RuntimeError(f"Only {len(all_dates)} sessions stored; strategy needs {warm} warm-up + 30.")
        split = chronological_split(all_dates[warm:])
        start, end = split.window(period)

        result: BacktestResult = run_backtest(strategy, p, bars, market, cfg, start, end, benchmark)
        _store(exp_id, result, start, end, split, missing=[x for x in syms if x not in bars])
        return exp_id
    except Exception as exc:
        with session_scope() as s:
            exp = s.get(Experiment, exp_id)
            exp.status, exp.finished_at = "failed", utcnow()
            exp.error = f"{exc}\n\n{traceback.format_exc(limit=4)}"
        raise


def _store(exp_id: int, r: BacktestResult, start, end, split, missing: List[str]) -> None:
    with session_scope() as s:
        exp = s.get(Experiment, exp_id)
        exp.status, exp.finished_at = "completed", utcnow()
        exp.start_date, exp.end_date = pd.Timestamp(start).to_pydatetime(), pd.Timestamp(end).to_pydatetime()
        exp.metrics = {**r.metrics, "split": split.to_dict(), "missing_symbols": missing}
        for t in r.trades.itertuples(index=False):
            s.add(Trade(experiment_id=exp_id, symbol=t.symbol, entry_ts=pd.Timestamp(t.entry_ts).to_pydatetime(),
                        entry_price=t.entry_price, exit_ts=pd.Timestamp(t.exit_ts).to_pydatetime(),
                        exit_price=t.exit_price, shares=t.shares, costs=t.costs, net_pnl=t.net_pnl,
                        return_pct=t.return_pct, bars_held=int(t.bars_held), entry_reason=t.entry_reason,
                        exit_reason=t.exit_reason))
        bm = r.benchmark.reindex(r.equity.index) if r.benchmark is not None else None
        s.add_all([EquityPoint(experiment_id=exp_id, ts=ts.to_pydatetime(), equity=float(v),
                               benchmark=None if bm is None or pd.isna(bm.loc[ts]) else float(bm.loc[ts]),
                               exposure=float(r.exposure.loc[ts]))
                   for ts, v in r.equity.items()])
