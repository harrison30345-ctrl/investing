"""Shared fixtures: an isolated database per test and synthetic price helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Point QuantEdge at a throwaway SQLite file and demo data."""
    monkeypatch.setenv("QE_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("QE_DATA_PROVIDER", "demo")
    from quantedge import config
    from quantedge.db import get_engine
    config.get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    config.get_settings.cache_clear()
    get_engine.cache_clear()


def make_bars(closes, start="2024-01-02", spread=0.01):
    """OHLC bars where open = previous close and high/low are ±spread around the bar."""
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(closes))
    opens = np.r_[closes[0], closes[:-1]]
    high = np.maximum(opens, closes) * (1 + spread)
    low = np.minimum(opens, closes) * (1 - spread)
    return pd.DataFrame({"open": opens, "high": high, "low": low, "close": closes,
                         "adj_close": closes, "volume": 1e6}, index=idx)


def random_walk(n=600, seed=1, drift=0.0005, vol=0.015, start="2020-01-01"):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    return make_bars(c, start=start)
