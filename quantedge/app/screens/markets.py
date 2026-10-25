"""Markets: single-instrument view (chart, stats, data quality) plus UK/US overview tables."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from quantedge.app import actions
from quantedge.app import data as D
from quantedge.data import repository as repo
from quantedge.data.providers import provider_info
from quantedge.strategies.indicators import atr, ema
from quantedge.ui import charts
from quantedge.ui import components as C
from quantedge.universe import EQUITIES, INDICES
from quantedge.universe import get as catalogue_get
from quantedge.watchlists import add_symbol, get_or_create_default, list_symbols

pkey = D.provider()
mkt = C.market_filter()
sym = st.session_state.get(C.SYMBOL_KEY) or "^FTSE"
inst = catalogue_get(sym)

C.page_header("Markets", f"{inst.symbol} · {inst.name} · {inst.market}")
hist = D.history(sym, pkey)

a, b, c = st.columns([5, 1.2, 1.2], vertical_alignment="bottom")
with b:
    if st.button("Fetch / update", icon=":material/sync:", width="stretch", key="mk_fetch"):
        res = actions.refresh([sym], label=f"Fetching {sym}")
        if res and res[0].status != "ok":
            st.error(f"{sym}: {res[0].message}")
        st.rerun()
with c:
    wid = get_or_create_default()
    on_list = sym in list_symbols(wid)
    if st.button("On watchlist" if on_list else "Add to watchlist", icon=":material/visibility:",
                 width="stretch", disabled=on_list or inst.kind == "index"):
        add_symbol(wid, sym)
        D.clear()
        st.rerun()

if hist.empty:
    C.empty_state(f"No stored data for {sym}",
                  f"Press Fetch / update to request it from {provider_info().name}. Unknown symbols are reported "
                  "as not found; nothing is substituted.")
else:
    RANGES = {"3M": 92, "6M": 183, "1Y": 366, "2Y": 731, "5Y": 1827, "Max": 0}
    left, right = st.columns([2.4, 1], gap="small")
    with left:
        with C.panel("mk_chart", "Price", "Daily · unadjusted OHLC · EMA20/50"):
            rng = st.segmented_control("Range", list(RANGES), default="1Y", key="mk_rng",
                                       label_visibility="collapsed") or "1Y"
            e20, e50 = ema(hist["close"], 20), ema(hist["close"], 50)
            view = hist if not RANGES[rng] else hist[hist.index >= hist.index[-1] - pd.Timedelta(days=RANGES[rng])]
            st.plotly_chart(charts.candles(view, sym, {"EMA 20": e20.loc[view.index], "EMA 50": e50.loc[view.index]},
                                           height=400), config=charts.CONFIG, use_container_width=True)
    with right:
        snap = D.snapshot((sym,), pkey).iloc[0]
        cur = inst.currency
        a14 = atr(hist, 14).iloc[-1]
        vol = hist["close"].pct_change().iloc[-63:].std() * (252 ** 0.5)
        with C.panel("mk_stats", "Snapshot", f"close {hist.index[-1]:%d %b %Y}"):
            C.kv([
                ("Last close", C.fmt_price(snap["last"], cur)),
                ("Day change", f'<span class="{C.tone(snap["change_pct"])}">{C.fmt_pct(snap["change_pct"])}</span>'),
                ("1-year return", f'<span class="{C.tone(snap.get("ret_1y"))}">{C.fmt_pct(snap.get("ret_1y"))}</span>'),
                ("52-week high", C.fmt_price(snap["high_52w"], cur)),
                ("52-week low", C.fmt_price(snap["low_52w"], cur)),
                ("ATR(14)", C.fmt_price(a14, cur)),
                ("Volatility (3m, ann.)", C.fmt_pct(vol, signed=False, digits=1)),
                ("Trend vs EMA50", "above" if hist["close"].iloc[-1] > e50.iloc[-1] else "below"),
                ("Bars stored", f"{len(hist):,}"),
            ])
        q = repo.latest_quality(pkey, sym)
        with C.panel("mk_quality", "Data quality", C.esc(C.fmt_time(q.checked_at)) if q else ""):
            if q is None:
                st.caption("Not yet validated.")
            elif not q.issues:
                C.html_block('<span class="qe-pos">All checks passed</span> <span class="qe-muted">'
                             '(sessions, duplicates, OHLC, jumps, staleness)</span>')
            else:
                C.list_rows([(i["message"], ", ".join(i.get("examples") or []),
                              f'<span class="{"qe-neg" if i["severity"] == "error" else "qe-muted"}">'
                              f'{C.esc(i["severity"])}{" · " + str(i["count"]) if i.get("count") else ""}</span>')
                             for i in q.issues])
    if inst.currency == "GBX":
        st.caption("London prices are quoted in pence (p). Backtests convert to pounds.")

# Overview tables
st.write("")
for m in [x for x in ("UK", "US") if mkt is None or x == mkt]:
    syms = [i.symbol for i in INDICES + EQUITIES if i.market == m]
    snap = D.snapshot(tuple(syms), pkey)
    have = snap[snap["last"].notna()]
    with C.panel(f"mk_over_{m}", f"{m} overview", f"{len(have)}/{len(snap)} with stored data"):
        if have.empty:
            C.empty_state("No stored data", "Fetch a symbol above, or run "
                          "`python3 -m quantedge.cli refresh --universe " + ("uk_large" if m == "UK" else "us_large") + "`.")
            continue
        sel = st.dataframe(pd.DataFrame({
            "Symbol": have["symbol"], "Name": have["name"],
            "Last": [C.fmt_price(v, cu) for v, cu in zip(have["last"], have["currency"])],
            "Chg %": have["change_pct"] * 100, "1Y %": have["ret_1y"] * 100,
            "30d": have["trend"], "As of": pd.to_datetime(have["as_of"]).dt.strftime("%d %b"),
        }), hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row",
            key=f"over_{m}", column_config={
                "Chg %": st.column_config.NumberColumn(format="%+.2f%%"),
                "1Y %": st.column_config.NumberColumn(format="%+.1f%%"),
                "30d": st.column_config.LineChartColumn(width="small")})
        rows = sel.selection.rows if sel else []
        if rows:
            st.session_state[C.SYMBOL_KEY] = have.iloc[rows[0]]["symbol"]
            st.rerun()
