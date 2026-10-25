"""AI Research: agent team status. Agents are built in Phase 6."""
from __future__ import annotations

import streamlit as st

from quantedge.config import get_settings
from quantedge.ui import components as C

s = get_settings()
C.page_header("AI Research", "Specialised agents that propose, critique and validate experiments")
C.html_block('<div class="qe-empty"><b>Agents not built yet (Phase 6)</b><p>No agent has run and no AI findings '
             'exist. When built, agents get structured tool results from the backtester and can propose experiments. '
             'They never write metrics themselves, and strategy or risk-limit changes need your approval.</p></div>')
st.write("")
AGENTS = [
    ("Research Director", "Assigns experiments, consolidates findings, prioritises the next task"),
    ("Trend Research", "Trend-following hypotheses (EMA pullback, breakouts)"),
    ("Momentum Research", "Stock selection and momentum variants"),
    ("Intraday Research", "Opening-range and VWAP setups, kept separate"),
    ("Risk Manager", "Challenges exposure, drawdowns, losing streaks and fragility"),
    ("Independent Validation", "Checks methodology, leakage and overfitting"),
]
with C.panel("ai_agents", "Agent team", f"LLM provider: {C.esc(s.llm_provider)} · budget ${s.llm_monthly_budget_usd:.0f}/month"):
    C.list_rows([(n, d, C.pill("", "not built", "off")) for n, d in AGENTS])
