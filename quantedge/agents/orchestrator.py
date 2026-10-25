"""
Research cycle orchestration.

One cycle:
  1. Refuse to start if another cycle is still running (no overlapping spend).
  2. For each specialist: build its evidence pack. If the pack is identical to the
     one behind its last completed task, skip it (nothing new to say, no call made).
     Otherwise check the budget, call the model (bounded retries for transient
     errors), verify cited numbers, and validate its proposals.
  3. If there's anything to decide, the Research Director picks which valid
     experiment proposals to run, up to QE_AGENT_MAX_EXPERIMENTS.
  4. Selected experiments run through the normal backtester (train/validation
     only), or wait for approval if QE_AGENT_AUTORUN=false.

Every step writes to agent_tasks / llm_calls / proposals / job_runs, including
skips, budget blocks, refusals and failures.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Dict, List, Optional, Sequence

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..db.models import AgentTask, JobRun, LLMCall, Proposal, utcnow
from . import budget, evidence, proposals
from .llm import LLMPermanentError, LLMProvider, LLMTransientError, estimate_max_cost, get_provider
from .roster import ALL, DIRECTOR, SPECIALIST_ORDER, AgentSpec, system_prompt
from .verify import verify_output

log = logging.getLogger(__name__)


@dataclass
class CycleResult:
    cycle_id: Optional[str]
    status: str
    detail: str
    task_ids: List[int] = field(default_factory=list)
    experiments: List[int] = field(default_factory=list)
    cost_usd: float = 0.0


# ── events ──────────────────────────────────────────────────────────────────

def enqueue(agent: str, trigger: str, reason: str) -> Optional[int]:
    """Queue work for the next cycle. Cheap: no model call happens here. One queued task per agent."""
    if agent not in ALL:
        raise KeyError(agent)
    with session_scope() as s:
        if s.scalars(select(AgentTask).where(AgentTask.agent == agent, AgentTask.status == "queued")).first():
            return None
        t = AgentTask(agent=agent, trigger=trigger, reason=reason, status="queued")
        s.add(t)
        s.flush()
        return t.id


# ── one agent call ──────────────────────────────────────────────────────────

def _user_message(pack: dict) -> str:
    return ("Evidence for this task (JSON, produced by the QuantEdge backtester and database):\n\n"
            + json.dumps(pack, default=str, separators=(",", ":"))
            + "\n\nReturn your analysis in the required JSON structure.")


def _log_call(task_id: int, agent: str, provider: LLMProvider, status: str, result=None, error: str = None) -> float:
    with session_scope() as s:
        call = LLMCall(task_id=task_id, agent=agent, provider=provider.name, model=provider.model, status=status,
                       error=error)
        if result is not None:
            call.served_model = result.served_model
            call.duration_ms = result.duration_ms
            call.input_tokens, call.output_tokens = result.input_tokens, result.output_tokens
            call.cache_read_tokens, call.cache_write_tokens = result.cache_read_tokens, result.cache_write_tokens
            call.cost_usd = result.cost_usd
            call.request_id = result.request_id
            if result.notes:
                call.error = "; ".join(result.notes)
        s.add(call)
    return result.cost_usd if result is not None else 0.0


def _finish(task_id: int, **fields) -> None:
    with session_scope() as s:
        t = s.get(AgentTask, task_id)
        for k, v in fields.items():
            setattr(t, k, v)
        t.finished_at = utcnow()


def run_agent(spec: AgentSpec, pack: dict, provider: LLMProvider, *, cycle_id: str, trigger: str,
              cycle_spent: float, reason: str = "", sleep: Callable[[float], None] = time.sleep) -> AgentTask:
    s_cfg = get_settings()
    system, user = system_prompt(spec), _user_message(pack)
    with session_scope() as s:
        task = AgentTask(agent=spec.key, cycle_id=cycle_id, trigger=trigger, reason=reason or None, status="running",
                         started_at=utcnow(), evidence=pack, evidence_hash=evidence.evidence_hash(pack))
        s.add(task)
        s.flush()
        task_id = task.id

    worst = estimate_max_cost(provider.model, len(system) + len(user), provider.max_tokens)
    decision = budget.check(worst, cycle_spent)
    if not decision.allowed:
        _log_call(task_id, spec.key, provider, "budget_blocked", error=decision.reason)
        _finish(task_id, status="budget_blocked", error=decision.reason)
        return _load(task_id)

    spent, result, last_error = 0.0, None, None
    for attempt in range(1, s_cfg.agent_max_attempts + 1):
        with session_scope() as s:
            s.get(AgentTask, task_id).attempts = attempt
        try:
            result = provider.complete_json(system, user, spec.schema)
            break
        except LLMTransientError as exc:
            last_error = str(exc)
            _log_call(task_id, spec.key, provider, "error", error=last_error)
            if attempt < s_cfg.agent_max_attempts:
                sleep(min(30.0, 2.0 ** attempt))
        except LLMPermanentError as exc:
            last_error = str(exc)
            _log_call(task_id, spec.key, provider, "error", error=last_error)
            break
    if result is None:
        _finish(task_id, status="failed", error=last_error)
        return _load(task_id)

    status = {"refusal": "refusal", "max_tokens": "max_tokens"}.get(result.stop_reason, "ok")
    if status == "ok" and result.data is None:
        status = "error"
    spent += _log_call(task_id, spec.key, provider, status, result)
    if status == "refusal":
        _finish(task_id, status="refused", cost_usd=spent, error="; ".join(result.notes))
        return _load(task_id)
    if result.data is None:
        _finish(task_id, status="failed", cost_usd=spent, error="; ".join(result.notes) or "No output")
        return _load(task_id)

    verification = verify_output(result.data, evidence.known_metrics()) if spec.key != "director" else None
    _finish(task_id, status="completed", output=result.data, verification=verification, cost_usd=spent)
    if spec.key != "director":
        proposals.record_specialist_output(task_id, cycle_id, spec.key, result.data)
    return _load(task_id)


def _load(task_id: int) -> AgentTask:
    with session_scope() as s:
        t = s.get(AgentTask, task_id)
        s.expunge(t)
        return t


# ── director ────────────────────────────────────────────────────────────────

def _director_pack(cycle_id: str, specialist_tasks: List[AgentTask], cycle_spent: float) -> Optional[dict]:
    with session_scope() as s:
        candidates = s.scalars(select(Proposal).where(Proposal.kind == "experiment", Proposal.status == "proposed")
                               .order_by(Proposal.id)).all()
        cand = [{"proposal_id": p.id, "agent": p.agent, "title": p.title, "priority": p.priority,
                 "strategy_key": p.payload.get("strategy_key"), "params": p.payload.get("params"),
                 "universe": p.payload.get("universe_name") or ",".join(p.payload.get("symbols", [])),
                 "period": p.payload.get("period"), "hypothesis": p.hypothesis, "rationale": p.rationale}
                for p in candidates]
        approvals = s.scalars(select(Proposal).where(Proposal.status == "awaiting_approval")).all()
        waiting = [{"proposal_id": p.id, "agent": p.agent, "kind": p.kind, "title": p.title} for p in approvals]
    reports = []
    for t in specialist_tasks:
        if t.status != "completed" or not t.output:
            continue
        ver = (t.verification or {}).get("findings", [])
        reports.append({"agent": t.agent, "summary": t.output.get("summary"),
                        "findings": [{"title": f.get("title"), "confidence": f.get("confidence"),
                                      "citation_check": ver[i]["status"] if i < len(ver) else "n/a"}
                                     for i, f in enumerate(t.output.get("findings") or [])],
                        "limitations": t.output.get("limitations")})
    if not reports and not cand:
        return None
    s_cfg = get_settings()
    return {"specialist_reports": reports, "experiment_candidates": cand,
            "max_experiments_this_cycle": s_cfg.agent_max_experiments,
            "awaiting_human_approval": waiting,
            "budget": {"cycle_spent_usd": round(cycle_spent, 4), "cycle_limit_usd": s_cfg.llm_cycle_budget_usd,
                       "month_spent_usd": round(budget.month_to_date(), 4),
                       "month_limit_usd": s_cfg.llm_monthly_budget_usd}}


def _apply_director(task: AgentTask, limit: int) -> List[int]:
    out = task.output or {}
    selected: List[int] = []
    with session_scope() as s:
        valid = {p.id: p for p in s.scalars(select(Proposal).where(Proposal.kind == "experiment",
                                                                   Proposal.status == "proposed"))}
        for item in out.get("selected_proposals") or []:
            p = valid.get(item.get("proposal_id"))
            if p is None or len(selected) >= limit:
                continue                       # ids not offered, or over the cap, are ignored
            p.status, p.director_note = "selected", item.get("reason")
            selected.append(p.id)
        for item in out.get("rejected_proposals") or []:
            p = valid.get(item.get("proposal_id"))
            if p is not None and p.id not in selected:
                p.status, p.director_note = "not_selected", item.get("reason")
    return selected


# ── cycle ───────────────────────────────────────────────────────────────────

def cycle_running() -> Optional[JobRun]:
    timeout = timedelta(seconds=get_settings().agent_cycle_timeout_s * 2)
    with session_scope() as s:
        job = s.scalars(select(JobRun).where(JobRun.job == "research_cycle", JobRun.status == "running")
                        .order_by(JobRun.id.desc())).first()
        if job and utcnow() - job.started_at < timeout:
            s.expunge(job)
            return job
        if job:   # stale: the process died mid-cycle
            job.status, job.finished_at, job.detail = "failed", utcnow(), "Abandoned (no finish recorded)"
    return None


def run_cycle(trigger: str = "manual", agents: Optional[Sequence[str]] = None,
              provider: Optional[LLMProvider] = None, force: bool = False,
              sleep: Callable[[float], None] = time.sleep) -> CycleResult:
    s_cfg = get_settings()
    provider = provider or get_provider()
    if provider is None:
        with session_scope() as s:
            s.add(JobRun(job="research_cycle", status="skipped", trigger=trigger, finished_at=utcnow(),
                         detail="AI agents disabled (QE_LLM_PROVIDER=none or no API key)"))
        return CycleResult(None, "skipped", "AI agents are not configured")
    running = cycle_running()
    if running is not None:
        return CycleResult(None, "skipped", f"Cycle already running (job #{running.id})")

    cycle_id = uuid.uuid4().hex[:10]
    t0 = time.monotonic()
    with session_scope() as s:
        job = JobRun(job="research_cycle", status="running", trigger=trigger, detail=f"cycle {cycle_id}")
        s.add(job)
        s.flush()
        job_id = job.id
        keys = list(agents) if agents else list(SPECIALIST_ORDER)
        # Only queued work for agents running in this cycle is consumed; the rest stays queued.
        queued = s.scalars(select(AgentTask).where(AgentTask.status == "queued", AgentTask.agent.in_(keys))).all()
        queued_reasons: Dict[str, str] = {}
        for q in queued:
            queued_reasons.setdefault(q.agent, q.reason or "")
            q.status, q.cycle_id, q.finished_at = "superseded", cycle_id, utcnow()
            q.error = f"Handled in cycle {cycle_id}"

    result = CycleResult(cycle_id, "ok", "")
    specialist_tasks: List[AgentTask] = []
    skipped = 0
    for key in keys:
        spec = ALL[key]
        if key == "director":
            continue
        if time.monotonic() - t0 > s_cfg.agent_cycle_timeout_s:
            _skip(key, cycle_id, trigger, "Cycle time limit reached")
            skipped += 1
            continue
        pack = evidence.build(key)
        h = evidence.evidence_hash(pack)
        if not force:
            prev = _last_completed(key)
            if prev is not None and prev.evidence_hash == h:
                _skip(key, cycle_id, trigger, f"No new evidence since task #{prev.id}")
                skipped += 1
                continue
        task = run_agent(spec, pack, provider, cycle_id=cycle_id, trigger=trigger, cycle_spent=result.cost_usd,
                         reason=queued_reasons.get(key, ""), sleep=sleep)
        result.task_ids.append(task.id)
        result.cost_usd += task.cost_usd or 0.0
        specialist_tasks.append(task)

    dpack = _director_pack(cycle_id, specialist_tasks, result.cost_usd)
    selected: List[int] = []
    if dpack is not None and time.monotonic() - t0 <= s_cfg.agent_cycle_timeout_s:
        dtask = run_agent(DIRECTOR, dpack, provider, cycle_id=cycle_id, trigger=trigger,
                          cycle_spent=result.cost_usd, sleep=sleep)
        result.task_ids.append(dtask.id)
        result.cost_usd += dtask.cost_usd or 0.0
        if dtask.status == "completed":
            selected = _apply_director(dtask, s_cfg.agent_max_experiments)

    for pid in selected:
        if s_cfg.agent_autorun_experiments:
            exp_id = proposals.execute(pid)
            if exp_id:
                result.experiments.append(exp_id)
        else:
            with session_scope() as s:
                s.get(Proposal, pid).status = "awaiting_approval"

    done = sum(1 for t in specialist_tasks if t.status == "completed")
    problems = [t for t in specialist_tasks if t.status in ("failed", "budget_blocked", "refused")]
    result.status = "partial" if problems else "ok"
    result.detail = (f"{done} agents reported, {skipped} skipped (no new evidence), {len(problems)} problems, "
                     f"{len(selected)} experiments selected, ${result.cost_usd:.3f} spent")
    with session_scope() as s:
        job = s.get(JobRun, job_id)
        job.status, job.finished_at, job.detail = result.status, utcnow(), f"cycle {cycle_id}: {result.detail}"
    return result


def _skip(agent: str, cycle_id: str, trigger: str, why: str) -> None:
    with session_scope() as s:
        s.add(AgentTask(agent=agent, cycle_id=cycle_id, trigger=trigger, status="skipped", reason=why,
                        finished_at=utcnow()))


def _last_completed(agent: str) -> Optional[AgentTask]:
    with session_scope() as s:
        t = s.scalars(select(AgentTask).where(AgentTask.agent == agent, AgentTask.status == "completed")
                      .order_by(AgentTask.id.desc())).first()
        if t is not None:
            s.expunge(t)
        return t


TYPICAL_OUTPUT_TOKENS = 5000     # thinking + JSON at high effort; a planning figure, not a measurement


def estimate_cycle(agents: Optional[Sequence[str]] = None) -> List[dict]:
    """Per-agent cost estimate for the next cycle, without calling anything."""
    from .llm import CHARS_PER_TOKEN, cost_of
    s_cfg = get_settings()
    rows = []
    for key in (agents or SPECIALIST_ORDER):
        spec = ALL[key]
        pack = evidence.build(key)
        chars = len(system_prompt(spec)) + len(_user_message(pack))
        prev = _last_completed(key)
        unchanged = prev is not None and prev.evidence_hash == evidence.evidence_hash(pack)
        in_tok = int(chars / CHARS_PER_TOKEN)
        rows.append({"agent": key, "name": spec.name, "input_tokens": in_tok, "will_skip": unchanged,
                     "typical_usd": 0.0 if unchanged else cost_of(s_cfg.llm_model, in_tok, TYPICAL_OUTPUT_TOKENS),
                     "max_usd": 0.0 if unchanged else estimate_max_cost(s_cfg.llm_model, chars, s_cfg.llm_max_tokens)})
    rows.append({"agent": "director", "name": DIRECTOR.name, "input_tokens": 3000, "will_skip": False,
                 "typical_usd": cost_of(s_cfg.llm_model, 3000, TYPICAL_OUTPUT_TOKENS),
                 "max_usd": estimate_max_cost(s_cfg.llm_model, 12000, s_cfg.llm_max_tokens)})
    return rows
