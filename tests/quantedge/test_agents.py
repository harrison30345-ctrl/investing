"""
Phase 6: agent orchestration with a scripted (offline) model.

These tests never call a real LLM. They check the controls around it: budgets,
retries, refusals, duplicate prevention, citation checking, proposal validation,
the Director's caps and the human approval gate.
"""
from __future__ import annotations

import pytest

from quantedge.agents.llm import LLMTransientError, ScriptedProvider
from quantedge.agents.verify import verify_output

SYMS = ["AAPL", "MSFT", "NVDA", "JPM"]


def specialist(findings=(), experiments=(), changes=()):
    return {"summary": "test summary", "findings": list(findings), "proposed_experiments": list(experiments),
            "proposed_changes": list(changes), "limitations": ["scripted"]}


def exp_prop(period="train", params=(("fast", "10"),), strategy="ema_pullback", universe=",".join(SYMS)):
    return {"strategy_key": strategy, "universe": universe, "period": period,
            "params": [{"name": n, "value": v} for n, v in params], "hypothesis": "h", "rationale": "r",
            "priority": "high"}


def director_selecting_all(system, user):
    import json
    import re
    ids = [int(x) for x in re.findall(r'"proposal_id":(\d+)', user)]
    return {"summary": "director", "selected_proposals": [{"proposal_id": i, "reason": "test"} for i in ids],
            "rejected_proposals": [], "next_priorities": ["validate"], "notes_for_human": "n"}


