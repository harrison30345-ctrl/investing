"""
Crypto Intelligence Dashboard — Research Tool
Not financial advice. Data sourced from CoinGecko and CryptoCompare free APIs.
"""

import re
import time
from collections import defaultdict
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

RSS_FEEDS = [
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt",       "https://decrypt.co/feed"),
]

API_DELAY         = 1.2   # seconds between CoinGecko calls (free-tier rate limit)
VOLUME_SPIKE_MULT = 2.0   # flag coin if 24h vol ≥ 2× its 7-day daily average

# Risk thresholds — user specified £10m/£100m; converted to USD at ~1.27
HIGH_RISK_USD   = 10_000_000 * 1.27   # ≈ $12.7m
MEDIUM_RISK_USD = 100_000_000 * 1.27  # ≈ $127m

# ─────────────────────────────────────────────────────────────────────────────
# SENTIMENT KEYWORD LISTS
# ─────────────────────────────────────────────────────────────────────────────

BULLISH_KW = [
    "bull", "bullish", "moon", "pump", "surge", "rally", "breakout", "buy",
    "gain", "rise", "rising", "growth", "ath", "all-time high", "upside",
    "positive", "strong", "support", "accumulate", "long", "hodl", "explode",
    "soar", "outperform", "recovery", "bounce", "green", "spike", "record",
    "adoption", "partnership", "listing", "upgrade",
]
BEARISH_KW = [
    "bear", "bearish", "crash", "dump", "collapse", "sell", "drop", "fall",
    "falling", "decline", "loss", "down", "downside", "negative", "weak",
    "resistance", "short", "correction", "warning", "risk", "danger", "plunge",
    "tumble", "tank", "red", "panic", "fear", "hack", "exploit", "scam",
    "fraud", "ban", "crackdown", "regulation", "delist", "lawsuit", "sec",
]

# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def fmt_usd(n):
    if n is None:
        return "N/A"
    if n >= 1e9:
        return f"${n/1e9:.2f}B"
    if n >= 1e6:
        return f"${n/1e6:.2f}M"
    if n >= 1e3:
        return f"${n/1e3:.1f}K"
    return f"${n:.4f}"


