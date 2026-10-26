"""
Strategy Lab: choose a strategy, set its allowed parameters, run a backtest or
a one-parameter sweep, then inspect and compare the results.

Every run goes through the same `run_experiment` path as the CLI, so it's
recorded in the experiment log whatever happens.
"""
from __future__ import annotations

import streamlit as st

from quantedge.app import actions
from quantedge.app import data as D
from quantedge.app import views as V
from quantedge.backtest.costs import CostModel
from quantedge.backtest.engine import BacktestConfig
from quantedge.backtest.runner import TestSetLocked, run_experiment, run_sweep
from quantedge.markets import MARKETS
from quantedge.strategies import REGISTRY
from quantedge.ui import components as C
from quantedge.universe import EQUITIES, UNIVERSES
from quantedge.universe import get as catalogue_get

pkey = D.provider()
C.page_header("Strategy Lab", "Configure a deterministic strategy, run it on stored data, compare variants")

cfg_col, out_col = st.columns([1, 2.1], gap="small")

with cfg_col:
    with C.panel("lab_cfg", "Configuration"):
        skey = st.selectbox("Strategy", list(REGISTRY), format_func=lambda k: REGISTRY[k].name, key="lab_strategy")
        strat = REGISTRY[skey]
        st.caption(strat.description)

        intraday = strat.timeframe == "intraday"
        presets = ["us_intraday"] if intraday else [u for u in UNIVERSES if u != "us_intraday"]
        mode = st.segmented_control("Universe", ["Preset", "Custom"], default="Preset", key="lab_umode") or "Preset"
        if mode == "Preset":
            uname = st.selectbox("Preset universe", presets, key=f"lab_universe_{intraday}",
                                 format_func=lambda u: f"{u} ({len(UNIVERSES[u])} symbols)")
            symbols = UNIVERSES[uname]
        else:
            markets = ["US"] if intraday else ["US", "UK"]
            market = st.segmented_control("Market", markets, default="US", key=f"lab_mkt_{intraday}") or "US"
            opts = [i.symbol for i in EQUITIES if i.market == market]
            if intraday:
                opts = list(dict.fromkeys(UNIVERSES["us_intraday"] + opts))
            symbols = st.multiselect("Symbols", opts, default=opts[:5], key=f"lab_syms_{market}_{intraday}")
            uname = None
        market = catalogue_get(symbols[0]).market if symbols else "US"
        if intraday:
            st.caption("Intraday: US 5-minute bars, about 60 days of history, so every result is a small sample. "
                       "Positions are always closed at the session close.")

        period = st.segmented_control("Period", ["train", "validation"], default="train", key="lab_period",
                                      help="Develop on train. Use validation to check a choice you've already "
                                           "made. The test period is unlocked from the Backtests page after "
                                           "validation.") or "train"

        st.markdown("**Parameters**")
        params = {}
        grid = st.columns(2, gap="small")
        for i, p in enumerate(strat.params):
            k = f"lab_{skey}_{p.name}"
            cell = grid[i % 2]
            if p.kind == "choice":
                params[p.name] = cell.selectbox(p.label, list(p.choices), index=list(p.choices).index(p.default),
                                              key=k, help=p.help or None)
            elif p.kind == "bool":
                params[p.name] = cell.checkbox(p.label, value=p.default, key=k, help=p.help or None)
            elif p.kind == "int":
                params[p.name] = int(cell.number_input(p.label, int(p.min), int(p.max), int(p.default), int(p.step or 1),
                                                     key=k, help=p.help or None))
            else:
                params[p.name] = float(cell.number_input(p.label, float(p.min), float(p.max), float(p.default),
                                                       float(p.step or 0.1), key=k, help=p.help or None))

        with st.expander("Sizing & costs"):
            capital = st.number_input("Starting capital", 1_000.0, 10_000_000.0, 100_000.0, 10_000.0, key="lab_cap")
            risk = st.number_input("Risk per trade (%)", 0.1, 5.0, 1.0, 0.1, key="lab_risk",
                                   help="Trend strategies: equity at risk if the stop is hit") / 100
            max_pos = int(st.number_input("Max positions", 1, 30, 5, 1, key="lab_maxpos"))
            max_pct = st.number_input("Max position size (%)", 5.0, 100.0, 20.0, 5.0, key="lab_maxpct") / 100
            spread = st.number_input("Spread (bps, full)", 0.0, 100.0, 5.0, 1.0, key="lab_spread")
            slip = st.number_input("Slippage (bps per side)", 0.0, 100.0, 5.0, 1.0, key="lab_slip")
            comm = st.number_input("Commission (bps)", 0.0, 100.0, 0.0, 1.0, key="lab_comm")
            stamp = st.number_input("UK stamp duty on buys (bps)", 0.0, 100.0, 50.0, 5.0, key="lab_stamp",
                                    help="0.5% SDRT on UK share purchases. Not charged on US stocks.")
        config = BacktestConfig(initial_capital=capital, risk_per_trade=risk, max_positions=max_pos,
                                max_position_pct=max_pct, costs=CostModel(spread, slip, comm, 0.0, stamp))

        run_mode = st.segmented_control("Run", ["Single", "Sweep one parameter"], default="Single",
                                        key="lab_runmode") or "Single"
        sweep_param, sweep_values = None, []
        if run_mode != "Single":
            numeric = [p for p in strat.params if p.kind in ("int", "float", "choice")]
            sp = st.selectbox("Parameter to vary", [p.name for p in numeric], key=f"lab_sp_{skey}",
                              format_func=lambda n: next(p.label for p in numeric if p.name == n))
            spec = next(p for p in numeric if p.name == sp)
            if spec.kind == "choice":
                sweep_values = st.multiselect("Values", list(spec.choices), default=list(spec.choices),
                                              key=f"lab_sv_{skey}_{sp}")
            else:
                d = spec.default
                guess = [d * f for f in (0.5, 0.75, 1, 1.5, 2)]
                guess = [max(spec.min, min(spec.max, round(g) if spec.kind == "int" else round(g, 2))) for g in guess]
                raw = st.text_input("Values (comma-separated)", ", ".join(str(g) for g in dict.fromkeys(guess)),
                                    key=f"lab_sv_{skey}_{sp}")
                try:
                    cast = int if spec.kind == "int" else float
                    sweep_values = [cast(x) for x in raw.replace(" ", "").split(",") if x]
                except ValueError:
                    st.error("Values must be numbers")
            sweep_values = list(dict.fromkeys(sweep_values))[:6]
            sweep_param = sp
            st.caption("Up to 6 values. A sweep shows sensitivity. Choosing the best value afterwards and calling "
                       "it the result is overfitting.")

        # Pre-flight: is the data there?
        stored = set(D.stored_symbols(pkey, "5m" if intraday else "1d"))
        need = list(symbols) + [MARKETS[market].benchmark]
        missing = [s for s in need if s not in stored]
        if missing:
            st.warning(f"No stored {'5-minute' if intraday else 'daily'} data for {len(missing)} symbol(s): "
                       f"{', '.join(missing[:8])}{'…' if len(missing) > 8 else ''}")
            if st.button("Fetch missing data", icon=":material/sync:", width="stretch"):
                if intraday:
                    from quantedge.data.service import refresh_intraday
                    with st.spinner("Fetching 5-minute bars…"):
                        refresh_intraday(missing)
                    D.clear()
                else:
                    actions.refresh(missing, label="Fetching")
                st.rerun()

        can_run = bool(symbols) and (run_mode == "Single" or len(sweep_values) >= 2)
        notes = st.text_input("Note (optional)", key="lab_notes", placeholder="What are you testing?")
        if st.button("Run backtest" if run_mode == "Single" else f"Run {len(sweep_values)} variants",
                     type="primary", icon=":material/play_arrow:", width="stretch", disabled=not can_run):
            try:
                with st.spinner("Running… (every run is recorded)"):
                    if run_mode == "Single":
                        exp_id = run_experiment(skey, params, symbols=list(symbols), universe=uname, period=period,
                                                config=config, source="dashboard", notes=notes or None)
                        st.session_state["lab_result"] = {"ids": [exp_id]}
                    else:
                        base = {k: v for k, v in params.items() if k != sweep_param}
                        batch, ids = run_sweep(skey, sweep_param, sweep_values, base, symbols=list(symbols),
                                               universe=uname, period=period, config=config, source="dashboard")
                        st.session_state["lab_result"] = {"ids": ids, "batch": batch}
            except TestSetLocked as exc:
                st.error(str(exc))
            except Exception as exc:
                st.session_state["lab_result"] = {"error": str(exc)}
            D.clear()
            st.rerun()

