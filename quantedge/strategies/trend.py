"""
Daily trend-following hypotheses (research family A).

These are hypotheses to test, not strategies known to work.

1. EMA pullback  — in an established uptrend (fast EMA above slow EMA, both
   rising), buy when price dips to the fast EMA and closes back above it.
2. Donchian breakout with EMA filter — buy a close above the prior N-day high
   while price is above the slow EMA; exit on a close below the prior M-day low.

Both use an ATR stop fixed at entry, an optional R-multiple target, and a
time stop. All conditions are evaluated on bar t's close; fills happen at t+1.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from .base import Param, SignalStrategy
from .indicators import atr, ema, prior_high, prior_low

_COMMON = [
    Param("atr_len", "ATR length", "int", 14, 5, 50, 1, help="Bars used for Average True Range"),
    Param("stop_atr", "Stop (× ATR)", "float", 2.0, 0.5, 6.0, 0.25, help="Initial stop distance below entry"),
    Param("target_r", "Target (R)", "float", 3.0, 0.0, 10.0, 0.5, help="Profit target as multiple of risk; 0 = none"),
    Param("max_bars", "Time stop (bars)", "int", 30, 0, 250, 1, help="Exit after N bars; 0 = none"),
]


def _risk_columns(out: pd.DataFrame, bars: pd.DataFrame, p: Dict[str, Any]) -> None:
    out["atr"] = atr(bars, p["atr_len"])
    out["stop_dist"] = np.where(out["entry"], p["stop_atr"] * out["atr"], np.nan)
    out["target_r"] = p["target_r"] if p["target_r"] > 0 else np.nan
    out["max_bars"] = p["max_bars"] if p["max_bars"] > 0 else np.nan


class EmaPullback(SignalStrategy):
    key = "ema_pullback"
    name = "EMA pullback"
    family = "trend"
    description = ("Uptrend defined by fast EMA > slow EMA, both rising. Enter when the bar's low "
                   "touches the fast EMA and the close recovers above it.")
    params = [
        Param("fast", "Fast EMA", "int", 20, 5, 100, 1),
        Param("slow", "Slow EMA", "int", 50, 10, 300, 1),
        Param("slope_bars", "Slope look-back", "int", 5, 1, 30, 1, help="EMA must be higher than N bars ago"),
        Param("touch_atr", "Touch tolerance (× ATR)", "float", 0.25, 0.0, 2.0, 0.05,
              help="Low may stay this far above the fast EMA and still count as a touch"),
        *_COMMON,
    ]

    def entry_rule(self, p):
        return (f"EMA{p['fast']} > EMA{p['slow']}, both rising over {p['slope_bars']} bars; close > EMA{p['slow']}; "
                f"low ≤ EMA{p['fast']} + {p['touch_atr']}×ATR and close > EMA{p['fast']}. Buy next open.")

    def exit_rule(self, p):
        parts = [f"stop {p['stop_atr']}×ATR{p['atr_len']} below entry"]
        if p["target_r"] > 0:
            parts.append(f"target {p['target_r']}R")
        if p["max_bars"] > 0:
            parts.append(f"time stop {p['max_bars']} bars")
        parts.append(f"or close < EMA{p['slow']} (exit next open)")
        return "; ".join(parts)

    def warmup_bars(self, p):
        return int(p["slow"] * 3)

    def generate(self, bars: pd.DataFrame, p: Dict[str, Any]) -> pd.DataFrame:
        if p["fast"] >= p["slow"]:
            raise ValueError("fast EMA must be shorter than slow EMA")
        c = bars["close"]
        out = pd.DataFrame(index=bars.index)
        out["ema_fast"], out["ema_slow"] = ema(c, p["fast"]), ema(c, p["slow"])
        a = atr(bars, p["atr_len"])
        k = p["slope_bars"]
        trend = (out["ema_fast"] > out["ema_slow"]) & (out["ema_fast"] > out["ema_fast"].shift(k)) \
            & (out["ema_slow"] > out["ema_slow"].shift(k)) & (c > out["ema_slow"])
        touch = (bars["low"] <= out["ema_fast"] + p["touch_atr"] * a) & (c > out["ema_fast"])
        out["entry"] = (trend & touch).fillna(False)
        out["exit"] = (c < out["ema_slow"]).fillna(False)
        _risk_columns(out, bars, p)
        out["reason"] = ""
        out.loc[out["entry"], "reason"] = (
            f"Uptrend (EMA{p['fast']} > EMA{p['slow']}, both rising); pulled back to EMA{p['fast']} and closed above it")
        out.loc[out["exit"] & ~out["entry"], "reason"] = f"Closed below EMA{p['slow']}: trend filter lost"
        return out


class DonchianBreakout(SignalStrategy):
    key = "donchian_breakout"
    name = "Breakout + EMA filter"
    family = "trend"
    description = ("Enter on a close above the prior N-day high while price is above the slow EMA; "
                   "exit on a close below the prior M-day low.")
    params = [
        Param("entry_n", "Breakout look-back", "int", 55, 10, 250, 1),
        Param("exit_n", "Exit look-back", "int", 20, 5, 120, 1),
        Param("slow", "Trend EMA", "int", 50, 10, 300, 1),
        *_COMMON[:2],
        Param("target_r", "Target (R)", "float", 0.0, 0.0, 10.0, 0.5, help="0 = let winners run"),
        Param("max_bars", "Time stop (bars)", "int", 0, 0, 250, 1, help="0 = none"),
    ]

    def entry_rule(self, p):
        return f"Close > highest high of prior {p['entry_n']} bars and close > EMA{p['slow']}. Buy next open."

    def exit_rule(self, p):
        parts = [f"stop {p['stop_atr']}×ATR{p['atr_len']} below entry",
                 f"close < lowest low of prior {p['exit_n']} bars (exit next open)"]
        if p["target_r"] > 0:
            parts.append(f"target {p['target_r']}R")
        if p["max_bars"] > 0:
            parts.append(f"time stop {p['max_bars']} bars")
        return "; ".join(parts)

    def warmup_bars(self, p):
        return int(max(p["entry_n"], p["slow"] * 3))

    def generate(self, bars: pd.DataFrame, p: Dict[str, Any]) -> pd.DataFrame:
        c = bars["close"]
        out = pd.DataFrame(index=bars.index)
        out["ema_slow"] = ema(c, p["slow"])
        out["upper"] = prior_high(bars["high"], p["entry_n"])
        out["lower"] = prior_low(bars["low"], p["exit_n"])
        out["entry"] = ((c > out["upper"]) & (c > out["ema_slow"])).fillna(False)
        out["exit"] = (c < out["lower"]).fillna(False)
        _risk_columns(out, bars, p)
        out["reason"] = ""
        out.loc[out["entry"], "reason"] = f"Closed above the {p['entry_n']}-day high with price above EMA{p['slow']}"
        out.loc[out["exit"] & ~out["entry"], "reason"] = f"Closed below the {p['exit_n']}-day low"
        return out
