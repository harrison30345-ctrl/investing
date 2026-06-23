"""
crypto_screener_app.py — Streamlit front-end for crypto_screener.py

Same four signals as the terminal tool (Trending, Recently Listed, Low-Cap
Momentum, Coinbase Crossover), rendered as an interactive web dashboard.

Run:  streamlit run crypto_screener_app.py
Deps: pip install streamlit requests rich pandas
"""

import datetime as dt

import pandas as pd
import streamlit as st

# Reuse the exact fetch/filter logic from the terminal screener.
import crypto_screener as cs

st.set_page_config(page_title="Crypto Early-Opportunity Screener", page_icon="🚀", layout="wide")

st.title("🚀 Crypto Early-Opportunity Screener")
st.caption(
    "Trending · Recently Listed · Low-Cap Momentum · Coinbase Crossover — "
    "free CoinGecko + Coinbase APIs, no key required. **Not financial advice.**"
)


@st.cache_data(ttl=300, show_spinner=False)
def run_scan():
    """Run all four sections. Cached for 5 min so reruns don't hammer the API."""
    trending = cs.fetch_trending() or []
    recent = cs.fetch_recent_listings() or []
    momentum = cs.fetch_momentum() or []
    coinbase_symbols = cs.fetch_coinbase_symbols()
    crossover = cs.coinbase_crossover(coinbase_symbols, trending, momentum)
    return trending, recent, momentum, crossover


with st.sidebar:
    st.header("Controls")
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
    st.caption("Data is cached for 5 minutes. Each scan takes ~15s due to API rate limits.")

with st.spinner("Scanning markets… (~15s)"):
    trending, recent, momentum, crossover = run_scan()


def show(df_rows, drop_cols=("section", "source")):
    if not df_rows:
        return None
    df = pd.DataFrame(df_rows)
    return df.drop(columns=[c for c in drop_cols if c in df.columns])


# --- Coinbase crossover first: it's the highest-priority signal ---
if crossover:
    st.error(f"⭐ {len(crossover)} HIGH-PRIORITY Coinbase crossover(s) — trending/momentum coins also on Coinbase")
    st.dataframe(pd.DataFrame(crossover), use_container_width=True, hide_index=True)
else:
    st.info("No Coinbase crossovers in this scan.")

col1, col2 = st.columns(2)

with col1:
    st.subheader("🔥 Trending (top 7)")
    df = show(trending)
    if df is not None:
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.warning("No trending data.")

    st.subheader("🆕 Recently Listed (≈ last 30 days)")
    df = show(recent)
    if df is not None:
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.warning("No recent-listing data.")

with col2:
    st.subheader("📈 Low-Cap Momentum ($5M–$300M · +15% 7d · vol/mcap > 0.1)")
    df = show(momentum)
    if df is not None:
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "price_change_percentage_7d": st.column_config.NumberColumn("7d %", format="%.1f%%"),
                "vol_mcap_ratio": st.column_config.NumberColumn("Vol/MCap", format="%.2f"),
            },
        )
    else:
        st.warning("No coins matched the momentum filters.")

# --- Save CSV + timestamp ---
all_rows = list(trending) + list(recent) + list(momentum) + list(crossover)
if all_rows:
    today = dt.date.today().isoformat()
    csv_bytes = pd.DataFrame(all_rows).to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇ Download results CSV",
        data=csv_bytes,
        file_name=f"crypto_scan_{today}.csv",
        mime="text/csv",
    )

st.caption(f"Scan completed: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
