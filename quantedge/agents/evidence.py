"""
Evidence packs: the structured data each agent is shown.

Everything here is computed by Python from the database. Agents never query
data themselves, so what an agent saw is stored verbatim on its task and can
be audited later.

Packs contain no timestamps that change between runs. That makes the hash
stable: if nothing new has happened, the hash matches the last completed run
and the orchestrator skips the call instead of paying for the same answer twice.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from ..backtest.engine import BacktestConfig
from ..data.providers import provider_info
from ..db import session_scope
from ..db.models import Experiment, QualityReport, Trade
from ..reports import build_reports, sweep_summary, universe_label
from ..strategies import REGISTRY

METRIC_KEYS = ("net_return", "benchmark_return", "excess_return", "cagr", "sharpe", "benchmark_sharpe",
               "max_drawdown", "benchmark_max_drawdown", "volatility", "trade_count", "win_rate", "profit_factor",
               "expectancy_pct", "avg_win_pct", "avg_loss_pct", "exposure", "turnover", "costs_pct_of_start",
               "longest_losing_streak", "universe_return", "excess_vs_universe", "sharpe_ci_low", "sharpe_ci_high",
               "excess_ann_ci_low", "excess_ann_ci_high", "excess_universe_ann_ci_low", "excess_universe_ann_ci_high",
               "deflated_sharpe", "trials", "tiebreak_net_min", "tiebreak_net_max", "entries_skipped_capacity")
MAX_EXPERIMENTS = 40

METHODOLOGY = {
    "fills": "Signals evaluated on bar t close; orders fill at bar t+1 open. Stop/target checked on later bars' "
             "high/low; gaps fill at the open; if one bar hits both, the stop is assumed first.",
    "prices": "Dividend/split-adjusted daily OHLC. UK prices converted from pence to pounds.",
    "costs": "Half-spread + slippage on every fill; optional commission; 0.5% UK stamp duty on UK buys.",
    "sizing": "Trend: fixed-fractional risk per trade capped by max position size. Momentum: equal weight top N.",
    "splits": "Chronological 60% train / 20% validation / 20% test after indicator warm-up. Test is locked; "
              "agents may never run it.",
    "units": "All returns, drawdowns, rates and exposures are fractions (0.12 means 12%). Sharpe is annualised, rf=0.",
    "baselines": "benchmark_return = market ETF (total return); universe_return = equal-weight buy-and-hold of the "
                 "run's own symbols. The universe is today's large caps, so beating the ETF but not the universe "
                 "is not evidence of skill.",
    "uncertainty": "*_ci_low/high are 95% block-bootstrap intervals; deflated_sharpe is the probability the Sharpe "
                   "is real after `trials` attempts on the same strategy and symbols (>= 0.95 needed).",
    "known_biases": ["Universes are today's large caps (survivorship bias).",
                     "Yahoo data is not point-in-time; adjusted history is revised after dividends/splits."],
}


def _round(v):
    if isinstance(v, float):
        return round(v, 4)
    return v


def load_experiments() -> List[dict]:
    with session_scope() as s:
        rows = s.scalars(select(Experiment).where(Experiment.source != "timing").order_by(Experiment.id)).all()
        return [{c.name: getattr(r, c.name) for c in Experiment.__table__.columns} for r in rows]


def digest(e: dict) -> dict:
    strat = REGISTRY.get(e["strategy_key"])
    m = e.get("metrics") or {}
    d = {
        "id": e["id"], "strategy_key": e["strategy_key"], "family": strat.family if strat else "unknown",
        "market": e["market"], "universe": universe_label(e["universe"]), "period": e["period"],
        "status": e["status"], "params": e["params"], "source": e["source"],
        "window": [str(e["start_date"])[:10], str(e["end_date"])[:10]] if e.get("start_date") else None,
        "benchmark": e.get("benchmark"),
    }
    if e.get("parent_id"):
        d["retest_of"] = e["parent_id"]
    if e.get("batch"):
        d["sweep_batch"] = e["batch"]
        d["sweep_note"] = e.get("notes")
    if e.get("used_test_set"):
        d["used_test_set"] = True
    if e["status"] == "completed":
        d["metrics"] = {k: _round(m.get(k)) for k in METRIC_KEYS if m.get(k) is not None}
    elif e["status"] == "failed":
        d["error"] = (e.get("error") or "").split("\n")[0][:200]
    return d


def strategy_catalogue(family: Optional[str] = None) -> List[dict]:
    out = []
    for s in REGISTRY.values():
        if family and s.family != family:
            continue
        out.append({"key": s.key, "name": s.name, "family": s.family, "description": s.description,
                    "default_entry_rule": s.entry_rule(s.defaults()), "default_exit_rule": s.exit_rule(s.defaults()),
                    "params": [{"name": p.name, "kind": p.kind, "default": p.default, "min": p.min, "max": p.max,
                                "choices": list(p.choices) or None, "help": p.help} for p in s.params]})
    return out


def data_coverage() -> dict:
    from ..config import get_settings
    from ..data import repository as repo
    pkey = get_settings().data_provider
    rows = [r for r in repo.freshness(pkey) if r.last_bar is not None]
    info = provider_info(pkey)
    return {"provider": info.name, "latency": info.latency, "is_demo": info.is_demo,
            "symbols_stored": len(rows),
            "latest_bar": max((r.last_bar for r in rows), default=None).strftime("%Y-%m-%d") if rows else None,
            "symbols_with_quality_issues": sorted(r.symbol for r in rows if r.quality_ok is False),
            "universes": {"us_large": "20 US large caps", "uk_large": "20 UK large caps"},
            }


def _family_pack(family: str, exps: List[dict]) -> dict:
    mine = [e for e in exps if REGISTRY.get(e["strategy_key"]) and REGISTRY[e["strategy_key"]].family == family]
    completed = [e for e in mine if e["status"] == "completed"][-MAX_EXPERIMENTS:]
    reports = build_reports(mine)
    sweeps = []
    for batch in sorted({e["batch"] for e in mine if e.get("batch")}):
        sm = sweep_summary([e for e in mine if e.get("batch") == batch])
        if sm:
            sweeps.append({"batch": batch, "param": sm.param, "variants": sm.variants, "beat_benchmark": sm.beat_benchmark,
                           "best_sharpe": round(sm.best_sharpe, 3), "median_sharpe": round(sm.median_sharpe, 3),
                           "finding": sm.finding.title})
    return {
        "strategies": strategy_catalogue(family),
        "experiments": [digest(e) for e in completed],
        "failed_experiments": [digest(e) for e in mine if e["status"] == "failed"][-10:],
        "configuration_verdicts": [{"strategy_key": r.strategy_key, "market": r.market, "universe": r.universe_label,
                                    "params": r.params, "verdict": r.verdict,
                                    "experiment_ids": r.experiment_ids,
                                    "findings": [f"{f.level}: {f.title}" for f in r.findings]} for r in reports[:25]],
        "sweeps": sweeps,
    }


def _concentration(exp_id: int) -> Optional[dict]:
    with session_scope() as s:
        rows = s.execute(select(Trade.symbol, func.sum(Trade.net_pnl)).where(Trade.experiment_id == exp_id)
                         .group_by(Trade.symbol)).all()
    if not rows:
        return None
    total = sum(abs(v) for _, v in rows) or 1.0
    top = max(rows, key=lambda r: abs(r[1]))
    return {"top_symbol": top[0], "share_of_abs_pnl": round(abs(top[1]) / total, 3), "symbols_traded": len(rows)}


def build(agent_key: str) -> Dict[str, Any]:
    exps = load_experiments()
    base = {"methodology": METHODOLOGY, "data": data_coverage()}
    if agent_key == "trend":
        return {**base, **_family_pack("trend", exps)}
    if agent_key == "momentum":
        return {**base, **_family_pack("momentum", exps)}
    if agent_key == "intraday":
        from ..data import repository as repo
        from ..data.providers import provider_info as pinfo
        from ..config import get_settings
        info = pinfo()
        stored = [r for r in repo.freshness(get_settings().data_provider, interval="5m") if r.last_bar is not None]
        return {**base, **_family_pack("intraday", exps),
                "intraday_data": {"provider": info.name, "interval": "5m", "max_history_days": info.max_intraday_days,
                                  "symbols_stored": sorted(r.symbol for r in stored),
                                  "universe_for_experiments": "us_intraday"},
                "constraint": "Intraday history is ~60 days (a few dozen sessions), so every result is a small "
                              "sample. US only. Positions are always closed at the session close."}
    if agent_key == "risk":
        completed = [e for e in exps if e["status"] == "completed"][-MAX_EXPERIMENTS:]
        rows = []
        for e in completed:
            d = digest(e)
            d["pnl_concentration"] = _concentration(e["id"])
            rows.append(d)
        cfg = BacktestConfig()
        return {**base, "current_default_risk_limits": {"risk_per_trade": cfg.risk_per_trade,
                                                        "max_position_pct": cfg.max_position_pct,
                                                        "max_positions": cfg.max_positions,
                                                        "leverage": "none", "shorting": "none"},
                "experiments": rows}
    if agent_key == "validation":
        reports = build_reports(exps)
        test_use = Counter(e["strategy_key"] for e in exps if e.get("used_test_set"))
        with session_scope() as s:
            bad_quality = s.scalar(select(func.count(func.distinct(QualityReport.symbol)))
                                   .where(QualityReport.ok.is_(False))) or 0
        return {**base,
                "configurations": [{"strategy_key": r.strategy_key, "market": r.market, "universe": r.universe_label,
                                    "params": r.params, "verdict": r.verdict, "experiment_ids": r.experiment_ids,
                                    "test_runs": r.test_runs,
                                    "periods": {p: digest(e) for p, e in r.periods.items()}} for r in reports[:30]],
                "sweeps": [s for f in ("trend", "momentum") for s in _family_pack(f, exps)["sweeps"]],
                "test_set_runs_by_strategy": dict(test_use),
                "symbols_with_failed_quality_checks": bad_quality}
    raise KeyError(agent_key)


def evidence_hash(pack: Dict[str, Any]) -> str:
    """Hash of the substance. A new daily bar alone isn't new research evidence, so latest_bar is excluded."""
    pack = json.loads(json.dumps(pack, default=str))
    pack.get("data", {}).pop("latest_bar", None)
    return hashlib.sha256(json.dumps(pack, sort_keys=True).encode()).hexdigest()


def known_metrics(exps: Optional[List[dict]] = None) -> Dict[int, dict]:
    """experiment id → stored metrics, for checking numbers agents cite."""
    exps = exps if exps is not None else load_experiments()
    return {e["id"]: (e.get("metrics") or {}) for e in exps}
