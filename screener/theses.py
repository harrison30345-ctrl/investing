"""
Thesis tracker — checks your written investment theses against fresh screener data.

Each thesis is a YAML file in ``theses/`` (one per ticker, e.g. ``theses/MSFT.yaml``).
The equity-research plugin's thesis skill writes and updates these files; the screener
reads them and tests every machine-checkable rule on each run, so a thesis is
falsifiable by data rather than by memory.

Thesis file shape (only ``ticker`` and ``thesis`` are required)::

    ticker: MSFT
    name: Microsoft Corporation
    position: long            # long | short | watch
    created: 2026-10-27
    thesis: >
      Long MSFT: Azure + AI services keep revenue compounding at double digits
      while margins hold above 40%.
    conviction: medium        # high | medium | low
    target_price: 600
    pillars:
      - text: Revenue keeps growing double digits
        check: {metric: revenue_growth_1y, min: 10}
      - text: Operating margin holds above 40%
        check: {metric: operating_margin, min: 40}
    risks:
      - AI capex fails to earn a return
    exit_triggers:            # any one firing means "re-read the thesis, consider exiting"
      - text: Balance sheet stretched
        check: {metric: debt_to_equity, max: 1.0}
    catalysts:
      - {date: 2027-01-27, event: Q2 FY27 earnings, impact: high}
    log:
      - {date: 2026-10-27, data_point: Thesis created, impact: neutral,
         action: no change, conviction: medium}

Pillar checks describe what should stay TRUE; exit-trigger checks describe what must
stay true too, and the trigger "fires" when the check fails. Metric names are the
screener's own (see ``screener/metrics.py`` or any output CSV header).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_THESES_DIR = Path(__file__).resolve().parent.parent / "theses"
REVIEW_EVERY_DAYS = 90       # a thesis untouched for a quarter is due a review
CATALYST_WINDOW_DAYS = 30    # show catalysts coming up within this many days

STATUS_OK = "holding"
STATUS_BROKEN = "broken"
STATUS_UNKNOWN = "no data"


# ── Loading ──────────────────────────────────────────────────────────────────

def _to_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def load_thesis(path: Path) -> Optional[dict]:
    """Load one thesis file. Returns None (and logs why) if it is unusable."""
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.error(f"{path.name}: could not read thesis — {e}")
        return None
    if not isinstance(data, dict) or not data.get("ticker") or not data.get("thesis"):
        logger.error(f"{path.name}: a thesis needs at least 'ticker' and 'thesis'")
        return None
    data["ticker"] = str(data["ticker"]).strip().upper()
    data["_path"] = str(path)
    return data


def load_theses(theses_dir: Optional[Path] = None) -> List[dict]:
    """Load every ``*.yaml`` / ``*.yml`` thesis in the folder, sorted by ticker."""
    folder = Path(theses_dir) if theses_dir else DEFAULT_THESES_DIR
    if not folder.is_dir():
        return []
    theses = []
    for path in sorted(list(folder.glob("*.yaml")) + list(folder.glob("*.yml"))):
        if path.name.startswith("_"):      # _TEMPLATE.yaml and friends
            continue
        thesis = load_thesis(path)
        if thesis:
            theses.append(thesis)
    return sorted(theses, key=lambda t: t["ticker"])


# ── Checking ─────────────────────────────────────────────────────────────────

def check_rule(check: Optional[dict], metrics: dict) -> dict:
    """
    Test one ``{metric, min, max, must_be_true}`` rule against a metrics dict.
    Same semantics as the screener's filters, but missing data is reported, never assumed.
    """
    if not check or not check.get("metric"):
        return {"status": STATUS_UNKNOWN, "value": None, "reason": "no machine check — review by hand"}
    name = check["metric"]
    value = metrics.get(name)
    if value is None:
        return {"status": STATUS_UNKNOWN, "value": None, "reason": f"{name}: no data"}

    if check.get("must_be_true"):
        ok = value is True
        return {"status": STATUS_OK if ok else STATUS_BROKEN, "value": value,
                "reason": f"{name} is {value}"}

    problems = []
    if "min" in check and value < check["min"]:
        problems.append(f"{name} {value:.2f} < {check['min']}")
    if "max" in check and value > check["max"]:
        problems.append(f"{name} {value:.2f} > {check['max']}")
    shown = round(value, 2) if isinstance(value, float) else value
    if problems:
        return {"status": STATUS_BROKEN, "value": shown, "reason": "; ".join(problems)}
    bounds = []
    if "min" in check:
        bounds.append(f">= {check['min']}")
    if "max" in check:
        bounds.append(f"<= {check['max']}")
    return {"status": STATUS_OK, "value": shown,
            "reason": f"{name} {shown} ({' and '.join(bounds) or 'no bound'})"}


def evaluate_thesis(thesis: dict, metrics: Optional[dict], today: Optional[date] = None) -> dict:
    """
    Evaluate one thesis against the latest metrics for its ticker.

    Returns a dict with per-pillar and per-trigger results, upcoming catalysts,
    review status and an overall verdict: "intact", "weakening", "exit trigger" or "no data".
    """
    today = today or date.today()
    metrics = metrics or {}

    pillars = []
    for p in thesis.get("pillars") or []:
        p = p if isinstance(p, dict) else {"text": str(p)}
        pillars.append({"text": p.get("text", ""), **check_rule(p.get("check"), metrics)})

    triggers = []
    for t in thesis.get("exit_triggers") or []:
        t = t if isinstance(t, dict) else {"text": str(t)}
        result = check_rule(t.get("check"), metrics)
        fired = result["status"] == STATUS_BROKEN
        triggers.append({"text": t.get("text", ""), "fired": fired, **result})

    upcoming, overdue_catalysts = [], []
    for c in thesis.get("catalysts") or []:
        if not isinstance(c, dict):
            continue
        when = _to_date(c.get("date"))
        if when is None:
            continue
        item = {"date": when, "event": c.get("event", ""), "impact": c.get("impact", "")}
        if today <= when <= today + timedelta(days=CATALYST_WINDOW_DAYS):
            upcoming.append(item)
        elif when < today and not c.get("outcome"):
            overdue_catalysts.append(item)   # happened, but nobody logged the result

    log_dates = [_to_date(e.get("date")) for e in (thesis.get("log") or []) if isinstance(e, dict)]
    log_dates = [d for d in log_dates if d]
    last_review = max(log_dates) if log_dates else _to_date(thesis.get("created"))
    days_since = (today - last_review).days if last_review else None
    review_due = days_since is None or days_since > REVIEW_EVERY_DAYS

    broken = [p for p in pillars if p["status"] == STATUS_BROKEN]
    checked = [p for p in pillars if p["status"] != STATUS_UNKNOWN] + \
              [t for t in triggers if t["status"] != STATUS_UNKNOWN]
    if not metrics:
        verdict = "no data"
    elif any(t["fired"] for t in triggers):
        verdict = "exit trigger"
    elif broken:
        verdict = "weakening"
    elif checked:
        verdict = "intact"
    else:
        verdict = "no data"

    return {
        "ticker": thesis["ticker"],
        "name": thesis.get("name") or metrics.get("name", ""),
        "position": thesis.get("position", "long"),
        "conviction": thesis.get("conviction", ""),
        "thesis": " ".join(str(thesis.get("thesis", "")).split()),
        "verdict": verdict,
        "score": metrics.get("composite_score"),
        "pillars": pillars,
        "triggers": triggers,
        "upcoming_catalysts": sorted(upcoming, key=lambda c: c["date"]),
        "unlogged_catalysts": sorted(overdue_catalysts, key=lambda c: c["date"]),
        "last_review": last_review,
        "days_since_review": days_since,
        "review_due": review_due,
    }


def evaluate_all(theses: List[dict], metrics_by_ticker: Dict[str, dict],
                 today: Optional[date] = None) -> List[dict]:
    return [evaluate_thesis(t, metrics_by_ticker.get(t["ticker"]), today) for t in theses]


# ── Reporting ────────────────────────────────────────────────────────────────

_VERDICT_ICON = {"intact": "✓", "weakening": "!", "exit trigger": "✗", "no data": "?"}
_STATUS_ICON = {STATUS_OK: "✓", STATUS_BROKEN: "✗", STATUS_UNKNOWN: "·"}
_ORDER = {"exit trigger": 0, "weakening": 1, "no data": 2, "intact": 3}


def format_thesis_report(results: List[dict]) -> str:
    """Plain-text report, worst news first."""
    if not results:
        return ("No theses found. Ask Claude to write one (equity-research /thesis), "
                "or copy theses/_TEMPLATE.yaml to theses/<TICKER>.yaml.")

    lines = ["=" * 70, "  THESIS CHECK", "=" * 70]
    for r in sorted(results, key=lambda r: (_ORDER.get(r["verdict"], 9), r["ticker"])):
        score = f"  score {r['score']:.1f}" if isinstance(r.get("score"), (int, float)) else ""
        lines.append("")
        lines.append(f"{_VERDICT_ICON.get(r['verdict'], '?')} {r['ticker']} — {r['verdict'].upper()}"
                     f"  ({r['position']}, conviction {r['conviction'] or 'unset'}){score}")
        if r["thesis"]:
            lines.append(f"    {r['thesis'][:110]}")
        for p in r["pillars"]:
            lines.append(f"    {_STATUS_ICON[p['status']]} pillar: {p['text']} — {p['reason']}")
        for t in r["triggers"]:
            mark = "✗ FIRED" if t["fired"] else _STATUS_ICON[t["status"]]
            lines.append(f"    {mark} exit trigger: {t['text']} — {t['reason']}")
        for c in r["upcoming_catalysts"]:
            lines.append(f"    → {c['date']:%d %b}: {c['event']} ({c['impact'] or 'impact ?'})")
        for c in r["unlogged_catalysts"]:
            lines.append(f"    ⚠ {c['date']:%d %b} {c['event']} has passed — log what happened")
        if r["review_due"]:
            since = (f"{r['days_since_review']} days since last update"
                     if r["days_since_review"] is not None else "never reviewed")
            lines.append(f"    ⚠ review due ({since})")
    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)
