"""
The game hub — a world map of "rooms", each home to a trading agent competing on
a live paper-trading leaderboard.

Run it:
    streamlit run trading/hub.py

Each room shows the agent's avatar, personality, live equity/P&L, holdings, and
recent trades. Buttons let you send an agent (or the whole roster) to "think" —
which runs a real paper-trading tick.
"""

import sys
from datetime import datetime
from pathlib import Path

# Streamlit runs this script with trading/ on sys.path, not the project root —
# put the repo root first so `import trading.*` and `import screener.*` resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from trading.config import load_config
from trading.engine import Engine
from trading.history import load_history
from trading.roster import build_roster

st.set_page_config(page_title="Trading Arena", page_icon="🎮", layout="wide")

# ---------------------------------------------------------------- styling ----
st.markdown("""
<style>
.stApp { background: radial-gradient(circle at 20% 0%, #1a1033 0%, #0a0a1a 55%, #05050f 100%); }
.arena-title { font-size: 2.6rem; font-weight: 800; letter-spacing: .04em;
    background: linear-gradient(90deg,#7f5bff,#22d3ee,#34d399);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    text-align:center; margin:.2rem 0 0 0; }
.arena-sub { text-align:center; color:#8b8ba7; margin-bottom:1.4rem; letter-spacing:.18em;
    text-transform:uppercase; font-size:.72rem; }
.room-card { border:1px solid rgba(127,91,255,.35); border-radius:18px; padding:1.1rem 1.2rem;
    background:linear-gradient(160deg, rgba(40,30,70,.65), rgba(15,15,35,.75));
    box-shadow:0 0 24px rgba(127,91,255,.12), inset 0 0 40px rgba(34,211,238,.04); }
.room-name { color:#c9c9e8; font-size:.72rem; letter-spacing:.18em; text-transform:uppercase; }
.agent-name { font-size:1.55rem; font-weight:800; color:#fff; margin:.1rem 0; }
.persona { color:#9a9ac0; font-style:italic; font-size:.86rem; min-height:2.4em; }
.equity { font-size:1.9rem; font-weight:800; color:#fff; }
.ret-pos { color:#34d399; font-weight:700; }
.ret-neg { color:#fb7185; font-weight:700; }
.rank-badge { display:inline-block; padding:.1rem .6rem; border-radius:999px; font-weight:800;
    font-size:.8rem; background:rgba(127,91,255,.25); border:1px solid rgba(127,91,255,.5); color:#d9d0ff; }
.lb-row { display:flex; justify-content:space-between; padding:.35rem .2rem;
    border-bottom:1px solid rgba(255,255,255,.06); }
.hold-line { color:#b9b9d6; font-size:.82rem; font-family:monospace; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="arena-title">⚔️  TRADING ARENA  ⚔️</div>', unsafe_allow_html=True)
st.markdown('<div class="arena-sub">Autonomous agents · live markets · paper capital</div>',
            unsafe_allow_html=True)


# ------------------------------------------------------------- data layer ----
@st.cache_resource
def get_world():
    config = load_config()
    agents = build_roster(config)
    engine = Engine(config, agents)
    return config, agents, engine


config, agents, engine = get_world()


def _fmt_return(pct: float) -> str:
    cls = "ret-pos" if pct >= 0 else "ret-neg"
    sign = "+" if pct >= 0 else ""
    return f'<span class="{cls}">{sign}{pct:.2f}%</span>'


def _fmt_pnl(dollars: float) -> str:
    cls = "ret-pos" if dollars >= 0 else "ret-neg"
    sign = "+" if dollars >= 0 else ""
    return f'<span class="{cls}">{sign}${dollars:,.0f}</span>'


# ----------------------------------------------------------- control bar ----
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    run_all = st.button("▶  Run ALL agents", use_container_width=True, type="primary")
with c2:
    refresh = st.button("↻  Refresh board", use_container_width=True)
with c3:
    mode_label = "🟢 PAPER (simulated)" if not config.is_live else "🔴 LIVE — REAL MONEY"
    st.markdown(f"**Mode:** {mode_label}  ·  *last look:* "
                f"{datetime.now().strftime('%H:%M:%S')}")

if run_all:
    with st.spinner("Agents are analyzing the market…"):
        engine.run_tick()
    st.success("All agents have moved.")


# ----------------------------------------------------------- leaderboard ----
st.markdown("### 🏆 Leaderboard")
board = engine.leaderboard()
medals = ["🥇", "🥈", "🥉"] + ["  "] * 10
lb_html = ['<div class="room-card">']
for i, row in enumerate(board):
    lb_html.append(
        f'<div class="lb-row"><span>{medals[i]} <b>{row["agent"]}</b> '
        f'<span style="color:#8b8ba7">· {row["room"]}</span></span>'
        f'<span>${row["equity"]:,.0f} &nbsp; {_fmt_return(row["return_pct"])}</span></div>'
    )
lb_html.append("</div>")
st.markdown("".join(lb_html), unsafe_allow_html=True)

# ----------------------------------------------------------- the race ----
history = load_history()
if history:
    hdf = pd.DataFrame(history)
    hdf["time"] = pd.to_datetime(hdf["time"])
    span_days = (hdf["time"].max() - hdf["time"].min()).total_seconds() / 86400
    leader = board[0]
    st.markdown("###  ")
    st.markdown(
        f'<div class="room-card"><b>🏁 The Race</b> — day {span_days:.1f} of ~30 · '
        f'current leader <b>{leader["agent"]}</b> at {_fmt_return(leader["return_pct"])} '
        f'· {len(hdf["time"].unique())} snapshots logged</div>',
        unsafe_allow_html=True)
    curve = hdf.pivot_table(index="time", columns="agent",
                            values="equity", aggfunc="last").ffill()
    st.line_chart(curve, height=280)
else:
    st.caption("No history yet — the scheduler will log equity as it runs.")

st.markdown("###  ")
st.markdown("### 🗺️ The World")

# ------------------------------------------------------------- room cards ----
cols = st.columns(max(1, min(len(agents), 4)))
for idx, agent in enumerate(agents):
    acct = agent.broker.get_account()
    start = getattr(agent.broker, "starting_cash", acct.equity)
    ret_pct = (acct.equity - start) / start * 100 if start else 0.0
    positions = agent.broker.get_positions()

    # Status badge — makes "flat because waiting" obvious vs "flat, idle".
    at_work = acct.equity - acct.cash
    if getattr(agent, "is_betting", False):
        badge = ("🟢 BETS OPEN" if positions else "⚪ NO BETS — waiting for value")
    elif at_work > 1:
        badge = f"🟢 INVESTED — ${at_work:,.0f} at work"
    else:
        badge = "⚪ IN CASH — waiting for an uptrend"

    with cols[idx % len(cols)]:
        st.markdown(
            f'<div class="room-card">'
            f'<div class="room-name">📍 {agent.room}</div>'
            f'<div style="font-size:3rem;line-height:1">{agent.avatar}</div>'
            f'<div class="agent-name">{agent.name}</div>'
            f'<div class="persona">{agent.personality}</div>'
            f'<div class="equity">${acct.equity:,.0f}</div>'
            f'<div>{_fmt_return(ret_pct)} &nbsp;·&nbsp; '
            f'<span style="color:#8b8ba7">cash ${acct.cash:,.0f}</span></div>'
            f'<div style="margin-top:.5rem;font-size:.78rem;color:#b9b9d6">{badge}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        if st.button(f"🧠  Send {agent.name} to think",
                     key=f"run_{agent.name}", use_container_width=True):
            with st.spinner(f"{agent.name} is analyzing…"):
                engine.run_agent(agent)
            st.rerun()

        # --- Betting agents (Ace) show bets; trading agents show holdings ---
        if getattr(agent, "is_betting", False):
            open_bets = agent.broker.open_bets()
            if open_bets:
                st.markdown("**Open bets**")
                for b in open_bets:
                    st.markdown(
                        f'<div class="hold-line">{b["matchup"]} · '
                        f'{b["side"]} @ {b["odds"]} · ${b["stake"]:,.0f}</div>',
                        unsafe_allow_html=True)
            else:
                st.caption("No open bets — waiting for value.")

            settled = agent.broker.recent_settled(limit=6)
            if settled:
                with st.expander(f"Recent results ({len(settled)})"):
                    for s in settled:
                        res = "✅ won" if s["won"] else "❌ lost"
                        st.markdown(
                            f'<div class="hold-line">{s["matchup"]} · {s["side"]} '
                            f'{res} ({_fmt_pnl(s["pnl"])})</div>',
                            unsafe_allow_html=True)
        else:
            if positions:
                st.markdown("**Holdings**")
                for p in positions:
                    st.markdown(
                        f'<div class="hold-line">{p.symbol}: {p.qty:g} @ '
                        f'${p.avg_entry_price:,.2f} → ${p.current_price:,.2f} '
                        f'({_fmt_return(p.unrealized_pl_pct)})</div>',
                        unsafe_allow_html=True)
            else:
                st.caption("Flat — no open positions (in cash).")

            if hasattr(agent.broker, "recent_trades"):
                trades = agent.broker.recent_trades(limit=5)
                if trades:
                    with st.expander(f"Recent trades ({len(trades)})"):
                        for t in trades:
                            when = datetime.fromtimestamp(t["ts"]).strftime("%m/%d %H:%M")
                            st.markdown(
                                f'<div class="hold-line">{when} · '
                                f'{t["side"].upper()} {t["qty"]:g} {t["symbol"]} '
                                f'@ ${t["price"]:,.2f}</div>',
                                unsafe_allow_html=True)
