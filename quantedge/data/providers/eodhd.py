"""
EODHD (eodhd.com): the recommended paid upgrade.

$19.99/mo gets end-of-day data for all exchanges; $29.99/mo adds intraday (prices as of
Oct 2026). Licensed data with LSE (`.LSE`) and US (`.US`) coverage.

STATUS: implemented from EODHD's public API docs but **not yet run against
a live key**. Verify with `python3 -m quantedge.cli health` after adding
EODHD_API_KEY.

Symbol mapping from internal Yahoo-style symbols:
    AAPL   -> AAPL.US
    SHEL.L -> SHEL.LSE
    ^GSPC  -> GSPC.INDX
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pandas as pd
import requests

from .base import (MarketDataProvider, NotSupported, ProviderError, ProviderInfo, RateLimited,
                   SymbolNotFound, normalise, with_retries)

_BASE = "https://eodhd.com/api"
_INTERVALS = ("1m", "5m", "1h")


def to_eodhd(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("^"):
        return f"{s[1:]}.INDX"
    if s.endswith(".L"):
        return f"{s[:-2]}.LSE"
    return f"{s}.US"


class EODHDProvider(MarketDataProvider):
    info = ProviderInfo(
        key="eodhd", name="EODHD", latency="End of day", is_demo=False, requires_key=True,
        markets=("UK", "US"), daily=True, intraday=True, intraday_intervals=_INTERVALS,
        max_intraday_days=120, licence_note="Licensed per EODHD plan terms (personal use on retail plans).",
        notes=["Intraday requires the EOD+Intraday plan", "Adapter not yet verified against a live key"],
    )

    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderError("EODHD_API_KEY is not set")
        self._key = api_key
        self._http = requests.Session()

    def _get(self, path: str, **params):
        params.update(api_token=self._key, fmt="json")

        def _call():
            try:
                r = self._http.get(f"{_BASE}/{path}", params=params, timeout=20)
            except requests.RequestException as exc:
                raise ProviderError(f"EODHD request failed: {exc}") from exc
            if r.status_code == 404:
                raise SymbolNotFound(f"EODHD: {path} not found")
            if r.status_code == 429:
                raise RateLimited("EODHD rate limit reached")
            if r.status_code in (401, 403):
                raise ProviderError(f"EODHD rejected the API key or plan ({r.status_code})")
            if not r.ok:
                raise ProviderError(f"EODHD error {r.status_code}: {r.text[:200]}")
            return r.json()

        return with_retries(_call, retry_on=(RateLimited, ProviderError))

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        rows = self._get(f"eod/{to_eodhd(symbol)}", **{"from": start.isoformat(), "to": end.isoformat()})
        if not rows:
            raise SymbolNotFound(f"{symbol}: no data returned by EODHD")
        df = pd.DataFrame(rows).set_index("date")
        df.index = pd.to_datetime(df.index)
        return normalise(df)

    def fetch_intraday(self, symbol: str, interval: str, start: date, end: date) -> pd.DataFrame:
        if interval not in _INTERVALS:
            raise NotSupported(f"EODHD intraday interval {interval!r} not supported")
        to_ts = lambda d, t: int(datetime.combine(d, t, tzinfo=timezone.utc).timestamp())  # noqa: E731
        rows = self._get(f"intraday/{to_eodhd(symbol)}", interval=interval,
                         **{"from": to_ts(start, time.min), "to": to_ts(end, time.max)})
        if not rows:
            raise SymbolNotFound(f"{symbol}: no intraday data returned by EODHD")
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["timestamp"], unit="s")
        return normalise(df)
