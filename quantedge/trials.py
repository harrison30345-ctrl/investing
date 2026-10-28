"""
Project-wide trial accounting (Phase Four).

Two different counts, for two different jobs. They are not interchangeable.

1. Per-universe trials (`metrics["trials"]`, set when an experiment runs; see
   backtest.runner._trial_stats). Distinct parameter sets of one strategy on one market and one
   symbol set. This is the number fed into the Deflated Sharpe Ratio, because the DSR asks "is this
   the best of N tries *on this data*?". Stored values are never rewritten.

2. Project-wide configurations (this module). Everything the project has examined, across
   strategies and universes, computed fresh from the experiment records. It's context for how
   much searching sits behind any one result. It is not a DSR input: different universes are
   different data, so the DSR's assumptions don't hold across them.

Counting rules for the project-wide count:
  * A configuration is (strategy, parameters, market, symbol set). Walk-forward runs contribute
    one configuration per candidate value they chose between.
  * The same configuration run on several periods (train, validation, re-runs after a fix) counts once.
  * The same rule on a different universe counts again: it's a new try on new data.
  * Failed runs count: they were attempted.
  * Not counted: engine timing runs (source "timing") and synthetic demo data (provider "demo").
  * Hypothesis families = distinct strategies (rules). Parameter values and filter switches
    (e.g. regime_filter) are configurations within a family, not new families.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

EXCLUDED_SOURCES = {"timing"}
EXCLUDED_PROVIDERS = {"demo"}


@dataclass
class TrialCount:
    configurations: int
    families: int
    experiments: int
    by_strategy: Dict[str, int] = field(default_factory=dict)

    def describe(self) -> str:
        return (f"{self.configurations} distinct configurations across {self.families} strategy "
                f"famil{'y' if self.families == 1 else 'ies'} ({self.experiments} experiments)")


def configurations(e: dict) -> List[str]:
    """Configuration keys one experiment examined."""
    params = dict(e.get("params") or {})
    wf = params.pop("walk_forward", None)
    variants = [params]
    if wf:
        variants = [{**params, wf["param"]: v} for v in wf["values"]]
    base = {"strategy": e["strategy_key"], "market": e.get("market"), "symbols": sorted(e.get("universe") or [])}
    return [json.dumps({**base, "params": v}, sort_keys=True, default=str) for v in variants]


def project_trials(experiments: Iterable[dict], upto_id: Optional[int] = None) -> TrialCount:
    """Count over experiment dicts (columns of the experiments table), optionally only ids <= upto_id."""
    seen, families, n = set(), {}, 0
    for e in experiments:
        if upto_id is not None and e["id"] > upto_id:
            continue
        if e.get("source") in EXCLUDED_SOURCES or e.get("data_provider") in EXCLUDED_PROVIDERS:
            continue
        n += 1
        for c in configurations(e):
            if c not in seen:
                seen.add(c)
                families[e["strategy_key"]] = families.get(e["strategy_key"], 0) + 1
    return TrialCount(len(seen), len(families), n, dict(sorted(families.items())))


def project_trials_from_db(upto_id: Optional[int] = None) -> TrialCount:
    from .db import session_scope
    from .db.models import Experiment
    cols = ("id", "strategy_key", "params", "market", "universe", "source", "data_provider")
    with session_scope() as s:
        rows = [{c: getattr(x, c) for c in cols} for x in s.query(Experiment)]
    return project_trials(rows, upto_id)
