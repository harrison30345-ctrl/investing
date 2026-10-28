"""
Market backdrop readings.

Two things are being protected. First, that a missing series costs its own
reading and nothing else -- the backdrop must degrade one row at a time rather
than blanking. Second, that the published thresholds are the ones actually
applied, since they are the only part of the interpretation a reader can audit.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services import market_context as mc


@pytest.fixture
def dates():
    return pd.bdate_range("2025-01-01", periods=300)


def _flat(dates, value):
    return pd.Series(float(value), index=dates)


def _ramp(dates, start, end):
    return pd.Series(np.linspace(start, end, len(dates)), index=dates)


def _frame(dates, **series):
    """A closes frame keyed by Yahoo symbol, as fetch_context builds it."""
    return pd.DataFrame({symbol: data for symbol, data in series.items()})


# ── Volatility ───────────────────────────────────────────────

def test_calm_volatility_reads_as_supportive(dates):
    reading = mc._volatility(_frame(dates, **{"^VIX": _flat(dates, 13)}))
    assert reading.tilt == mc.SUPPORTIVE


def test_high_volatility_reads_as_restrictive(dates):
    reading = mc._volatility(_frame(dates, **{"^VIX": _flat(dates, 31)}))
    assert reading.tilt == mc.RESTRICTIVE


def test_a_fast_spike_from_a_calm_level_still_reads_as_restrictive(dates):
    """It is the change in the price of protection that matters, not the level."""
    series = _flat(dates, 12).copy()
    series.iloc[-21:] = np.linspace(12, 19, 21)      # +58% in a month
    reading = mc._volatility(_frame(dates, **{"^VIX": series}))
    assert reading.value < 25                         # still a low level
    assert reading.tilt == mc.RESTRICTIVE


# ── Rates and the curve ──────────────────────────────────────

def test_sharply_rising_yields_read_as_restrictive(dates):
    series = _flat(dates, 4.0).copy()
    series.iloc[-63:] = np.linspace(4.0, 4.8, 63)     # +0.8pp in three months
    reading = mc._ten_year(_frame(dates, **{"^TNX": series}))
    assert reading.tilt == mc.RESTRICTIVE
    assert reading.chg_3m == pytest.approx(0.8, abs=0.02)


def test_falling_yields_read_as_supportive(dates):
    series = _flat(dates, 4.5).copy()
    series.iloc[-63:] = np.linspace(4.5, 3.8, 63)
    reading = mc._ten_year(_frame(dates, **{"^TNX": series}))
    assert reading.tilt == mc.SUPPORTIVE


def test_an_inverted_curve_is_flagged_and_named(dates):
    frame = _frame(dates, **{"^TNX": _flat(dates, 3.8), "^IRX": _flat(dates, 4.6)})
    reading = mc._curve(frame)
    assert reading.value == pytest.approx(-0.8, abs=0.01)
    assert reading.tilt == mc.RESTRICTIVE
    assert "inverted" in reading.note


def test_a_normal_curve_is_not_flagged(dates):
    frame = _frame(dates, **{"^TNX": _flat(dates, 4.6), "^IRX": _flat(dates, 3.8)})
    reading = mc._curve(frame)
    assert reading.tilt == mc.NEUTRAL
    assert "upward-sloping" in reading.note


# ── Trend, credit, breadth ───────────────────────────────────

def test_index_above_its_long_average_reads_as_supportive(dates):
    reading = mc._trend(_frame(dates, **{"^GSPC": _ramp(dates, 4000, 5200)}))
    assert reading.tilt == mc.SUPPORTIVE
    assert "above" in reading.note


def test_index_below_its_long_average_reads_as_restrictive(dates):
    reading = mc._trend(_frame(dates, **{"^GSPC": _ramp(dates, 5200, 4000)}))
    assert reading.tilt == mc.RESTRICTIVE
    assert "below" in reading.note


def test_trend_needs_two_hundred_days_before_it_will_say_anything():
    short = pd.bdate_range("2025-01-01", periods=120)
    assert mc._trend(_frame(short, **{"^GSPC": _ramp(short, 100, 120)})) is None


def test_credit_pulling_back_reads_as_restrictive(dates):
    """High yield falling against Treasuries is credit appetite narrowing."""
    hy = _flat(dates, 80).copy()
    hy.iloc[-63:] = np.linspace(80, 76, 63)           # about -5%
    reading = mc._credit(_frame(dates, HYG=hy, IEF=_flat(dates, 95)))
    assert reading.tilt == mc.RESTRICTIVE
    assert "widening" in reading.note


def test_narrow_breadth_is_described_as_a_few_names_carrying_the_index(dates):
    equal = _flat(dates, 180).copy()
    equal.iloc[-63:] = np.linspace(180, 171, 63)      # -5% against the index
    reading = mc._breadth(_frame(dates, RSP=equal, **{"^GSPC": _flat(dates, 5000)}))
    assert reading.tilt == mc.RESTRICTIVE
    assert "narrow" in reading.note


# ── Gold is deliberately not given a lean ────────────────────

def test_gold_is_never_given_a_tilt(dates):
    """It rises on fear and on falling real rates, which point opposite ways."""
    for path in (_ramp(dates, 2000, 3000), _ramp(dates, 3000, 2000)):
        reading = mc._gold(_frame(dates, **{"GC=F": path}))
        assert reading.tilt == mc.NEUTRAL


# ── Degradation ──────────────────────────────────────────────

def test_a_missing_series_returns_no_reading_rather_than_a_guess(dates):
    empty = _frame(dates)
    assert mc._volatility(empty) is None
    assert mc._curve(_frame(dates, **{"^TNX": _flat(dates, 4.0)})) is None   # no ^IRX
    assert mc._credit(_frame(dates, HYG=_flat(dates, 80))) is None           # no IEF


def test_a_single_observation_is_not_enough_for_a_reading():
    one = pd.bdate_range("2025-01-01", periods=1)
    assert mc._volatility(_frame(one, **{"^VIX": _flat(one, 15)})) is None


def test_fetch_context_reports_a_provider_failure_instead_of_raising(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")
    monkeypatch.setattr(mc.yf, "download", boom)
    context = mc.fetch_context()
    assert not context.ok
    assert "Could not reach" in context.error


def test_fetch_context_handles_an_empty_response(monkeypatch):
    monkeypatch.setattr(mc.yf, "download", lambda *a, **k: pd.DataFrame())
    context = mc.fetch_context()
    assert not context.ok
    assert context.readings == []


def test_one_broken_indicator_does_not_blank_the_others(dates, monkeypatch):
    frame = _frame(dates, **{"^VIX": _flat(dates, 14),
                             "^GSPC": _ramp(dates, 4000, 5000)})
    monkeypatch.setattr(mc.yf, "download", lambda *a, **k: frame)

    def exploding(_closes):
        raise ValueError("bad series")
    monkeypatch.setattr(mc, "_BUILDERS", (exploding, mc._volatility, mc._trend))

    context = mc.fetch_context()
    assert context.ok
    assert {r.key for r in context.readings} == {"volatility", "trend"}


# ── Summary ──────────────────────────────────────────────────

def test_summary_counts_the_tilts_and_states_the_lean(dates, monkeypatch):
    frame = _frame(dates, **{"^VIX": _flat(dates, 31),
                             "^GSPC": _ramp(dates, 5200, 4000)})
    monkeypatch.setattr(mc.yf, "download", lambda *a, **k: frame)
    monkeypatch.setattr(mc, "_BUILDERS", (mc._volatility, mc._trend))

    summary = mc.fetch_context().summary
    assert "2 in territory they have historically struggled in" in summary
    assert "more against equities than for them" in summary


def test_summary_uses_singular_grammar_for_one_indicator(dates, monkeypatch):
    frame = _frame(dates, **{"^GSPC": _ramp(dates, 4000, 5200)})
    monkeypatch.setattr(mc.yf, "download", lambda *a, **k: frame)
    monkeypatch.setattr(mc, "_BUILDERS", (mc._trend,))
    assert "1 sits in territory" in mc.fetch_context().summary


def test_summary_does_not_claim_a_lean_when_tilts_are_balanced(dates, monkeypatch):
    frame = _frame(dates, **{"^VIX": _flat(dates, 31),
                             "^GSPC": _ramp(dates, 4000, 5200)})
    monkeypatch.setattr(mc.yf, "download", lambda *a, **k: frame)
    monkeypatch.setattr(mc, "_BUILDERS", (mc._volatility, mc._trend))
    assert "mixed, with no clear lean" in mc.fetch_context().summary


def test_every_reading_explains_why_it_matters(dates, monkeypatch):
    """A number with no explanation is not research."""
    frame = _frame(
        dates,
        **{"^VIX": _flat(dates, 15), "^TNX": _flat(dates, 4.2),
           "^IRX": _flat(dates, 3.9), "DX-Y.NYB": _flat(dates, 100),
           "CL=F": _flat(dates, 75), "GC=F": _flat(dates, 2400),
           "HYG": _flat(dates, 80), "IEF": _flat(dates, 95),
           "RSP": _flat(dates, 180), "^GSPC": _ramp(dates, 4000, 5000)},
    )
    monkeypatch.setattr(mc.yf, "download", lambda *a, **k: frame)
    context = mc.fetch_context()
    assert len(context.readings) == len(mc._BUILDERS)
    for reading in context.readings:
        assert reading.meaning and len(reading.meaning) > 40
        assert reading.note
        assert reading.tilt in mc.TILT_ORDER