with out_col:
    res = st.session_state.get("lab_result")
    with C.panel("lab_rules", f"{strat.name}: rules being tested", C.tag(strat.family, "accent")):
        st.markdown(f"**Entry.** {strat.entry_rule(params)}  \n**Exit.** {strat.exit_rule(params)}")
        st.caption(f"Signals use bar close; orders fill at the next open. {len(symbols)} symbols · {market} · "
                   f"benchmark {MARKETS[market].benchmark} · data {pkey}.")
    if not res:
        C.empty_state("No run yet in this session",
                      "Set parameters on the left and press Run. Results are simulated on historical data. A good "
                      "train result is a hypothesis to validate, not evidence it will work.")
    elif "error" in res:
        with C.panel("lab_err", "Run failed", C.tag("recorded", "neg")):
            st.error(res["error"])
            st.caption("The failed attempt is kept in the experiment log.")
    else:
        exps = D.experiments(1000)
        mine = exps[exps["id"].isin(res["ids"])].sort_values("id")
        if len(mine) == 1:
            V.experiment_detail(mine.iloc[0], key="lab")
        else:
            V.comparison(mine, key="lab_cmp", title=f"Sweep · {len(mine)} variants")
            pick = st.selectbox("Inspect variant", mine["id"].tolist(), key="lab_pick",
                                format_func=lambda i: f"#{i} {mine.set_index('id').loc[i, 'notes']}")
            V.experiment_detail(mine[mine["id"] == pick].iloc[0], key="lab_v")
