"""
Intraday hypotheses for US stocks (research family C). Long only, 5-minute bars.

Both strategies share these session rules, enforced in two places:
  * Strategy: no new entries before the setup is complete or after `entry_cutoff`
    minutes from the open, and never on a session's final two bars.
  * Engine: any open position is sold at the close of the session's last bar
    ("session_close"), and queued orders are cancelled, so nothing is held overnight.

Bars arrive as naive UTC. Session times are worked out in New York time. Early-close days end
earlier, and the engine's session-end rule still applies.

1. Opening-range breakout (ORB): the opening range is the high/low of the first
   `or_minutes`. Buy the first close above the range high. Stop at the range low
   (or midpoint), optional R-multiple target, optional exit on a close back below
   the range midpoint (a failed breakout).
2. VWAP trend continuation: after the first `start_minutes`, in a session where VWAP
   is rising and price holds above it, buy a bullish bar that dips to VWAP and
   closes back above it. Stop a multiple of 5-minute ATR below, exit on a close
   below VWAP.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from .base import Param, SignalStrategy
from .indicators import atr

NY = "America/New_York"
OPEN_MINUTE = 9 * 60 + 30


def session_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """Session date, minutes since 09:30 ET, and position-in-session for each bar."""
    local = pd.DatetimeIndex(bars.index).tz_localize("UTC").tz_convert(NY)
    f = pd.DataFrame(index=bars.index)
    f["session"] = local.normalize().tz_localize(None)
    f["minute"] = local.hour * 60 + local.minute - OPEN_MINUTE
    return f


def _no_entry_near_close(f: pd.DataFrame) -> pd.Series:
    """
    Blocks entries on the last bars of a regular session (bars starting after 15:45 ET).
    The session's length isn't known until it ends, so this uses time of day, not "bars remaining".
    """
    return f["minute"] <= 390 - 15


class OpeningRangeBreakout(SignalStrategy):
    key = "orb"
    name = "Opening-range breakout"
    family = "intraday"
    timeframe = "intraday"
    description = "Buy the first 5-minute close above the opening range high; flat by the close."
    params = [
        Param("or_minutes", "Opening range (minutes)", "int", 15, 5, 60, 5),
        Param("entry_cutoff", "No entries after (min from open)", "int", 120, 30, 330, 15),
        Param("stop_at", "Stop at", "choice", "or_low", choices=("or_low", "or_mid")),
        Param("target_r", "Target (R)", "float", 2.0, 0.0, 10.0, 0.5, help="0 = hold to the close"),
        Param("exit_on_failure", "Exit on close below range midpoint", "bool", True),
    ]

    def entry_rule(self, p):
        return (f"Opening range = first {p['or_minutes']} min (09:30 ET). First 5-min close above the range high, "
                f"between {p['or_minutes']} and {p['entry_cutoff']} min after the open. Buy next bar's open.")

    def exit_rule(self, p):
        parts = [f"stop at range {'low' if p['stop_at'] == 'or_low' else 'midpoint'}"]
        if p["target_r"] > 0:
            parts.append(f"target {p['target_r']}R")
        if p["exit_on_failure"]:
            parts.append("close below range midpoint (exit next bar)")
        parts.append("always flat at the session close")
        return "; ".join(parts)

    def warmup_bars(self, p):
        return 0

    def generate(self, bars: pd.DataFrame, p: Dict[str, Any]) -> pd.DataFrame:
        f = session_frame(bars)
        out = pd.DataFrame(index=bars.index)
        in_or = (f["minute"] >= 0) & (f["minute"] < p["or_minutes"])
        hi = bars["high"].where(in_or).groupby(f["session"]).cummax()
        lo = bars["low"].where(in_or).groupby(f["session"]).cummin()
        # Carry the completed range forward within the session; undefined until the range has finished.
        hi = hi.groupby(f["session"]).ffill()
        lo = lo.groupby(f["session"]).ffill()
        ready = f["minute"] >= p["or_minutes"]
        out["or_high"] = hi.where(ready)
        out["or_low"] = lo.where(ready)
        out["or_mid"] = (out["or_high"] + out["or_low"]) / 2
        c = bars["close"]
        brk = ready & (f["minute"] <= p["entry_cutoff"]) & _no_entry_near_close(f) & (c > out["or_high"])
        first = brk & (brk.astype(int).groupby(f["session"]).cumsum() == 1)
        out["entry"] = first.fillna(False)
        out["exit"] = ((c < out["or_mid"]) & ready).fillna(False) if p["exit_on_failure"] else False
        stop_level = out["or_low"] if p["stop_at"] == "or_low" else out["or_mid"]
        out["stop_dist"] = np.where(out["entry"], c - stop_level, np.nan)
        out["target_r"] = p["target_r"] if p["target_r"] > 0 else np.nan
        out["max_bars"] = np.nan
        out["reason"] = ""
        out.loc[out["entry"], "reason"] = f"Closed above the {p['or_minutes']}-minute opening range high"
        out.loc[out["exit"] & ~out["entry"], "reason"] = "Closed back below the opening range midpoint"
        return out


class VwapContinuation(SignalStrategy):
    key = "vwap_continuation"
    name = "VWAP trend continuation"
    family = "intraday"
    timeframe = "intraday"
    description = "In a session with rising VWAP, buy a bullish bar that dips to VWAP and closes above it."
    params = [
        Param("start_minutes", "No entries before (min from open)", "int", 30, 5, 120, 5),
        Param("entry_cutoff", "No entries after (min from open)", "int", 330, 60, 360, 15),
        Param("slope_bars", "VWAP slope look-back (bars)", "int", 6, 1, 24, 1),
        Param("touch_atr", "Touch tolerance (× ATR)", "float", 0.25, 0.0, 2.0, 0.05),
        Param("atr_len", "ATR length (bars)", "int", 14, 5, 50, 1),
        Param("stop_atr", "Stop (× ATR)", "float", 1.5, 0.5, 5.0, 0.25),
        Param("target_r", "Target (R)", "float", 2.0, 0.0, 10.0, 0.5),
        Param("max_trades", "Max entries per session", "int", 2, 1, 5, 1),
    ]

    def entry_rule(self, p):
        return (f"Between {p['start_minutes']} and {p['entry_cutoff']} min after the open: VWAP higher than "
                f"{p['slope_bars']} bars ago, low ≤ VWAP + {p['touch_atr']}×ATR, close > VWAP and close > open. "
                f"At most {p['max_trades']} signals per session. Buy next bar's open.")

    def exit_rule(self, p):
        parts = [f"stop {p['stop_atr']}×ATR{p['atr_len']} below entry"]
        if p["target_r"] > 0:
            parts.append(f"target {p['target_r']}R")
        parts += ["close below VWAP (exit next bar)", "always flat at the session close"]
        return "; ".join(parts)

    def warmup_bars(self, p):
        return int(p["atr_len"]) * 2

    def generate(self, bars: pd.DataFrame, p: Dict[str, Any]) -> pd.DataFrame:
        f = session_frame(bars)
        out = pd.DataFrame(index=bars.index)
        typical = (bars["high"] + bars["low"] + bars["close"]) / 3
        vol = bars["volume"].fillna(0).clip(lower=0)
        pv = (typical * vol).groupby(f["session"]).cumsum()
        cv = vol.groupby(f["session"]).cumsum()
        out["vwap"] = (pv / cv.replace(0, np.nan))
        out["atr"] = atr(bars, p["atr_len"])
        k = p["slope_bars"]
        prior_vwap = out["vwap"].groupby(f["session"]).shift(k)        # within the same session only
        c, o = bars["close"], bars["open"]
        window = (f["minute"] >= p["start_minutes"]) & (f["minute"] <= p["entry_cutoff"]) & _no_entry_near_close(f)
        setup = window & (out["vwap"] > prior_vwap) & (bars["low"] <= out["vwap"] + p["touch_atr"] * out["atr"]) \
            & (c > out["vwap"]) & (c > o)
        setup = setup.fillna(False)
        count = setup.astype(int).groupby(f["session"]).cumsum()
        out["entry"] = setup & (count <= p["max_trades"])
        out["exit"] = (c < out["vwap"]).fillna(False)
        out["stop_dist"] = np.where(out["entry"], p["stop_atr"] * out["atr"], np.nan)
        out["target_r"] = p["target_r"] if p["target_r"] > 0 else np.nan
        out["max_bars"] = np.nan
        out["reason"] = ""
        out.loc[out["entry"], "reason"] = "Rising session VWAP; dipped to VWAP and closed back above on a bullish bar"
        out.loc[out["exit"] & ~out["entry"], "reason"] = "Closed below session VWAP"
        return out
