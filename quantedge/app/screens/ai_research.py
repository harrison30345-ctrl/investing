"""AI Research: agent status, findings with checked citations, proposals and approvals, activity and spend."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from quantedge.agents import orchestrator
from quantedge.agents.budget import month_to_date
from quantedge.agents.proposals import decide
from quantedge.agents.roster import ALL, SPECIALIST_ORDER
from quantedge.app import data as D
from quantedge.config import get_settings
from quantedge.ui import components as C

s = get_settings()
C.page_header("AI Research", "Agents propose and critique. The backtester produces every number. You approve changes.")

STATUS_STATE = {"completed": "ok", "running": "warn", "queued": "warn", "skipped": "off", "superseded": "off",
                "failed": "err", "refused": "err", "budget_blocked": "err"}
VER_CLASS = {"verified": "qe-pos", "unverified": "qe-neg", "uncited": "qe-warn"}
VER_TEXT = {"verified": "✓ numbers verified", "unverified": "✗ cited numbers don't match the database",
            "uncited": "! no numbers cited"}

tasks = D.agent_tasks(300)
props = D.proposals(500)
mtd = month_to_date()

# ── configuration + run ─────────────────────────────────────────────────────
a, b = st.columns([1.5, 1], gap="small")
with a:
    with C.panel("ai_cfg", "Configuration & budget",
                 C.tag("enabled", "accent") if s.llm_configured else C.tag("disabled", "")):
        pct_used = mtd / s.llm_monthly_budget_usd if s.llm_monthly_budget_usd else 1
        C.kv([("Provider / model", C.esc(f"{s.llm_provider} · {s.llm_model}")),
              ("Effort", C.esc(s.llm_effort)),
              ("Spend this month", f"${mtd:.2f} of ${s.llm_monthly_budget_usd:.2f} ({pct_used:.0%})"),
              ("Per-cycle cap", f"${s.llm_cycle_budget_usd:.2f}"),
              ("Backtests per cycle", f"up to {s.agent_max_experiments} · "
                                      + ("run automatically (train/validation only)" if s.agent_autorun_experiments
                                         else "each needs your approval")),
              ("Retries / timeout", f"{s.agent_max_attempts} attempts · {s.llm_timeout_s:.0f}s per call")])
        if not s.llm_configured:
            st.caption("No AI calls are made. To enable: set QE_LLM_PROVIDER=anthropic and ANTHROPIC_API_KEY in .env, "
                       "then restart the dashboard.")
        st.caption("Spend is estimated from token usage at list prices; your Anthropic console shows the invoice.")
with b:
    with C.panel("ai_run", "Run a research cycle"):
        est = orchestrator.estimate_cycle()
        typical, worst = sum(r["typical_usd"] for r in est), sum(r["max_usd"] for r in est)
        skipping = [r["name"] for r in est if r["will_skip"]]
        st.markdown(f"Estimated cost **≈ \\${typical:.2f}** (worst case \\${worst:.2f}).")   # \\$: not LaTeX
        if skipping:
            st.caption("Will skip (no new evidence since last run): " + ", ".join(skipping))
        running = orchestrator.cycle_running()
        ok = st.checkbox(f"I understand this makes paid API calls (up to \\${min(worst, s.llm_cycle_budget_usd):.2f})",
                         disabled=not s.llm_configured or running is not None, key="ai_ack")
        if st.button("Run research cycle", type="primary", icon=":material/play_arrow:", width="stretch",
                     disabled=not (s.llm_configured and ok) or running is not None):
            with st.status("Research cycle running… agents are called one at a time", expanded=True) as box:
                r = orchestrator.run_cycle("manual")
                box.write(r.detail)
                box.update(label=f"Cycle {r.status}: {r.detail}", state="complete" if r.status == "ok" else "error")
            D.clear()
        if running is not None:
            st.caption(f"A cycle is already running (job #{running.id}).")
        queued = tasks[tasks["status"] == "queued"] if not tasks.empty else tasks
        if not queued.empty:
            st.caption("Queued by events: " + "; ".join(f"{ALL[r.agent].name}: {r.reason}" for r in queued.itertuples()))

tab_agents, tab_findings, tab_props, tab_log = st.tabs(
    ["Agents", "Findings", f"Proposals & approvals ({int((props['status'] == 'awaiting_approval').sum()) if not props.empty else 0})",
     "Activity & spend"])

# ── agents ──────────────────────────────────────────────────────────────────
with tab_agents:
    cols = st.columns(3, gap="small")
    for i, key in enumerate(list(SPECIALIST_ORDER) + ["director"]):
        spec = ALL[key]
        mine = tasks[tasks["agent"] == key] if not tasks.empty else tasks
        last = mine.iloc[0] if not mine.empty else None
        done = mine[mine["status"] == "completed"] if not mine.empty else mine
        with cols[i % 3]:
            state = STATUS_STATE.get(last["status"], "off") if last is not None else "off"
            label = last["status"].replace("_", " ") if last is not None else "never run"
            with C.panel(f"ag_{key}", spec.name, C.pill("", label, state)):
                st.caption(spec.role)
                if done.empty:
                    st.caption("No completed analysis yet.")
                else:
                    t = done.iloc[0]
                    summary = (t["output"] or {}).get("summary", "")
                    st.markdown(summary[:420] + ("…" if len(summary) > 420 else ""))
                    tot = (t["verification"] or {}).get("totals", {})
                    meta = f"{C.ago(t['finished_at'])} · ${t['cost_usd'] or 0:.3f}"
                    if tot:
                        meta += f" · {tot.get('findings', 0)} findings, {tot.get('unverified_findings', 0)} unverified"
                    st.caption(meta)
                if last is not None and last["status"] != "completed" and (last["reason"] or last["error"]):
                    st.caption(f"Latest: {last['reason'] or last['error']}"[:200])

# ── findings ────────────────────────────────────────────────────────────────
with tab_findings:
    done = tasks[(tasks["status"] == "completed")] if not tasks.empty else tasks
    if done.empty:
        C.empty_state("No findings yet", "Agents haven't completed any analysis. Findings appear here with every cited "
                      "number checked against the backtest database.")
    else:
        opts = done["id"].tolist()
        pick = st.selectbox("Report", opts, key="ai_pick",
                            format_func=lambda i: (lambda r: f"#{i} · {ALL[r['agent']].name} · "
                                                             f"{C.fmt_time(r['finished_at'])}")(done.set_index('id').loc[i]))
        t = done.set_index("id").loc[pick]
        out, ver = t["output"] or {}, (t["verification"] or {}).get("findings", [])
        with C.panel("ai_rep", ALL[t["agent"]].name, f"task #{pick} · cycle {t['cycle_id']} · ${t['cost_usd'] or 0:.3f}"):
            st.markdown(out.get("summary", ""))
            if t["agent"] == "director":
                if out.get("next_priorities"):
                    st.markdown("**Next priorities**\n" + "\n".join(f"- {x}" for x in out["next_priorities"]))
                if out.get("notes_for_human"):
                    st.info(out["notes_for_human"])
                # Show what actually happened, not just what the Director asked for: ids that weren't
                # valid candidates, or were over the per-cycle cap, are ignored by the orchestrator.
                actual = props.set_index("id")["status"].to_dict() if not props.empty else {}
                rows = []
                for verb, items in (("selected", out.get("selected_proposals") or []),
                                    ("rejected", out.get("rejected_proposals") or [])):
                    for x in items:
                        pid = x.get("proposal_id")
                        now = actual.get(pid)
                        applied = (verb == "selected" and now in ("selected", "running", "completed", "failed",
                                                                  "awaiting_approval", "approved", "declined")) or \
                                  (verb == "rejected" and now == "not_selected")
                        tag = (f'<span class="{"qe-pos" if verb == "selected" else "qe-muted"}">{verb}</span>'
                               if applied else f'<span class="qe-warn">{verb} → ignored</span>')
                        rows.append((f"Proposal #{pid}" + (f" · now {now.replace('_', ' ')}" if now else " · unknown id"),
                                     x.get("reason", ""), tag))
                C.list_rows(rows)
                st.caption("“Ignored” means the id wasn't an eligible experiment candidate or exceeded the per-cycle cap.")
            for j, f in enumerate(out.get("findings") or []):
                v = ver[j] if j < len(ver) else {"status": "uncited", "citations": []}
                cites = "; ".join(
                    f"#{c['experiment_id']} {c['metric']}={c['value']}"
                    + ("" if c["status"] == "verified" else
                       f" → {c['status']}" + (f" (stored {c['stored']:.4g})" if c.get("stored") is not None else ""))
                    for c in v["citations"])
                C.html_block(f'<div style="margin:10px 0 2px 0;font-size:13px"><b>{C.esc(f.get("title", ""))}</b> '
                             f'<span class="qe-muted">· {C.esc(f.get("confidence", ""))} confidence</span> '
                             f'<span class="{VER_CLASS[v["status"]]}" style="font-size:11.5px">{VER_TEXT[v["status"]]}</span></div>')
                st.markdown(f.get("detail", ""))
                if cites:
                    st.caption("Evidence: " + cites)
            if out.get("limitations"):
                st.markdown("**Limitations the agent acknowledged**\n" + "\n".join(f"- {x}" for x in out["limitations"]))
            mine = props[props["task_id"] == pick] if not props.empty else props
            if not mine.empty:
                st.markdown("**Proposals from this report**")
                C.list_rows([(f"#{p.id} {p.title}", (p.hypothesis or p.rationale or "")[:160],
                              C.esc(p.status.replace("_", " "))) for p in mine.itertuples()])
        with st.expander("Evidence the agent was shown (exact JSON)"):
            st.json(t["evidence"] or {}, expanded=False)

# ── proposals & approvals ───────────────────────────────────────────────────
with tab_props:
    waiting = props[props["status"] == "awaiting_approval"] if not props.empty else props
    with C.panel("ai_wait", "Awaiting your decision", f"{len(waiting)} open"):
        if waiting.empty:
            C.empty_state("Nothing awaiting approval", "Strategy changes, risk-limit changes and paper-trading "
                          "promotions proposed by agents appear here. Nothing changes until you approve it.")
        for p in waiting.itertuples():
            st.markdown(f"**#{p.id} · {p.title}**  \n{C.tag(p.kind.replace('_', ' '), 'demo')} &nbsp;"
                        f"<span class='qe-muted'>from {ALL[p.agent].name if p.agent in ALL else p.agent} · "
                        f"{C.fmt_time(p.created_at)}</span>", unsafe_allow_html=True)
            desc = (p.payload or {}).get("description") or p.hypothesis or ""
            if desc:
                st.markdown(desc)
            if p.rationale:
                st.caption("Rationale: " + p.rationale)
            note = st.text_input("Decision note", key=f"note_{p.id}", placeholder="Why (recorded)")
            c1, c2, _ = st.columns([1, 1, 4])
            with c1:
                if st.button("Approve", key=f"ap_{p.id}", type="primary"):
                    msg = decide(int(p.id), True, note, by="human (dashboard)")
                    D.clear()
                    st.toast(f"#{p.id} {msg}")
                    st.rerun()
            with c2:
                if st.button("Decline", key=f"de_{p.id}"):
                    decide(int(p.id), False, note, by="human (dashboard)")
                    D.clear()
                    st.rerun()
            if p.kind != "experiment":
                st.caption("Approving records your decision. It does not change code, limits or accounts; "
                           "implementing an approved change is a separate, deliberate step.")
            st.divider()
    if not props.empty:
        status_pick = st.multiselect("Status", sorted(props["status"].unique()), placeholder="All statuses")
        view = props[props["status"].isin(status_pick)] if status_pick else props
        st.dataframe(pd.DataFrame({
            "ID": view["id"], "Created": pd.to_datetime(view["created_at"]), "Agent": view["agent"],
            "Kind": view["kind"], "Title": view["title"], "Status": view["status"],
            "Experiment": view["experiment_id"], "Director": view["director_note"].fillna(""),
            "Problems": view["validation_errors"].map(lambda e: "; ".join(e or [])),
            "Decided by": view["decided_by"].fillna(""),
        }), hide_index=True, use_container_width=True, height=320,
            column_config={"Created": st.column_config.DatetimeColumn(format="DD MMM HH:mm"),
                           "Experiment": st.column_config.NumberColumn(format="%d")})

# ── activity & spend ────────────────────────────────────────────────────────
with tab_log:
    calls = D.llm_calls(500)
    if tasks.empty and calls.empty:
        C.empty_state("No agent activity", "Every task, skip, budget block, refusal and model call is logged here.")
    else:
        if not calls.empty:
            c = calls.copy()
            c["month"] = pd.to_datetime(c["started_at"]).dt.to_period("M")
            this = c[c["month"] == pd.Timestamp.utcnow().tz_localize(None).to_period("M")]
            by_agent = this.groupby("agent")["cost_usd"].sum().sort_values(ascending=False)
            C.metric_grid([(ALL[k].name if k in ALL else k, f"${v:.3f}", "") for k, v in by_agent.items()]
                          + [("Calls this month", str(len(this)), ""),
                             ("Tokens in / out", f"{int(this['input_tokens'].sum()):,} / {int(this['output_tokens'].sum()):,}", "")])
        if not tasks.empty:
            st.dataframe(pd.DataFrame({
                "Task": tasks["id"], "Agent": tasks["agent"], "Cycle": tasks["cycle_id"], "Trigger": tasks["trigger"],
                "Status": tasks["status"], "Created": pd.to_datetime(tasks["created_at"]), "Attempts": tasks["attempts"],
                "Cost $": tasks["cost_usd"], "Reason / error": (tasks["reason"].fillna("") + " " + tasks["error"].fillna("")).str.strip(),
            }), hide_index=True, use_container_width=True, height=300,
                column_config={"Created": st.column_config.DatetimeColumn(format="DD MMM HH:mm"),
                               "Cost $": st.column_config.NumberColumn(format="%.4f")})
        if not calls.empty:
            st.dataframe(pd.DataFrame({
                "Call": calls["id"], "Task": calls["task_id"], "Agent": calls["agent"], "Model": calls["model"],
                "Served by": calls["served_model"], "Status": calls["status"], "In": calls["input_tokens"],
                "Out": calls["output_tokens"], "Cost $": calls["cost_usd"], "ms": calls["duration_ms"],
                "Request id": calls["request_id"], "Note": calls["error"].fillna(""),
            }), hide_index=True, use_container_width=True, height=260,
                column_config={"Cost $": st.column_config.NumberColumn(format="%.4f")})
