"""
Refresh orchestration: fetch → clean → validate → store → log.

Incremental updates re-request a short overlap window (OVERLAP_DAYS) so a
vendor's late corrections to the latest bars get picked up.

Corporate actions: when a stock pays a dividend or splits, the vendor
rewrites the *whole* adjusted-close history. If the adjustment ratio
(adj_close / close) on an overlapping bar no longer matches what we
stored, we refetch the full history for that symbol. Otherwise old and new
adjustments would be stitched together into a wrong series.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional

import pandas as pd

from ..config import get_settings
from ..db import session_scope
from ..db.models import JobRun, utcnow
from ..markets import last_completed_session
from ..universe import get as catalogue_get
from . import repository as repo
from .providers import MarketDataProvider, ProviderError, SymbolNotFound, get_provider
from .validation import clean_daily, validate_daily

log = logging.getLogger(__name__)

OVERLAP_DAYS = 7
ADJ_TOLERANCE = 0.002


@dataclass
class RefreshResult:
    symbol: str
    status: str            # ok | not_found | error
    rows: int = 0
    full_reload: bool = False
    message: str = ""
    quality_ok: Optional[bool] = None


def _plan_start(session, provider_key: str, symbol: str, years: int) -> date:
    last = repo.last_bar_ts(session, provider_key, symbol)
    if last is None:
        return date.today() - timedelta(days=int(365.25 * years))
    return (last - timedelta(days=OVERLAP_DAYS)).date()


def _adjustment_changed(session, provider_key: str, symbol: str, df: pd.DataFrame) -> bool:
    for ts, row in df.head(OVERLAP_DAYS).iterrows():
        stored = repo.adj_ratio_at(session, provider_key, symbol, ts.to_pydatetime())
        if stored is None or pd.isna(row["adj_close"]) or not row["close"]:
            continue
        if abs(row["adj_close"] / row["close"] - stored) > ADJ_TOLERANCE * stored:
            return True
    return False


def refresh_daily(symbols: Iterable[str], provider: Optional[MarketDataProvider] = None,
                  years: Optional[int] = None, trigger: str = "manual") -> List[RefreshResult]:
    provider = provider or get_provider()
    pkey = provider.info.key
    years = years or get_settings().history_years
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    end = date.today()
    results: List[RefreshResult] = []

    with session_scope() as s:
        job = JobRun(job="refresh_daily", status="running", trigger=trigger,
                     detail=f"{len(symbols)} symbols via {pkey}")
        s.add(job)
        s.flush()
        job_id = job.id
        starts = {sym: _plan_start(s, pkey, sym, years) for sym in symbols}

    # Group symbols by start date so batch-capable vendors make few requests.
    by_start: dict = {}
    for sym, st in starts.items():
        by_start.setdefault(st, []).append(sym)

    fetched: dict = {}
    for st, group in by_start.items():
        t0 = time.monotonic()
        try:
            fetched.update(provider.fetch_daily_many(group, st, end))
        except ProviderError as exc:
            fetched.update({g: exc for g in group})
        elapsed = int((time.monotonic() - t0) * 1000 / max(len(group), 1))
        for g in group:
            fetched.setdefault(g, ProviderError("no response"))
            fetched[g] = (fetched[g], elapsed)

    for sym in symbols:
        payload, ms = fetched[sym]
        inst = catalogue_get(sym)
        with session_scope() as s:
            repo.ensure_instrument(s, sym)
            if isinstance(payload, SymbolNotFound):
                repo.log_fetch(s, pkey, sym, "1d", "not_found", message=str(payload), duration_ms=ms)
                results.append(RefreshResult(sym, "not_found", message=str(payload)))
                continue
            if isinstance(payload, Exception):
                repo.log_fetch(s, pkey, sym, "1d", "error", message=str(payload), duration_ms=ms)
                results.append(RefreshResult(sym, "error", message=str(payload)))
                continue

            df = clean_daily(payload)
            full = False
            if repo.last_bar_ts(s, pkey, sym) is not None and _adjustment_changed(s, pkey, sym, df):
                full = True
                try:
                    df = clean_daily(provider.fetch_daily(
                        sym, date.today() - timedelta(days=int(365.25 * years)), end))
                except ProviderError as exc:
                    repo.log_fetch(s, pkey, sym, "1d", "error", message=f"full reload failed: {exc}")
                    results.append(RefreshResult(sym, "error", message=str(exc)))
                    continue
                repo.delete_bars(s, pkey, sym, "1d")

            n = repo.upsert_bars(s, pkey, sym, "1d", df)
            repo.log_fetch(s, pkey, sym, "1d", "ok", rows=n, duration_ms=ms,
                           message="full reload (adjustments changed)" if full else None)

        stored = repo.load_bars(pkey, sym)
        q = validate_daily(stored, sym, inst.market, is_index=inst.kind == "index",
                           expected_last=last_completed_session(inst.market))
        with session_scope() as s:
            repo.save_quality(s, pkey, "1d", q)
        results.append(RefreshResult(sym, "ok", n, full, quality_ok=q.ok))

    ok = sum(r.status == "ok" for r in results)
    with session_scope() as s:
        job = s.get(JobRun, job_id)
        job.finished_at = utcnow()
        job.status = "ok" if ok == len(results) else ("partial" if ok else "failed")
        job.detail = f"{ok}/{len(results)} symbols updated via {pkey}"
    return results
