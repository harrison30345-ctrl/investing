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
import uuid
from typing import Any, Dict, List, Optional, Tuple

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


INTRADAY_INTERVAL = "5m"


class TestSetLocked(PermissionError):
    """Raised when the untouched test period is requested without confirmation."""


def _prepare(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Adjusted OHLC, converted to major currency units (pence → pounds)."""
    out = adjusted_ohlc(df)
    if catalogue_get(symbol).currency == "GBX":
        for c in ("open", "high", "low", "close", "adj_close"):
            out[c] = out[c] / 100.0
    return out.dropna(subset=["open", "high", "low", "close"])


def bias_notes(universe_name: Optional[str], provider: str, intraday: bool = False) -> List[str]:
    notes = []
    if intraday:
        notes.append("Short sample: Yahoo keeps only ~60 days of 5-minute bars, so this covers a few dozen sessions "
                     "at most. Treat any result as anecdotal.")
        notes.append("Fills assume the next 5-minute bar's open plus the cost model; real slippage around the open "
                     "and on breakouts is often larger.")
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
                   source: str = "cli", notes: Optional[str] = None, batch: Optional[str] = None,
                   parent_id: Optional[int] = None, min_warmup: int = 0) -> int:
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
    intraday = strategy.timeframe == "intraday"
    interval = INTRADAY_INTERVAL if intraday else "1d"
    if intraday and market != "US":
        raise ValueError("Intraday strategies are US-only for now (session rules are written for NYSE hours).")
    bench_sym = MARKETS[market].benchmark

    with session_scope() as s:
        exp = Experiment(strategy_key=strategy.key, params=p, universe=syms, market=market, period=period,
                         used_test_set=period in ("test", "full"), config=cfg.to_dict(), data_provider=pkey,
                         benchmark=bench_sym, status="running", bias_notes=bias_notes(universe, pkey, intraday),
                         source=source, notes=notes, batch=batch, parent_id=parent_id)
        s.add(exp)
        s.flush()
        exp_id = exp.id

    try:
        bars = {}
        # Intraday bars are used as stored (unadjusted, US dollars); daily bars are adjusted and in major units.
        prep = (lambda df, sym: df.dropna(subset=["open", "high", "low", "close"])) if intraday else _prepare
        for sym in syms:
            raw = repo.load_bars(pkey, sym, interval)
            if raw.empty:
                log.warning("%s: no stored %s data for provider %s; excluded", sym, interval, pkey)
                continue
            bars[sym] = prep(raw, sym)
        if not bars:
            raise RuntimeError(f"No stored {interval} price data for any symbol under provider {pkey!r}. "
                               "Run a refresh first.")
        bench_raw = repo.load_bars(pkey, bench_sym, interval)
        benchmark = prep(bench_raw, bench_sym)["close"] if not bench_raw.empty else None

        all_dates = pd.DatetimeIndex(sorted(set().union(*[set(b.index) for b in bars.values()])))
        if intraday:
            # Split by whole sessions so no day is cut in half between train and validation.
            local_day = all_dates.tz_localize("UTC").tz_convert(MARKETS[market].tz).normalize().tz_localize(None)
            sessions = pd.DatetimeIndex(sorted(set(local_day)))
            warm = 0
            split = chronological_split(sessions)
            d0, d1 = split.window(period)
            in_window = all_dates[(local_day >= d0) & (local_day <= d1)]
            start, end = in_window[0], in_window[-1]
        else:
            # The warm-up decides where the train/validation/test split starts. Sweeps and re-tests pass
            # min_warmup so every variant is judged on exactly the same dates.
            warm = max(strategy.warmup_bars(p), int(min_warmup))
            if len(all_dates) <= warm + 30:
                raise RuntimeError(f"Only {len(all_dates)} sessions stored; strategy needs {warm} warm-up + 30.")
            split = chronological_split(all_dates[warm:])
            start, end = split.window(period)

        result: BacktestResult = run_backtest(strategy, p, bars, market, cfg, start, end, benchmark)
        result.metrics.update(tie_break_spread(strategy, p, bars, market, cfg, start, end, benchmark, result))
        result.metrics.update(_trial_stats(exp_id, strategy.key, market, syms, result))
        _store(exp_id, result, start, end, split, missing=[x for x in syms if x not in bars], warmup=warm)
        if not source.startswith("agent:") and source != "timing":
            # Event trigger: new human experiments get an independent look at the next research cycle.
            # Queuing is free; no model is called until a cycle runs.
            from ..agents.orchestrator import enqueue
            enqueue("validation", "event", f"Experiment #{exp_id} completed ({strategy.key}, {period})")
        return exp_id
    except Exception as exc:
        with session_scope() as s:
            exp = s.get(Experiment, exp_id)
            exp.status, exp.finished_at = "failed", utcnow()
            exp.error = f"{exc}\n\n{traceback.format_exc(limit=4)}"
        raise


def _store(exp_id: int, r: BacktestResult, start, end, split, missing: List[str], warmup: int) -> None:
    with session_scope() as s:
        exp = s.get(Experiment, exp_id)
        exp.status, exp.finished_at = "completed", utcnow()
        exp.start_date, exp.end_date = pd.Timestamp(start).to_pydatetime(), pd.Timestamp(end).to_pydatetime()
        exp.metrics = {**r.metrics, "split": split.to_dict(), "missing_symbols": missing, "warmup_bars": warmup}
        for t in r.trades.itertuples(index=False):
            s.add(Trade(experiment_id=exp_id, symbol=t.symbol, entry_ts=pd.Timestamp(t.entry_ts).to_pydatetime(),
                        entry_price=t.entry_price, exit_ts=pd.Timestamp(t.exit_ts).to_pydatetime(),
                        exit_price=t.exit_price, shares=t.shares, costs=t.costs, net_pnl=t.net_pnl,
                        return_pct=t.return_pct, bars_held=int(t.bars_held), entry_reason=t.entry_reason,
                        exit_reason=t.exit_reason))
        bm = r.benchmark.reindex(r.equity.index) if r.benchmark is not None else None
        uni = r.universe.reindex(r.equity.index) if r.universe is not None else None
        val = lambda series, ts: None if series is None or pd.isna(series.loc[ts]) else float(series.loc[ts])  # noqa: E731
        s.add_all([EquityPoint(experiment_id=exp_id, ts=ts.to_pydatetime(), equity=float(v), benchmark=val(bm, ts),
                               universe=val(uni, ts), exposure=float(r.exposure.loc[ts]))
                   for ts, v in r.equity.items()])


TIE_BREAK_SEEDS = 8


def tie_break_spread(strategy, params, bars, market, cfg: BacktestConfig, start, end, benchmark,
                     result: BacktestResult) -> Dict[str, float]:
    """
    How much the result depends on which of several same-bar signals got a free slot. If no entry was
    ever refused for lack of a slot, the order can't matter and no extra runs are needed.
    """
    from dataclasses import replace
    from ..strategies.base import SignalStrategy
    if not isinstance(strategy, SignalStrategy) or not result.metrics.get("entries_skipped_capacity"):
        return {"tiebreak_seeds": 1.0}
    nets, sharpes = [result.metrics["net_return"]], [result.metrics.get("sharpe") or 0.0]
    for seed in range(1, TIE_BREAK_SEEDS):
        alt = run_backtest(strategy, params, bars, market, replace(cfg, tie_break_seed=cfg.tie_break_seed + seed),
                           start, end, benchmark)
        nets.append(alt.metrics["net_return"])
        sharpes.append(alt.metrics.get("sharpe") or 0.0)
    return {"tiebreak_seeds": float(len(nets)), "tiebreak_net_min": float(min(nets)),
            "tiebreak_net_median": float(pd.Series(nets).median()), "tiebreak_net_max": float(max(nets)),
            "tiebreak_sharpe_min": float(min(sharpes)), "tiebreak_sharpe_max": float(max(sharpes))}


def _trial_stats(exp_id: int, strategy_key: str, market: str, symbols: List[str],
                 result: BacktestResult) -> Dict[str, float]:
    """
    Trials = every recorded attempt (any parameters, any period, including failures) of this strategy
    on this market and symbol set, up to and including this one. The Deflated Sharpe Ratio discounts
    the result for that many tries.
    """
    from . import stats as S
    with session_scope() as s:
        rows = s.query(Experiment.id, Experiment.universe).filter(
            Experiment.strategy_key == strategy_key, Experiment.market == market,
            Experiment.source != "timing", Experiment.id <= exp_id).all()
    n = max(1, sum(1 for _, u in rows if list(u) == list(symbols)))
    daily = result.equity.pct_change().fillna(0.0).values[1:]
    out = {"trials": float(n)}
    dsr = S.deflated_sharpe(daily, n)
    if dsr == dsr:  # not NaN
        out["deflated_sharpe"] = dsr
    return out


def run_sweep(strategy_key: str, param: str, values: List[Any], base_params: Optional[Dict[str, Any]] = None,
              **kwargs) -> Tuple[str, List[int]]:
    """
    Run one experiment per value of `param`, all tagged with the same batch id.

    A sweep shows how sensitive a result is to one choice. If only one value
    works, that's a warning sign (fragility/overfitting), not a discovery.
    Failed variants are recorded and the sweep carries on.
    """
    strategy = get_strategy(strategy_key)
    if param not in {p.name for p in strategy.params}:
        raise ValueError(f"{strategy_key} has no parameter {param!r}")
    batch = uuid.uuid4().hex[:12]
    ids: List[int] = []
    variants = [{**(base_params or {}), param: v} for v in values]
    warm = max(strategy.warmup_bars(strategy.resolve(v)) for v in variants)
    for v, params in zip(values, variants):
        note = f"sweep {param}={v}"
        try:
            ids.append(run_experiment(strategy_key, params, batch=batch, notes=note, min_warmup=warm, **kwargs))
        except TestSetLocked:
            raise
        except Exception as exc:  # recorded by run_experiment; keep going
            log.warning("sweep %s=%s failed: %s", param, v, exc)
            with session_scope() as s:
                last = s.query(Experiment).filter_by(batch=batch).order_by(Experiment.id.desc()).first()
                if last is not None and last.id not in ids:
                    ids.append(last.id)
    return batch, ids


def rerun_on_period(exp_id: int, period: str, confirm_test: bool = False, source: str = "dashboard") -> int:
    """Re-test an experiment's exact strategy, parameters, universe and costs on another period."""
    with session_scope() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise KeyError(f"No experiment #{exp_id}")
        spec = dict(strategy_key=e.strategy_key, params=e.params, symbols=list(e.universe),
                    config=BacktestConfig.from_dict(e.config), provider=e.data_provider,
                    min_warmup=int((e.metrics or {}).get("warmup_bars") or 0))
        universe_name = next((k for k, v in UNIVERSES.items() if v == list(e.universe)), None)
    return run_experiment(spec.pop("strategy_key"), spec.pop("params"), universe=universe_name, period=period,
                          confirm_test=confirm_test, source=source, parent_id=exp_id,
                          notes=f"{period} re-test of #{exp_id}", **spec)
