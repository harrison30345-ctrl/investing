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

PERIOD_ORDER = ("train", "validation", "test", "full")
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


def _verdict(train: Optional[dict], val: Optional[dict]) -> Tuple[str, str]:
    if val is None:
        if train is None:
            return "No completed runs", "caution"
        return "Not validated: train-period result only", "caution"
    vs, ts = _m(val, "sharpe") or 0.0, _m(train, "sharpe") if train else None
    v_ex = _m(val, "excess_return")
    if (_m(val, "trade_count") or 0) < MIN_TRADES:
        return "Inconclusive: too few validation trades", "caution"
    if v_ex is not None and v_ex < 0 and vs < (_m(val, "benchmark_sharpe") or 0):
        return "Failed validation: lagged the benchmark out of sample", "warning"
    if ts is not None and ts > 0 and vs < 0.5 * ts:
        return "Degraded out of sample: possible overfitting", "warning"
    if v_ex is not None and v_ex < 0:
        return "Mixed: better risk-adjusted, but lower return than benchmark", "caution"
    if vs < (_m(val, "benchmark_sharpe") or 0):
        return "Mixed: beat the benchmark's return, not its risk-adjusted return", "caution"
    return "Held up on validation (not proof of future returns)", "good"


def _summary(name: str, uni: str, e: dict, label: str) -> str:
    start = pd.Timestamp(e["start_date"]).strftime("%b %Y") if e.get("start_date") is not None else "?"
    end = pd.Timestamp(e["end_date"]).strftime("%b %Y") if e.get("end_date") is not None else "?"
    pf = _m(e, "profit_factor")
    return (f"On the {label} period ({start} to {end}), {name} on {uni} ({e['market']}) returned "
            f"{pct(_m(e, 'net_return'))} after costs, against {pct(_m(e, 'benchmark_return'))} for "
            f"{e.get('benchmark') or 'the benchmark'}. The worst peak-to-trough fall was "
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
        head = runs[-1]
        name = REGISTRY[head["strategy_key"]].name if head["strategy_key"] in REGISTRY else head["strategy_key"]
        uni = universe_label(head["universe"])
        verdict, level = _verdict(train, val)

        primary_label = "validation" if val else ("train" if train else None)
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
            ("Benchmark", "benchmark_return"), ("Excess", "excess_return"), ("CAGR", "cagr"),
            ("Sharpe", "sharpe"), ("Max drawdown", "max_drawdown"), ("Volatility", "volatility"),
            ("Trades", "trade_count"), ("Win rate", "win_rate"), ("Profit factor", "profit_factor"),
            ("Expectancy / trade", "expectancy_pct"), ("Exposure", "exposure"), ("Turnover / yr", "turnover"),
            ("Costs (% capital)", "costs_pct_of_start"), ("Parameters", None)]
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
            elif label == "Parameters":
                col[label] = ", ".join(f"{a}={b}" for a, b in (e["params"] or {}).items())
            else:
                v = m.get(k)
                if v is None:
                    col[label] = "n/a"
                elif k in ("sharpe", "profit_factor"):
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
