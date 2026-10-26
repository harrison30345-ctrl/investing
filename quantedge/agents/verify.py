"""
Check every number an agent cites against the stored backtest results.

A citation is {experiment_id, metric, value}. It's verified when the experiment
exists, the metric exists, and the value matches the stored one. Matching allows
rounding, and also accepts the percentage form, e.g. 12.3 for 0.123. Findings
with no citations are marked "uncited", not "verified".
"""
from __future__ import annotations

from typing import Dict, List

REL_TOL = 0.02
ABS_TOL = 0.006


def _matches(cited: float, stored: float) -> bool:
    for candidate in (stored, stored * 100):
        if abs(cited - candidate) <= max(ABS_TOL, REL_TOL * abs(candidate)):
            return True
    return False


def check_citation(c: dict, metrics: Dict[int, dict]) -> dict:
    eid, metric, value = c.get("experiment_id"), c.get("metric"), c.get("value")
    if eid not in metrics:
        return {**c, "status": "unknown_experiment"}
    stored = metrics[eid].get(metric)
    if stored is None:
        return {**c, "status": "unknown_metric"}
    try:
        ok = _matches(float(value), float(stored))
    except (TypeError, ValueError):
        ok = False
    return {**c, "status": "verified" if ok else "mismatch", "stored": stored}


def verify_output(output: dict, metrics: Dict[int, dict]) -> dict:
    """Per-finding verdicts plus totals. Never edits the agent's text."""
    findings: List[dict] = []
    totals = {"verified": 0, "mismatch": 0, "unknown_experiment": 0, "unknown_metric": 0}
    for i, f in enumerate(output.get("findings") or []):
        checks = [check_citation(c, metrics) for c in (f.get("evidence") or [])]
        for ch in checks:
            totals[ch["status"]] += 1
        if not checks:
            status = "uncited"
        elif all(ch["status"] == "verified" for ch in checks):
            status = "verified"
        else:
            status = "unverified"
        findings.append({"index": i, "status": status, "citations": checks})
    totals["findings"] = len(findings)
    totals["unverified_findings"] = sum(1 for f in findings if f["status"] == "unverified")
    return {"findings": findings, "totals": totals}
