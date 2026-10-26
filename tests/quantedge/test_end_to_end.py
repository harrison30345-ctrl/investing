"""
End-to-end smoke test through the CLI, on a fresh database with demo data (no network, no AI calls).
Mirrors the launch steps in docs/quantedge/README.md, so a broken step there fails here too.
"""
from __future__ import annotations

import re


def cli(capsys, *args) -> tuple:
    from quantedge.cli import main
    code = main(list(args))
    return code, capsys.readouterr().out


def test_launch_flow(isolated_db, capsys):
    code, out = cli(capsys, "refresh", "--core", "--universe", "us_large", "--years", "6")
    assert code == 0 and "updated" in out

    code, out = cli(capsys, "backtest", "donchian_breakout", "--universe", "us_large")
    assert code == 0
    train_id = int(re.search(r"Experiment #(\d+)", out).group(1))
    assert "Survivorship" in out or "DEMO" in out

    code, out = cli(capsys, "backtest", "donchian_breakout", "--universe", "us_large", "--period", "test")
    assert code == 2 and "locked" in out

    code, out = cli(capsys, "sweep", "xs_momentum", "lookback", "63,126,252", "--universe", "us_large")
    assert code == 0 and "Sweep" in out

    code, out = cli(capsys, "validate", str(train_id))
    assert code == 0
    val_id = int(re.search(r"as #(\d+)", out).group(1))
    assert "**Verdict:**" in out

    code, out = cli(capsys, "report")
    assert code == 0 and out.count("**Verdict:**") >= 4

    code, out = cli(capsys, "agents", "estimate")
    assert code == 0 and "Research Director" in out
    code, out = cli(capsys, "agents", "run")
    assert code == 0 and "not configured" in out          # AI is off by default: no calls, no spend

    code, out = cli(capsys, "paper", "create", "--experiment", str(train_id), "--approve")
    assert code == 2 and "validation" in out                # train runs can't be promoted
    code, out = cli(capsys, "paper", "create", "--experiment", str(val_id))
    assert code == 2 and "--approve" in out                 # approval is explicit
    code, out = cli(capsys, "paper", "create", "--experiment", str(val_id), "--approve", "--name", "Smoke")
    assert code == 0 and "created" in out
    code, out = cli(capsys, "paper", "list")
    assert "Smoke" in out and "active" in out

    code, out = cli(capsys, "worker", "--run", "health_check")
    assert code == 0 and "health_check: ok" in out
    code, out = cli(capsys, "health")
    assert code in (0, 1)                                   # warnings allowed (no worker running)
    code, out = cli(capsys, "health", "--fail-on", "error")
    assert code == 0

    code, out = cli(capsys, "status")
    assert code == 0 and "Mode research" in out


def test_refusals_are_messages_not_tracebacks(isolated_db, capsys):
    for args in (("paper", "pause", "999"), ("agents", "approve", "999"),
                 ("backtest", "ema_pullback", "--symbols", "AAPL", "--params", '{"fast": 999}'),
                 ("backtest", "no_such_strategy", "--symbols", "AAPL")):
        code, out = cli(capsys, *args)
        assert code in (1, 2) and ("Refused" in out or "failed" in out), (args, out)
