"""Phase 5: sweeps, re-tests on other periods, report verdicts and the schema migration."""
from __future__ import annotations

import sqlite3

import pytest

from quantedge.reports import build_reports, compare_rows, report_markdown, sweep_summary


def exp(id, period="train", net=0.2, bench=0.1, sharpe=1.0, bsharpe=0.8, dd=-0.1, bdd=-0.2, trades=100,
        pf=1.5, costs=0.02, params=None, status="completed", used_test=False, batch=None, notes=None,
        uni=0.05, usharpe=0.5, ci=(0.02, 0.10), dsr=0.99, measured=True):
    return {"id": id, "strategy_key": "ema_pullback", "market": "US", "universe": ["AAPL", "MSFT"],
            "params": params or {"fast": 20}, "config": {"initial_capital": 100000}, "period": period,
            "status": status, "used_test_set": used_test, "benchmark": "SPY", "start_date": "2020-01-01",
            "end_date": "2021-01-01", "created_at": "2026-10-25", "bias_notes": ["Survivorship bias"],
            "batch": batch, "notes": notes,
            "metrics": {"net_return": net, "benchmark_return": bench, "excess_return": net - bench, "sharpe": sharpe,
                        "benchmark_sharpe": bsharpe, "max_drawdown": dd, "benchmark_max_drawdown": bdd,
                        "trade_count": trades, "profit_factor": pf, "costs_pct_of_start": costs, "win_rate": 0.5,
                        "longest_losing_streak": 4, "exposure": 0.7,
                        **({"universe_return": uni, "universe_sharpe": usharpe, "excess_vs_universe": net - uni,
                            "sharpe_ci_low": 0.3, "sharpe_ci_high": 1.7, "excess_ann_ci_low": ci[0],
                            "excess_ann_ci_high": ci[1], "excess_universe_ann_ci_low": ci[0],
                            "excess_universe_ann_ci_high": ci[1], "deflated_sharpe": dsr, "trials": 3.0}
                           if measured else {})}}


def verdict(*runs):
    reports = build_reports(list(runs))
    assert len(reports) == 1
    return reports[0]


# ── verdicts ───────────────────────────────────────────────────────────────

def test_train_only_is_not_validated():
    assert verdict(exp(1)).verdict_level == "caution"
    assert "Not validated" in verdict(exp(1)).verdict


def test_validation_that_lags_benchmark_fails():
    r = verdict(exp(1), exp(2, "validation", net=0.01, bench=0.1, sharpe=0.2, bsharpe=0.9))
    assert r.verdict_level == "warning" and "Failed" in r.verdict


def test_validation_sharpe_collapse_is_flagged_as_degraded():
    r = verdict(exp(1, sharpe=2.0), exp(2, "validation", net=0.15, bench=0.1, sharpe=0.6, bsharpe=0.5))
    assert r.verdict_level == "warning" and "overfitting" in r.verdict


def test_beating_return_but_not_risk_adjusted_is_mixed():
    r = verdict(exp(1), exp(2, "validation", net=0.15, bench=0.1, sharpe=0.9, bsharpe=1.2))
    assert r.verdict_level == "caution" and "Mixed" in r.verdict


def test_genuine_hold_up_still_says_not_proof():
    r = verdict(exp(1), exp(2, "validation", net=0.15, bench=0.1, sharpe=1.1, bsharpe=0.7))
    assert r.verdict_level == "good" and "not proof" in r.verdict


def test_too_few_validation_trades_is_inconclusive():
    r = verdict(exp(1), exp(2, "validation", trades=12))
    assert r.verdict_level == "caution" and "too few" in r.verdict


def test_configurations_are_grouped_by_params():
    assert len(build_reports([exp(1), exp(2, params={"fast": 10})])) == 2


# ── findings ───────────────────────────────────────────────────────────────

def test_costs_eating_the_edge_is_flagged():
    r = verdict(exp(1, net=0.02, costs=0.10))
    assert any(f.title == "Costs ate most of the edge" for f in r.findings)


def test_repeated_test_set_use_is_flagged():
    r = verdict(exp(1), exp(2, "validation"), exp(3, "test", used_test=True), exp(4, "test", used_test=True))
    assert r.test_runs == 2 and any("repeatedly" in f.title for f in r.findings)


def test_failed_runs_counted_and_markdown_is_complete():
    r = verdict(exp(1), exp(2, status="failed"))
    assert r.failed_runs == 1
    md = report_markdown(r)
    assert "**Verdict:**" in md and "Not investment advice" in md and "Survivorship" in md


