"""
Market data access.

All external market-data calls go through this module. The UI must never
import yfinance directly, so that swapping providers is a change here and
nowhere else.

LICENSING -- READ BEFORE LAUNCH
-------------------------------
The current provider is yfinance, which scrapes Yahoo Finance. It has no API
key, no contract, no rate-limit entitlement, and Yahoo's terms do not permit
commercial redistribution of the data. It is fine for personal and development
use. It is NOT a lawful basis for a paid product.

Before charging anyone, replace YFinanceProvider with a commercially licensed
feed (Polygon, Tiingo, EODHD or similar) and confirm the licence covers
*redistribution* -- showing data to paying users -- which vendors price
separately from internal use. Implement MarketDataProvider and swap the
instance returned by get_provider(); nothing else should need to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

__all__ = [
    "CompanySnapshot", "MarketDataProvider", "YFinanceProvider",
    "get_provider", "PROVIDER_NAME", "PROVIDER_IS_LICENSED",
]

PROVIDER_NAME = "Yahoo Finance (via yfinance)"
PROVIDER_IS_LICENSED = False  # must be True before any paid launch


@dataclass(frozen=True)
class CompanySnapshot:
    """Everything the scoring engine and stock page need for one company."""
    ticker: str
    name: str
    sector: str | None
    industry: str | None
    currency: str | None
    price: float | None
    fundamentals: dict[str, Any]
    history: pd.DataFrame | None
    fetched_at: datetime
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.price is not None


class MarketDataProvider(ABC):
    """Interface a licensed provider must implement to replace yfinance."""

    @abstractmethod
    def get_snapshot(self, ticker: str) -> CompanySnapshot: ...

    @abstractmethod
    def get_history(self, ticker: str, period: str = "1y") -> pd.DataFrame | None: ...


def _num(value: Any) -> float | None:
    """Coerce to float or None. Never substitutes a value -- see scoring.py."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _pct_change(closes: pd.Series, days: int) -> float | None:
    if len(closes) <= days:
        return None
    prev = float(closes.iloc[-days - 1])
    if prev <= 0:
        return None
    return (float(closes.iloc[-1]) / prev - 1) * 100


class YFinanceProvider(MarketDataProvider):
    """Development provider. See the licensing note at the top of this module."""

    def get_history(self, ticker: str, period: str = "1y") -> pd.DataFrame | None:
        try:
            hist = yf.Ticker(ticker).history(period=period)
        except Exception:
            return None
        return None if hist is None or hist.empty else hist

    def get_snapshot(self, ticker: str) -> CompanySnapshot:
        now = datetime.now(timezone.utc)
        ticker = ticker.strip().upper()

        try:
            tk = yf.Ticker(ticker)
            info = tk.info or {}
        except Exception as exc:
            return CompanySnapshot(ticker, ticker, None, None, None, None, {}, None,
                                   now, f"Could not reach the data provider ({type(exc).__name__}).")

        hist = self.get_history(ticker, "1y")
        if hist is None or not info.get("shortName"):
            return CompanySnapshot(ticker, info.get("shortName") or ticker,
                                   info.get("sector"), info.get("industry"),
                                   info.get("currency"), None, {}, None, now,
                                   f"No data found for {ticker}. Check the symbol is correct.")

        closes = hist["Close"].dropna()
        price = float(closes.iloc[-1]) if len(closes) else None

        # Momentum inputs are derived from price history rather than read from
        # the provider, so they stay available even when fundamentals are thin.
        fundamentals: dict[str, Any] = {}
        for field in (
            "returnOnEquity", "profitMargins", "operatingMargins",
            "revenueGrowth", "earningsGrowth",
            "trailingPE", "forwardPE", "priceToSalesTrailing12Months",
            "debtToEquity", "currentRatio", "freeCashflow",
            "marketCap", "beta", "dividendYield", "targetMeanPrice",
            "recommendationMean", "numberOfAnalystOpinions",
        ):
            value = _num(info.get(field))
            if value is not None:  # absent stays absent -- never coerced to 0
                fundamentals[field] = value

        for key, days in (("chg_1w", 5), ("chg_1m", 21), ("chg_3m", 63)):
            change = _pct_change(closes, days)
            if change is not None:
                fundamentals[key] = change

        if len(closes) >= 50:
            sma50 = float(closes.rolling(50).mean().iloc[-1])
            if sma50 > 0 and price:
                fundamentals["vs_sma50"] = (price - sma50) / sma50 * 100

        return CompanySnapshot(
            ticker=ticker,
            name=info.get("shortName") or ticker,
            sector=info.get("sector"),
            industry=info.get("industry"),
            currency=info.get("currency"),
            price=price,
            fundamentals=fundamentals,
            history=hist,
            fetched_at=now,
        )


_provider: MarketDataProvider = YFinanceProvider()


def get_provider() -> MarketDataProvider:
    """The active provider. Swap the instance here to change data source."""
    return _provider
