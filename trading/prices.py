from __future__ import annotations

"""
Live-ish price feed for the paper simulator. Uses yfinance (already a project
dependency) to get the latest trade price. Cached briefly so a single engine
tick doesn't hammer the API for the same symbol.
"""

import logging
import time

import yfinance as yf

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, float]] = {}  # symbol -> (price, fetched_at)
_TTL_SECONDS = 60


def get_price(symbol: str) -> float | None:
    """Return the latest price for `symbol`, or None if unavailable."""
    now = time.time()
    cached = _CACHE.get(symbol)
    if cached and now - cached[1] < _TTL_SECONDS:
        return cached[0]

    price = _fetch_price(symbol)
    if price is not None:
        _CACHE[symbol] = (price, now)
    return price


def _fetch_price(symbol: str) -> float | None:
    try:
        t = yf.Ticker(symbol)
        # fast_info is the cheapest path to a last price.
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            for key in ("last_price", "lastPrice", "regular_market_price"):
                val = _safe_get(fi, key)
                if val:
                    return float(val)
        # Fallback: last close from a 1-day history.
        hist = t.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:  # noqa: BLE001 — price feed must never crash the loop
        logger.warning("price fetch failed for %s: %s", symbol, e)
    return None


def _safe_get(fast_info, key: str):
    try:
        if isinstance(fast_info, dict):
            return fast_info.get(key)
        return getattr(fast_info, key, None)
    except Exception:
        return None
