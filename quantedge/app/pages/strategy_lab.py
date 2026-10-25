"""Strategy Lab: strategy catalogue now; interactive configure-and-run arrives in Phase 5."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from quantedge.strategies import REGISTRY
from quantedge.ui import components as C

C.page_header("Strategy Lab", "Deterministic strategy definitions and their configurable parameters")
C.html_block('<div class="qe-empty"><b>Interactive runs: Phase 5 (not built yet)</b><p>Picking parameters, running a '
             'backtest from this page and comparing experiments side by side come in Phase 5. Until then, run '
             'backtests from the CLI. Every run appears on the Backtests page.</p></div>')
st.write("")
for s in REGISTRY.values():
    p = s.defaults()
    with C.panel(f"lab_{s.key}", s.name, C.tag(s.family, "accent") + f" &nbsp;{C.esc(s.timeframe)} · key {C.esc(s.key)}"):
        st.markdown(s.description)
        st.markdown(f"**Entry.** {s.entry_rule(p)}  \n**Exit.** {s.exit_rule(p)}")
        st.dataframe(pd.DataFrame([{"Parameter": x.label, "Name": x.name, "Default": str(x.default),
                                    "Range": (", ".join(map(str, x.choices)) if x.choices else
                                              (f"{x.min} – {x.max}" if x.min is not None else "")),
                                    "Meaning": x.help} for x in s.params]),
                     hide_index=True, use_container_width=True)
        st.code(f"python3 -m quantedge.cli backtest {s.key} --universe us_large --period train", language="bash")
