"""
Reusable Streamlit components: page chrome, panels, tiles, pills, empty states
and value formatting. Pages compose these. They don't write raw CSS.
"""
from __future__ import annotations

import html
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import pandas as pd
import streamlit as st

from . import theme as T
from .charts import sparkline_svg

DISPLAY_TZ = "Europe/London"
MARKET_KEY = "qe_market"
SYMBOL_KEY = "qe_symbol"


# ── formatting ──────────────────────────────────────────────────────────────

def esc(x) -> str:
    return html.escape(str(x))


def fmt_price(v: Optional[float], currency: str = "USD") -> str:
    if v is None or pd.isna(v):
        return "—"
    if currency == "GBX":
        return f"{v:,.1f}p"
    if currency == "USD":
        return f"${v:,.2f}"
    if currency == "GBP":
        return f"£{v:,.2f}"
    return f"{v:,.2f}"


def fmt_pct(v: Optional[float], signed: bool = True, digits: int = 2) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"


def tone(v: Optional[float]) -> str:
    if v is None or pd.isna(v) or v == 0:
        return "qe-muted"
    return "qe-pos" if v > 0 else "qe-neg"


def to_local(ts, tz: str = DISPLAY_TZ) -> Optional[pd.Timestamp]:
    """Naive UTC (database convention) → display timezone."""
    if ts is None or pd.isna(ts):
        return None
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return t.tz_convert(tz)


def fmt_time(ts, fmt: str = "%d %b %H:%M") -> str:
    t = to_local(ts)
    return t.strftime(fmt) if t is not None else "—"


def fmt_date(ts) -> str:
    return pd.Timestamp(ts).strftime("%d %b %Y") if ts is not None and not pd.isna(ts) else "—"


def ago(ts) -> str:
    if ts is None or pd.isna(ts):
        return "never"
    secs = (datetime.now(timezone.utc).replace(tzinfo=None) - pd.Timestamp(ts).to_pydatetime()).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400 * 2:
        return f"{int(secs // 3600)} h ago"
    return f"{int(secs // 86400)} d ago"


# ── primitives ──────────────────────────────────────────────────────────────

def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def pill(label: str, value: str = "", state: str = "", title: str = "") -> str:
    dot = f'<i class="qe-dot {state}"></i>' if state is not None else ""
    val = f" <b>{esc(value)}</b>" if value else ""
    return f'<span class="qe-pill" title="{esc(title)}">{dot}{esc(label)}{val}</span>'


def tag(text: str, kind: str = "") -> str:
    return f'<span class="qe-tag {kind}">{esc(text)}</span>'


def page_header(title: str, subtitle: str = "") -> None:
    html_block(f'<div class="qe-page-head"><h1>{esc(title)}</h1><span>{esc(subtitle)}</span></div>')


def panel_head(title: str, meta: str = "") -> None:
    html_block(f'<div class="qe-panel-head"><h3>{esc(title)}</h3><span>{meta}</span></div>')


@contextmanager
def panel(key: str, title: str, meta: str = ""):
    """Bordered surface with a small uppercase header. `meta` may contain trusted HTML."""
    with st.container(key=f"panel_{key}"):
        panel_head(title, meta)
        yield


def empty_state(title: str, body: str, hint: str = "") -> None:
    hint_html = f'<p style="margin-top:6px !important"><code>{esc(hint)}</code></p>' if hint else ""
    html_block(f'<div class="qe-empty"><b>{esc(title)}</b><p>{esc(body)}</p>{hint_html}</div>')


def kv(rows: Iterable[tuple]) -> None:
    items = "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in rows)
    html_block(f'<dl class="qe-kv">{items}</dl>')


def metric_grid(items: Iterable[tuple]) -> None:
    """[(label, value_text, css_class)]"""
    cells = "".join(f'<div><span>{esc(label)}</span><b class="{cls}">{esc(val)}</b></div>' for label, val, cls in items)
    html_block(f'<div class="qe-metrics">{cells}</div>')


def list_rows(rows: Iterable[tuple]) -> None:
    """[(title, subtitle, right_html)]"""
    items = "".join(f'<li><div><div class="t">{esc(t)}</div><div class="s">{esc(s)}</div></div>'
                    f'<div class="r">{r}</div></li>' for t, s, r in rows)
    html_block(f'<ul class="qe-list">{items}</ul>')


def tiles(snapshot: pd.DataFrame) -> None:
    cells = []
    for r in snapshot.itertuples():
        if r.last is None or pd.isna(r.last):
            cells.append(f'<div class="qe-tile off"><div class="lbl"><span>{esc(r.name)}</span><span>{esc(r.market)}</span></div>'
                         f'<div class="val">No data stored</div><div class="chg qe-muted">Refresh to fetch</div></div>')
            continue
        unit = fmt_price(r.last, r.currency)
        cells.append(
            f'<div class="qe-tile" title="{esc(r.name)} · close {fmt_date(r.as_of)}">'
            f'<div class="lbl"><span>{esc(r.name)}</span><span>{esc(r.market)} · {pd.Timestamp(r.as_of):%d %b}</span></div>'
            f'<div class="val">{unit}</div>'
            f'<div class="chg {tone(r.change_pct)}">{r.change:+,.2f} ({fmt_pct(r.change_pct)})</div>'
            f'{sparkline_svg(r.trend)}</div>')
    html_block(f'<div class="qe-tiles">{"".join(cells)}</div>')


def demo_banner() -> None:
    html_block('<div class="qe-banner"><b>DEMO DATA</b> &nbsp;All prices, signals and backtests on this screen '
               'come from a synthetic random walk (QE_DATA_PROVIDER=demo). They are not market data.</div>')


def market_filter() -> Optional[str]:
    """Global UK/US selection from the top bar. None means both markets."""
    v = st.session_state.get(MARKET_KEY) or "All"
    return None if v == "All" else v
