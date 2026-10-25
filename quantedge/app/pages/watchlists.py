"""Watchlists: create lists, add/remove symbols, see latest prices."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from quantedge import watchlists as W
from quantedge.app import actions
from quantedge.app import data as D
from quantedge.ui import components as C
from quantedge.universe import CATALOGUE

pkey = D.provider()
C.page_header("Watchlists", "Symbols tracked on Home and scanned for signals")

lists = W.all_watchlists()
names = {wid: name for wid, name in lists}
top_l, top_r = st.columns([3, 2], vertical_alignment="bottom")
with top_l:
    wid = st.selectbox("Watchlist", list(names), format_func=names.get, key="wl_pick")
with top_r:
    with st.popover("New watchlist", icon=":material/add:", use_container_width=True):
        new_name = st.text_input("Name", key="wl_new")
        if st.button("Create", type="primary", disabled=not new_name.strip() or new_name.strip() in names.values()):
            W.create(new_name)
            st.rerun()

syms = W.list_symbols(wid)
with C.panel("wl_main", names[wid], f"{len(syms)} symbols"):
    a, b, c = st.columns([3, 1, 1], vertical_alignment="bottom")
    with a:
        opts = [s for s in CATALOGUE if s not in syms and not s.startswith("^")]
        add = st.selectbox("Add symbol", opts, index=None, accept_new_options=True, key="wl_add",
                           format_func=lambda s: f"{s} — {CATALOGUE[s].name}" if s in CATALOGUE else s,
                           placeholder="Pick or type a symbol (e.g. TSCO.L)")
    with b:
        if st.button("Add", type="primary", width="stretch", disabled=not add):
            sym = add.strip().upper()
            res = actions.refresh([sym], label=f"Validating {sym}")
            if res[0].status == "ok":
                W.add_symbol(wid, sym)
                D.clear()
                st.rerun()
            else:
                st.error(f"{sym} was not added: {res[0].message}")
    with c:
        if st.button("Refresh prices", icon=":material/sync:", width="stretch", disabled=not syms):
            actions.refresh(syms)
            st.rerun()

    if not syms:
        C.empty_state("This watchlist is empty", "Add a symbol above. It's checked against the data provider first.")
    else:
        snap = D.snapshot(tuple(syms), pkey)
        sel = st.dataframe(pd.DataFrame({
            "Symbol": snap["symbol"], "Name": snap["name"], "Mkt": snap["market"],
            "Last": [C.fmt_price(v, cu) for v, cu in zip(snap["last"], snap["currency"])],
            "Chg %": snap["change_pct"] * 100, "1Y %": snap.get("ret_1y", pd.Series(dtype=float)) * 100,
            "30d": snap["trend"],
            "As of": [C.fmt_date(x) for x in snap["as_of"]],
        }), hide_index=True, use_container_width=True, on_select="rerun", selection_mode="multi-row", key="wl_table",
            column_config={"Chg %": st.column_config.NumberColumn(format="%+.2f%%"),
                           "1Y %": st.column_config.NumberColumn(format="%+.1f%%"),
                           "30d": st.column_config.LineChartColumn(width="small")})
        chosen = [snap.iloc[i]["symbol"] for i in (sel.selection.rows if sel else [])]
        if chosen and st.button(f"Remove {len(chosen)} selected", icon=":material/delete:"):
            for s in chosen:
                W.remove_symbol(wid, s)
            D.clear()
            st.rerun()
        st.caption("Select rows to remove them. Click a column header to sort.")

if len(lists) > 1:
    with st.expander("Delete this watchlist"):
        if st.button(f"Delete “{names[wid]}”", type="secondary"):
            W.delete(wid)
            st.rerun()
