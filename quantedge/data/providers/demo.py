"""
Synthetic demo data, for offline development and UI work.

Prices are a seeded random walk: deterministic per symbol, plausible-looking
and completely fictional. Everything is stored under provider "demo", and the
app shows a banner whenever this provider is active. Never use these numbers for decisions.
"""
from __future__ import annotations

import zlib
from datetime import date

import numpy as np
import pandas as pd

from ...markets import market_for_symbol, sessions
from .base import MarketDataProvider, ProviderInfo, normalise

_ANCHOR = date(2010, 1, 4)  # walk always starts here so values are stable across calls


class DemoProvider(MarketDataProvider):
    info = ProviderInfo(
        key="demo", name="Demo (synthetic)", latency="Synthetic", is_demo=True, requires_key=False,
        markets=("UK", "US"), daily=True, intraday=False,
        licence_note="Synthetic data generated locally. Not market data.",
        notes=["Seeded random walk; deterministic per symbol"],
    )

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        days = sessions(market_for_symbol(symbol), _ANCHOR, end)
        seed = zlib.crc32(symbol.upper().encode())
        rng = np.random.default_rng(seed)
        n = len(days)
        drift, vol = 0.0003 + (seed % 7) * 0.00005, 0.011 + (seed % 5) * 0.003
        rets = rng.normal(drift, vol, n)
        close = (50 + seed % 400) * np.exp(np.cumsum(rets))
        open_ = close * np.exp(rng.normal(0, vol / 3, n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                           "adj_close": close, "volume": rng.integers(1e5, 5e6, n).astype(float)},
                          index=days)
        return normalise(df.loc[pd.Timestamp(start):pd.Timestamp(end)])