def safe_get(url, params=None, retries=3):
    """HTTP GET with retry and graceful rate-limit handling."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=20)
            if resp.status_code == 429:
                wait = 60 * (attempt + 1)
                st.warning(f"Rate limited by CoinGecko — waiting {wait}s…")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == retries - 1:
                st.warning(f"Request failed: {exc}")
                return None
            time.sleep(2 ** attempt)
    return None

# ─────────────────────────────────────────────────────────────────────────────
# SENTIMENT ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def analyze_sentiment(text: str) -> tuple:
    """Return (label, bull_count, bear_count) for a headline."""
    t = text.lower()
    bull = sum(1 for kw in BULLISH_KW if kw in t)
    bear = sum(1 for kw in BEARISH_KW if kw in t)
    if bull > bear:
        return "🟢 Bullish", bull, bear
    if bear > bull:
        return "🔴 Bearish", bull, bear
    return "⚪ Neutral", bull, bear


def extract_coin_mentions(title: str, known_symbols: set) -> list:
    """Find uppercase ticker symbols mentioned in a headline."""
    words = re.findall(r'\b[A-Z]{2,10}\b', title)
    return [w for w in words if w in known_symbols]

# ─────────────────────────────────────────────────────────────────────────────
# COINGECKO DATA FETCHERS  (all cached; cache cleared on manual refresh)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=0, show_spinner=False)
def fetch_top50():
    return safe_get(f"{COINGECKO_BASE}/coins/markets", params={
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 50,
        "page": 1,
        "sparkline": False,
        "price_change_percentage": "24h,7d",
    }) or []


@st.cache_data(ttl=0, show_spinner=False)
def fetch_undervalued_universe():
    """Coins ranked 51-350 across three pages."""
    coins = []
    for page in [2, 3, 4]:
        data = safe_get(f"{COINGECKO_BASE}/coins/markets", params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 100,
            "page": page,
            "sparkline": False,
            "price_change_percentage": "24h,7d",
        })
        if data:
            coins.extend(data)
        time.sleep(0.6)
    return coins




@st.cache_data(ttl=0, show_spinner=False)
def fetch_coin_history(coin_id: str, days: int = 30):
    """Price + volume history for the chart panel."""
    return safe_get(f"{COINGECKO_BASE}/coins/{coin_id}/market_chart", params={
        "vs_currency": "usd",
        "days": days,
    })

# ─────────────────────────────────────────────────────────────────────────────
# NEWS  — parsed from free public RSS feeds, no API key required
# ─────────────────────────────────────────────────────────────────────────────

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

@st.cache_data(ttl=0, show_spinner=False)
def fetch_news(pages: int = 3) -> list:
    """Fetch articles from CoinTelegraph, CoinDesk and Decrypt RSS feeds."""
    articles = []
    for source, url in RSS_FEEDS:
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall("./channel/item"):
                title   = (item.findtext("title") or "").strip()
                link    = (item.findtext("link")  or "").strip()
                pub_raw = item.findtext("pubDate") or ""
                try:
                    pub_str = parsedate_to_datetime(pub_raw).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pub_str = pub_raw[:16]
                if title:
                    articles.append({
                        "title":        title,
                        "url":          link,
                        "published_at": pub_str,
                        "source":       source,
                        "tags":         "",
                        "categories":   "",
                    })
        except Exception:
            pass
    return articles


def process_news(posts: list, known_symbols: set) -> tuple:
    """
    Annotate CryptoCompare articles with sentiment + coin mentions.
    Returns (processed_list, coin_sentiment_dict).

    CryptoCompare article fields used:
      title         — headline text
      url           — article URL
      published_on  — unix timestamp
      categories    — pipe-separated topics/symbols  e.g. "BTC|ETH|Trading"
      tags          — pipe-separated coin symbols    e.g. "BTC|ETH"
    """
    processed = []
    coin_sentiment = defaultdict(lambda: {"🟢 Bullish": 0, "🔴 Bearish": 0, "⚪ Neutral": 0})

    for post in posts:
        title = post.get("title", "")
        sentiment, bull_score, bear_score = analyze_sentiment(title)

        # Symbols tagged by CryptoCompare in the `tags` and `categories` fields
        raw_tags = post.get("tags", "") or ""
        raw_cats = post.get("categories", "") or ""
        api_symbols = {
            t.strip().upper()
            for t in (raw_tags + "|" + raw_cats).split("|")
            if t.strip().upper() in known_symbols
        }
        # Also scan the headline itself for uppercase tickers
        headline_symbols = set(extract_coin_mentions(title, known_symbols))
        all_symbols = sorted(api_symbols | headline_symbols)

        for sym in all_symbols:
            coin_sentiment[sym][sentiment] += 1

        ts = post.get("published_on")
        pub_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""

        processed.append({
            "title":        title,
            "sentiment":    sentiment,
            "bull_score":   bull_score,
            "bear_score":   bear_score,
            "coins":        ", ".join(all_symbols) if all_symbols else "—",
            "url":          post.get("url", ""),
            "published_at": pub_str,
        })

    return processed, dict(coin_sentiment)

# ─────────────────────────────────────────────────────────────────────────────
# VOLUME SPIKE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def compute_volume_spikes(top_coins: list, progress_bar=None) -> dict:
    """
    Estimate volume spike ratio using data already in the markets response —
    no extra API calls needed.

    vol/mcap > ~5% is typical; we normalise so that ratio gives a spike
    multiplier comparable to the 2× threshold used in the signal engine.
    Coins with 7d price change data get a slight adjustment to reward
    volume that outpaces recent price movement.
    """
    spikes = {}
    for coin in top_coins:
        cid     = coin["id"]
        vol     = coin.get("total_volume") or 0
        mcap    = coin.get("market_cap") or 1
        ch_7d   = abs(coin.get("price_change_percentage_7d_in_currency") or 1)

        vol_ratio = vol / mcap          # typical range 0.02 – 0.20
        # Normalise: divide by 0.05 so a "normal" day ≈ 1.0
        ratio = (vol_ratio / 0.05) * max(1.0, ch_7d / 5)
        spikes[cid] = round(ratio, 2)

    return spikes

# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def build_signals(top_coins: list, spike_ratios: dict, coin_sentiment: dict) -> list:
    """
    Combine volume spike + news sentiment into buy / risk / watch signals.

    Labelling logic:
      - Buy Signal  : elevated volume (≥1.3×) AND more bullish news than bearish
      - Risk Signal : elevated volume (≥1.3×) AND more bearish news than bullish
      - Volume Alert: elevated volume (≥1.3×) but no clear news sentiment
      - Watch       : below-average volume but notable news sentiment
    All coins with spike ≥ 1.2 or any news mentions are included.
    """
    signals = []
    for coin in top_coins:
        cid   = coin["id"]
        sym   = coin["symbol"].upper()
        spike = spike_ratios.get(cid, 1.0)
        sent  = coin_sentiment.get(sym, {})
        bull  = sent.get("🟢 Bullish", 0)
        bear  = sent.get("🔴 Bearish", 0)

        has_volume = spike >= 1.3
        has_news   = bull > 0 or bear > 0

        if not has_volume and not has_news:
            continue

        if has_volume and bull > bear:
            label    = "🚀 Buy Signal"
            strength = spike * (bull + 1)
        elif has_volume and bear > bull:
            label    = "⚠️ Risk Signal"
            strength = spike * (bear + 1)
        elif has_volume:
            label    = "📊 Volume Alert"
            strength = spike
        else:
            label    = "👀 Watch"
            strength = (bull - bear) * 0.5 + 1

        ch24 = coin.get("price_change_percentage_24h_in_currency")
        signals.append({
            "Signal":       label,
            "Coin":         coin["name"],
            "Symbol":       sym,
            "Price":        coin.get("current_price"),
            "24h %":        ch24,
            "Vol Spike":    f"{spike:.1f}×",
            "Bullish News": bull,
            "Bearish News": bear,
            "Strength":     round(strength, 2),
            "_id":          cid,
        })

    signals.sort(key=lambda x: x["Strength"], reverse=True)
    return signals

# ─────────────────────────────────────────────────────────────────────────────
# UNDERVALUED SCORER
# ─────────────────────────────────────────────────────────────────────────────

def score_undervalued(coins: list, coin_sentiment: dict) -> list:
    """
    Score coins ranked 51-300 out of 100 using five metrics.

    Scoring breakdown:
      30 pts — volume / market-cap ratio   (high turnover = growing interest)
      25 pts — distance from ATL           (closer to all-time low = better)
      20 pts — 7-day momentum              (slight positive, not already pumped)
      10 pts — 24h price change            (small positive preferred)
      15 pts — CryptoCompare mention count  (social awareness)
    """
    scored = []
    for coin in coins:
        sym       = coin["symbol"].upper()
        mcap      = coin.get("market_cap") or 0
        vol_24h   = coin.get("total_volume") or 0
        price     = coin.get("current_price") or 0
        atl_chg   = coin.get("atl_change_percentage") or 999
        ch_24h    = coin.get("price_change_percentage_24h_in_currency") or 0
        ch_7d     = coin.get("price_change_percentage_7d_in_currency") or 0

        # 1. Vol / Market-cap ratio (0-30 pts)
        vol_ratio  = (vol_24h / mcap) if mcap > 0 else 0
        score_vol  = min(30, vol_ratio * 150)

        # 2. Distance from ATL (0-25 pts) — lower atl_change_percentage is better
        if atl_chg < 50:
            score_atl = 25
        elif atl_chg < 300:
            score_atl = max(0, 25 - (atl_chg - 50) / 10)
        else:
            score_atl = 0

        # 3. 7-day momentum (0-20 pts)
        if 1 <= ch_7d <= 15:
            score_mom = 20
        elif 0 <= ch_7d < 1:
            score_mom = 12
        elif -10 <= ch_7d < 0:
            score_mom = 8
        elif 15 < ch_7d <= 30:
            score_mom = 8
        elif -20 <= ch_7d < -10:
            score_mom = 4
        else:
            score_mom = 0

        # 4. 24h change (0-10 pts)
        if 0 <= ch_24h <= 5:
            score_24h = 10
        elif -3 <= ch_24h < 0:
            score_24h = 6
        elif 5 < ch_24h <= 10:
            score_24h = 5
        else:
            score_24h = 0

        # 5. Social mentions (0-15 pts)
        sent     = coin_sentiment.get(sym, {})
        mentions = sum(sent.values()) if sent else 0
        score_social = min(15, mentions * 3)

        total = score_vol + score_atl + score_mom + score_24h + score_social

        if mcap < HIGH_RISK_USD:
            risk = "🔴 High"
        elif mcap < MEDIUM_RISK_USD:
            risk = "🟡 Medium"
        else:
            risk = "🟢 Low"

        scored.append({
            "Coin":        coin["name"],
            "Symbol":      sym,
            "Score":       round(total, 1),
            "Price (USD)": price,
            "Mkt Cap":     mcap,
            "Vol/MCap %":  round(vol_ratio * 100, 2),
            "7d %":        round(ch_7d, 1),
            "24h %":       round(ch_24h, 1),
            "From ATL":    f"+{atl_chg:.0f}%",
            "Mentions":    mentions,
            "Risk":        risk,
            "_id":         coin["id"],
        })

    scored.sort(key=lambda x: x["Score"], reverse=True)
    for i, row in enumerate(scored):
        row["#"] = i + 1

    return scored[:20]

# ─────────────────────────────────────────────────────────────────────────────
# CHART BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def generate_coin_summary(coin: dict, spike: float, sentiment: dict) -> tuple:
    """
    Return (pros, cons) lists based on the coin's current market data and sentiment.
    Each item is a plain string — no emoji prefix (added by the UI).
    """
    pros, cons = [], []

    ch_24h = coin.get("price_change_percentage_24h_in_currency") or 0
    ch_7d  = coin.get("price_change_percentage_7d_in_currency")  or 0
    mcap   = coin.get("market_cap") or 0
    vol    = coin.get("total_volume") or 0
    atl_ch = coin.get("atl_change_percentage") or 999
    ath_ch = coin.get("ath_change_percentage") or 0   # negative = below ATH
    price  = coin.get("current_price") or 0
    name   = coin.get("name", "")

    bull = sentiment.get("🟢 Bullish", 0)
    bear = sentiment.get("🔴 Bearish", 0)
    vol_ratio = (vol / mcap * 100) if mcap else 0

    # ── Momentum ─────────────────────────────────────────────────────────────
    if ch_24h >= 5:
        pros.append(f"Strong 24h price gain of +{ch_24h:.1f}% — buying pressure present")
    elif ch_24h > 0:
        pros.append(f"Positive 24h momentum (+{ch_24h:.1f}%)")
    elif ch_24h < -10:
        cons.append(f"Sharp 24h drop of {ch_24h:.1f}% — possible sell-off")
    elif ch_24h < 0:
        cons.append(f"Negative 24h momentum ({ch_24h:.1f}%)")

    if ch_7d >= 10:
        pros.append(f"Up {ch_7d:.1f}% over 7 days — sustained upward trend")
    elif ch_7d > 0:
        pros.append(f"Positive 7-day trend (+{ch_7d:.1f}%)")
    elif ch_7d < -15:
        cons.append(f"Down {ch_7d:.1f}% over 7 days — extended weakness")
    elif ch_7d < 0:
        cons.append(f"Negative 7-day momentum ({ch_7d:.1f}%)")

    # ── Volume ───────────────────────────────────────────────────────────────
    if spike >= 2.0:
        pros.append(f"Volume spike detected ({spike:.1f}× normal) — unusual market interest")
    elif spike >= 1.3:
        pros.append(f"Above-average trading volume ({spike:.1f}× normal)")
    elif vol_ratio < 1:
        cons.append("Very low trading volume — thin liquidity, harder to exit positions")

    # ── Price vs ATL / ATH ────────────────────────────────────────────────────
    if atl_ch < 50:
        pros.append(f"Price is only {atl_ch:.0f}% above its all-time low — near historical bottom")
    elif atl_ch < 150:
        pros.append(f"Relatively close to all-time low (+{atl_ch:.0f}%) — potential value zone")
    elif atl_ch > 1000:
        cons.append(f"Already {atl_ch:.0f}% above all-time low — significant downside risk if trend reverses")

    if ath_ch > -10:
        cons.append(f"Near all-time high (only {abs(ath_ch):.0f}% below ATH) — limited upside, high retracement risk")
    elif ath_ch < -80:
        pros.append(f"Well below all-time high ({ath_ch:.0f}%) — room to recover if conditions improve")

    # ── Market cap / risk ─────────────────────────────────────────────────────
    if mcap >= 10e9:
        pros.append(f"Large market cap ({fmt_usd(mcap)}) — established, liquid asset")
    elif mcap >= 1e9:
        pros.append(f"Mid-cap coin ({fmt_usd(mcap)}) — reasonable liquidity")
    elif mcap < HIGH_RISK_USD:
        cons.append(f"Very small market cap ({fmt_usd(mcap)}) — high volatility and manipulation risk")
    elif mcap < MEDIUM_RISK_USD:
        cons.append(f"Small market cap ({fmt_usd(mcap)}) — elevated risk, lower liquidity")

    # ── News sentiment ────────────────────────────────────────────────────────
    if bull > bear and bull >= 2:
        pros.append(f"{bull} bullish news mentions vs {bear} bearish — positive media sentiment")
    elif bull > 0 and bear == 0:
        pros.append(f"{bull} bullish news mention(s) with no negative coverage")
    elif bear > bull and bear >= 2:
        cons.append(f"{bear} bearish news mentions vs {bull} bullish — negative media sentiment")
    elif bear > 0 and bull == 0:
        cons.append(f"{bear} bearish news mention(s) with no positive coverage")
    elif bull == 0 and bear == 0:
        cons.append("No news mentions found — low visibility in current news cycle")

    return pros, cons


def build_chart(coin_id: str, coin_name: str):
    data = fetch_coin_history(coin_id, days=30)
    if not data:
        return None

    prices  = data.get("prices", [])
    volumes = data.get("total_volumes", [])
    if not prices:
        return None

    ts     = [datetime.utcfromtimestamp(p[0] / 1000) for p in prices]
    p_vals = [p[1] for p in prices]
    v_vals = [v[1] for v in volumes] if volumes else []

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.70, 0.30],
        vertical_spacing=0.03,
    )

    fig.add_trace(go.Scatter(
        x=ts, y=p_vals,
        mode="lines",
        name="Price",
        line=dict(color="#2563eb", width=2),
        fill="tozeroy",
        fillcolor="rgba(37,99,235,0.07)",
    ), row=1, col=1)

    if v_vals:
        colors = [
            "rgba(22,163,74,0.7)" if p_vals[i] >= p_vals[i - 1] else "rgba(220,38,38,0.7)"
            for i in range(len(p_vals))
        ]
        fig.add_trace(go.Bar(
            x=ts, y=v_vals,
            name="Volume",
            marker_color=colors,
        ), row=2, col=1)

    fig.update_layout(
        title=f"{coin_name} — 30-Day Price & Volume (USD)",
        template="plotly_white",
        showlegend=False,
        margin=dict(l=0, r=0, t=44, b=0),
        height=440,
        paper_bgcolor="#ffffff",
        plot_bgcolor="#f8f9fb",
    )
    fig.update_yaxes(title_text="Price", row=1, col=1, tickprefix="$", gridcolor="#e2e6ed")
    fig.update_yaxes(title_text="Volume", row=2, col=1, gridcolor="#e2e6ed")
    fig.update_xaxes(gridcolor="#e2e6ed")

    return fig

# ─────────────────────────────────────────────────────────────────────────────
# STREAMLIT UI
# ─────────────────────────────────────────────────────────────────────────────

CSS = """
<style>
/* ── Global ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; background: #f8f9fb; color: #1a1f2e; }

/* ── Hide default Streamlit chrome ── */
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 1.5rem; padding-bottom: 2rem; background: #f8f9fb; }

