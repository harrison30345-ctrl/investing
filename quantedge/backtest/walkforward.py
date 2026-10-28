"""
Walk-forward analysis (audit fix 6).

A single train/validation split asks one question in one market regime. Walk-forward asks it
repeatedly:

    [ in-sample: pick the best value ][ out-of-sample: trade it ] → roll forward by the OOS length

In every fold, each candidate value of one parameter is backtested on the in-sample window and
scored by its Sharpe above cash. The winner is then traded, untouched, on the next
out-of-sample window. Only those out-of-sample pieces are stitched together and reported,
so the stitched result never benefits from hindsight about which value worked best there.

  * Uses train + validation data only. The locked test period is never touched.
  * Each fold starts flat and ends flat (positions are closed at the fold end, with costs).
  * Every candidate's in-sample score is stored in the experiment's `folds`, so the full
    search is on the record, not just the winners.
  * Daily strategies only. ~60 days of intraday history can't support it.
  * Cost curve (Phase Four): the stitched out-of-sample result is re-run with trading frictions at
    0×, 2× and 3×. Each fold keeps the value chosen at baseline costs, so the curve shows what costs
    do to the traded path. Signals never see costs, so the trades' timing is the same at every level.
"""
from __future__ import annotations

import json
import traceback
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pandas as pd

from ..config import get_settings
from ..data import repository as repo
from ..db import session_scope
from ..db.models import Experiment, utcnow
from ..markets import MARKETS
from ..strategies import get_strategy
from ..universe import UNIVERSES
from ..universe import get as catalogue_get
from .engine import BacktestConfig, _finish, default_config, run_backtest
from .splits import chronological_split, walk_forward_windows

TRAIN_SESSIONS = 756     # ~3 years
TEST_SESSIONS = 126      # ~6 months


