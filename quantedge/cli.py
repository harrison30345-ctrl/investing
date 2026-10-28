"""
QuantEdge command line.

    python3 -m quantedge.cli refresh --core               # indices, benchmarks, watchlist
    python3 -m quantedge.cli refresh --universe us_large  # plus a strategy universe
    python3 -m quantedge.cli refresh --symbols SPY --since 1998-12-01  # back-fill long history
    python3 -m quantedge.cli scan                         # record signal observations
    python3 -m quantedge.cli backtest ema_pullback --universe us_large --period train
    python3 -m quantedge.cli sweep xs_momentum lookback 63,126,252 --universe us_large
    python3 -m quantedge.cli walkforward ema_pullback fast 10,20,30 --universe us_large
    python3 -m quantedge.cli validate 12                   # same config on the validation period
    python3 -m quantedge.cli report --ids 12,13
    python3 -m quantedge.cli agents estimate                # what the next research cycle would cost
    python3 -m quantedge.cli agents run                     # needs QE_LLM_PROVIDER=anthropic + key
    python3 -m quantedge.cli agents approve 7 --note "ok"
    python3 -m quantedge.cli refresh --intraday             # US 5-minute bars (~60 days on Yahoo)
    python3 -m quantedge.cli worker                         # always-on scheduler (or: python3 -m quantedge.worker)
    python3 -m quantedge.cli paper create --experiment 12 --approve --name "Breakout US"
    python3 -m quantedge.cli health                         # exit code 0/1/2
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
from datetime import date
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
    from quantedge.data.service import refresh_daily, refresh_intraday
    if args.intraday:
        from quantedge.universe import UNIVERSES
        syms = _symbols(args) or UNIVERSES["us_intraday"]
        for r in refresh_intraday(syms, trigger="cli"):
            print(f"{r.symbol:10s} {r.status:10s} {r.rows:6d} rows  {r.message}")
        return 0
    from quantedge.signals import scan_signals
    from quantedge.universe import BENCHMARKS
    syms = _symbols(args)
    if args.universe:
        syms += [b.symbol for b in BENCHMARKS]
    if not syms:
        print("Nothing to refresh: pass --core, --universe or --symbols")
        return 2
    since = date.fromisoformat(args.since) if args.since else None
    res = refresh_daily(list(dict.fromkeys(syms)), years=args.years, trigger="cli", since=since)
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
    from quantedge.backtest.runner import TestSetLocked, run_experiment
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    from quantedge.backtest.engine import default_config
    from quantedge.strategies import get_strategy
    params = json.loads(args.params) if args.params else {}
    cfg = _config_from_args(args, default_config(get_strategy(args.strategy)))
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


def cmd_agents(args) -> int:
    from quantedge.agents import orchestrator, proposals
    from quantedge.agents.budget import month_to_date
    from quantedge.config import get_settings
    s = get_settings()
    if args.action == "estimate":
        rows = orchestrator.estimate_cycle(args.agents.split(",") if args.agents else None)
        for r in rows:
            print(f"{r['name']:24s} ~{r['input_tokens']:6d} in-tokens  typical ${r['typical_usd']:.3f}  "
                  f"max ${r['max_usd']:.3f}{'  (skipped: no new evidence)' if r['will_skip'] else ''}")
        print(f"Total typical ${sum(r['typical_usd'] for r in rows):.2f}, worst case ${sum(r['max_usd'] for r in rows):.2f}. "
              f"Month to date ${month_to_date():.2f} of ${s.llm_monthly_budget_usd:.2f}.")
        return 0
    if args.action == "run":
        r = orchestrator.run_cycle("cli", agents=args.agents.split(",") if args.agents else None, force=args.force)
        print(f"{r.status}: {r.detail}")
        if r.experiments:
            print("Experiments run: " + ", ".join(f"#{i}" for i in r.experiments))
        return 0 if r.status in ("ok", "skipped") else 1
    if args.action in ("approve", "decline"):
        if args.id is None:
            print("Pass a proposal id")
            return 2
        print(proposals.decide(args.id, args.action == "approve", args.note or "", by="human (cli)"))
        return 0
    # status
    from quantedge.db import session_scope
    from quantedge.db.models import AgentTask, Proposal
    print(f"Provider {s.llm_provider} · model {s.llm_model} · effort {s.llm_effort} · "
          f"{'configured' if s.llm_configured else 'NOT configured (no calls will be made)'}")
    print(f"Spend this month ${month_to_date():.3f} of ${s.llm_monthly_budget_usd:.2f}")
    with session_scope() as db:
        for t in db.query(AgentTask).order_by(AgentTask.id.desc()).limit(12):
            print(f"  task #{t.id:<4d} {t.agent:11s} {t.status:15s} ${t.cost_usd or 0:.3f}  {t.reason or t.error or ''}"[:140])
        waiting = db.query(Proposal).filter_by(status="awaiting_approval").all()
        print(f"\n{len(waiting)} proposals awaiting your approval")
        for p in waiting:
            print(f"  #{p.id} [{p.kind}] {p.title}  (from {p.agent})")
    return 0


def _config_from_args(args, base):
    """Explicit CLI values override the strategy's timeframe defaults (intraday costs are higher)."""
    from dataclasses import replace
    from quantedge.backtest.costs import CostModel
    c = base.costs
    costs = CostModel(spread_bps=c.spread_bps if args.spread_bps is None else args.spread_bps,
                      slippage_bps=c.slippage_bps if args.slippage_bps is None else args.slippage_bps,
                      commission_bps=c.commission_bps if args.commission_bps is None else args.commission_bps,
                      commission_min=c.commission_min, uk_stamp_duty_bps=c.uk_stamp_duty_bps)
    return replace(base, initial_capital=args.capital, risk_per_trade=args.risk, max_positions=args.max_positions,
                   costs=costs, cash_interest=not args.no_cash_interest)