@pytest.fixture()
def ai_env(isolated_db, monkeypatch):
    from quantedge import config
    monkeypatch.setenv("QE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("QE_LLM_MONTHLY_BUDGET_USD", "10")
    monkeypatch.setenv("QE_LLM_CYCLE_BUDGET_USD", "5")
    config.get_settings.cache_clear()
    from quantedge.data.service import refresh_daily
    refresh_daily(SYMS + ["SPY"], years=6)
    yield
    config.get_settings.cache_clear()


def _set(monkeypatch, **env):
    from quantedge import config
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()


def _run(provider, agents, **kw):
    from quantedge.agents.orchestrator import run_cycle
    return run_cycle("manual", agents=agents, provider=provider, sleep=lambda s: None, **kw)


# ── citation checking ──────────────────────────────────────────────────────

def test_citations_verified_mismatched_and_percent_form():
    metrics = {7: {"net_return": 0.1234, "sharpe": 1.05}}
    out = {"findings": [
        {"title": "ok", "evidence": [{"experiment_id": 7, "metric": "net_return", "value": 0.1234}]},
        {"title": "pct", "evidence": [{"experiment_id": 7, "metric": "net_return", "value": 12.34}]},
        {"title": "wrong", "evidence": [{"experiment_id": 7, "metric": "sharpe", "value": 2.5}]},
        {"title": "ghost", "evidence": [{"experiment_id": 99, "metric": "sharpe", "value": 1.0}]},
        {"title": "none", "evidence": []},
    ]}
    v = verify_output(out, metrics)
    assert [f["status"] for f in v["findings"]] == ["verified", "verified", "unverified", "unverified", "uncited"]
    assert v["totals"]["mismatch"] == 1 and v["totals"]["unknown_experiment"] == 1


# ── cycle behaviour ────────────────────────────────────────────────────────

def test_disabled_ai_makes_no_calls(isolated_db):
    from quantedge.agents.orchestrator import run_cycle
    r = run_cycle("manual")
    assert r.status == "skipped" and "not configured" in r.detail


def test_full_cycle_validates_verifies_selects_and_runs(ai_env):
    from quantedge.backtest.runner import run_experiment
    from quantedge.db import session_scope
    from quantedge.db.models import AgentTask, Experiment, LLMCall, Proposal
    base = run_experiment("ema_pullback", symbols=SYMS, period="train", source="cli")
    with session_scope() as s:
        net = s.get(Experiment, base).metrics["net_return"]
    trend_out = specialist(
        findings=[{"title": "real", "detail": "d", "confidence": "low",
                   "evidence": [{"experiment_id": base, "metric": "net_return", "value": net}]},
                  {"title": "invented", "detail": "d", "confidence": "high",
                   "evidence": [{"experiment_id": base, "metric": "net_return", "value": net + 0.5}]}],
        experiments=[exp_prop(), exp_prop(params=()),          # second duplicates the existing default-params run
                     exp_prop(period="test"),
                     exp_prop(params=(("fast", "15"),))],      # fourth is over the 3-per-agent cap: dropped
        changes=[{"kind": "risk_limit_change", "title": "Cut risk to 0.5%", "description": "d", "rationale": "r"}])
    provider = ScriptedProvider([trend_out, director_selecting_all])
    r = _run(provider, ["trend"])
    assert r.status == "ok" and len(r.experiments) == 1 and len(provider.calls) == 2

    with session_scope() as s:
        task = s.query(AgentTask).filter_by(agent="trend", status="completed").one()
        assert [f["status"] for f in task.verification["findings"]] == ["verified", "unverified"]
        assert task.evidence["experiments"] and task.cost_usd > 0
        props = s.query(Proposal).order_by(Proposal.id).all()
        assert [p.status for p in props] == ["completed", "duplicate", "invalid", "awaiting_approval"], \
            [(p.status, p.validation_errors, p.director_note) for p in props]
        ran = s.query(Proposal).filter_by(status="completed").one()
        e = s.get(Experiment, ran.experiment_id)
        assert e.source == "agent:trend" and e.period == "train" and e.params["fast"] == 10
        assert s.query(LLMCall).count() == 2 and all(c.status == "ok" for c in s.query(LLMCall))
    # the agent's own experiment must not trigger more agent work
    with session_scope() as s:
        assert s.query(AgentTask).filter_by(status="queued").count() == 0 or \
            all("agent" not in (t.reason or "") for t in s.query(AgentTask).filter_by(status="queued"))


def test_unchanged_evidence_is_skipped_without_a_call(ai_env):
    from quantedge.db import session_scope
    from quantedge.db.models import AgentTask
    provider = ScriptedProvider([specialist(), director_selecting_all, specialist(), director_selecting_all])
    _run(provider, ["intraday"])
    assert len(provider.calls) == 2                      # specialist + director
    _run(provider, ["intraday"])
    assert len(provider.calls) == 2                      # nothing new: no calls at all
    with session_scope() as s:
        assert s.query(AgentTask).filter_by(agent="intraday", status="skipped").count() == 1
    _run(provider, ["intraday"], force=True)
    assert len(provider.calls) == 4


def test_budget_blocks_before_calling(ai_env, monkeypatch):
    from quantedge.db import session_scope
    from quantedge.db.models import AgentTask, LLMCall
    _set(monkeypatch, QE_LLM_MONTHLY_BUDGET_USD="0.01")
    provider = ScriptedProvider([specialist()])
    r = _run(provider, ["intraday"])
    assert provider.calls == [] and r.status == "partial"
    with session_scope() as s:
        assert s.query(AgentTask).filter_by(status="budget_blocked").count() == 1
        assert s.query(LLMCall).one().status == "budget_blocked"


def test_transient_error_retried_then_succeeds(ai_env):
    from quantedge.db import session_scope
    from quantedge.db.models import AgentTask, LLMCall
    provider = ScriptedProvider([LLMTransientError("429"), specialist(), director_selecting_all])
    _run(provider, ["intraday"])
    with session_scope() as s:
        t = s.query(AgentTask).filter_by(agent="intraday").order_by(AgentTask.id.desc()).first()
        assert t.status == "completed" and t.attempts == 2
        assert sorted(c.status for c in s.query(LLMCall).filter_by(agent="intraday")) == ["error", "ok"]


def test_retries_are_limited(ai_env):
    from quantedge.db import session_scope
    from quantedge.db.models import AgentTask
    provider = ScriptedProvider([LLMTransientError("x"), LLMTransientError("y"), specialist()])
    _run(provider, ["intraday"])
    with session_scope() as s:
        assert s.query(AgentTask).filter_by(agent="intraday").one().status == "failed"
    assert len(provider.calls) == 2


def test_refusal_recorded(ai_env):
    from quantedge.db import session_scope
    from quantedge.db.models import AgentTask
    _run(ScriptedProvider(["REFUSE"]), ["intraday"])
    with session_scope() as s:
        assert s.query(AgentTask).filter_by(agent="intraday").one().status == "refused"


def test_overlapping_cycles_refused(ai_env):
    from quantedge.db import session_scope
    from quantedge.db.models import JobRun
    with session_scope() as s:
        s.add(JobRun(job="research_cycle", status="running", trigger="manual"))
    r = _run(ScriptedProvider([specialist()]), ["intraday"])
    assert r.status == "skipped" and "already running" in r.detail


# ── proposals and approvals ────────────────────────────────────────────────

def test_autorun_off_requires_human_approval(ai_env, monkeypatch):
    from quantedge.agents.proposals import decide
    from quantedge.db import session_scope
    from quantedge.db.models import Proposal
    _set(monkeypatch, QE_AGENT_AUTORUN="false")
    r = _run(ScriptedProvider([specialist(experiments=[exp_prop()]), director_selecting_all]), ["trend"])
    assert r.experiments == []
    with session_scope() as s:
        p = s.query(Proposal).filter_by(kind="experiment").one()
        assert p.status == "awaiting_approval"
        pid = p.id
    assert decide(pid, True, "go") == "approved and run"
    with session_scope() as s:
        p = s.get(Proposal, pid)
        assert p.status == "completed" and p.experiment_id and p.decided_by == "human"


def test_change_proposals_only_recorded_and_need_awaiting_status(ai_env):
    from quantedge.agents.proposals import decide
    from quantedge.db import session_scope
    from quantedge.db.models import Proposal
    _run(ScriptedProvider([specialist(changes=[
        {"kind": "strategy_change", "title": "ORB spec", "description": "d", "rationale": "r"}]),
        director_selecting_all]), ["intraday"])
    with session_scope() as s:
        pid = s.query(Proposal).one().id
    assert "manual" in decide(pid, True)
    with pytest.raises(ValueError):
        decide(pid, False)


def test_out_of_range_and_unknown_inputs_rejected(ai_env):
    from quantedge.agents.proposals import validate_experiment
    errs, _ = validate_experiment(exp_prop(params=(("fast", "999"),)))
    assert any("outside" in e for e in errs)
    errs, _ = validate_experiment(exp_prop(params=(("leverage", "3"),)))
    assert any("Unknown parameter" in e for e in errs)
    errs, _ = validate_experiment(exp_prop(strategy="secret_sauce"))
    assert any("Unknown strategy" in e for e in errs)
    errs, _ = validate_experiment(exp_prop(universe="AAPL,SHEL.L"))
    assert any("one market" in e for e in errs)
    errs, _ = validate_experiment(exp_prop(universe="GOOGL"))
    assert any("No stored data" in e for e in errs)


def test_validation_proposal_requires_existing_train_run(ai_env):
    from quantedge.db import session_scope
    from quantedge.db.models import Proposal
    _run(ScriptedProvider([specialist(experiments=[exp_prop(period="validation")]), director_selecting_all]),
         ["validation"])
    with session_scope() as s:
        p = s.query(Proposal).one()
        assert p.status == "invalid" and "already run on train" in " ".join(p.validation_errors)


def test_director_cannot_exceed_cap_or_invent_ids(ai_env, monkeypatch):
    from quantedge.db import session_scope
    from quantedge.db.models import Proposal
    _set(monkeypatch, QE_AGENT_MAX_EXPERIMENTS="1")
    def director(system, user):
        out = director_selecting_all(system, user)
        out["selected_proposals"].append({"proposal_id": 4242, "reason": "made up"})
        return out
    out = specialist(experiments=[exp_prop(), exp_prop(params=(("fast", "15"),))])
    r = _run(ScriptedProvider([out, director]), ["trend"])
    assert len(r.experiments) == 1
    with session_scope() as s:
        assert s.query(Proposal).filter_by(status="completed").count() == 1
        assert s.query(Proposal).filter_by(status="proposed").count() == 1   # left for a later cycle


def test_human_experiment_enqueues_one_validation_task(ai_env):
    from quantedge.backtest.runner import run_experiment
    from quantedge.db import session_scope
    from quantedge.db.models import AgentTask
    run_experiment("ema_pullback", symbols=SYMS, period="train", source="dashboard")
    run_experiment("donchian_breakout", symbols=SYMS, period="train", source="dashboard")
    with session_scope() as s:
        q = s.query(AgentTask).filter_by(status="queued").all()
        assert len(q) == 1 and q[0].agent == "validation"


# ── Anthropic adapter wire format (mock HTTP, no network, no cost) ─────────

def _mock_provider(response_json, status=200, capture=None):
    import json as _json
    import httpx
    from quantedge.agents.llm import AnthropicProvider

    def handler(request):
        if capture is not None:
            capture.append(request)
        return httpx.Response(status, json=response_json, headers={"request-id": "req_test123"})
    return AnthropicProvider("sk-test", "claude-opus-5-5", max_tokens=16000, effort="high",
                             http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _message(text, stop="end_turn", model="claude-opus-5-5"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "stop_reason": stop,
            "stop_sequence": None, "content": [{"type": "text", "text": text}] if text is not None else [],
            "usage": {"input_tokens": 5000, "output_tokens": 2000, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}}


def test_anthropic_request_shape_and_parsing():
    import json as _json
    from quantedge.agents.schemas import SPECIALIST
    sent = []
    p = _mock_provider(_message(_json.dumps(specialist())), capture=sent)
    r = p.complete_json("sys", "user", SPECIALIST)
    body = _json.loads(sent[0].content)
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default" and "thinking" not in body and "temperature" not in body
    assert "server-side-fallback-2026-07-01" in sent[0].headers.get("anthropic-beta", "")
    assert r.data["summary"] == "test summary" and r.request_id == "req_test123"
    assert r.cost_usd == pytest.approx((5000 * 4 + 2000 * 20) / 1e6)


def test_anthropic_refusal_truncation_and_errors():
    from quantedge.agents.llm import LLMPermanentError
    from quantedge.agents.schemas import SPECIALIST
    r = _mock_provider(_message(None, stop="refusal")).complete_json("s", "u", SPECIALIST)
    assert r.stop_reason == "refusal" and r.data is None
    r = _mock_provider(_message('{"summary": "cut of', stop="max_tokens")).complete_json("s", "u", SPECIALIST)
    assert r.data is None and any("max_tokens" in n for n in r.notes)
    r = _mock_provider(_message("{}", model="claude-opus-4-8")).complete_json("s", "u", SPECIALIST)
    assert r.served_model == "claude-opus-4-8" and any("fallback" in n for n in r.notes)
    err = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    with pytest.raises(LLMPermanentError):
        _mock_provider(err, status=400).complete_json("s", "u", SPECIALIST)
    with pytest.raises(LLMTransientError):
        _mock_provider({"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
                       status=529).complete_json("s", "u", SPECIALIST)


def test_cycle_only_consumes_queued_work_for_agents_it_runs(ai_env):
    from quantedge.agents.orchestrator import enqueue
    from quantedge.db import session_scope
    from quantedge.db.models import AgentTask
    enqueue("validation", "event", "experiment finished")
    _run(ScriptedProvider([specialist(), director_selecting_all]), ["intraday"])
    with session_scope() as s:
        assert s.query(AgentTask).filter_by(agent="validation").one().status == "queued"
