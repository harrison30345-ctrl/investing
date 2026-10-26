"""
Agent proposals: deterministic validation, execution and human decisions.

Flow for an experiment proposal:
    proposed ──validate──▶ invalid | duplicate | proposed
    proposed ──Research Director──▶ selected | not_selected
    selected ──autorun on──▶ running ──▶ completed | failed
    selected ──autorun off──▶ awaiting_approval ──human──▶ approved (then runs) | declined

Strategy changes, risk-limit changes and paper-trading promotions always go to
awaiting_approval. Approving one records the decision. It doesn't change any code,
limit or account: implementing it is deliberate human work. Paper trading arrives in Phase 7.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from ..backtest.engine import default_config
from ..backtest.runner import rerun_on_period, run_experiment
from ..config import get_settings
from ..data import repository as repo
from ..db import session_scope
from ..db.models import Experiment, Proposal, utcnow
from ..markets import MARKETS
from ..strategies import REGISTRY
from ..universe import CATALOGUE, UNIVERSES
from ..universe import get as catalogue_get

APPROVAL_KINDS = ("strategy_change", "risk_limit_change", "promote_to_paper")


def _cast(spec, raw: str) -> Any:
    raw = str(raw).strip()
    if spec.kind == "bool":
        if raw.lower() in ("true", "1", "yes"):
            return True
        if raw.lower() in ("false", "0", "no"):
            return False
        raise ValueError(f"{spec.name} must be true/false, got {raw!r}")
    if spec.kind == "int":
        f = float(raw)
        if f != int(f):
            raise ValueError(f"{spec.name} must be a whole number, got {raw!r}")
        return int(f)
    if spec.kind == "float":
        return float(raw)
    return type(spec.default)(raw)


def _symbols(universe: str) -> Tuple[Optional[str], List[str]]:
    u = universe.strip()
    if u in UNIVERSES:
        return u, list(UNIVERSES[u])
    return None, [s.strip().upper() for s in u.split(",") if s.strip()]


def _config_key(strategy_key: str, params: dict, symbols: List[str], period: str) -> str:
    return json.dumps([strategy_key, params, symbols, period, default_config(REGISTRY[strategy_key]).to_dict()],
                      sort_keys=True)


def _existing(session, strategy_key: str, params: dict, symbols: List[str], period: str) -> Optional[int]:
    default_cfg = default_config(REGISTRY[strategy_key]).to_dict()
    for e in session.scalars(select(Experiment).where(Experiment.strategy_key == strategy_key,
                                                      Experiment.period == period,
                                                      Experiment.status == "completed")):
        if e.params == params and list(e.universe) == symbols and e.config == default_cfg:
            return e.id
    return None


def validate_experiment(payload: dict) -> Tuple[List[str], dict]:
    """Returns (errors, normalised payload). Normalised payload is what would actually run."""
    errors: List[str] = []
    key = payload.get("strategy_key", "")
    strat = REGISTRY.get(key)
    if strat is None:
        return [f"Unknown strategy {key!r}. Known: {', '.join(REGISTRY)}"], payload
    period = payload.get("period")
    if period not in ("train", "validation"):
        errors.append(f"Period {period!r} not allowed for agents (train or validation only)")

    params: Dict[str, Any] = {}
    for item in payload.get("params") or []:
        spec = next((p for p in strat.params if p.name == item.get("name")), None)
        if spec is None:
            errors.append(f"Unknown parameter {item.get('name')!r} for {key}")
            continue
        try:
            params[spec.name] = _cast(spec, item.get("value"))
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
    try:
        resolved = strat.resolve(params)
    except ValueError as exc:
        errors.append(str(exc))
        resolved = strat.defaults()

    uname, symbols = _symbols(payload.get("universe", ""))
    if not symbols:
        errors.append("No symbols given")
    unknown = [s for s in symbols if s not in CATALOGUE]
    if unknown:
        errors.append(f"Symbols outside the catalogue: {', '.join(unknown)}")
    markets = {catalogue_get(s).market for s in symbols}
    if len(markets) > 1:
        errors.append("Symbols span UK and US; one market per backtest")
    market = markets.pop() if len(markets) == 1 else "US"
    intraday = strat.timeframe == "intraday"
    if intraday and market != "US":
        errors.append("Intraday strategies are US-only")
    pkey = get_settings().data_provider
    stored = {r.symbol for r in repo.freshness(pkey, interval="5m" if intraday else "1d") if r.last_bar is not None}
    missing = [s for s in symbols + [MARKETS[market].benchmark] if s not in stored]
    if missing:
        errors.append(f"No stored data for {', '.join(missing[:6])} (agents cannot fetch data)")

    normalised = {"strategy_key": key, "params": resolved, "symbols": symbols, "universe_name": uname,
                  "period": period, "market": market}
    return errors, normalised


def record_specialist_output(task_id: int, cycle_id: str, agent: str, output: dict) -> List[int]:
    ids: List[int] = []
    seen = set()
    with session_scope() as s:
        for p in (output.get("proposed_experiments") or [])[:3]:
            errors, norm = validate_experiment(p)
            status, note = "proposed", None
            if not errors:
                ck = _config_key(norm["strategy_key"], norm["params"], norm["symbols"], norm["period"])
                dup = _existing(s, norm["strategy_key"], norm["params"], norm["symbols"], norm["period"])
                pending = s.scalars(select(Proposal).where(Proposal.kind == "experiment",
                                                           Proposal.status.in_(("proposed", "selected", "running",
                                                                                "awaiting_approval")))).all()
                if dup is not None:
                    status, note = "duplicate", f"Already run as experiment #{dup}"
                elif ck in seen or any(_config_key(x.payload["strategy_key"], x.payload["params"], x.payload["symbols"],
                                                   x.payload["period"]) == ck for x in pending if x.payload):
                    status, note = "duplicate", "Same experiment already proposed"
                elif norm["period"] == "validation" and _existing(
                        s, norm["strategy_key"], norm["params"], norm["symbols"], "train") is None:
                    errors.append("Validation re-tests are only for configurations already run on train")
                seen.add(ck)
            prop = Proposal(task_id=task_id, cycle_id=cycle_id, agent=agent, kind="experiment",
                            title=f"{norm.get('strategy_key')} · {p.get('universe')} · {p.get('period')}",
                            payload=norm if not errors else {"raw": p, "normalised": norm},
                            hypothesis=p.get("hypothesis"), rationale=p.get("rationale"),
                            priority=p.get("priority") or "medium",
                            status="invalid" if errors else status, validation_errors=errors,
                            director_note=note)
            s.add(prop)
            s.flush()
            ids.append(prop.id)
        for c in (output.get("proposed_changes") or [])[:5]:
            if c.get("kind") not in APPROVAL_KINDS:
                continue
            prop = Proposal(task_id=task_id, cycle_id=cycle_id, agent=agent, kind=c["kind"],
                            title=(c.get("title") or c["kind"])[:200], payload={"description": c.get("description")},
                            rationale=c.get("rationale"), status="awaiting_approval", validation_errors=[])
            s.add(prop)
            s.flush()
            ids.append(prop.id)
    return ids


def execute(proposal_id: int) -> Optional[int]:
    """Run a selected/approved experiment proposal. Returns the experiment id (failures are recorded too)."""
    with session_scope() as s:
        p = s.get(Proposal, proposal_id)
        if p is None or p.kind != "experiment" or p.status not in ("selected", "approved"):
            raise ValueError(f"Proposal #{proposal_id} is not runnable (status {p.status if p else 'missing'})")
        p.status = "running"
        norm, agent, hyp = dict(p.payload), p.agent, (p.hypothesis or "")[:160]
    exp_id = None
    try:
        if norm["period"] == "validation":
            with session_scope() as s:
                parent = _existing(s, norm["strategy_key"], norm["params"], norm["symbols"], "train")
            exp_id = rerun_on_period(parent, "validation", source=f"agent:{agent}")
        else:
            exp_id = run_experiment(norm["strategy_key"], norm["params"], symbols=norm["symbols"],
                                    universe=norm["universe_name"], period="train",
                                    config=default_config(REGISTRY[norm["strategy_key"]]),
                                    source=f"agent:{agent}", notes=f"proposal #{proposal_id}: {hyp}")
        status = "completed"
    except Exception as exc:  # recorded on the experiment row by the runner
        status = "failed"
        with session_scope() as s:
            last = s.scalars(select(Experiment).where(Experiment.source == f"agent:{agent}")
                             .order_by(Experiment.id.desc()).limit(1)).first()
            exp_id = last.id if last is not None else None
        err = str(exc)
    with session_scope() as s:
        p = s.get(Proposal, proposal_id)
        p.status, p.experiment_id = status, exp_id
        if status == "failed":
            p.validation_errors = list(p.validation_errors or []) + [f"Run failed: {err}"]
    return exp_id


def decide(proposal_id: int, approve: bool, note: str = "", by: str = "human") -> str:
    """Human decision on a proposal awaiting approval. Agents can't call this."""
    with session_scope() as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise KeyError(f"No proposal #{proposal_id}")
        if p.status != "awaiting_approval":
            raise ValueError(f"Proposal #{proposal_id} is {p.status}, not awaiting approval")
        p.status = "approved" if approve else "declined"
        p.decided_by, p.decided_at, p.decision_note = by, utcnow(), note or None
        kind = p.kind
    if approve and kind == "experiment":
        execute(proposal_id)
        return "approved and run"
    return "approved (recorded; implementation is manual)" if approve else "declined"