def cmd_walkforward(args) -> int:
    from quantedge.backtest.engine import default_config
    from quantedge.backtest.walkforward import run_walk_forward
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    from quantedge.strategies import get_strategy
    values = [json.loads(v) for v in args.values.split(",")]
    try:
        exp_id = run_walk_forward(args.strategy, args.param, values,
                                  base_params=json.loads(args.params) if args.params else None,
                                  symbols=_symbols(args) or None, universe=args.universe,
                                  train_sessions=args.train, test_sessions=args.test,
                                  config=_config_from_args(args, default_config(get_strategy(args.strategy))),
                                  source="cli", notes=args.notes)
    except (RuntimeError, ValueError) as exc:
        print(f"Walk-forward failed (recorded where possible): {exc}")
        return 1
    with session_scope() as s:
        m = s.get(Experiment, exp_id).metrics
    print(f"Walk-forward experiment #{exp_id}: {len(m['folds'])} folds")
    for f in m["folds"]:
        print(f"  OOS {f['out_of_sample'][0]} → {f['out_of_sample'][1]}  chose {args.param}={f['chosen']}  "
              f"net {f['oos_net_return'] * 100:+.1f}%")
    pct = lambda k: f"{m[k] * 100:+.1f}%" if m.get(k) is not None else "n/a"  # noqa: E731
    print(f"Stitched out-of-sample: net {pct('net_return')}  ETF {pct('benchmark_return')}  "
          f"same-stocks B&H {pct('universe_return')}  Sharpe {m.get('sharpe', 0):.2f}")
    return cmd_report(argparse.Namespace(ids=str(exp_id), sweep=False))


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
    """Exit 0 = healthy, 1 = warnings, 2 = errors (usable as a container health check)."""
    from quantedge import health
    checks = health.run_checks(include_worker=args.worker, force_provider=True)
    if args.json:
        print(json.dumps([c.__dict__ for c in checks], indent=2))
    else:
        for c in checks:
            print(f"{'OK  ' if c.ok else ('ERR ' if c.level == 'error' else 'WARN')} {c.key:22s} {c.message}")
    errors = any(not c.ok and c.level == "error" for c in checks)
    warnings = any(not c.ok for c in checks)
    if args.fail_on == "error":           # container health checks: only errors make it unhealthy
        return 2 if errors else 0
    return 2 if errors else (1 if warnings else 0)


def cmd_worker(args) -> int:
    from quantedge.worker import main as worker_main
    argv = (["--once"] if args.once else []) + (["--run", args.run] if args.run else [])
    return worker_main(argv)