# ── sweeps and comparison ──────────────────────────────────────────────────

def test_sweep_summary_detects_fragility_and_failure():
    fragile = [exp(i, sharpe=s, net=n, notes=f"sweep fast={i}") for i, s, n in
               [(1, 2.0, 0.3), (2, 0.2, 0.11), (3, 0.1, 0.12), (4, 0.3, 0.11)]]
    assert "Fragile" in sweep_summary(fragile).finding.title
    losers = [exp(i, net=0.0, bench=0.1, notes=f"sweep fast={i}") for i in (1, 2)]
    assert sweep_summary(losers).finding.level == "warning"
    weak = [exp(i, sharpe=0.2, net=0.12, notes=f"sweep fast={i}") for i in (1, 2, 3)]
    assert sweep_summary(weak).finding.title == "Weak at every setting"
    assert sweep_summary(fragile).param == "fast"


def test_compare_rows_one_column_per_experiment():
    df = compare_rows([exp(1), exp(2, net=-0.1)])
    assert list(df.columns) == ["#1", "#2"] and df.loc["Net return", "#2"] == "-10.0%"


# ── runner integration (demo data, throwaway DB) ───────────────────────────

def test_sweep_and_validation_rerun(isolated_db):
    from quantedge.backtest.runner import TestSetLocked, rerun_on_period, run_sweep
    from quantedge.data.service import refresh_daily
    from quantedge.db import session_scope
    from quantedge.db.models import Experiment
    refresh_daily(["AAPL", "MSFT", "NVDA", "JPM", "SPY"], years=6)
    batch, ids = run_sweep("ema_pullback", "slow", [30, 60], symbols=["AAPL", "MSFT", "NVDA", "JPM"])
    assert len(ids) == 2
    val_id = rerun_on_period(ids[0], "validation")
    with session_scope() as s:
        runs = s.query(Experiment).filter_by(batch=batch).all()
        assert {r.params["slow"] for r in runs} == {30, 60}
        train, val = s.get(Experiment, ids[0]), s.get(Experiment, val_id)
        assert val.parent_id == train.id and val.period == "validation"
        assert val.params == train.params and val.universe == train.universe and val.config == train.config
        assert val.start_date > train.end_date            # chronological: no overlap with training data
        assert {(r.start_date, r.end_date) for r in runs} == {(train.start_date, train.end_date)}  # same window
    with pytest.raises(TestSetLocked):
        rerun_on_period(val_id, "test")


def test_sweep_rejects_unknown_parameter(isolated_db):
    from quantedge.backtest.runner import run_sweep
    with pytest.raises(ValueError):
        run_sweep("ema_pullback", "not_a_param", [1, 2], symbols=["AAPL"])


def test_new_nullable_columns_added_to_existing_database(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE experiments (id INTEGER PRIMARY KEY, strategy_key VARCHAR(48))")
    con.commit()
    con.close()
    from quantedge.db import get_engine
    get_engine.cache_clear()
    get_engine(f"sqlite:///{path}")
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(experiments)")}
    assert {"batch", "parent_id", "notes"} <= cols
    get_engine.cache_clear()



# ── audit fixes: strict benchmark, uncertainty, pre-audit runs ─────────────

def test_beating_etf_but_not_own_universe_fails():
    r = verdict(exp(1), exp(2, "validation", net=0.40, bench=0.30, uni=1.20, sharpe=1.4, bsharpe=1.0, usharpe=1.6))
    assert r.verdict_level == "warning" and "equal-weight universe" in r.verdict
    assert any(f.title.startswith("Lagged buy-and-hold of its own symbols") for f in r.findings)


def test_edge_with_ci_including_zero_is_inconclusive():
    r = verdict(exp(1), exp(2, "validation", net=0.15, bench=0.1, sharpe=1.1, bsharpe=0.7, ci=(-0.03, 0.12)))
    assert r.verdict_level == "caution" and "statistically" in r.verdict


def test_low_deflated_sharpe_is_inconclusive():
    r = verdict(exp(1), exp(2, "validation", net=0.15, bench=0.1, sharpe=1.1, bsharpe=0.7, dsr=0.6))
    assert r.verdict_level == "caution" and "variants" in r.verdict


def test_pre_audit_runs_never_get_held_up():
    r = verdict(exp(1, measured=False), exp(2, "validation", net=0.15, bench=0.1, sharpe=1.1, bsharpe=0.7,
                                            measured=False))
    assert r.verdict_level == "caution" and "Pre-audit" in r.verdict