/* ── Page header ── */
.dash-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 1.2rem 0 0.8rem;
    border-bottom: 2px solid #e2e6ed;
    margin-bottom: 1.4rem;
}
.dash-title {
    font-size: 1.6rem; font-weight: 700; color: #1a1f2e;
    letter-spacing: -0.5px;
}
.dash-title span { color: #2563eb; }
.dash-sub {
    font-size: 0.75rem; color: #6b7280;
    background: #fff; border: 1px solid #e2e6ed;
    padding: 3px 12px; border-radius: 20px;
}

/* ── Section headings ── */
.section-head {
    font-size: 0.7rem; font-weight: 700; letter-spacing: 1.5px;
    text-transform: uppercase; color: #2563eb;
    margin: 1.8rem 0 0.8rem;
    display: flex; align-items: center; gap: 8px;
}
.section-head::after {
    content: ""; flex: 1; height: 1px; background: #e2e6ed;
}

/* ── KPI cards ── */
.kpi-grid { display: flex; gap: 12px; margin-bottom: 1rem; }
.kpi-card {
    flex: 1; background: #fff;
    border: 1px solid #e2e6ed;
    border-radius: 10px; padding: 16px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
.kpi-label { font-size: 0.68rem; color: #6b7280; letter-spacing: 0.8px; text-transform: uppercase; margin-bottom: 6px; }
.kpi-value { font-size: 1.8rem; font-weight: 700; color: #1a1f2e; line-height: 1; }
.kpi-card.green .kpi-value { color: #16a34a; }
.kpi-card.red   .kpi-value { color: #dc2626; }
.kpi-card.blue  .kpi-value { color: #2563eb; }

/* ── News feed ── */
.news-item {
    display: flex; align-items: flex-start; gap: 12px;
    padding: 10px 14px; margin-bottom: 6px;
    background: #fff; border: 1px solid #e2e6ed;
    border-radius: 8px;
}
.news-sent { font-size: 0.78rem; font-weight: 600; min-width: 70px; padding-top: 2px; }
.news-body { flex: 1; }
.news-title { font-size: 0.85rem; color: #1a1f2e; line-height: 1.4; }
.news-title a { color: #1a1f2e; text-decoration: none; }
.news-title a:hover { color: #2563eb; text-decoration: underline; }
.news-meta { font-size: 0.7rem; color: #9ca3af; margin-top: 3px; }
.news-coin { font-size: 0.68rem; background: #eff6ff; color: #2563eb;
             padding: 1px 7px; border-radius: 3px; margin-left: 6px;
             border: 1px solid #bfdbfe; }

/* ── Disclaimer bar ── */
.disclaimer {
    margin-top: 2rem; padding: 12px 16px;
    background: #fff; border: 1px solid #e2e6ed;
    border-radius: 8px; font-size: 0.72rem; color: #6b7280; line-height: 1.6;
}

/* ── Streamlit overrides ── */
div[data-testid="stDataFrame"] {
    border: 1px solid #e2e6ed; border-radius: 10px;
    overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
[data-testid="stSidebar"] {
    background: #fff !important;
    border-right: 1px solid #e2e6ed;
}
.stButton > button {
    background: #2563eb !important; color: #fff !important;
    font-weight: 600 !important; border: none !important;
    border-radius: 8px !important; padding: 0.5rem 1rem !important;
}
.stButton > button:hover { background: #1d4ed8 !important; }
</style>
"""


def _section(icon: str, title: str):
    st.markdown(f'<div class="section-head">{icon}&nbsp; {title}</div>', unsafe_allow_html=True)


def _kpi(label: str, value, style: str = ""):
    return (
        f'<div class="kpi-card {style}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'</div>'
    )


def _badge(signal: str) -> str:
    if "Buy"    in signal: return f'<span class="badge badge-buy">{signal}</span>'
    if "Risk"   in signal: return f'<span class="badge badge-risk">{signal}</span>'
    if "Volume" in signal: return f'<span class="badge badge-vol">{signal}</span>'
    return f'<span class="badge badge-watch">{signal}</span>'


def _color_pct(val_str: str) -> str:
    """Wrap a percentage string in a coloured span."""
    try:
        v = float(val_str.replace("%", "").replace("+", ""))
        color = "#16a34a" if v > 0 else ("#dc2626" if v < 0 else "#6b7280")
        return f'<span style="color:{color};font-weight:600">{val_str}</span>'
    except Exception:
        return val_str


def main():
    st.set_page_config(
        page_title="Crypto Calls",
        page_icon="📡",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CSS, unsafe_allow_html=True)

    # ── Page header ───────────────────────────────────────────────────────────
    last_refresh = st.session_state.get("last_refresh", "Not loaded")
    st.markdown(
        f'<div class="dash-header">'
        f'<div class="dash-title">📡 <span>Crypto</span> Calls</div>'
        f'<div class="dash-sub">Updated {last_refresh} · Research only, not financial advice</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### ⚙️ Controls")
        refresh = st.button("↺  Refresh All Data", use_container_width=True)
        st.divider()
        spike_thresh = st.slider("Volume spike threshold", 1.5, 5.0, VOLUME_SPIKE_MULT, 0.5,
                                 help="Minimum spike multiplier to show in Signal table")
        st.divider()
        st.markdown(
            "<small style='color:#6b7280'>Data sources<br>"
            "· CoinGecko — market data<br>"
            "· CoinTelegraph / CoinDesk / Decrypt — news</small>",
            unsafe_allow_html=True,
        )

    # ── Load / refresh data ───────────────────────────────────────────────────
    needs_load = refresh or "data_loaded" not in st.session_state

    if needs_load:
        if refresh:
            for fn in [fetch_top50, fetch_undervalued_universe, fetch_news, fetch_coin_history]:
                fn.clear()

        with st.status("Loading data…", expanded=True) as status:
            st.write("Fetching market data from CoinGecko…")
            top_coins    = fetch_top50()
            uv_coins     = fetch_undervalued_universe()
            st.write("Fetching news from CoinTelegraph, CoinDesk & Decrypt…")
            news_posts   = fetch_news()
            spike_ratios = compute_volume_spikes(top_coins)
            status.update(label="✅ Data loaded", state="complete", expanded=False)

        known_symbols  = {c["symbol"].upper() for c in top_coins + uv_coins}
        processed_news, coin_sentiment = process_news(news_posts, known_symbols)
        signals        = build_signals(top_coins, spike_ratios, coin_sentiment)
        uv_scored      = score_undervalued(uv_coins, coin_sentiment)

        st.session_state.update({
            "data_loaded":     True,
            "top_coins":       top_coins,
            "uv_coins":        uv_coins,
            "spike_ratios":    spike_ratios,
            "processed_news":  processed_news,
            "coin_sentiment":  coin_sentiment,
            "signals":         signals,
            "uv_scored":       uv_scored,
            "last_refresh":    datetime.utcnow().strftime("%d %b %Y %H:%M UTC"),
        })

    # ── Pull from session state ───────────────────────────────────────────────
    top_coins      = st.session_state.get("top_coins", [])
    uv_coins       = st.session_state.get("uv_coins", [])
    signals        = st.session_state.get("signals", [])
    processed_news = st.session_state.get("processed_news", [])
    coin_sentiment = st.session_state.get("coin_sentiment", {})
    uv_scored      = st.session_state.get("uv_scored", [])
    spike_ratios   = st.session_state.get("spike_ratios", {})

    if not top_coins:
        st.markdown(
            '<div style="text-align:center;padding:4rem 0;color:#6b7280">'
            '<div style="font-size:2.5rem">📡</div>'
            '<div style="font-size:1rem;margin-top:0.5rem">Press <b style="color:#2563eb">Refresh All Data</b> to load the dashboard</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # =========================================================================
    # SECTION 1 — SIGNAL SUMMARY
    # =========================================================================
    _section("🎯", "Signal Summary")

    buy_sigs   = [s for s in signals if "Buy"    in s["Signal"] and float(s["Vol Spike"].rstrip("×")) >= spike_thresh]
    risk_sigs  = [s for s in signals if "Risk"   in s["Signal"] and float(s["Vol Spike"].rstrip("×")) >= spike_thresh]
    vol_alerts = [s for s in signals if "Volume" in s["Signal"]]

    st.markdown(
        '<div class="kpi-grid">'
        + _kpi("Buy Signals",    len(buy_sigs),   "green")
        + _kpi("Risk Signals",   len(risk_sigs),  "red")
        + _kpi("Volume Alerts",  len(vol_alerts), "blue")
        + _kpi("Coins Screened", len(top_coins),  "")
        + '</div>',
        unsafe_allow_html=True,
    )

    if signals:
        rows = []
        for s in signals[:20]:
            ch  = s["24h %"]
            ch_str = f"{ch:+.2f}%" if ch is not None else "—"
            rows.append({
                "Signal":       s["Signal"],
                "Coin":         s["Coin"],
                "Symbol":       s["Symbol"],
                "Price":        f"${s['Price']:,.4f}" if s["Price"] else "—",
                "24h %":        ch_str,
                "Vol Spike":    s["Vol Spike"],
                "Bull News":    s["Bullish News"],
                "Bear News":    s["Bearish News"],
                "Strength":     s["Strength"],
            })
        sig_df = pd.DataFrame(rows)

        def style_signals(df):
            styles = pd.DataFrame("", index=df.index, columns=df.columns)
            for i, row in df.iterrows():
                if "Buy"  in row["Signal"]: styles.loc[i, "Signal"] = "color:#16a34a;font-weight:600"
                if "Risk" in row["Signal"]: styles.loc[i, "Signal"] = "color:#dc2626;font-weight:600"
                if "Volume" in row["Signal"]: styles.loc[i, "Signal"] = "color:#2563eb;font-weight:600"
                try:
                    v = float(row["24h %"].replace("%","").replace("+",""))
                    styles.loc[i, "24h %"] = f"color:{'#16a34a' if v>0 else '#dc2626'};font-weight:600"
                except Exception:
                    pass
            return styles

        st.dataframe(
            sig_df.style.apply(style_signals, axis=None),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No signals at current threshold — try lowering the spike multiplier in the sidebar.")

    # =========================================================================
    # SECTION 2 — UNDERVALUED ASSET SCANNER
    # =========================================================================
    _section("💎", "Undervalued Asset Scanner  ·  Rank 51–150")
    st.markdown(
        "<small style='color:#6b7280'>Scored out of 100 · Vol/MCap 30pts · Distance from ATL 25pts · "
        "7d momentum 20pts · 24h change 10pts · Social mentions 15pts</small>",
        unsafe_allow_html=True,
    )

    if uv_scored:
        uv_rows = []
        for s in uv_scored:
            uv_rows.append({
                "#":          s["#"],
                "Coin":       s["Coin"],
                "Symbol":     s["Symbol"],
                "Score":      s["Score"],
                "Price":      f"${s['Price (USD)']:,.4f}" if s["Price (USD)"] else "—",
                "Mkt Cap":    fmt_usd(s["Mkt Cap"]),
                "Vol/MCap":   f"{s['Vol/MCap %']}%",
                "7d %":       f"{s['7d %']:+.1f}%",
                "24h %":      f"{s['24h %']:+.1f}%",
                "From ATL":   s["From ATL"],
                "Mentions":   s["Mentions"],
                "Risk":       s["Risk"],
            })
        uv_df = pd.DataFrame(uv_rows)

        def style_uv(df):
            styles = pd.DataFrame("", index=df.index, columns=df.columns)
            for i, row in df.iterrows():
                for col in ["7d %", "24h %"]:
                    try:
                        v = float(row[col].replace("%","").replace("+",""))
                        styles.loc[i, col] = f"color:{'#16a34a' if v>0 else '#dc2626'};font-weight:600"
                    except Exception:
                        pass
                if "High"   in str(row["Risk"]): styles.loc[i, "Risk"] = "color:#dc2626"
                if "Medium" in str(row["Risk"]): styles.loc[i, "Risk"] = "color:#d97706"
                if "Low"    in str(row["Risk"]): styles.loc[i, "Risk"] = "color:#16a34a"
                score = row["Score"]
                if score >= 60:   styles.loc[i, "Score"] = "color:#16a34a;font-weight:700"
                elif score >= 40: styles.loc[i, "Score"] = "color:#d97706;font-weight:600"
                else:             styles.loc[i, "Score"] = "color:#6b7280"
            return styles

        st.dataframe(
            uv_df.style.apply(style_uv, axis=None),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No undervalued data available.")

    # =========================================================================
    # SECTION 3 — LIVE NEWS FEED
    # =========================================================================
    _section("📰", "Live News Feed")

    col_left, col_right = st.columns([1.4, 1])

    with col_left:
        # Filter bar
        fc1, fc2 = st.columns(2)
        with fc1:
            f_sent = st.selectbox("Sentiment", ["All", "🟢 Bullish", "🔴 Bearish", "⚪ Neutral"],
                                  key="news_sentiment_filter", label_visibility="collapsed")
        with fc2:
            f_coin = st.text_input("Coin symbol", placeholder="Filter by coin e.g. BTC",
                                   key="news_coin_filter", label_visibility="collapsed").upper()

        filtered = processed_news or []
        if f_sent != "All":
            filtered = [n for n in filtered if n["sentiment"] == f_sent]
        if f_coin:
            filtered = [n for n in filtered if f_coin in n["coins"]]

        if filtered:
            items_html = ""
            for item in filtered[:40]:
                pub   = item["published_at"]
                url   = item["url"]
                title = item["title"]
                coins = item["coins"]
                sent  = item["sentiment"]

                sent_color = "#16a34a" if "Bullish" in sent else ("#dc2626" if "Bearish" in sent else "#6b7280")
                sent_label = sent.split(" ", 1)[-1] if " " in sent else sent
                coin_tag   = f'<span class="news-coin">{coins}</span>' if coins != "—" else ""
                link       = f'<a href="{url}" target="_blank">{title}</a>' if url else title

                items_html += (
                    f'<div class="news-item">'
                    f'<span class="news-sent" style="color:{sent_color}">{sent_label}</span>'
                    f'<div class="news-body">'
                    f'<div class="news-title">{link}{coin_tag}</div>'
                    f'<div class="news-meta">{pub}</div>'
                    f'</div></div>'
                )
            st.markdown(items_html, unsafe_allow_html=True)
        else:
            st.info("No news loaded — RSS feeds may be temporarily unavailable. Try refreshing.")

    with col_right:
        st.markdown(
            "<div style='font-size:0.7rem;color:#6b7280;letter-spacing:1px;"
            "text-transform:uppercase;margin-bottom:0.5rem'>Most Mentioned Coins</div>",
            unsafe_allow_html=True,
        )
        if coin_sentiment:
            sent_rows = [
                {
                    "Symbol":  sym,
                    "Bull":    v.get("🟢 Bullish", 0),
                    "Bear":    v.get("🔴 Bearish", 0),
                    "Neutral": v.get("⚪ Neutral", 0),
                    "Total":   sum(v.values()),
                }
                for sym, v in coin_sentiment.items() if sum(v.values()) >= 1
            ]
            if sent_rows:
                sent_df = (
                    pd.DataFrame(sent_rows)
                    .sort_values("Total", ascending=False)
                    .head(15)
                )

                def style_sent(df):
                    styles = pd.DataFrame("", index=df.index, columns=df.columns)
                    for i in df.index:
                        styles.loc[i, "Bull"]    = "color:#16a34a;font-weight:600"
                        styles.loc[i, "Bear"]    = "color:#dc2626;font-weight:600"
                        styles.loc[i, "Neutral"] = "color:#6b7280"
                    return styles

                st.dataframe(
                    sent_df.style.apply(style_sent, axis=None),
                    use_container_width=True, hide_index=True, height=420,
                )

    # =========================================================================
    # SECTION 4 — PRICE & VOLUME CHART
    # =========================================================================
    _section("📈", "Price & Volume Chart")

    all_coins = top_coins + uv_coins
    seen_ids, options = set(), {}
    for c in all_coins:
        if c["id"] not in seen_ids:
            options[f"{c['name']} ({c['symbol'].upper()})"] = c
            seen_ids.add(c["id"])

    selected_label = st.selectbox(
        f"Select from {len(options)} coins",
        list(options.keys()),
        key="chart_coin_select",
        label_visibility="collapsed",
    )

    if selected_label:
        coin  = options[selected_label]
        sym   = coin["symbol"].upper()
        spike = spike_ratios.get(coin["id"], 1.0)
        sent  = coin_sentiment.get(sym, {})

        # ── Stats row ────────────────────────────────────────────────────────
        ch = coin.get("price_change_percentage_24h_in_currency") or 0
        ch7 = coin.get("price_change_percentage_7d_in_currency") or 0
        ch_color  = "#16a34a" if ch  >= 0 else "#dc2626"
        ch7_color = "#16a34a" if ch7 >= 0 else "#dc2626"
        st.markdown(
            '<div class="kpi-grid">'
            + _kpi("Price (USD)",  f"${coin.get('current_price', 0):,.4f}", "")
            + _kpi("Market Cap",   fmt_usd(coin.get("market_cap")), "")
            + _kpi("24h Volume",   fmt_usd(coin.get("total_volume")), "")
            + f'<div class="kpi-card"><div class="kpi-label">24h Change</div>'
              f'<div class="kpi-value" style="color:{ch_color}">{ch:+.2f}%</div></div>'
            + f'<div class="kpi-card"><div class="kpi-label">7d Change</div>'
              f'<div class="kpi-value" style="color:{ch7_color}">{ch7:+.2f}%</div></div>'
            + '</div>',
            unsafe_allow_html=True,
        )

        # ── Chart + Pros/Cons side by side ────────────────────────────────────
        chart_col, summary_col = st.columns([2, 1])

        with chart_col:
            with st.spinner(""):
                fig = build_chart(coin["id"], coin["name"])
            if fig:
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Could not load chart data.")

        with summary_col:
            pros, cons = generate_coin_summary(coin, spike, sent)

            st.markdown(
                f'<div style="font-size:0.8rem;font-weight:700;color:#1a1f2e;'
                f'margin-bottom:0.6rem;padding-top:0.4rem">'
                f'{coin["name"]} — Quick Summary</div>',
                unsafe_allow_html=True,
            )

            if pros:
                st.markdown(
                    '<div style="font-size:0.72rem;font-weight:700;color:#16a34a;'
                    'letter-spacing:0.8px;text-transform:uppercase;margin-bottom:4px">'
                    'Positives</div>',
                    unsafe_allow_html=True,
                )
                for p in pros:
                    st.markdown(
                        f'<div style="display:flex;gap:8px;padding:6px 10px;margin-bottom:4px;'
                        f'background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;'
                        f'font-size:0.78rem;color:#15803d;line-height:1.4">'
                        f'<span>✓</span><span>{p}</span></div>',
                        unsafe_allow_html=True,
                    )

            if cons:
                st.markdown(
                    '<div style="font-size:0.72rem;font-weight:700;color:#dc2626;'
                    'letter-spacing:0.8px;text-transform:uppercase;margin:10px 0 4px">'
                    'Risks</div>',
                    unsafe_allow_html=True,
                )
                for c in cons:
                    st.markdown(
                        f'<div style="display:flex;gap:8px;padding:6px 10px;margin-bottom:4px;'
                        f'background:#fff5f5;border:1px solid #fecaca;border-radius:6px;'
                        f'font-size:0.78rem;color:#b91c1c;line-height:1.4">'
                        f'<span>✗</span><span>{c}</span></div>',
                        unsafe_allow_html=True,
                    )

            if not pros and not cons:
                st.info("Not enough data to generate a summary.")

    # ── Disclaimer ────────────────────────────────────────────────────────────
    st.markdown(
        '<div class="disclaimer">'
        '⚠️ <b>Disclaimer:</b> This dashboard is a research tool only. It does not constitute '
        'financial advice, investment recommendations, or any offer to buy or sell any asset. '
        'Cryptocurrency markets are highly volatile and you may lose all capital invested. '
        'Always conduct independent research and consult a qualified financial adviser before '
        'making investment decisions. Data sourced from CoinGecko and CryptoCompare free APIs.'
        '</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
