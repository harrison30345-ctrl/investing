"""
Plotly defaults and chart builders.

All charts share one template: transparent background, recessive grid,
unified crosshair hover, monospaced tick labels. One y-axis per chart, always.
Two measures on different scales get two charts.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from . import theme as T

_TEMPLATE = go.layout.Template(layout=go.Layout(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family=T.FONT_SANS, size=11, color=T.TEXT_2),
    margin=dict(l=8, r=8, t=8, b=8),
    xaxis=dict(gridcolor=T.GRID, linecolor=T.BORDER, zeroline=False, showspikes=True, spikemode="across",
               spikethickness=1, spikecolor=T.BORDER_STRONG, spikedash="solid", tickfont=dict(family=T.FONT_MONO)),
    yaxis=dict(gridcolor=T.GRID, linecolor=T.BORDER, zeroline=False, side="right", tickfont=dict(family=T.FONT_MONO)),
    hovermode="x unified",
    hoverlabel=dict(bgcolor=T.SURFACE_2, bordercolor=T.BORDER_STRONG, font=dict(family=T.FONT_MONO, size=11, color=T.TEXT)),
    legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, font=dict(size=11, color=T.TEXT_2),
                bgcolor="rgba(0,0,0,0)"),
    colorway=[T.SERIES_1, T.SERIES_2],
))
pio.templates["quantedge"] = _TEMPLATE

CONFIG = {"displayModeBar": False, "scrollZoom": False}


def _fig(height: int) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(template="quantedge", height=height)
    return fig


def price_line(df: pd.DataFrame, name: str, height: int = 300, unit: str = "") -> go.Figure:
    """Close-price area for an index or stock. Colour shows direction over the window."""
    fig = _fig(height)
    if df.empty:
        return fig
    c = df["close"]
    up = c.iloc[-1] >= c.iloc[0]
    line = T.POS if up else T.NEG
    fill = "rgba(47,182,124,0.08)" if up else "rgba(229,83,75,0.08)"
    fig.add_trace(go.Scatter(x=c.index, y=c.values, name=name, mode="lines", line=dict(color=line, width=2),
                             fill="tozeroy", fillcolor=fill, hovertemplate=f"%{{y:,.2f}}{unit}<extra>{name}</extra>"))
    lo, hi = float(c.min()), float(c.max())
    pad = (hi - lo) * 0.08 or hi * 0.01
    fig.update_yaxes(range=[lo - pad, hi + pad])
    fig.update_layout(showlegend=False)
    return fig


def candles(df: pd.DataFrame, name: str, overlays: Optional[dict] = None, height: int = 380) -> go.Figure:
    fig = _fig(height)
    if df.empty:
        return fig
    fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                                 name=name, increasing=dict(line=dict(color=T.POS, width=1), fillcolor=T.POS),
                                 decreasing=dict(line=dict(color=T.NEG, width=1), fillcolor=T.NEG)))
    palette = [T.SERIES_1, T.SERIES_2]
    for i, (label, series) in enumerate((overlays or {}).items()):
        fig.add_trace(go.Scatter(x=series.index, y=series.values, name=label, mode="lines",
                                 line=dict(color=palette[i % 2], width=1.5), hovertemplate="%{y:,.2f}"))
    fig.update_layout(xaxis_rangeslider_visible=False, showlegend=bool(overlays))
    return fig


def equity_vs_benchmark(eq: pd.Series, bm: Optional[pd.Series], height: int = 260,
                        strategy_label: str = "Strategy", benchmark_label: str = "Benchmark") -> go.Figure:
    """Both rebased to 100 so they share one axis honestly."""
    fig = _fig(height)
    if eq.empty:
        return fig
    fig.add_trace(go.Scatter(x=eq.index, y=eq / eq.iloc[0] * 100, name=strategy_label, mode="lines",
                             line=dict(color=T.SERIES_1, width=2), hovertemplate="%{y:.1f}"))
    if bm is not None and not bm.dropna().empty:
        b = bm.dropna()
        fig.add_trace(go.Scatter(x=b.index, y=b / b.iloc[0] * 100, name=benchmark_label, mode="lines",
                                 line=dict(color=T.SERIES_2, width=2), hovertemplate="%{y:.1f}"))
    fig.update_layout(margin=dict(t=26), legend=dict(y=1.02, yanchor="bottom"))
    return fig


def drawdown(eq: pd.Series, height: int = 150, bm: Optional[pd.Series] = None) -> go.Figure:
    fig = _fig(height)
    if eq.empty:
        return fig
    dd = (eq / eq.cummax() - 1) * 100
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values, name="Strategy", mode="lines", line=dict(color=T.NEG, width=1.5),
                             fill="tozeroy", fillcolor="rgba(229,83,75,0.12)", hovertemplate="%{y:.1f}%"))
    if bm is not None and not bm.dropna().empty:
        b = bm.dropna()
        bdd = (b / b.cummax() - 1) * 100
        fig.add_trace(go.Scatter(x=bdd.index, y=bdd.values, name="Benchmark", mode="lines",
                                 line=dict(color=T.SERIES_2, width=1.25, dash="dot"), hovertemplate="%{y:.1f}%"))
    fig.update_yaxes(ticksuffix="%")
    fig.update_layout(margin=dict(t=26), legend=dict(y=1.02, yanchor="bottom"))
    return fig


def sparkline_svg(values, width: int = 180, height: int = 30) -> str:
    """Tiny inline SVG trend line for stat tiles (no JS, renders instantly)."""
    vals = [float(v) for v in values if pd.notna(v)]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = width / (len(vals) - 1)
    pts = " ".join(f"{i * step:.1f},{height - 2 - (v - lo) / span * (height - 4):.1f}" for i, v in enumerate(vals))
    color = T.POS if vals[-1] >= vals[0] else T.NEG
    return (f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5" '
            f'vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>')
