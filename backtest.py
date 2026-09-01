"""
Backtest for the Hedge Fund Engine's scoring model.

WHAT THIS CAN AND CANNOT PROVE
------------------------------
The dashboard's four strategies do not have equal standing under a backtest:

  Momentum  — 100% technical (RSI, moving averages, price change, volume).
              Fully reconstructible from history. TESTED HERE.
  Breakout  — 100% technical (52w range, ATR, volume, MAs).
              Fully reconstructible from history. TESTED HERE.
  Bounce    — 65% technical, 35% fundamentals (net margin, ROE).
              Tested with the fundamental block held neutral.
  Catalyst  — ~100% fundamentals and analyst estimates (revenue growth,
              earnings growth, price targets, forward PE).
              NOT TESTABLE. yfinance returns today's values, not what was
              known on the rebalance date. Scoring history with them is
              look-ahead bias and would produce fake results.

Known biases that flatter these numbers, stated up front:
  * Survivorship — the universe is today's ticker list, so companies that
    were delisted or went to zero never appear.
  * No costs — no spread, commission, slippage or tax.
  * Point-in-time 52w high/low is derived from the price window, which is
    correct, but sector/universe membership is still as-of-today.

Usage:  python3 backtest.py [--years 5] [--top 15] [--hold 21]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "screener"))

try:
    from screener.universe import get_universe
except ImportError:
    from universe import get_universe


# ── Scoring — mirrors screener/dashboard.py, technical components only ──

def _rsi(closes: pd.Series, n: int = 14) -> float:
    d = closes.diff().dropna()
    if len(d) < n:
        return 50.0
    gain = d.clip(lower=0).rolling(n).mean()
    loss = (-d.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    v = (100 - 100 / (1 + rs)).iloc[-1]
    return 50.0 if pd.isna(v) else float(v)


def score_asof(hist: pd.DataFrame) -> dict | None:
    """Score one ticker using ONLY the rows in `hist` (data up to that date)."""
    if len(hist) < 60:
        return None
    closes = hist["Close"].dropna()
    vols = hist["Volume"].dropna()
    if len(closes) < 60 or closes.iloc[-1] <= 0:
        return None

    price = float(closes.iloc[-1])
    sma20 = float(closes.rolling(20).mean().iloc[-1])
    sma50 = float(closes.rolling(min(50, len(closes))).mean().iloc[-1])
    if not np.isfinite(sma20) or not np.isfinite(sma50) or sma20 <= 0 or sma50 <= 0:
        return None

    vs_20 = (price - sma20) / sma20 * 100
    vs_50 = (price - sma50) / sma50 * 100
    ma_aligned = sma20 > sma50
    rsi = _rsi(closes)

    chg_1w = (price / float(closes.iloc[-6]) - 1) * 100 if len(closes) > 6 else 0.0
    chg_1m = (price / float(closes.iloc[-22]) - 1) * 100 if len(closes) > 22 else 0.0

    vol_5d = float(vols.iloc[-5:].mean()) if len(vols) >= 5 else 0.0
    vol_20d = float(vols.iloc[-20:].mean()) if len(vols) >= 20 else 0.0
    vol_surge = vol_5d / vol_20d if vol_20d > 0 else 1.0

    # ATR% over the trailing window
    win = hist.iloc[-60:]
    tr = pd.concat([
        win["High"] - win["Low"],
        (win["High"] - win["Close"].shift(1)).abs(),
        (win["Low"] - win["Close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else price * 0.02
    atr_pct = atr / price * 100 if price > 0 else 2.0

    # 52-week positioning, computed point-in-time from the price window
    w = closes.iloc[-252:] if len(closes) >= 252 else closes
    hi, lo = float(w.max()), float(w.min())
    pct_range = (price - lo) / (hi - lo) * 100 if hi > lo else 50.0

    # ── Strategy scores (identical formulas to the dashboard) ──
    rsi_mom = min(100, max(0, 100 - abs(rsi - 62) * 4))
    ma_mom = (30 if vs_20 > 0 else 0) + (30 if vs_50 > 0 else 0) + (40 if ma_aligned else 0)
    chg_mom = min(100, max(0, (chg_1w * 3 + chg_1m) / 4 * 5 + 50))
    vol_mom = min(100, max(0, (vol_surge - 0.8) / 1.2 * 100))
    momentum = rsi_mom * 0.25 + ma_mom * 0.30 + chg_mom * 0.25 + vol_mom * 0.20

    near_high = min(100, max(0, 100 - abs(pct_range - 91) * 3.5))
    vol_coiling = min(100, max(0, (1.5 - atr_pct) / 1.5 * 100))
    vol_expand = min(100, max(0, (vol_surge - 0.9) / 1.1 * 100))
    above_mas = (50 if vs_20 > 0 else 0) + (50 if vs_50 > 0 else 0)
    breakout = near_high * 0.35 + vol_coiling * 0.25 + vol_expand * 0.20 + above_mas * 0.20

    # Bounce with the fundamental block held neutral (50) — see module docstring
    rsi_bounce = min(100, max(0, (45 - rsi) / 20 * 100))
    trend_intact = min(100, max(0, (vs_50 + 20) / 25 * 100))
    support_prox = min(100, max(0, 100 - pct_range))
    bounce = rsi_bounce * 0.35 + trend_intact * 0.25 + 50.0 * 0.25 + support_prox * 0.15

    return {
        "momentum": momentum,
        "breakout": breakout,
        "bounce": bounce,
        "best": max(momentum, breakout, bounce),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--top", type=int, default=15, help="picks per rebalance")
    ap.add_argument("--hold", type=int, default=21, help="trading days held")
    ap.add_argument("--universe", default="broad")
    args = ap.parse_args()

    tickers = get_universe(args.universe)
    print(f"Universe: {args.universe} ({len(tickers)} tickers)")
    print(f"Downloading {args.years}y of daily data…")

    raw = yf.download(tickers + ["SPY"], period=f"{args.years + 1}y", interval="1d",
                      auto_adjust=True, group_by="ticker", threads=True, progress=False)

    data: dict[str, pd.DataFrame] = {}
    for t in tickers + ["SPY"]:
        try:
            df = raw[t].dropna(how="all")
            if len(df) > 300:
                data[t] = df
        except (KeyError, TypeError):
            pass
    print(f"Usable price history: {len(data)-1} tickers + SPY\n")

    spy = data.get("SPY")
    if spy is None:
        print("ERROR: no SPY data for the benchmark.")
        return 1

    # Monthly rebalance dates
    dates = sorted(spy.index)
    rebal = [d for i, d in enumerate(dates) if i >= 260 and i % args.hold == 0
             and i + args.hold < len(dates)]
    print(f"Rebalances: {len(rebal)} (every {args.hold} trading days)\n")

    rows = []
    for d in rebal:
        scored = []
        for t, df in data.items():
            if t == "SPY":
                continue
            hist = df[df.index <= d]
            fut = df[df.index > d]
            if len(fut) < args.hold:
                continue
            sc = score_asof(hist)
            if sc is None:
                continue
            p0 = float(hist["Close"].iloc[-1])
            p1 = float(fut["Close"].iloc[args.hold - 1])
            if p0 <= 0:
                continue
            sc["ticker"] = t
            sc["fwd"] = (p1 / p0 - 1) * 100
            scored.append(sc)

        if len(scored) < 30:
            continue
        sdf = pd.DataFrame(scored)

        # Benchmarks
        univ_ret = sdf["fwd"].mean()
        s_hist = spy[spy.index <= d]
        s_fut = spy[spy.index > d]
        spy_ret = (float(s_fut["Close"].iloc[args.hold - 1]) / float(s_hist["Close"].iloc[-1]) - 1) * 100

        rec = {"date": d.date(), "n": len(sdf), "universe": univ_ret, "spy": spy_ret}
        for strat in ["best", "momentum", "breakout", "bounce"]:
            top = sdf.nlargest(args.top, strat)
            rec[strat] = top["fwd"].mean()
        # High-conviction bucket, matching the dashboard's >=72 threshold
        hc = sdf[sdf["best"] >= 72]
        rec["high_conv"] = hc["fwd"].mean() if len(hc) else np.nan
        rec["high_conv_n"] = len(hc)
        rows.append(rec)

    if not rows:
        print("ERROR: no rebalance periods produced results.")
        return 1

    r = pd.DataFrame(rows)
    per_year = 252 / args.hold

    print("=" * 74)
    print(f"RESULTS — {len(r)} periods, top {args.top} picks, {args.hold}-day hold")
    print(f"{r['date'].iloc[0]} to {r['date'].iloc[-1]}")
    print("=" * 74)
    print(f"{'Strategy':<16}{'Avg/period':>12}{'Annualised':>13}{'Win rate':>11}{'vs SPY':>11}")
    print("-" * 74)

    def line(label, col):
        s = r[col].dropna()
        if s.empty:
            print(f"{label:<16}{'no data':>12}")
            return
        avg = s.mean()
        ann = ((1 + avg / 100) ** per_year - 1) * 100
        win = (s > 0).mean() * 100
        beat = (r[col] > r["spy"]).mean() * 100
        print(f"{label:<16}{avg:>11.2f}%{ann:>12.1f}%{win:>10.0f}%{beat:>10.0f}%")

    line("Best score", "best")
    line("Momentum", "momentum")
    line("Breakout", "breakout")
    line("Bounce", "bounce")
    line("High conv (>=72)", "high_conv")
    print("-" * 74)
    line("Universe avg", "universe")
    line("SPY", "spy")
    print("=" * 74)

    print(f"\nAvg high-conviction picks per rebalance: {r['high_conv_n'].mean():.1f}")
    print("\nNOTE: Catalyst is untested — it scores on current fundamentals and")
    print("analyst estimates, which cannot be reconstructed for past dates.")
    print("Results carry survivorship bias and exclude all trading costs.")

    out = Path("backtest_results.csv")
    r.to_csv(out, index=False)
    print(f"\nPer-period detail written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
