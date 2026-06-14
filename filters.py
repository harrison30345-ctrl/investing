"""
Screener — applies pass/fail filters from config to a set of computed metrics.
Supports --dry-run mode showing per-filter pass/fail detail.
"""

import logging

import yaml

logger = logging.getLogger(__name__)


def load_config(config_path: str) -> dict:
    """Load a YAML screening config."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def apply_filters(metrics: dict, config: dict) -> tuple[bool, dict[str, dict]]:
    """
    Apply all filters from config to a single stock's metrics.

    Returns:
        (passed: bool, details: {filter_name: {value, threshold, passed, reason}})
    """
    filters = config.get("filters", {})
    details = {}
    all_passed = True

    for metric_name, rules in filters.items():
        if not rules.get("enabled", True):
            details[metric_name] = {
                "value": metrics.get(metric_name),
                "passed": True,
                "reason": "disabled",
            }
            continue

        value = metrics.get(metric_name)

        # Handle missing data
        if value is None:
            on_missing = rules.get("on_missing", "pass")
            if on_missing == "fail":
                details[metric_name] = {
                    "value": None,
                    "passed": False,
                    "reason": "missing data (configured to fail)",
                }
                all_passed = False
            else:
                details[metric_name] = {
                    "value": None,
                    "passed": True,
                    "reason": "missing data (configured to pass)",
                }
            continue

        # Handle boolean flags (fcf_positive_3y, no_dilution_3y)
        if rules.get("must_be_true", False):
            passed = value is True
            details[metric_name] = {
                "value": value,
                "passed": passed,
                "reason": f"must be True, got {value}",
            }
            if not passed:
                all_passed = False
            continue

        # Numeric min/max thresholds
        passed = True
        reasons = []

        if "min" in rules:
            if value < rules["min"]:
                passed = False
                reasons.append(f"{value:.2f} < min {rules['min']}")

        if "max" in rules:
            if value > rules["max"]:
                passed = False
                reasons.append(f"{value:.2f} > max {rules['max']}")

        details[metric_name] = {
            "value": round(value, 2) if isinstance(value, float) else value,
            "passed": passed,
            "reason": "; ".join(reasons) if reasons else "passed",
        }
        if not passed:
            all_passed = False

    return all_passed, details


def screen_batch(all_metrics: list[dict], config: dict) -> tuple[list[dict], list[dict]]:
    """
    Screen a batch of stocks.

    Returns:
        (passed: list of metrics dicts that passed all filters,
         failed: list of {ticker, metrics, filter_details})
    """
    passed = []
    failed = []

    for metrics in all_metrics:
        ticker = metrics.get("ticker", "???")
        ok, details = apply_filters(metrics, config)
        if ok:
            passed.append(metrics)
            logger.debug(f"{ticker}: PASSED all filters")
        else:
            failed_filters = {k: v for k, v in details.items() if not v["passed"]}
            failed.append({
                "ticker": ticker,
                "metrics": metrics,
                "filter_details": details,
                "failed_filters": failed_filters,
            })
            logger.debug(f"{ticker}: FAILED — {list(failed_filters.keys())}")

    return passed, failed


def format_dry_run(passed: list[dict], failed: list[dict], config: dict, limit: int = 30) -> str:
    """Format a dry-run report showing which filters each stock passed/failed."""
    lines = []
    lines.append("=" * 80)
    lines.append("DRY RUN — Filter Pass/Fail Detail")
    lines.append("=" * 80)

    filters = config.get("filters", {})
    active_filters = [k for k, v in filters.items() if v.get("enabled", True)]

    # Show passed stocks first
    lines.append(f"\n✓ PASSED ({len(passed)} stocks):")
    lines.append("-" * 40)
    for m in passed[:limit]:
        lines.append(f"  {m['ticker']:8s} {m.get('name', '')[:30]}")

    # Show failed stocks with detail
    lines.append(f"\n✗ FAILED ({len(failed)} stocks):")
    lines.append("-" * 40)
    for entry in failed[:limit]:
        ticker = entry["ticker"]
        failed_filters = entry["failed_filters"]
        reasons = []
        for fname, fdetail in failed_filters.items():
            reasons.append(f"{fname}: {fdetail['reason']}")
        lines.append(f"  {ticker:8s} — {'; '.join(reasons)}")

    if len(passed) + len(failed) > limit:
        lines.append(f"\n  ... showing first {limit} of {len(passed) + len(failed)} total")

    lines.append("=" * 80)
    return "\n".join(lines)
