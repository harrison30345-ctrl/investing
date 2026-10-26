"""
Market-data provider interface.

Strategy and backtest code never talks to a vendor directly. It asks for
bars through this interface, so a vendor can be swapped by changing
`QE_DATA_PROVIDER`.

Every provider returns bars in one shape:

    index   : DatetimeIndex, naive. Daily bars use the exchange-local trading
              date at midnight; intraday bars use naive UTC.
    columns : open, high, low, close, adj_close, volume   (floats)

`adj_close` is the dividend- and split-adjusted close where the vendor
supplies it, otherwise NaN.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, Iterable, List, Optional, Tuple, TypeVar

import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]


class ProviderError(RuntimeError):
    """A request failed (network, auth, vendor error)."""


class SymbolNotFound(ProviderError):
    """The vendor doesn't know the symbol, or returned no rows for it."""


class RateLimited(ProviderError):
    """The vendor asked us to slow down."""


class NotSupported(ProviderError):
    """The provider doesn't offer this capability (e.g. intraday)."""


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    name: str
    latency: str                 # "Delayed" | "End of day" | "Real-time" | "Synthetic"
    is_demo: bool
    requires_key: bool
    markets: Tuple[str, ...]
    daily: bool
    intraday: bool
    intraday_intervals: Tuple[str, ...] = ()
    max_intraday_days: int = 0
    licence_note: str = ""
    notes: List[str] = field(default_factory=list)


T = TypeVar("T")


def with_retries(fn: Callable[[], T], attempts: int = 3, base_delay: float = 1.0,
                 retry_on: Tuple[type, ...] = (ProviderError,)) -> T:
    """Exponential backoff. SymbolNotFound is never retried: it won't fix itself."""
    for i in range(attempts):
        try:
            return fn()
        except SymbolNotFound:
            raise
        except retry_on:
            if i == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** i))
    raise AssertionError("unreachable")


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=BAR_COLUMNS, index=pd.DatetimeIndex([], name="ts"), dtype=float)


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a vendor frame into the standard shape; leaves data errors for validation."""
    out = df.copy()
    for col in BAR_COLUMNS:
        if col not in out.columns:
            out[col] = float("nan")
    out = out[BAR_COLUMNS].astype(float)
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx.rename("ts")
    return out


class MarketDataProvider(ABC):
    info: ProviderInfo

    @abstractmethod
    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Daily bars for start..end inclusive. Raises SymbolNotFound or ProviderError."""

    def fetch_daily_many(self, symbols: Iterable[str], start: date, end: date
                         ) -> Dict[str, "pd.DataFrame | ProviderError"]:
        """Batch fetch. Default loops; vendors with batch endpoints override."""
        out: Dict[str, "pd.DataFrame | ProviderError"] = {}
        for s in symbols:
            try:
                out[s] = with_retries(lambda: self.fetch_daily(s, start, end))
            except ProviderError as exc:
                out[s] = exc
        return out

    def fetch_intraday(self, symbol: str, interval: str, start: date, end: date) -> pd.DataFrame:
        raise NotSupported(f"{self.info.name} does not provide intraday bars")

    def healthcheck(self) -> Tuple[bool, str]:
        """Cheap request to confirm the vendor is reachable."""
        try:
            today = date.today()
            df = self.fetch_daily("SPY", today - pd.Timedelta(days=10), today)
            return (not df.empty, f"{len(df)} bars returned")
        except ProviderError as exc:
            return False, str(exc)

    def describe_symbol(self, symbol: str) -> Optional[dict]:
        """Optional metadata lookup (name, currency, exchange)."""
        return None