def run_walk_forward(strategy_key: str, param: str, values: List[Any], *, base_params: Optional[Dict[str, Any]] = None,
                     symbols: Optional[List[str]] = None, universe: Optional[str] = None,
                     train_sessions: int = TRAIN_SESSIONS, test_sessions: int = TEST_SESSIONS,
                     config: Optional[BacktestConfig] = None, provider: Optional[str] = None,
                     source: str = "cli", notes: Optional[str] = None, holdout_start: Optional[str] = None,
                     min_warmup: int = 0) -> int:
    from .runner import _prepare, _store, _trial_stats, bias_notes
    strategy = get_strategy(strategy_key)
    if strategy.timeframe != "daily":
        raise ValueError("Walk-forward needs years of history; intraday strategies have ~60 days.")
    if param not in {p.name for p in strategy.params}:
        raise ValueError(f"{strategy_key} has no parameter {param!r}")
    values = list(dict.fromkeys(values))
    if not values:
        raise ValueError("Walk-forward needs at least one candidate value")
    fixed = len(values) == 1          # a fixed rule: nothing is chosen in-sample; folds give OOS dispersion
    base = {k: v for k, v in (base_params or {}).items() if k != param}
    candidates = {json.dumps(v): strategy.resolve({**base, param: v}) for v in values}   # validates every value
    cfg = config or default_config(strategy)
    pkey = provider or get_settings().data_provider
    syms = [s.upper() for s in (symbols or UNIVERSES.get(universe or "", []))]
    markets = {catalogue_get(s).market for s in syms}
    if not syms or len(markets) != 1:
        raise ValueError("Pass symbols from one market (or a known universe)")
    market = markets.pop()
    bench_sym = MARKETS[market].benchmark
    spec = {"param": param, "values": values, "train_sessions": train_sessions, "test_sessions": test_sessions}
    if fixed:
        spec["fixed_rule"] = True

    with session_scope() as s:
        exp = Experiment(strategy_key=strategy.key, params={**strategy.resolve(base), "walk_forward": spec},
                         universe=syms, market=market, period="walkforward", used_test_set=False,
                         config=cfg.to_dict(), data_provider=pkey, benchmark=bench_sym, status="running",
                         bias_notes=bias_notes(universe, pkey, symbols=syms) + [
                             "Walk-forward (fixed rule): nothing is chosen in-sample; results are the stitched "
                             "out-of-sample folds, each starting and ending flat." if fixed else
                             "Walk-forward: parameters re-chosen every fold on past data only; results are the "
                             "stitched out-of-sample folds."],
                         source=source, notes=notes)
        s.add(exp)
        s.flush()
        exp_id = exp.id

    try:
        bars = {}
        for sym in syms:
            raw = repo.load_bars(pkey, sym)
            if not raw.empty:
                bars[sym] = _prepare(raw, sym)
        if not bars:
            raise RuntimeError(f"No stored 1d price data under provider {pkey!r}. Run a refresh first.")
        bench_raw = repo.load_bars(pkey, bench_sym)
        benchmark = _prepare(bench_raw, bench_sym)["close"] if not bench_raw.empty else None
        all_dates = pd.DatetimeIndex(sorted(set().union(*[set(b.index) for b in bars.values()])))
        # min_warmup aligns the window with train/validation runs that used the same value (as in run_experiment).
        warm = max(max(strategy.warmup_bars(p) for p in candidates.values()), int(min_warmup))
        if holdout_start:
            # Fixed calendar holdout: walk forward over all development data before it.
            from .runner import holdout_split
            split = holdout_split(all_dates, warm, holdout_start)
        else:
            split = chronological_split(all_dates[warm:])
        usable = all_dates[(all_dates >= split.train[0]) & (all_dates <= split.validation[1])]   # never the test set
        windows = walk_forward_windows(usable, train_sessions, test_sessions)
        if not windows:
            raise RuntimeError(f"Only {len(usable)} sessions before the test period; need at least "
                               f"{train_sessions + test_sessions} for one fold.")
        from ..rates import annual_rates
        cash_rates, cash_source = (annual_rates(market, all_dates) if cfg.cash_interest
                                   else (None, "none (cash interest off)"))

        folds, pieces, chosen = [], [], []
        for ins, oos in windows:
            scores = {}
            for key, p in candidates.items():
                r = run_backtest(strategy, p, bars, market, cfg, ins[0], ins[1], benchmark, cash_rates)
                scores[key] = float(r.metrics.get("sharpe") or 0.0)
            best = max(scores, key=lambda k: scores[k])           # ties: the first value listed
            r = run_backtest(strategy, candidates[best], bars, market, cfg, oos[0], oos[1], benchmark, cash_rates)
            pieces.append(r)
            chosen.append((candidates[best], oos))
            folds.append({"in_sample": [f"{ins[0]:%Y-%m-%d}", f"{ins[1]:%Y-%m-%d}"],
                          "out_of_sample": [f"{oos[0]:%Y-%m-%d}", f"{oos[1]:%Y-%m-%d}"],
                          "chosen": json.loads(best), "in_sample_sharpe": {k: round(v, 4) for k, v in scores.items()},
                          "oos_net_return": float(r.metrics["net_return"]),
                          "oos_benchmark_return": r.metrics.get("benchmark_return"),
                          "oos_universe_return": r.metrics.get("universe_return")})

        result = _stitch(pieces, cfg, cash_rates, benchmark)
        result.metrics["result_basis"] = "stitched out-of-sample folds"
        result.metrics.update(_cost_curve(strategy, chosen, bars, market, cfg, cash_rates, benchmark, result))
        result.metrics["folds"] = folds
        result.metrics["cash_rate_source"] = cash_source
        result.metrics["walkforward_values_chosen"] = [f["chosen"] for f in folds]
        result.metrics.update(_trial_stats(exp_id, strategy.key, market, syms,
                                           {**strategy.resolve(base), "walk_forward": spec}, result))
        result.metrics["trials"] = float(max(result.metrics.get("trials", 1), len(values)))
        shim = SimpleNamespace(to_dict=lambda: {"walk_forward_window": [f"{usable[0]:%Y-%m-%d}",
                                                                        f"{usable[-1]:%Y-%m-%d}"],
                                                "test_locked_from": f"{split.test[0]:%Y-%m-%d}"},
                               holdout_start=holdout_start)
        _store(exp_id, result, windows[0][1][0], windows[-1][1][1], shim,
               missing=[x for x in syms if x not in bars], warmup=warm)
        return exp_id
    except Exception as exc:
        with session_scope() as s:
            e = s.get(Experiment, exp_id)
            e.status, e.finished_at = "failed", utcnow()
            e.error = f"{exc}\n\n{traceback.format_exc(limit=4)}"
        raise


