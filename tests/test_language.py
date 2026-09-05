"""
Guards the language policy documented at the top of screener/dashboard.py.

This is a UK consumer-facing research product. Copy that instructs someone to
trade -- telling them what to buy, at what size, or where to exit -- moves the
product closer to giving a personal recommendation. These tests fail the build
if that wording reappears in user-facing strings.

Scope note: this checks strings that reach the screen, not internal variable
names. `rr_ratio` as a dataframe column is fine; "R/R" as a column *label* is
not. The distinction is what a user can read.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parent.parent / "screener" / "dashboard.py"

# Phrases that must never appear in user-visible copy, with the reason.
BANNED = {
    r"\bstop loss\b": "instructs an exit price",
    r"\bstop-loss\b": "instructs an exit price",
    r"\bposition siz": "instructs how much to allocate",
    r"\bentry trigger\b": "instructs when to buy",
    r"\bentry point\b": "instructs when to buy",
    r"\bentry zone\b": "instructs when to buy",
    r"\bcut quickly\b": "instructs an exit",
    r"\bR/R\b": "presents a trade's payoff ratio",
    r"\brisk/reward stands\b": "presents a trade's payoff ratio",
    r"\btighten stops\b": "instructs risk management",
    r"\bhalf-siz": "instructs position sizing",
    r"\bcommitting full size\b": "instructs position sizing",
    r"\bAlloc%": "presents an allocation instruction",
    r"\bPosition \$": "presents a position size",
    r"\bStop \$": "presents an exit price",
    r"\bStop%": "presents an exit price",
    r"\btrading strateg": "frames research output as trades to execute",
    r"\btactical entry": "instructs when to buy",
    r"\bposition before": "instructs when to buy",
    r"\bconviction\b": "frames a score as recommendation strength",
    r"\bhigh-conviction\b": "frames a score as recommendation strength",
    r"💼": "the briefcase icon marked a position-size display",
    r"% of portfolio": "presents an allocation instruction",
}


def _user_facing_strings() -> list[str]:
    """Every string literal in the dashboard, minus docstrings and comments.

    Parsing the AST rather than grepping means the language policy note in the
    module docstring -- which necessarily lists the banned words -- does not
    trip its own test.
    """
    tree = ast.parse(DASHBOARD.read_text())

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                out.append(node.value)
    return out


@pytest.fixture(scope="module")
def copy_text() -> str:
    return "\n".join(_user_facing_strings())


@pytest.mark.parametrize("pattern,reason", sorted(BANNED.items()))
def test_no_trade_instruction_language(copy_text, pattern, reason):
    hits = re.findall(pattern, copy_text, flags=re.IGNORECASE)
    assert not hits, (
        f"user-facing copy contains {pattern!r} ({len(hits)} occurrence(s)) — {reason}. "
        f"See the LANGUAGE POLICY note in screener/dashboard.py."
    )


def test_analyst_upside_is_attributed(copy_text):
    """Upside figures must be attributed to analysts, not stated as our forecast."""
    if "upside" in copy_text.lower():
        assert "analyst" in copy_text.lower(), (
            "copy mentions upside without attributing it to third-party analysts"
        )


def test_disclaimer_states_it_is_not_advice(copy_text):
    lowered = copy_text.lower()
    assert "not financial advice" in lowered
    assert "recommendation to buy or sell" in lowered, (
        "the disclaimer should say plainly that nothing is a recommendation"
    )


def test_navigation_uses_research_vocabulary(copy_text):
    """Page names must not be framed as trading activities."""
    for banned_page in ("Sell Watch", "Hot Stocks", "Hedge Fund Engine"):
        assert banned_page not in copy_text, f"page name {banned_page!r} still present"
