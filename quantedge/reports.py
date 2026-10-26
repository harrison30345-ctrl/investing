"""
Plain-English strategy reports, built deterministically from stored experiments.

No language model is involved: every sentence is a template filled from
backtest numbers, so a report can't claim anything the engine didn't
measure. The AI agents (Phase 6) may add commentary later, but alongside
these facts, not instead of them.

A "strategy configuration" is one strategy + parameters + universe + costs.
Its report puts the train, validation and (if used) test runs side by side
and judges whether the result held up on data it wasn't developed on.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from .strategies import REGISTRY
from .universe import UNIVERSES

PERIOD_ORDER = ("train", "validation", "walkforward", "test", "full")
MIN_TRADES = 30          # below this, win rate and profit factor are mostly noise
WEAK_SHARPE = 0.5        # below this, a "robust" result is consistently unimpressive
LEVELS = ("good", "info", "caution", "warning")


@dataclass
class Finding:
    level: str           # good | info | caution | warning
    title: str
    detail: str = ""


@dataclass
class StrategyReport:
    key: str
    strategy_key: str
    strategy_name: str
    market: str
    universe_label: str
    params: Dict[str, Any]
    periods: Dict[str, dict]             # period -> latest completed experiment (as dict)
    verdict: str
    verdict_level: str
    summary: str
    findings: List[Finding] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)
    experiment_ids: List[int] = field(default_factory=list)
    failed_runs: int = 0
    test_runs: int = 0
    last_run: Optional[pd.Timestamp] = None


# ── helpers ─────────────────────────────────────────────────────────────────

def pct(v: Optional[float], digits: int = 1, signed: bool = True) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    return f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"


def universe_label(symbols: Iterable[str]) -> str:
    syms = list(symbols)
    for name, members in UNIVERSES.items():
        if members == syms:
            return name
    return ", ".join(syms) if len(syms) <= 4 else f"{len(syms)} symbols"


def config_key(e: dict) -> str:
    return json.dumps([e["strategy_key"], e["market"], list(e["universe"]), e["params"], e["config"]],
                      sort_keys=True, default=str)


def _m(e: Optional[dict], k: str) -> Optional[float]:
    return None if e is None else (e.get("metrics") or {}).get(k)


# ── findings ────────────────────────────────────────────────────────────────

def _period_findings(e: dict, label: str) -> List[Finding]:
    f: List[Finding] = []
    net, bench, excess = _m(e, "net_return"), _m(e, "benchmark_return"), _m(e, "excess_return")
    sharpe, bsharpe = _m(e, "sharpe"), _m(e, "benchmark_sharpe")
    dd, bdd = _m(e, "max_drawdown"), _m(e, "benchmark_max_drawdown")
    trades = int(_m(e, "trade_count") or 0)
    bm = e.get("benchmark") or "the benchmark"

    if excess is not None:
        if excess >= 0:
            f.append(Finding("good", f"Beat {bm} on {label}",
                             f"{pct(net)} vs {pct(bench)} ({pct(excess)} difference)."))
        else:
            f.append(Finding("warning", f"Lagged {bm} on {label}",
                             f"{pct(net)} vs {pct(bench)}. Simply holding the benchmark would have done better."))
    uni, ex_u = _m(e, "universe_return"), _m(e, "excess_vs_universe")
    if ex_u is not None:
        if ex_u >= 0:
            f.append(Finding("good", f"Beat buy-and-hold of its own symbols on {label}",
                             f"{pct(net)} vs {pct(uni)} for holding the same stocks equally weighted."))
        else:
            f.append(Finding("warning", f"Lagged buy-and-hold of its own symbols on {label}",
                             f"{pct(net)} vs {pct(uni)}. Owning the same stocks and doing nothing did better. "
                             "Any gain over the index may come from the stock list, not the rules."))
    lo, hi = _m(e, "sharpe_ci_low"), _m(e, "sharpe_ci_high")
    if lo is not None:
        f.append(Finding("info" if lo > 0 else "caution", "Sharpe 95% confidence interval",
                         f"{lo:.2f} to {hi:.2f} (block bootstrap)."
                         + ("" if lo > 0 else " It includes zero: the risk-adjusted return could be luck.")))
    sb = strict_benchmark(e)
    if sb and sb["ci"][0] is not None:
        clo, chi = sb["ci"]
        f.append(Finding("good" if clo > 0 else "caution", f"Excess return over {sb['label']}",
                         f"95% interval {pct(clo)} to {pct(chi)} a year."
                         + (" Entirely above zero." if clo > 0 else " Includes zero: not a demonstrated edge.")))
    dsr, trials = _m(e, "deflated_sharpe"), _m(e, "trials")
    if dsr is not None:
        f.append(Finding("info" if dsr >= DSR_THRESHOLD else "caution", "Multiple-testing haircut",
                         f"Deflated Sharpe {dsr:.2f} after {int(trials or 1)} tries of this strategy on these symbols "
                         f"(≥ {DSR_THRESHOLD} needed to rule out 'best of many lucky variants')."))
    tmin, tmax = _m(e, "tiebreak_net_min"), _m(e, "tiebreak_net_max")
    if tmin is not None and tmax is not None:
        wide = (tmax - tmin) > max(0.10, 0.25 * abs(_m(e, "tiebreak_net_median") or 0))
        f.append(Finding("caution" if wide else "info", "Depends on which signals got a slot",
                         f"Across {int(_m(e, 'tiebreak_seeds') or 0)} neutral tie-break orders, net return ranged "
                         f"{pct(tmin)} to {pct(tmax)} (median {pct(_m(e, 'tiebreak_net_median'))})."
                         + (" A wide range means the position cap often decides the result." if wide else "")))
    x1, x2, x3 = net, _m(e, "cost_x2_net_return"), _m(e, "cost_x3_net_return")
    if x2 is not None and x1 is not None:
        be = _m(e, "cost_breakeven_multiple")
        fragile = x2 <= 0 < x1 or (x1 > 0 and x2 < 0.5 * x1)
        f.append(Finding("caution" if fragile else "info", "Cost sensitivity",
                         f"Net return {pct(_m(e, 'cost_x0_net_return'))} with no trading costs, {pct(x1)} as tested, "
                         f"{pct(x2)} at 2× and {pct(x3)} at 3×."
                         + (f" Breaks even at about {be:.1f}× the assumed costs." if be is not None else "")
                         + (" Costs decide whether this makes money." if fragile else "")))
    up, down = _m(e, "regime_uptrend_ann"), _m(e, "regime_downtrend_ann")
    if up is not None or down is not None:
        dpct = _m(e, "regime_downtrend_days_pct") or 0.0
        txt = []
        if up is not None:
            txt.append(f"benchmark uptrend days {pct(up)}/yr (benchmark {pct(_m(e, 'regime_uptrend_bench_ann'))})")
        if down is not None:
            txt.append(f"downtrend days {pct(down)}/yr (benchmark {pct(_m(e, 'regime_downtrend_bench_ann'))})")
        rare = dpct < 0.10
        f.append(Finding("caution" if rare else "info", "By market regime",
                         "; ".join(txt) + f". {pct(dpct, 0, signed=False)} of days were in a downtrend."
                         + (" This window barely tested falling markets." if rare else "")))
    cash = _m(e, "avg_cash_rate")
    if cash is not None:
        f.append(Finding("info", "Cash interest",
                         f"Uninvested cash earned {pct(_m(e, 'interest_earned_pct'), signed=False)} of capital; Sharpe "
                         f"ratios are measured above the average cash rate of {pct(cash, signed=False)}."))
    if sharpe is not None and bsharpe is not None:
        better = sharpe > bsharpe
        f.append(Finding("good" if better else "caution", "Risk-adjusted return " + ("higher" if better else "lower")
                         + " than benchmark", f"Sharpe {sharpe:.2f} vs {bsharpe:.2f}."))
    if dd is not None and bdd is not None:
        shallower = dd > bdd
        f.append(Finding("good" if shallower else "caution",
                         "Shallower drawdown than benchmark" if shallower else "Deeper drawdown than benchmark",
                         f"Worst fall {pct(dd)} vs {pct(bdd)}."))
    if trades < MIN_TRADES:
        f.append(Finding("warning", "Too few trades to judge",
                         f"Only {trades} trades. With fewer than {MIN_TRADES}, win rate and profit factor are "
                         "dominated by luck."))
    pf = _m(e, "profit_factor")
    if pf is not None and pf < 1:
        f.append(Finding("warning", "Losing trades outweigh winners",
                         f"Profit factor {pf:.2f}: every 1.00 won was matched by {1 / pf:.2f} lost." if pf > 0 else
                         "Profit factor is zero: no winning trades."))
    costs, net_ret = _m(e, "costs_pct_of_start"), net
    if costs is not None and net_ret is not None:
        gross = net_ret + costs
        if gross > 0 and costs > 0.5 * gross:
            f.append(Finding("caution", "Costs ate most of the edge",
                             f"Trading costs were {pct(costs, signed=False)} of starting capital, over half of the "
                             f"{pct(gross)} gross gain."))
        elif gross <= 0 < costs:
            f.append(Finding("info", "Lost money before costs too",
                             f"Costs ({pct(costs, signed=False)} of capital) made a loss worse but didn't cause it."))
        else:
            f.append(Finding("info", "Cost drag", f"Spread, slippage, commission and stamp duty totalled "
                                                  f"{pct(costs, signed=False)} of starting capital."))
    streak = _m(e, "longest_losing_streak")
    if streak:
        f.append(Finding("info", "Longest losing streak", f"{int(streak)} consecutive losing trades. Ask yourself "
                                                          "whether you'd keep following the rules through that."))
    exp = _m(e, "exposure")
    if exp is not None:
        f.append(Finding("info", "Market exposure", f"On average {pct(exp, 0, signed=False)} of capital was invested."))
    return f


DSR_THRESHOLD = 0.95      # Deflated Sharpe below this: could plausibly be the luckiest of the variants tried


def strict_benchmark(e: dict) -> Optional[dict]:
    """
    The harder of the two baselines for this run: the market ETF, or an equal-weight buy-and-hold of
    the run's own symbols, whichever returned more. Beating only the easier one isn't an edge.
    """
    cands = []
    if _m(e, "benchmark_return") is not None:
        cands.append({"label": e.get("benchmark") or "the benchmark", "ret": _m(e, "benchmark_return"),
                      "excess": _m(e, "excess_return"), "sharpe": _m(e, "benchmark_sharpe"),
                      "ci": (_m(e, "excess_ann_ci_low"), _m(e, "excess_ann_ci_high"))})
    if _m(e, "universe_return") is not None:
        cands.append({"label": "the equal-weight universe", "ret": _m(e, "universe_return"),
                      "excess": _m(e, "excess_vs_universe"), "sharpe": _m(e, "universe_sharpe"),
                      "ci": (_m(e, "excess_universe_ann_ci_low"), _m(e, "excess_universe_ann_ci_high"))})
    return max(cands, key=lambda c: c["ret"]) if cands else None


def measured(e: Optional[dict]) -> bool:
    """Runs from before the audit fixes have no universe baseline or confidence intervals."""
    return e is not None and _m(e, "universe_return") is not None and _m(e, "sharpe_ci_low") is not None


def _verdict(train: Optional[dict], val: Optional[dict], label: str = "validation") -> Tuple[str, str]:
    if val is None:
        if train is None:
            return "No completed runs", "caution"
        return "Not validated: train-period result only", "caution"
    vs, ts = _m(val, "sharpe") or 0.0, _m(train, "sharpe") if train else None
    if (_m(val, "trade_count") or 0) < MIN_TRADES:
        return f"Inconclusive: too few {label} trades", "caution"
    if not measured(val):
        return "Pre-audit run: re-run to measure it against its own universe and with confidence intervals", "caution"
    b = strict_benchmark(val)
    if b["excess"] is not None and b["excess"] < 0 and vs < (b["sharpe"] or 0):
        return f"Failed {label}: lagged {b['label']} out of sample", "warning"
    if ts is not None and ts > 0 and vs < 0.5 * ts:
        return "Degraded out of sample: possible overfitting", "warning"
    if b["excess"] is not None and b["excess"] < 0:
        return f"Mixed: better risk-adjusted, but lower return than {b['label']}", "caution"
    if vs < (b["sharpe"] or 0):
        return f"Mixed: beat {b['label']}'s return, not its risk-adjusted return", "caution"
    lo = b["ci"][0]
    if lo is None or lo <= 0:
        return f"Inconclusive: the edge over {b['label']} isn't statistically distinguishable from zero", "caution"
    dsr = _m(val, "deflated_sharpe")
    if dsr is not None and dsr < DSR_THRESHOLD:
        return "Inconclusive: doesn't survive the haircut for the number of variants tried", "caution"
    return f"Held up on {label} (not proof of future returns)", "good"


def _summary(name: str, uni: str, e: dict, label: str) -> str:
    start = pd.Timestamp(e["start_date"]).strftime("%b %Y") if e.get("start_date") is not None else "?"
    end = pd.Timestamp(e["end_date"]).strftime("%b %Y") if e.get("end_date") is not None else "?"
    pf = _m(e, "profit_factor")
    return (f"On the {label} period ({start} to {end}), {name} on {uni} ({e['market']}) returned "
            f"{pct(_m(e, 'net_return'))} after costs, against {pct(_m(e, 'benchmark_return'))} for "
            f"{e.get('benchmark') or 'the benchmark'}"
            + (f" and {pct(_m(e, 'universe_return'))} for simply holding the same stocks equally weighted"
               if _m(e, 'universe_return') is not None else "")
            + f". The worst peak-to-trough fall was "
            f"{pct(_m(e, 'max_drawdown'))} (benchmark {pct(_m(e, 'benchmark_max_drawdown'))}). It made "
            f"{int(_m(e, 'trade_count') or 0)} trades with a {pct(_m(e, 'win_rate'), 0, signed=False)} win rate and a "
            f"profit factor of {'n/a' if pf is None else f'{pf:.2f}'}. Sharpe ratio {(_m(e, 'sharpe') or 0):.2f}.")


# ── public API ──────────────────────────────────────────────────────────────

def build_reports(experiments: List[dict]) -> List[StrategyReport]:
    """One report per strategy configuration, newest activity first."""
    groups: Dict[str, List[dict]] = {}
    for e in experiments:
        groups.setdefault(config_key(e), []).append(e)

    reports = []
    for key, runs in groups.items():
        runs = sorted(runs, key=lambda r: r["id"])
        done = [r for r in runs if r["status"] == "completed"]
        periods = {}
        for r in done:
            periods[r["period"]] = r            # latest run per period wins
        train, val, test = periods.get("train"), periods.get("validation"), periods.get("test")
        oos_label = "validation"
        if val is None and periods.get("walkforward") is not None:
            # Walk-forward folds are out-of-sample by construction, so they can stand in for validation.
            val, oos_label = periods["walkforward"], "walk-forward"
        head = runs[-1]
        name = REGISTRY[head["strategy_key"]].name if head["strategy_key"] in REGISTRY else head["strategy_key"]
        uni = universe_label(head["universe"])
        verdict, level = _verdict(train, val, oos_label)

        primary_label = oos_label if val else ("train" if train else None)
        primary = val or train
        findings = _period_findings(primary, primary_label) if primary else []
        if train and val:
            ts, vs = _m(train, "sharpe") or 0, _m(val, "sharpe") or 0
            findings.insert(0, Finding("info", "Train → validation",
                                       f"Sharpe {ts:.2f} → {vs:.2f}; net return {pct(_m(train, 'net_return'))} → "
                                       f"{pct(_m(val, 'net_return'))}."))
        if test:
            findings.append(Finding("info", "Final test period",
                                    f"{pct(_m(test, 'net_return'))} vs benchmark {pct(_m(test, 'benchmark_return'))}, "
                                    f"Sharpe {(_m(test, 'sharpe') or 0):.2f}."))
        test_runs = sum(1 for r in runs if r.get("used_test_set"))
        if test_runs > 1:
            findings.append(Finding("caution", "Test set used repeatedly",
                                    f"The untouched period has been run {test_runs} times. Every look makes it less "
                                    "untouched."))
        caveats = list(dict.fromkeys(n for r in done for n in (r.get("bias_notes") or [])))
        reports.append(StrategyReport(
            key=key, strategy_key=head["strategy_key"], strategy_name=name, market=head["market"],
            universe_label=uni, params=head["params"], periods=periods, verdict=verdict, verdict_level=level,
            summary=_summary(name, uni, primary, primary_label) if primary else "No completed runs yet.",
            findings=findings, caveats=caveats, experiment_ids=[r["id"] for r in runs],
            failed_runs=sum(1 for r in runs if r["status"] == "failed"), test_runs=test_runs,
            last_run=pd.Timestamp(head["created_at"]) if head.get("created_at") is not None else None))
    reports.sort(key=lambda r: max(r.experiment_ids), reverse=True)
    return reports


@dataclass
class SweepSummary:
    param: str
    variants: int
    beat_benchmark: int
    best_sharpe: float
    median_sharpe: float
    worst_sharpe: float
    finding: Finding


def sweep_summary(runs: List[dict]) -> Optional[SweepSummary]:
    """How sensitive is the result to the swept parameter?"""
    done = [r for r in runs if r["status"] == "completed"]
    if len(done) < 2:
        return None
    note = (done[0].get("notes") or "")
    param = note.split(" ")[1].split("=")[0] if note.startswith("sweep ") else "parameter"
    sharpes = [_m(r, "sharpe") or 0.0 for r in done]
    beat = sum(1 for r in done if (_m(r, "excess_return") or -1) > 0)
    best, med, worst = max(sharpes), median(sharpes), min(sharpes)
    if beat == 0:
        f = Finding("warning", "No variant beat the benchmark", f"0 of {len(done)} values of {param} outperformed.")
    elif best < WEAK_SHARPE:
        f = Finding("caution", "Weak at every setting",
                    f"Best Sharpe across {len(done)} values of {param} was only {best:.2f} (range {worst:.2f} to "
                    f"{best:.2f}). {beat} of {len(done)} beat the benchmark, but by too little to rely on.")
    elif best > 0 and med < 0.5 * best:
        f = Finding("caution", "Fragile: result depends on the exact setting",
                    f"Best Sharpe {best:.2f} but median {med:.2f} across {len(done)} values of {param}. Picking the "
                    "best one after the fact is a form of overfitting.")
    else:
        f = Finding("good" if beat == len(done) else "info", "Reasonably robust to this parameter",
                    f"{beat} of {len(done)} values of {param} beat the benchmark; Sharpe ranged {worst:.2f} to {best:.2f}.")
    return SweepSummary(param, len(done), beat, best, med, worst, f)


def compare_rows(experiments: List[dict]) -> pd.DataFrame:
    """Metrics for several experiments side by side (one column per experiment)."""
    keys = [("Strategy", None), ("Period", None), ("Window", None), ("Net return", "net_return"),
            ("Benchmark", "benchmark_return"), ("Excess", "excess_return"),
            ("EW universe", "universe_return"), ("Excess vs universe", "excess_vs_universe"), ("CAGR", "cagr"),
            ("Sharpe", "sharpe"), ("Max drawdown", "max_drawdown"), ("Volatility", "volatility"),
            ("Trades", "trade_count"), ("Win rate", "win_rate"), ("Profit factor", "profit_factor"),
            ("Expectancy / trade", "expectancy_pct"), ("Exposure", "exposure"), ("Turnover / yr", "turnover"),
            ("Costs (% capital)", "costs_pct_of_start"), ("Sharpe 95% CI", None), ("Deflated Sharpe", "deflated_sharpe"),
            ("Tie-break range", None), ("Parameters", None)]
    cols = {}
    for e in experiments:
        m = e.get("metrics") or {}
        col = {}
        for label, k in keys:
            if label == "Strategy":
                col[label] = REGISTRY[e["strategy_key"]].name if e["strategy_key"] in REGISTRY else e["strategy_key"]
            elif label == "Period":
                col[label] = f"{e['period']} · {e['market']} · {universe_label(e['universe'])}"
            elif label == "Window":
                col[label] = (f"{pd.Timestamp(e['start_date']):%Y-%m} → {pd.Timestamp(e['end_date']):%Y-%m}"
                              if e.get("start_date") is not None else "")
            elif label == "Sharpe 95% CI":
                col[label] = (f"{m['sharpe_ci_low']:.2f} to {m['sharpe_ci_high']:.2f}"
                              if m.get("sharpe_ci_low") is not None else "n/a")
            elif label == "Tie-break range":
                col[label] = (f"{pct(m['tiebreak_net_min'])} to {pct(m['tiebreak_net_max'])}"
                              if m.get("tiebreak_net_min") is not None else
                              ("no slot contention" if m.get("tiebreak_seeds") == 1 else "n/a"))
            elif label == "Parameters":
                col[label] = ", ".join(f"{a}={b}" for a, b in (e["params"] or {}).items())
            else:
                v = m.get(k)
                if v is None:
                    col[label] = "n/a"
                elif k in ("sharpe", "profit_factor", "deflated_sharpe"):
                    col[label] = f"{v:.2f}"
                elif k == "trade_count":
                    col[label] = f"{int(v)}"
                elif k == "turnover":
                    col[label] = f"{v:.1f}×"
                else:
                    col[label] = pct(v, 1, signed=k not in ("win_rate", "exposure", "volatility", "costs_pct_of_start"))
        cols[f"#{e['id']}"] = col
    return pd.DataFrame(cols)


def report_markdown(r: StrategyReport) -> str:
    icon = {"good": "✓", "info": "·", "caution": "!", "warning": "✗"}
    lines = [f"# {r.strategy_name}: {r.universe_label} ({r.market})", "",
             f"**Verdict:** {r.verdict}", "", r.summary, "", "## Periods", "",
             "| Period | Window | Net | Benchmark | Sharpe | Max DD | Trades |", "|---|---|---|---|---|---|---|"]
    for p in PERIOD_ORDER:
        e = r.periods.get(p)
        if e:
            lines.append(f"| {p} | {pd.Timestamp(e['start_date']):%Y-%m-%d} → {pd.Timestamp(e['end_date']):%Y-%m-%d} | "
                         f"{pct(_m(e, 'net_return'))} | {pct(_m(e, 'benchmark_return'))} | {(_m(e, 'sharpe') or 0):.2f} | "
                         f"{pct(_m(e, 'max_drawdown'))} | {int(_m(e, 'trade_count') or 0)} |")
    lines += ["", "## Findings", ""] + [f"- {icon[f.level]} **{f.title}.** {f.detail}" for f in r.findings]
    lines += ["", "## Parameters", "", "```json", json.dumps(r.params, indent=2), "```"]
    lines += ["", "## Caveats", ""] + [f"- {c}" for c in r.caveats]
    lines += ["", f"Experiments: {', '.join('#' + str(i) for i in r.experiment_ids)}"
              f"{f' ({r.failed_runs} failed)' if r.failed_runs else ''}.",
              "", "_Generated by QuantEdge from stored backtest results. Not investment advice._"]
    return "\n".join(lines)
