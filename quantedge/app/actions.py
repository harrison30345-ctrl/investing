"""User-triggered actions that write data. Each one clears the read cache afterwards."""
from __future__ import annotations

from typing import Iterable, List

import streamlit as st

from quantedge.data.service import RefreshResult, refresh_daily
from quantedge.signals import scan_signals
from quantedge.universe import BENCHMARKS, INDICES
from quantedge.watchlists import all_watched_symbols

from . import data as D


def core_symbols() -> List[str]:
    return [i.symbol for i in INDICES + BENCHMARKS] + all_watched_symbols()


def refresh(symbols: Iterable[str], label: str = "Fetching daily bars") -> List[RefreshResult]:
    symbols = list(dict.fromkeys(symbols))
    with st.status(f"{label} for {len(symbols)} symbols…", expanded=False) as box:
        results = refresh_daily(symbols, trigger="manual")
        ok = [r for r in results if r.status == "ok"]
        box.write(f"{len(ok)}/{len(results)} updated")
        scan_signals([r.symbol for r in ok], trigger="manual")
        bad = [r for r in results if r.status != "ok"]
        for r in bad:
            box.write(f"{r.symbol}: {r.status}: {r.message}")
        box.update(label=f"Data refresh: {len(ok)}/{len(results)} symbols updated",
                   state="complete" if not bad else "error")
    D.clear()
    return results
