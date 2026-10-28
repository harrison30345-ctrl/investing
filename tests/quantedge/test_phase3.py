"""Phase Three support: sector-ETF universe, history back-fill, FRED request start."""
from __future__ import annotations

from datetime import date

import pytest


def test_us_sectors_universe_is_the_nine_original_spdrs():
    from quantedge.universe import UNIVERSES, get
    assert UNIVERSES["us_sectors"] == ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
    for sym in UNIVERSES["us_sectors"]:
        i = get(sym)
        assert (i.market, i.kind, i.currency) == ("US", "etf", "USD") and i.name != sym
    assert not {"XLRE", "XLC"} & set(UNIVERSES["us_sectors"])


def test_backfill_adds_older_history_without_deleting(isolated_db):
    from quantedge.data import repository as repo
    from quantedge.data.service import refresh_daily
    refresh_daily(["XLK"], years=2)
    before = repo.load_bars("demo", "XLK")
    res = refresh_daily(["XLK"], since=date(2012, 1, 3))
    after = repo.load_bars("demo", "XLK")
    assert res[0].status == "ok" and not res[0].full_reload
    assert after.index[0] <= __import__("pandas").Timestamp("2012-01-05") < before.index[0]
    assert set(before.index) <= set(after.index)                  # nothing dropped
    assert after.index.is_unique and after.index.is_monotonic_increasing


def test_backfill_must_cover_the_stored_history(isolated_db):
    from quantedge.data.service import refresh_daily
    refresh_daily(["XLK"], years=2)
    with pytest.raises(ValueError, match="first stored bar"):
        refresh_daily(["XLK"], since=date.today())


def test_fred_request_starts_in_1990_and_ignores_older_caches(tmp_path, monkeypatch):
    from quantedge import rates
    monkeypatch.setenv("QE_CASH_RATE", "fred")
    monkeypatch.setattr(rates, "HOME_DIR", tmp_path)
    (tmp_path / "rates").mkdir()
    (tmp_path / "rates" / "DTB3.csv").write_text("observation_date,DTB3\n2000-01-03,5.27\n")  # old-style cache
    seen = []

    class R:
        text = "observation_date,DTB3\n1990-01-02,7.79\n2000-01-03,5.27\n"
        def raise_for_status(self):
            pass

    monkeypatch.setattr(rates.requests, "get", lambda url, **k: seen.append(url) or R())
    import pandas as pd
    s, src = rates.annual_rates("US", pd.DatetimeIndex(["1999-06-01"]))
    assert "cosd=1990-01-01" in seen[0] and src == "FRED DTB3"
    assert s.iloc[0] == pytest.approx(0.0779)