def _cost_curve(strategy, chosen, bars, market, cfg: BacktestConfig, cash_rates, benchmark,
                result) -> Dict[str, Any]:
    """Stitched out-of-sample net return, Sharpe and drawdown at 0×, 2× and 3× trading frictions."""
    from dataclasses import replace
    from .runner import COST_MULTIPLES
    out: Dict[str, Any] = {"cost_curve_basis": "stitched out-of-sample folds; fold choices fixed at baseline costs"}
    pts = {1.0: float(result.metrics["net_return"])}
    for k in COST_MULTIPLES:
        c = replace(cfg, costs=cfg.costs.scaled(k))
        alt = _stitch([run_backtest(strategy, p, bars, market, c, oos[0], oos[1], benchmark, cash_rates)
                       for p, oos in chosen], c, cash_rates, benchmark)
        tag = f"x{int(k)}"
        out[f"cost_{tag}_net_return"] = float(alt.metrics["net_return"])
        out[f"cost_{tag}_sharpe"] = float(alt.metrics.get("sharpe") or 0.0)
        out[f"cost_{tag}_max_drawdown"] = float(alt.metrics["max_drawdown"])
        out[f"cost_{tag}_trade_count"] = float(alt.metrics.get("trade_count") or 0)
        pts[k] = out[f"cost_{tag}_net_return"]
    xs = sorted(pts)
    for a, b in zip(xs, xs[1:]):
        if pts[a] > 0 >= pts[b]:
            out["cost_breakeven_multiple"] = float(a + (b - a) * pts[a] / (pts[a] - pts[b]))
            break
    return out


def _chain(series_list: List[pd.Series], start_value: float) -> pd.Series:
    """
    Join per-fold value curves into one by compounding each fold's daily returns. Every fold starts
    from `start_value`, so its first-day return (entries and costs on day one) is measured from there.
    """
    parts = []
    for s in series_list:
        r = s.pct_change()
        r.iloc[0] = s.iloc[0] / start_value - 1
        parts.append(r.fillna(0.0))
    rets = pd.concat(parts)
    rets = rets[~rets.index.duplicated(keep="first")]
    return start_value * (1 + rets).cumprod()


def _stitch(pieces, cfg: BacktestConfig, cash_rates, benchmark):
    eq = _chain([p.equity for p in pieces], cfg.initial_capital)
    ex = pd.concat([p.exposure for p in pieces])
    ex = ex[~ex.index.duplicated(keep="first")]
    trades = pd.concat([p.trades for p in pieces], ignore_index=True)
    bm = _chain([p.benchmark for p in pieces], cfg.initial_capital) if all(p.benchmark is not None for p in pieces) else None
    uni = _chain([p.universe for p in pieces], cfg.initial_capital) if all(p.universe is not None for p in pieces) else None
    um = (_chain([p.universe_monthly for p in pieces], cfg.initial_capital)
          if all(p.universe_monthly is not None for p in pieces) else None)
    interest = sum((p.metrics.get("interest_earned_pct") or 0.0) * cfg.initial_capital for p in pieces)
    return _finish(list(eq.values), list(ex.reindex(eq.index).fillna(0.0).values), trades.to_dict("records"),
                   bm, eq.index, cfg, sum(p.total_costs for p in pieces), sum(p.traded_notional for p in pieces),
                   uni, int(sum(p.metrics.get("entries_skipped_capacity") or 0 for p in pieces)),
                   cash_rates, interest, benchmark, um)
