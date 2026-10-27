"""
Trading 212 client: read-only, credentials never leaked, failures legible.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import trading212 as t212mod  # noqa: E402
from services.trading212 import (  # noqa: E402
    Position, T212Credentials, T212Error, Trading212, load_credentials, save_credentials,
)


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    """Records requests and replays queued responses."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers})
        return self.responses.pop(0)


def _client(responses, key="KEY", secret="SECRET", demo=False):
    c = Trading212(T212Credentials(key, secret, demo))
    c._session = FakeSession(responses)
    return c


# ── Read-only by construction ────────────────────────────────────────────────

def test_client_cannot_trade():
    """An account link that can also place orders is a different, riskier thing."""
    for forbidden in ("place_order", "buy", "sell", "cancel_order", "modify_order",
                      "create_order", "execute", "withdraw", "deposit"):
        assert not hasattr(Trading212, forbidden), f"client must not expose {forbidden}()"


def test_module_makes_no_post_or_delete_calls():
    source = Path(t212mod.__file__).read_text()
    for verb in (".post(", ".put(", ".delete(", ".patch("):
        assert verb not in source, f"read-only client must not use {verb}"


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_uses_basic_auth_first():
    c = _client([FakeResp(200, {"id": 1})])
    c.check()
    sent = c._session.calls[0]["headers"]["Authorization"]
    assert sent.startswith("Basic ")
    decoded = base64.b64decode(sent.split(" ", 1)[1]).decode()
    assert decoded == "KEY:SECRET"


def test_falls_back_to_the_legacy_raw_key_header():
    """Older keys use a raw header; the user should not have to know which."""
    c = _client([FakeResp(401, text="nope"), FakeResp(200, {"id": 1})])
    c.check()
    assert c._session.calls[1]["headers"]["Authorization"] == "KEY"


def test_remembers_the_scheme_that_worked():
    c = _client([FakeResp(401), FakeResp(200, {}), FakeResp(200, {})])
    c.check()
    c.check()
    assert len(c._session.calls) == 3, "should not retry basic once legacy succeeded"


def test_both_schemes_rejected_raises_a_useful_message():
    c = _client([FakeResp(401, text="bad key"), FakeResp(401, text="bad key")])
    with pytest.raises(T212Error) as err:
        c.check()
    message = str(err.value).lower()
    assert "rejected" in message
    assert "ip restriction" in message, "IP restriction is the most common cause"


def test_rate_limit_is_explained_not_just_reported():
    c = _client([FakeResp(429, text="slow down")])
    with pytest.raises(T212Error) as err:
        c.cash()
    assert "rate limiting" in str(err.value).lower()


def test_network_failure_is_wrapped():
    class Boom:
        def get(self, *a, **k):
            import requests
            raise requests.RequestException("down")
    c = Trading212(T212Credentials("K"))
    c._session = Boom()
    with pytest.raises(T212Error) as err:
        c.cash()
    assert "could not reach" in str(err.value).lower()


def test_demo_account_uses_the_demo_host():
    c = _client([FakeResp(200, {})], demo=True)
    c.check()
    assert "demo.trading212.com" in c._session.calls[0]["url"]


# ── Parsing ──────────────────────────────────────────────────────────────────

def test_positions_are_parsed():
    c = _client([FakeResp(200, [
        {"ticker": "AAPL_US_EQ", "quantity": "10", "averagePrice": "100",
         "currentPrice": "150", "ppl": "500", "initialFillDate": "2025-01-05T10:00:00Z"},
    ])])
    p = c.positions()[0]
    assert (p.symbol, p.quantity, p.value, p.cost) == ("AAPL", 10.0, 1500.0, 1000.0)
    assert abs(p.return_pct - 50.0) < 1e-9
    assert p.initial_fill_date.year == 2025


def test_malformed_positions_are_skipped_not_guessed():
    c = _client([FakeResp(200, [
        {"ticker": "AAPL_US_EQ", "quantity": "x", "averagePrice": "1",
         "currentPrice": "1", "ppl": "0"},
        {"ticker": "MSFT_US_EQ", "quantity": "5", "averagePrice": "10",
         "currentPrice": "12", "ppl": "10"},
    ])])
    out = c.positions()
    assert [p.symbol for p in out] == ["MSFT"]


def test_zero_cost_position_reports_no_return_rather_than_dividing_by_zero():
    p = Position("X_EQ", "X", 1.0, 0.0, 5.0, 5.0)
    assert p.return_pct is None


def test_cash_parses_and_tolerates_missing_fields():
    c = _client([FakeResp(200, {"free": "100.5", "total": "2000", "currencyCode": "GBP"})])
    cash = c.cash()
    assert (cash.free, cash.total, cash.currency) == (100.5, 2000.0, "GBP")
    assert cash.invested is None, "a field the API omitted must stay unknown"


def test_empty_portfolio_is_not_an_error():
    assert _client([FakeResp(200, [])]).positions() == []


# ── Credentials ──────────────────────────────────────────────────────────────

def test_environment_credentials_are_used(monkeypatch):
    monkeypatch.setenv("T212_API_KEY", "envkey")
    monkeypatch.setenv("T212_API_SECRET", "envsecret")
    creds = load_credentials()
    assert (creds.api_key, creds.api_secret) == ("envkey", "envsecret")


def test_credentials_file_is_read_and_locked_down(tmp_path, monkeypatch):
    monkeypatch.delenv("T212_API_KEY", raising=False)
    path = tmp_path / "creds"
    monkeypatch.setattr(t212mod, "CREDENTIALS_PATH", path)
    save_credentials("filekey", "filesecret")
    assert (path.stat().st_mode & 0o777) == 0o600, "credentials must not be world readable"
    creds = load_credentials()
    assert creds.api_key == "filekey"


def test_no_credentials_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("T212_API_KEY", raising=False)
    monkeypatch.setattr(t212mod, "CREDENTIALS_PATH", tmp_path / "absent")
    assert load_credentials() is None


def test_errors_never_include_the_key():
    c = _client([FakeResp(401, text="denied"), FakeResp(401, text="denied")],
                key="SUPERSECRETKEY")
    with pytest.raises(T212Error) as err:
        c.check()
    assert "SUPERSECRETKEY" not in str(err.value)
