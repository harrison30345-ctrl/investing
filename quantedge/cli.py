"""
QuantEdge command line.

    python3 -m quantedge.cli refresh --core               # indices, benchmarks, watchlist
    python3 -m quantedge.cli refresh --universe us_large  # plus a strategy universe
    python3 -m quantedge.cli scan                         # record signal observations
    python3 -m quantedge.cli backtest ema_pullback --universe us_large --period train
    python3 -m quantedge.cli sweep xs_momentum lookback 63,126,252 --universe us_large
    python3 -m quantedge.cli validate 12                   # same config on the validation period
    python3 -m quantedge.cli report --ids 12,13
    python3 -m quantedge.cli status                       # freshness, last jobs, experiments
    python3 -m quantedge.cli health                       # provider reachability
    python3 -m quantedge.cli dashboard                    # launch the web UI
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings
from pathlib import Path


def _symbols(args) -> list:
    from quantedge.universe import BENCHMARKS, INDICES, UNIVERSES
    from quantedge.watchlists import all_watched_symbols
    out = []
    if getattr(args, "core", False):
        out += [i.symbol for i in INDICES + BENCHMARKS] + all_watched_symbols()
    if getattr(args, "universe", None):
        out += UNIVERSES[args.universe]
    if getattr(args, "symbols", None):
        out += [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    return list(dict.fromkeys(out))


def cmd_refresh(args) -> int:
    from quantedge.data.service import refresh_daily
    from quantedge.signals import scan_signals
    from quantedge.universe import BENCHMARKS
    syms = _symbols(args)
    if args.universe:
        syms += [b.symbol for b in BENCHMARKS]
    if not syms:
        print("Nothing to refresh: pass --core, --universe or --symbols")
        return 2
    res = refresh_daily(list(dict.fromkeys(syms)), years=args.years, trigger="cli")
    for r in res:
        flag = "" if r.quality_ok in (True, None) else "  (quality issues; see Markets page)"
        print(f"{r.symbol:10s} {r.status:10s} {r.rows:6d} rows{'  full reload' if r.full_reload else ''}{flag}"
              f"{'  ' + r.message if r.message and r.status != 'ok' else ''}")
    n = scan_signals([r.symbol for r in res if r.status == "ok"], trigger="cli")
    print(f"\n{sum(r.status == 'ok' for r in res)}/{len(res)} updated · {n} new signal observations")
    return 0 if all(r.status == "ok" for r in res) else 1


def cmd_scan(args) -> int:
    from quantedge.data import repository as repo
    from quantedge.config import get_settings
    from quantedge.signals import scan_signals
    syms = _symbols(args) or [r.symbol for r in repo.freshness(get_settings().data_provider) if r.last_bar]
    n = scan_signals(syms, lookback_sessions=args.lookback, trigger="cli")
    print(f"{n} new signal observations across {len(syms)} symbols")
    return 0


def cmd_backtest(args) -> int:
    from quantedge.backtest.engine import BacktestConfig
    from quantedge.backtest.costs import CostModel
    from quantedge.backtest.runner import TestSetLocked, run_experiment
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    params = json.loads(args.params) if args.params else {}
    cfg = BacktestConfig(initial_capital=args.capital, risk_per_trade=args.risk, max_positions=args.max_positions,
                         costs=CostModel(spread_bps=args.spread_bps, slippage_bps=args.slippage_bps,
                                         commission_bps=args.commission_bps))
    try:
        exp_id = run_experiment(args.strategy, params, symbols=_symbols(args) or None, universe=args.universe,
                                period=args.period, config=cfg, confirm_test=args.confirm_test, source="cli",
                                notes=args.notes)
    except TestSetLocked as exc:
        print(exc)
        return 2
    except Exception as exc:
        print(f"Backtest failed (recorded): {exc}")
        return 1
    with session_scope() as s:
        e = s.get(Experiment, exp_id)
        m = e.metrics
    pct = lambda k: f"{m[k] * 100:+.1f}%" if m.get(k) is not None else "n/a"  # noqa: E731
    print(f"Experiment #{exp_id}  {args.strategy}  {e.market}  {args.period} "
          f"{e.start_date:%Y-%m-%d} → {e.end_date:%Y-%m-%d}")
    print(f"  net {pct('net_return')}   benchmark {e.benchmark} {pct('benchmark_return')}   excess {pct('excess_return')}")
    pf = f"{m['profit_factor']:.2f}" if m.get("profit_factor") is not None else "n/a"
    win = f"{m['win_rate'] * 100:.0f}%" if m.get("win_rate") is not None else "n/a"
    print(f"  sharpe {m.get('sharpe', 0):.2f}   max DD {pct('max_drawdown')}   trades {int(m.get('trade_count', 0))}"
          f"   win {win}   PF {pf}")
    for n in e.bias_notes:
        print(f"  ! {n}")
    return 0


def cmd_sweep(args) -> int:
    from quantedge.backtest.runner import run_sweep
    values = [json.loads(v) for v in args.values.split(",")]
    batch, ids = run_sweep(args.strategy, args.param, values, json.loads(args.params) if args.params else None,
                           symbols=_symbols(args) or None, universe=args.universe, period=args.period, source="cli")
    print(f"Sweep {batch}: experiments {', '.join('#' + str(i) for i in ids)}")
    return cmd_report(argparse.Namespace(ids=",".join(map(str, ids)), sweep=True))


def cmd_validate(args) -> int:
    from quantedge.backtest.runner import TestSetLocked, rerun_on_period
    try:
        new_id = rerun_on_period(args.experiment, args.period, confirm_test=args.confirm_test, source="cli")
    except TestSetLocked as exc:
        print(exc)
        return 2
    print(f"Recorded {args.period} re-test of #{args.experiment} as #{new_id}")
    return cmd_report(argparse.Namespace(ids=f"{args.experiment},{new_id}", sweep=False))


def cmd_report(args) -> int:
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    from quantedge.reports import build_reports, report_markdown, sweep_summary
    with session_scope() as s:
        q = s.query(Experiment)
        if args.ids:
            q = q.filter(Experiment.id.in_([int(i) for i in args.ids.split(",")]))
        rows = [{c.name: getattr(e, c.name) for c in Experiment.__table__.columns} for e in q.all()]
    rows = [r for r in rows if r["source"] != "timing"]
    if getattr(args, "sweep", False):
        sm = sweep_summary(rows)
        if sm:
            print(f"\n{sm.finding.title}: {sm.finding.detail}")
        return 0
    for r in build_reports(rows):
        print(report_markdown(r), "\n" + "-" * 72)
    return 0


def cmd_status(args) -> int:
    from quantedge.config import get_settings
    from quantedge.data import repository as repo
    from quantedge.markets import session_status
    s = get_settings()
    print(f"Mode {s.trading_mode} · provider {s.data_provider} · db {s.database_url}")
    for m in ("UK", "US"):
        print(f"  {m}: {session_status(m)}")
    rows = repo.freshness(s.data_provider)
    print(f"\n{len(rows)} symbols stored")
    for r in rows:
        print(f"  {r.symbol:10s} last bar {r.last_bar:%Y-%m-%d}" if r.last_bar else f"  {r.symbol:10s} no bars",
              f"  fetch {r.last_status}  quality {'ok' if r.quality_ok else r.quality_ok}")
    return 0


def cmd_health(args) -> int:
    from quantedge.data.providers import get_provider
    p = get_provider()
    ok, msg = p.healthcheck()
    print(f"{p.info.name}: {'OK' if ok else 'FAIL'}: {msg}")
    return 0 if ok else 1


def cmd_dashboard(args) -> int:
    from quantedge.ui.theme import STREAMLIT_THEME
    app = Path(__file__).resolve().parent / "app" / "main.py"
    argv = ["streamlit", "run", str(app), "--server.port", str(args.port), "--server.headless", "true",
            "--browser.gatherUsageStats", "false"]
    for k, v in STREAMLIT_THEME.items():
        argv += [f"--{k}", v]
    os.execvp(sys.executable, [sys.executable, "-m"] + argv)
    return 0


def main(argv=None) -> int:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(prog="quantedge", description="QuantEdge research platform (no live trading)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def sym_args(p):
        p.add_argument("--core", action="store_true", help="indices, benchmarks and watchlist symbols")
        p.add_argument("--universe", choices=["us_large", "uk_large"])
        p.add_argument("--symbols", help="comma-separated, e.g. AAPL,SHEL.L")

    p = sub.add_parser("refresh", help="fetch daily bars, validate, store, scan signals")
    sym_args(p)
    p.add_argument("--years", type=int, default=None)
    p.set_defaults(fn=cmd_refresh)

    p = sub.add_parser("scan", help="record signal observations from stored bars")
    sym_args(p)
    p.add_argument("--lookback", type=int, default=10, help="sessions to look back")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("backtest", help="run and record a backtest experiment")
    p.add_argument("strategy")
    sym_args(p)
    p.add_argument("--period", default="train", choices=["train", "validation", "test", "full"])
    p.add_argument("--confirm-test", action="store_true", help="allow the locked test period (flagged)")
    p.add_argument("--params", help='JSON, e.g. \'{"fast": 10, "slow": 40}\'')
    p.add_argument("--capital", type=float, default=100_000)
    p.add_argument("--risk", type=float, default=0.01, help="fraction of equity risked per trade")
    p.add_argument("--max-positions", type=int, default=5)
    p.add_argument("--spread-bps", type=float, default=5.0)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--commission-bps", type=float, default=0.0)
    p.add_argument("--notes")
    p.set_defaults(fn=cmd_backtest)

    p = sub.add_parser("sweep", help="run one experiment per value of a parameter")
    p.add_argument("strategy")
    p.add_argument("param")
    p.add_argument("values", help="comma-separated, e.g. 63,126,252")
    sym_args(p)
    p.add_argument("--period", default="train", choices=["train", "validation"])
    p.add_argument("--params", help="JSON of the other parameters")
    p.set_defaults(fn=cmd_sweep)

    p = sub.add_parser("validate", help="re-test an experiment on the validation (or test) period")
    p.add_argument("experiment", type=int)
    p.add_argument("--period", default="validation", choices=["validation", "test"])
    p.add_argument("--confirm-test", action="store_true")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("report", help="print plain-English reports (Markdown)")
    p.add_argument("--ids", help="comma-separated experiment ids; default all")
    p.set_defaults(fn=cmd_report)

    sub.add_parser("status", help="data freshness and configuration").set_defaults(fn=cmd_status)
    sub.add_parser("health", help="check the data provider is reachable").set_defaults(fn=cmd_health)
    p = sub.add_parser("dashboard", help="launch the web dashboard")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8503)))
    p.set_defaults(fn=cmd_dashboard)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