def cmd_paper(args) -> int:
    from quantedge import paper
    from quantedge.db import session_scope
    from quantedge.db.models import PaperAccount
    if args.action == "list":
        with session_scope() as s:
            for a in s.query(PaperAccount).order_by(PaperAccount.id):
                sm = paper.summary(a.id)
                print(f"#{a.id:<3d} {a.status:7s} {a.name:28s} {a.strategy_key:18s} {a.timeframe:8s} "
                      f"equity {sm['equity']:,.0f} ({sm['return'] * 100:+.2f}%)  trades {sm['trades']}  "
                      f"last bar {a.last_bar_ts:%Y-%m-%d %H:%M}")
        return 0
    if args.action == "create":
        if not args.experiment or not args.approve:
            print("Needs --experiment ID and --approve (you are explicitly approving paper trading).")
            return 2
        acct = paper.create_account(args.experiment, args.name or "", "human (cli)", args.note or "",
                                    capital=args.capital)
        print(f"Paper account #{acct} created; it trades only bars that arrive from now on.")
        return 0
    if args.action == "step":
        print(paper.step_all())
        return 0
    if args.id is None:
        print("Pass an account id")
        return 2
    paper.set_status(args.id, {"pause": "paused", "resume": "active", "close": "closed"}[args.action],
                     "human (cli)", args.note or "")
    print(f"Account #{args.id}: {args.action}d")
    return 0


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
        p.add_argument("--universe", choices=["us_large", "uk_large", "us_sectors"])
        p.add_argument("--symbols", help="comma-separated, e.g. AAPL,SHEL.L")

    p = sub.add_parser("refresh", help="fetch daily bars, validate, store, scan signals")
    sym_args(p)
    p.add_argument("--years", type=int, default=None)
    p.add_argument("--since", help="back-fill from this date (YYYY-MM-DD); must precede the stored history")
    p.add_argument("--intraday", action="store_true", help="5-minute bars (default universe us_intraday)")
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
    def cost_args(p):
        p.add_argument("--capital", type=float, default=100_000)
        p.add_argument("--risk", type=float, default=0.01, help="fraction of equity risked per trade")
        p.add_argument("--max-positions", type=int, default=5)
        p.add_argument("--spread-bps", type=float, default=None, help="default 5 daily / 10 intraday")
        p.add_argument("--slippage-bps", type=float, default=None, help="default 5 daily / 10 intraday")
        p.add_argument("--commission-bps", type=float, default=None)
        p.add_argument("--no-cash-interest", action="store_true", help="cash earns 0%% (pre-audit behaviour)")
    cost_args(p)
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

    p = sub.add_parser("walkforward", help="walk-forward: re-choose one parameter each fold, stitch out-of-sample")
    p.add_argument("strategy")
    p.add_argument("param")
    p.add_argument("values", help="comma-separated candidates, e.g. 10,20,30")
    sym_args(p)
    p.add_argument("--params", help="JSON of the other parameters")
    p.add_argument("--train", type=int, default=756, help="in-sample sessions per fold (~3 years)")
    p.add_argument("--test", type=int, default=126, help="out-of-sample sessions per fold (~6 months)")
    p.add_argument("--notes")
    cost_args(p)
    p.set_defaults(fn=cmd_walkforward)

    p = sub.add_parser("validate", help="re-test an experiment on the validation (or test) period")
    p.add_argument("experiment", type=int)
    p.add_argument("--period", default="validation", choices=["validation", "test"])
    p.add_argument("--confirm-test", action="store_true")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("report", help="print plain-English reports (Markdown)")
    p.add_argument("--ids", help="comma-separated experiment ids; default all")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("agents", help="AI research agents: status | estimate | run | approve | decline")
    p.add_argument("action", choices=["status", "estimate", "run", "approve", "decline"])
    p.add_argument("id", nargs="?", type=int, help="proposal id for approve/decline")
    p.add_argument("--agents", help="comma-separated subset, e.g. trend,validation")
    p.add_argument("--force", action="store_true", help="run even when evidence is unchanged")
    p.add_argument("--note", help="decision note for approve/decline")
    p.set_defaults(fn=cmd_agents)

    sub.add_parser("status", help="data freshness and configuration").set_defaults(fn=cmd_status)
    p = sub.add_parser("health", help="run health checks (exit 0 ok, 1 warnings, 2 errors)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--worker", action="store_true", help="also require a live worker heartbeat")
    p.add_argument("--fail-on", choices=["warning", "error"], default="warning",
                   help="non-zero exit on warnings (default) or only on errors")
    p.set_defaults(fn=cmd_health)

    p = sub.add_parser("worker", help="run the background worker (scheduled jobs)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--run", help="run one job now: uk_eod_refresh, us_eod_refresh, us_intraday_poll, "
                                 "health_check, research_cycle")
    p.set_defaults(fn=cmd_worker)

    p = sub.add_parser("paper", help="paper trading: list | create | step | pause | resume | close")
    p.add_argument("action", choices=["list", "create", "step", "pause", "resume", "close"])
    p.add_argument("id", nargs="?", type=int)
    p.add_argument("--experiment", type=int, help="validated experiment to paper trade (create)")
    p.add_argument("--approve", action="store_true", help="explicit human approval (create)")
    p.add_argument("--name")
    p.add_argument("--capital", type=float)
    p.add_argument("--note")
    p.set_defaults(fn=cmd_paper)
    p = sub.add_parser("dashboard", help="launch the web dashboard")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8503)))
    p.set_defaults(fn=cmd_dashboard)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from quantedge.config import ConfigError
    from quantedge.paper import PaperError
    try:
        return args.fn(args)
    except (PaperError, ConfigError, KeyError, ValueError) as exc:
        # Expected refusals (unapproved promotion, unknown id, bad parameter): a message, not a traceback.
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"Refused: {msg}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
