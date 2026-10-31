"""Tests for the thesis tracker (no network)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from screener.theses import (
    check_rule,
    evaluate_thesis,
    format_thesis_report,
    load_theses,
)

TODAY = date(2026, 10, 27)

THESIS = {
    "ticker": "ACME",
    "thesis": "Long ACME: margins expand as software mix grows.",
    "position": "long",
    "conviction": "high",
    "created": "2026-09-01",
    "pillars": [
        {"text": "Growth", "check": {"metric": "revenue_growth_1y", "min": 10}},
        {"text": "Margins", "check": {"metric": "operating_margin", "min": 20}},
        {"text": "Moat (judgement call)"},
    ],
    "exit_triggers": [
        {"text": "Leverage", "check": {"metric": "debt_to_equity", "max": 1.0}},
    ],
    "catalysts": [
        {"date": "2026-11-05", "event": "Q3 earnings", "impact": "high"},
        {"date": "2027-03-01", "event": "Investor day"},
        {"date": "2026-10-01", "event": "Product launch"},
        {"date": "2026-09-15", "event": "Old event", "outcome": "beat"},
    ],
    "log": [{"date": "2026-10-10", "data_point": "created"}],
}

GOOD = {"revenue_growth_1y": 15.0, "operating_margin": 30.0, "debt_to_equity": 0.4,
        "composite_score": 70.0}


def test_check_rule_bounds_and_missing():
    assert check_rule({"metric": "roe", "min": 15}, {"roe": 20.0})["status"] == "holding"
    assert check_rule({"metric": "roe", "min": 15}, {"roe": 10.0})["status"] == "broken"
    assert check_rule({"metric": "pe", "max": 25}, {"pe": 30.0})["status"] == "broken"
    assert check_rule({"metric": "roe", "min": 15}, {})["status"] == "no data"
    assert check_rule(None, {"roe": 1})["status"] == "no data"
    assert check_rule({"metric": "fcf_positive_3y", "must_be_true": True},
                      {"fcf_positive_3y": False})["status"] == "broken"


def test_intact_thesis():
    r = evaluate_thesis(THESIS, GOOD, TODAY)
    assert r["verdict"] == "intact"
    assert [p["status"] for p in r["pillars"]] == ["holding", "holding", "no data"]
    assert not r["triggers"][0]["fired"]
    assert [c["event"] for c in r["upcoming_catalysts"]] == ["Q3 earnings"]
    assert [c["event"] for c in r["unlogged_catalysts"]] == ["Product launch"]
    assert r["review_due"] is False


def test_broken_pillar_means_weakening():
    r = evaluate_thesis(THESIS, {**GOOD, "operating_margin": 12.0}, TODAY)
    assert r["verdict"] == "weakening"


def test_exit_trigger_beats_weakening():
    r = evaluate_thesis(THESIS, {**GOOD, "operating_margin": 12.0, "debt_to_equity": 2.0}, TODAY)
    assert r["verdict"] == "exit trigger"
    assert r["triggers"][0]["fired"]


def test_missing_metrics_is_no_data_not_intact():
    assert evaluate_thesis(THESIS, None, TODAY)["verdict"] == "no data"


def test_review_due_after_a_quarter():
    old = {**THESIS, "log": [{"date": "2026-06-01"}]}
    r = evaluate_thesis(old, GOOD, TODAY)
    assert r["review_due"] and r["days_since_review"] == 148


def test_load_skips_template_and_bad_files(tmp_path: Path):
    (tmp_path / "_TEMPLATE.yaml").write_text("ticker: X\nthesis: t\n")
    (tmp_path / "acme.yaml").write_text("ticker: acme\nthesis: Long it\n")
    (tmp_path / "bad.yaml").write_text("name: no ticker\n")
    (tmp_path / "broken.yaml").write_text("ticker: [unclosed\n")
    theses = load_theses(tmp_path)
    assert [t["ticker"] for t in theses] == ["ACME"]


def test_load_missing_folder_is_empty(tmp_path: Path):
    assert load_theses(tmp_path / "nope") == []


def test_report_puts_worst_first():
    ok = evaluate_thesis(THESIS, GOOD, TODAY)
    bad = evaluate_thesis({**THESIS, "ticker": "BAD"}, {**GOOD, "debt_to_equity": 3.0}, TODAY)
    text = format_thesis_report([ok, bad])
    assert text.index("BAD") < text.index("ACME")
    assert "FIRED" in text and "Q3 earnings" in text
    assert "No theses found" in format_thesis_report([])
