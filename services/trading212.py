"""
Trading 212 account access.

Read-only. This module fetches positions, cash and orders. It deliberately
contains no code that can place, modify or cancel an order, and none should be
added: an account-linking feature that can also trade is a different and much
riskier thing than one that can only look.

CREDENTIALS
-----------
The key is never stored by this module and never travels anywhere except to
Trading 212. It is read, in order, from:

  1. the T212_API_KEY / T212_API_SECRET environment variables
  2. ~/.screener/t212_credentials (chmod 600, git-ignored)
  3. whatever the user types into the app for that session only

Trading 212 shows the secret once, at creation, and treats it like a password.
Generate the key at Settings → API (Beta), and restrict it by IP if you can.

AUTH
----
The published docs are inconsistent: the quickstart uses HTTP Basic with
base64("key:secret"), while several endpoint pages still show a legacy header
carrying the raw key. Both are attempted, Basic first, because a user whose key
predates the change should not have to work out which scheme they have.

ACCOUNT TYPES
-------------
The API covers Invest and Stocks & Shares ISA accounts. SIPP is not supported
by Trading 212.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

__all__ = [
    "Trading212", "T212Credentials", "T212Error", "Position", "AccountCash",
    "CREDENTIALS_PATH", "load_credentials", "save_credentials",
]

BASE_URL = "https://live.trading212.com/api/v0"
DEMO_URL = "https://demo.trading212.com/api/v0"
CREDENTIALS_PATH = Path.home() / ".screener" / "t212_credentials"
TIMEOUT = 20


class T212Error(RuntimeError):
    """Anything that stopped a request succeeding, phrased for a reader."""


@dataclass(frozen=True)
class T212Credentials:
    api_key: str
    api_secret: str = ""
    demo: bool = False

    @property
    def base_url(self) -> str:
        return DEMO_URL if self.demo else BASE_URL


@dataclass(frozen=True)
class Position:
    ticker: str            # Trading 212's own instrument code
    symbol: str            # best-effort market ticker, for matching our data
    quantity: float
    average_price: float
    current_price: float
    ppl: float             # profit/loss in account currency
    initial_fill_date: datetime | None = None

    @property
    def value(self) -> float:
        return self.quantity * self.current_price

    @property
    def cost(self) -> float:
        return self.quantity * self.average_price

    @property
    def return_pct(self) -> float | None:
        return (self.ppl / self.cost * 100) if self.cost else None


@dataclass(frozen=True)
class AccountCash:
    free: float | None = None
    total: float | None = None
    invested: float | None = None
    ppl: float | None = None
    result: float | None = None
    currency: str | None = None
    raw: dict = field(default_factory=dict)


def _symbol_from(t212_ticker: str) -> str:
    """Map a Trading 212 instrument code to a market ticker.

    T212 codes look like AAPL_US_EQ or BP_EQ. This takes the leading segment,
    which matches the market ticker for the large majority of instruments. It
    is best effort and the caller should treat a miss as "unmatched" rather
    than guessing further.
    """
    return (t212_ticker or "").split("_")[0].upper()


def load_credentials() -> T212Credentials | None:
    """Environment first, then the local file. Returns None if neither exists."""
    key = os.environ.get("T212_API_KEY", "").strip()
    secret = os.environ.get("T212_API_SECRET", "").strip()
    if key:
        return T212Credentials(key, secret,
                               demo=os.environ.get("T212_DEMO", "").lower() in ("1", "true"))

    if CREDENTIALS_PATH.exists():
        values: dict[str, str] = {}
        for line in CREDENTIALS_PATH.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                name, _, value = line.partition("=")
                values[name.strip().upper()] = value.strip()
        if values.get("T212_API_KEY"):
            return T212Credentials(
                values["T212_API_KEY"], values.get("T212_API_SECRET", ""),
                demo=values.get("T212_DEMO", "").lower() in ("1", "true"),
            )
    return None


def save_credentials(key: str, secret: str = "", demo: bool = False) -> Path:
    """Write credentials to a user-only file. Never logged, never committed."""
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(
        "# Trading 212 API credentials. Treat this file like a password.\n"
        f"T212_API_KEY={key.strip()}\n"
        f"T212_API_SECRET={secret.strip()}\n"
        f"T212_DEMO={'true' if demo else 'false'}\n"
    )
    CREDENTIALS_PATH.chmod(0o600)
    return CREDENTIALS_PATH


class Trading212:
    """Read-only client. No order placement, by design."""

    def __init__(self, credentials: T212Credentials):
        self.credentials = credentials
        self._session = requests.Session()
        self._scheme: str | None = None     # remembered after the first success

    # ── Transport ────────────────────────────────────────────

    def _headers(self, scheme: str) -> dict:
        if scheme == "basic":
            token = base64.b64encode(
                f"{self.credentials.api_key}:{self.credentials.api_secret}".encode()
            ).decode()
            return {"Authorization": f"Basic {token}"}
        return {"Authorization": self.credentials.api_key}      # legacy raw key

    def _get(self, path: str) -> Any:
        url = f"{self.credentials.base_url}{path}"
        schemes = [self._scheme] if self._scheme else ["basic", "legacy"]
        last_status, last_body = None, ""

        for scheme in schemes:
            try:
                resp = self._session.get(url, headers=self._headers(scheme), timeout=TIMEOUT)
            except requests.RequestException as exc:
                raise T212Error(f"Could not reach Trading 212: {type(exc).__name__}") from exc

            if resp.status_code == 200:
                self._scheme = scheme
                return resp.json()
            if resp.status_code in (401, 403):
                last_status, last_body = resp.status_code, resp.text[:200]
                continue            # try the other auth scheme before giving up
            if resp.status_code == 429:
                raise T212Error(
                    "Trading 212 is rate limiting. Its limits are strict — the account "
                    "summary allows roughly one request every five seconds. Wait a "
                    "moment and try again."
                )
            raise T212Error(f"Trading 212 returned {resp.status_code}: {resp.text[:200]}")

        raise T212Error(
            f"Trading 212 rejected the credentials ({last_status}). Check the key and "
            f"secret are correct and still active, that the key has the needed "
            f"permissions, and that any IP restriction on it allows this machine. "
            f"{last_body}"
        )

    # ── Read endpoints ───────────────────────────────────────

    def check(self) -> dict:
        """Verify the credentials work. Returns the account metadata."""
        return self._get("/equity/account/info")

    def cash(self) -> AccountCash:
        data = self._get("/equity/account/cash") or {}
        def num(key):
            value = data.get(key)
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
        return AccountCash(
            free=num("free"), total=num("total"), invested=num("invested"),
            ppl=num("ppl"), result=num("result"),
            currency=data.get("currencyCode"), raw=data,
        )

    def positions(self) -> list:
        rows = self._get("/equity/portfolio") or []
        out = []
        for r in rows:
            try:
                qty = float(r.get("quantity") or 0)
                avg = float(r.get("averagePrice") or 0)
                cur = float(r.get("currentPrice") or 0)
                ppl = float(r.get("ppl") or 0)
            except (TypeError, ValueError):
                continue
            filled = r.get("initialFillDate")
            when = None
            if filled:
                try:
                    when = datetime.fromisoformat(str(filled).replace("Z", "+00:00"))
                except ValueError:
                    when = None
            code = r.get("ticker") or ""
            out.append(Position(code, _symbol_from(code), qty, avg, cur, ppl, when))
        return out

    def orders(self) -> list:
        """Pending orders. Reading only; this client cannot place or cancel."""
        return self._get("/equity/orders") or []

    @staticmethod
    def fetched_at() -> datetime:
        return datetime.now(timezone.utc)
