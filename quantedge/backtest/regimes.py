"""
Per-regime breakdown (audit fix 6): how did a result behave in different markets?

Each day is labelled by the benchmark's state at the *previous* close, so labels use no
information from the day being measured:
  trend:      uptrend   = benchmark above its 200-day average,  downtrend = below
  volatility: high-vol  = 21-day realised volatility above its trailing 1-year median, else low-vol

For each regime: share of days, annualised strategy and benchmark return, and the strategy's
Sharpe above cash. A strategy whose whole result comes from one regime is fragile, and a
validation window that never visits a regime can't speak for it.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

import pandas as pd

MIN_DAYS = 20
REGIMES = ("uptrend", "downtrend", "highvol", "lowvol")


def labels(bench: pd.Series) -> pd.DataFrame:
    """Causal regime flags on the benchmark's own (full) history, lagged one day."""
    b = bench.dropna()
    up = b > b.rolling(200, min_periods=200).mean()
    vol = b.pct_change().rolling(21, min_periods=21).std() * math.sqrt(252)
    med = vol.rolling(252, min_periods=126).median()
    hv = vol > med
    known = b.rolling(200, min_periods=200).mean().notna() & med.notna()
    out = pd.DataFrame({"uptrend": up, "highvol": hv, "known": known}).shift(1)
    return out.fillna({"known": False})


def regime_breakdown(equity: pd.Series, bench_full: Optional[pd.Series],
                     rf: Optional[pd.Series] = None) -> Dict[str, float]:
    if bench_full is None or bench_full.dropna().size < 260 or len(equity) < MIN_DAYS:
        return {}
    lab = labels(bench_full).reindex(equity.index, method="ffill")
    known = lab["known"].fillna(False).astype(bool)
    r = equity.pct_change()
    b = bench_full.reindex(equity.index, method="ffill").pct_change()
    cash = rf.reindex(equity.index).fillna(0.0) if rf is not None else pd.Series(0.0, index=equity.index)
    masks = {"uptrend": lab["uptrend"] == True, "downtrend": lab["uptrend"] == False,  # noqa: E712
             "highvol": lab["highvol"] == True, "lowvol": lab["highvol"] == False}  # noqa: E712
    out: Dict[str, float] = {}
    total = int(known.iloc[1:].sum()) or 1
    for name in REGIMES:
        m = (masks[name] & known).iloc[1:]
        days = int(m.sum())
        out[f"regime_{name}_days_pct"] = days / total
        if days < MIN_DAYS:
            continue
        rr, bb, cc = r.iloc[1:][m], b.iloc[1:][m], cash.iloc[1:][m]
        out[f"regime_{name}_ann"] = float(rr.mean() * 252)
        out[f"regime_{name}_bench_ann"] = float(bb.mean() * 252)
        ex = rr - cc
        sd = ex.std(ddof=1)
        out[f"regime_{name}_sharpe"] = float(ex.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0
    return out
