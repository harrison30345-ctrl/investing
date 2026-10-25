"""
Spend guard. Every LLM call is checked *before* it's made, against its worst-case
cost (whole prompt plus every allowed output token):

  * month-to-date spend + worst case must stay within QE_LLM_MONTHLY_BUDGET_USD
  * this cycle's spend + worst case must stay within QE_LLM_CYCLE_BUDGET_USD

A blocked call is recorded (status budget_blocked), so the UI can show why an
agent didn't run. Spend is summed from our own call log, which uses list prices;
check the provider console for the invoice.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select

from ..config import get_settings
from ..db import session_scope
from ..db.models import LLMCall


def month_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.utcnow()
    return datetime(now.year, now.month, 1)


def spend_since(since: datetime) -> float:
    with session_scope() as s:
        return float(s.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
                              .where(LLMCall.started_at >= since)) or 0.0)


def month_to_date() -> float:
    return spend_since(month_start())


@dataclass
class BudgetDecision:
    allowed: bool
    reason: str
    month_spent: float
    month_limit: float
    cycle_spent: float
    cycle_limit: float
    worst_case: float


def check(worst_case: float, cycle_spent: float) -> BudgetDecision:
    s = get_settings()
    mtd = month_to_date()
    if mtd + worst_case > s.llm_monthly_budget_usd:
        return BudgetDecision(False, f"Monthly budget: ${mtd:.2f} spent of ${s.llm_monthly_budget_usd:.2f}; this call "
                                     f"could cost up to ${worst_case:.2f}", mtd, s.llm_monthly_budget_usd,
                              cycle_spent, s.llm_cycle_budget_usd, worst_case)
    if cycle_spent + worst_case > s.llm_cycle_budget_usd:
        return BudgetDecision(False, f"Cycle budget: ${cycle_spent:.2f} spent of ${s.llm_cycle_budget_usd:.2f}; this "
                                     f"call could cost up to ${worst_case:.2f}", mtd, s.llm_monthly_budget_usd,
                              cycle_spent, s.llm_cycle_budget_usd, worst_case)
    return BudgetDecision(True, "ok", mtd, s.llm_monthly_budget_usd, cycle_spent, s.llm_cycle_budget_usd, worst_case)
