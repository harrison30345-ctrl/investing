"""
Interactive Streamlit dashboard for the stock screener.
Launch with:  python3 -m streamlit run dashboard.py   (root launcher)

LANGUAGE POLICY
---------------
This is a UK consumer-facing research and education product. It describes what
company data shows; it does not instruct anyone to trade.

User-facing copy must NOT contain: buy, sell, entry, exit, stop loss, target
price, position size, allocation, risk/reward ratio, or "conviction" as a
recommendation strength. `tests/test_language.py` enforces this and will fail
the build if such wording reappears.

Use instead: research score, strengths, risks, factors, what changed, what to
monitor, typical downside, analyst upside (always attributed to third-party
analysts, never presented as this platform's forecast).

This policy reduces the risk of the product reading as a personal
recommendation. It is not a legal opinion, and the product has not been
reviewed against the FCA perimeter -- that review is still outstanding.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from screener.data_fetcher import YFinanceFetcher
    from screener.metrics import calculate_all_metrics
    from screener.filters import load_config, screen_batch
    from screener.scoring import score_batch
    from screener.universe import get_universe, load_tickers_from_file, get_ai_leaders, get_tech_mega, get_trading212_isa
except ImportError:
    from data_fetcher import YFinanceFetcher
    from metrics import calculate_all_metrics
    from filters import load_config, screen_batch
    from scoring import score_batch
    from universe import get_universe, load_tickers_from_file, get_ai_leaders, get_tech_mega, get_trading212_isa

# Research scoring stack. The UI talks to these services, never to a data
# provider directly -- see the licensing note in services/market_data.py.
from services.explanations import GLOSSARY, explain, format_value
from services.market_data import PROVIDER_IS_LICENSED, PROVIDER_NAME, get_provider
from services.hidden_gems import (
    GEM_GATES, MAX_ANALYSTS, METHODOLOGY_VERSION as GEM_METHODOLOGY_VERSION, assess_gem,
)
from config.screener_presets import FILTERABLE, PRESETS, PRESETS_BY_KEY
from services.comparison import MAX_COMPANIES, MIN_MEANINGFUL_GAP, compare
from services.momentum import assess_momentum
from services.score_history import ScoreHistory, describe_change
from services.screening import apply_preset, screen
from services.watchlist import Watchlist

try:
    from screener import theme
except ImportError:  # pragma: no cover
    import theme  # type: ignore
from services.scoring import SCORING_VERSION, score_company


@st.cache_resource(show_spinner=False)
def _watchlist() -> Watchlist:
    """Shared watchlist store. Local single-user today; the `owner` column is
    already in place so accounts can be added without migrating data."""
    return Watchlist()


@st.cache_resource(show_spinner=False)
def _score_history() -> ScoreHistory:
    """Shared append-only store of scores.

    Snapshots start accruing from the first day this runs. Past scores are
    never reconstructed -- see the module docstring in services/score_history.py
    for why that would be fabrication rather than history.
    """
    return ScoreHistory()

PACKAGE_DIR = Path(__file__).parent
# Check multiple locations for config files (local dev vs Streamlit Cloud)
def _find_configs_dir() -> Path:
    for candidate in [PACKAGE_DIR / "configs", PACKAGE_DIR, Path.cwd() / "configs", Path.cwd()]:
        if candidate.exists() and any(candidate.glob("config_*.yaml")):
            return candidate
    return PACKAGE_DIR
CONFIGS_DIR = _find_configs_dir()
OUTPUT_DIR = Path.home() / ".screener" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Page config ──────────────────────────────────────────────
st.set_page_config(
    page_title="Barry's Investor Square",
    page_icon="♜",
    layout="wide",
    initial_sidebar_state="expanded",
)

theme.inject()




CHART_BG     = theme.SURFACE
CHART_PAPER  = theme.PAPER
CHART_GRID   = theme.RULE
CHART_TEXT   = theme.MUTED
GOLD         = theme.BRASS
# Desaturated series colours. Bright blue/green/red read as a crypto dashboard.
PALETTE      = ["#8d7434", "#3f5876", "#2f6b4f", "#8a6a2f", "#59527a", "#6d6a5c"]


def chart_layout(**kwargs):
    """Shared Plotly layout.

    Delegates to the theme's quiet layout: transparent ground, no legend, a
    single horizontal gridline set, small muted type. Charts here answer one
    question each and should not compete with the type around them.
    """
    return theme.chart_layout_quiet(**kwargs)


# ── Fast batch data helpers ──────────────────────────────────
# Cache keyed on (tickers_tuple, period, interval, bucket) — see _bucket() below.
# Using tuples because st.cache_data requires hashable args.

_CACHE_TTL = 6 * 3600  # 6-hour refresh window — balances freshness with speed


def _bucket() -> int:
    """Current 6-hour time bucket.

    Streamlit ignores `ttl` on any cache_data function using persist="disk", so
    disk-cached results would otherwise live forever. Passing this bucket in as a
    hashed argument changes the cache key every _CACHE_TTL seconds, which gives us
    real expiry back without giving up disk persistence.
    """
    return int(time.time() // _CACHE_TTL)


@st.cache_data(persist="disk", show_spinner=False)
def _batch_prices_cached(tickers_tuple: tuple, period: str, interval: str, bucket: int) -> dict:
    """Download OHLCV for every ticker in one yfinance call. Persisted to disk for fast reloads."""
    tickers_list = list(tickers_tuple)
    if not tickers_list:
        return {}
    raw = yf.download(
        tickers_list, period=period, interval=interval,
        auto_adjust=True, group_by="ticker",
        threads=True, progress=False,
    )
    out: dict = {}
    if len(tickers_list) == 1:
        if not raw.empty:
            out[tickers_list[0]] = raw
    else:
        for t in tickers_list:
            try:
                df = raw[t].dropna(how="all")
                if not df.empty:
                    out[t] = df
            except (KeyError, TypeError):
                pass
    return out


def _batch_prices(tickers_tuple: tuple, period: str = "1mo", interval: str = "1d") -> dict:
    return _batch_prices_cached(tickers_tuple, period, interval, _bucket())


def _fetch_one_info(ticker: str) -> tuple:
    try:
        return ticker, yf.Ticker(ticker).info or {}
    except Exception:
        return ticker, {}


@st.cache_data(persist="disk", show_spinner=False)
def _batch_info_cached(tickers_tuple: tuple, max_workers: int, bucket: int) -> dict:
    """Fetch .info dicts for all tickers in parallel. 25 workers + disk persistence = fast reloads."""
    results: dict = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_one_info, t): t for t in tickers_tuple}
        for f in as_completed(futures):
            ticker, info = f.result()
            results[ticker] = info
    return results


def _batch_info(tickers_tuple: tuple, max_workers: int = 25) -> dict:
    return _batch_info_cached(tickers_tuple, max_workers, _bucket())


def _fundamentals_from_info(info: dict, hist=None) -> dict:
    """Map a provider info dict (+ price history) to the scoring engine's inputs.

    Absent, non-numeric and NaN values are omitted rather than coerced. An
    omitted key is treated by the scoring engine as unavailable, which lowers
    confidence -- it is never filled in with a neutral or favourable stand-in.
    """
    out: dict = {}
    for field in (
        "returnOnEquity", "profitMargins", "operatingMargins",
        "revenueGrowth", "earningsGrowth",
        "trailingPE", "forwardPE", "priceToSalesTrailing12Months",
        "debtToEquity", "currentRatio", "freeCashflow",
        "marketCap", "beta", "numberOfAnalystOpinions",
    ):
        value = info.get(field)
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number:  # NaN
            continue
        out[field] = number

    if hist is not None and not hist.empty and "Close" in hist:
        closes = hist["Close"].dropna()
        if len(closes) > 1:
            price = float(closes.iloc[-1])
            for key, days in (("chg_1w", 5), ("chg_1m", 21), ("chg_3m", 63)):
                if len(closes) > days:
                    prev = float(closes.iloc[-days - 1])
                    if prev > 0:
                        out[key] = (price / prev - 1) * 100
            if len(closes) >= 50:
                sma50 = float(closes.rolling(50).mean().iloc[-1])
                if sma50 > 0:
                    out["vs_sma50"] = (price - sma50) / sma50 * 100
    return out


def _weighted_known(parts: list) -> tuple:
    """Weighted average over the components that are actually known.

    `parts` is a list of (value_or_None, weight). Components whose input was
    unavailable are EXCLUDED and the remaining weights renormalised. They are
    never replaced with a stand-in value.

    This exists because the legacy inline scoring coerced missing inputs with
    `or 0`. On these pages the scores are *warnings*, where a low number means
    "nothing to worry about" -- so a company with no reported debt figure was
    scored as having no debt problem, and missing data made a holding look
    safer than one with real data. Unknown is now unknown.

    Returns (score_or_None, coverage) where coverage is the share of total
    weight that was known. Returns (None, 0.0) if nothing was known.
    """
    total = sum(w for _, w in parts)
    known = [(v, w) for v, w in parts if v is not None]
    have = sum(w for _, w in known)
    if not known or total <= 0:
        return None, 0.0
    return sum(v * w for v, w in known) / have, have / total


@st.cache_data(persist="disk", show_spinner=False)
def _research(ticker: str, bucket: int) -> dict:
    """Snapshot + score + explanation for one company, cached per 6h bucket.

    Module scope rather than page scope: both the Company page and Compare use
    it, and a shared cache means comparing companies you have already looked at
    costs no extra provider calls.
    """
    snap = get_provider().get_snapshot(ticker)
    if not snap.ok:
        return {"error": snap.error or "No data available."}
    score = score_company(snap.ticker, snap.fundamentals, snap.sector)
    return {
        "name": snap.name, "sector": snap.sector, "industry": snap.industry,
        "price": snap.price, "currency": snap.currency or "USD",
        "fetched_at": snap.fetched_at, "fundamentals": snap.fundamentals,
        "score": score, "explanation": explain(score, snap.name),
        "history": snap.history,
    }


@st.cache_data(persist="disk", show_spinner=False)
def _company_directory(bucket: int) -> dict:
    """Ticker -> company name for the searchable picker.

    Built from the curated universe's cached batch info, which the Overview
    already fetches, so in practice this is served from cache rather than
    costing another round of provider calls.
    """
    try:
        tickers = get_universe("all_curated")
        infos = _batch_info(tuple(tickers))
    except Exception:  # noqa: BLE001 - the picker degrades to tickers only
        return {}
    return {t: (info or {}).get("shortName") or t
            for t, info in infos.items() if (info or {}).get("shortName")}


def _company_picker(default: str = "AAPL") -> str:
    """Search by company name or ticker, with a free-text escape hatch.

    Streamlit's selectbox filters as you type, so this behaves as a search box
    while still showing what is available -- a bare text field gives no
    indication of what can be looked up, or whether a symbol is even valid.
    """
    directory = _company_directory(_bucket())
    known = sorted(directory)
    labels = {t: f"{t} — {directory[t]}" for t in known}

    # Anything the user has already looked at stays reachable even if it is
    # outside the curated list.
    try:
        for entry in _watchlist().recent(limit=20) + _watchlist().all():
            if entry.ticker not in labels:
                labels[entry.ticker] = f"{entry.ticker} — {entry.name}" if entry.name else entry.ticker
                known.append(entry.ticker)
    except Exception:  # noqa: BLE001
        pass

    known = sorted(set(known))
    OTHER = "Search another ticker…"
    options = known + [OTHER]
    previous = st.session_state.get("_co_last", default)
    index = options.index(previous) if previous in options else (
        options.index(default) if default in options else 0)

    pick = st.selectbox(
        "Company", options, index=index,
        format_func=lambda t: OTHER if t == OTHER else labels.get(t, t),
        label_visibility="collapsed",
        help="Type to filter by company name or ticker.",
    )
    if pick == OTHER:
        typed = st.text_input(
            "Ticker", value="", placeholder="Any ticker, for example BP.L",
            label_visibility="collapsed",
        ).strip().upper()
        return typed
    return pick


def _scan_gate(state_key: str, label: str, forced: bool = False, note: str = "") -> bool:
    """Decide whether an expensive scan should run on this page load.

    A scan costs hundreds of requests to the data provider, so it waits for an
    explicit click instead of firing for every visitor who opens the page. Once
    the result is in session state the gate stays out of the way, and the page's
    own refresh button bypasses it via `forced`.
    """
    if forced:
        return True
    if st.session_state.get(state_key) is not None:
        return False
    st.info(note or "This scan pulls live data for several hundred tickers — press the button to run it.")
    return st.button(label, type="primary", key=f"_gate_{state_key}")


# ── Research narrative generator ────────────────────
def generate_hf_summary(row: dict) -> dict:
    """
    Produce a data-driven narrative for a hedge fund pick.
    Returns: overview (str), bull_factors (list), bear_factors (list),
             strategy_note (str), risk_note (str).
    """
    ticker   = row.get("ticker", "")
    strategy = row.get("primary_strategy", "")
    conv     = row.get("score_band", "med")
    conv_w   = {"high": "high-scoring", "med": "mid-scoring", "low": "low-scoring"}.get(conv, "mid-scoring")

    def _v(key, default=0):
        v = row.get(key)
        return v if v is not None else default

    rev      = _v("rev_growth")
    earn     = _v("earn_growth")
    rsi      = _v("rsi", 50)
    vs20     = _v("vs_sma20")
    vs50     = _v("vs_sma50")
    vol      = _v("vol_surge", 1)
    pe       = _v("pe")
    fwd_pe   = _v("forward_pe")
    upside   = _v("upside")
    short    = _v("short_pct")
    pct_rng  = _v("pct_52w_range", 50)
    beta     = _v("beta", 1.0) or 1.0
    margin   = _v("net_margin")
    roe      = _v("roe")
    chg_1w   = _v("chg_1w")
    chg_1m   = _v("chg_1m")
    chg_3m   = _v("chg_3m")
    stop_p   = _v("stop_pct", 5)
    score    = _v("best_score")
    atr      = _v("atr_pct", 2)

    bull: list[str] = []
    bear: list[str] = []

    # ── Revenue ──────────────────────────────────────────────
    if rev > 25:
        bull.append(f"Revenue surging {rev:.0f}% year-on-year — well above market average, signalling strong and accelerating demand.")
    elif rev > 10:
        bull.append(f"Solid {rev:.0f}% revenue growth; the business is expanding consistently and gaining market share.")
    elif 0 < rev <= 10:
        bull.append(f"Revenue growing modestly at {rev:.0f}% — stable, if unspectacular, top-line expansion.")
    elif rev < -5:
        bear.append(f"Revenue declining {abs(rev):.0f}% — business is contracting. Watch for whether this is cyclical or structural.")

    # ── Earnings ─────────────────────────────────────────────
    if earn > 30:
        bull.append(f"Earnings accelerating {earn:.0f}% — profitability is expanding faster than revenue, a classic margin-expansion story that markets reward with multiple expansion.")
    elif earn > 10:
        bull.append(f"Earnings up {earn:.0f}%, confirming that revenue growth is converting into real profit.")
    elif earn < -15:
        bear.append(f"Earnings down {abs(earn):.0f}% — profitability is under pressure. Determine whether this is investment spend or structural deterioration.")

    # ── PE compression ───────────────────────────────────────
    if pe and fwd_pe and pe > 0 and fwd_pe > 0 and fwd_pe < pe * 0.85:
        comp = (pe - fwd_pe) / pe * 100
        bull.append(f"Forward P/E ({fwd_pe:.1f}×) is {comp:.0f}% below trailing P/E ({pe:.1f}×) — the market is pricing in substantial earnings growth over the next 12 months, a strong forward-looking signal.")
    elif pe and pe > 60:
        bear.append(f"Trailing P/E of {pe:.1f}× prices in near-perfection. Any earnings miss or guidance cut could trigger a sharp de-rating.")

    # ── RSI / momentum ──────────────────────────────────────
    if rsi < 32:
        bull.append(f"RSI at {rsi:.0f} places the shares in territory usually described as heavily oversold, meaning the recent fall has been steep relative to the stock's own history. This describes what has already happened and does not indicate what the price will do next.")
    elif 50 <= rsi <= 68:
        bull.append(f"RSI at {rsi:.0f} — in the momentum continuation sweet spot (50–70). The trend has room to run before reaching overbought territory.")
    elif rsi > 76:
        bear.append(f"RSI at {rsi:.0f} — overbought. Near-term pullback or consolidation is likely before the next leg higher. Consider waiting for a dip to add.")

    # ── Moving averages ───────────────────────────────────────
    if vs20 > 0 and vs50 > 0:
        bull.append(f"Price is above both the 20-day SMA (+{vs20:.1f}%) and 50-day SMA (+{vs50:.1f}%) with the SMAs aligned upward — the trend structure is clear and bullish at multiple timeframes.")
    elif vs20 > 0 and vs50 < 0:
        bull.append(f"Price has reclaimed its 20-day SMA (+{vs20:.1f}%) but is still below the 50-day. A close above the 50-day would significantly strengthen the bull case.")
    elif vs50 < -12:
        bear.append(f"Trading {abs(vs50):.0f}% below the 50-day SMA — the medium-term trend is broken. A recovery above SMA50 is needed to confirm a reversal.")

    # ── Volume ───────────────────────────────────────────────
    if vol > 1.8:
        bull.append(f"Volume running at {vol:.1f}× the 20-day average — unusual activity at this level typically signals institutional accumulation or a significant catalyst event.")
    elif vol > 1.3:
        bull.append(f"Volume {vol:.1f}× above average — healthy buying pressure supporting the price move.")
    elif vol < 0.65:
        bear.append(f"Volume has dried up to {vol:.1f}× normal — weak participation behind recent price action. A move on thin volume is easier to reverse.")

    # ── 52-week range ─────────────────────────────────────────
    if 87 <= pct_rng <= 97:
        bull.append(f"Sitting at {pct_rng:.0f}% of its 52-week range — coiling just below all-time highs. A confirmed breakout above resistance on volume would be a powerful signal.")
    elif pct_rng > 97:
        bear.append(f"At {pct_rng:.0f}% of its 52-week range — stretched to the upside. Resistance at the prior high may cap gains unless accompanied by a strong fundamental catalyst.")
    elif pct_rng < 25:
        bull.append(f"Only at {pct_rng:.0f}% of its 52-week range — deep in recovery territory with substantial upside potential if business fundamentals stabilise.")

    # ── Analyst consensus ────────────────────────────────────
    if upside > 25:
        bull.append(f"Analyst consensus target implies {upside:.0f}% upside — a wide gap between current price and Wall Street's view, suggesting the market may be undervaluing the stock.")
    elif upside > 10:
        bull.append(f"Analysts see {upside:.0f}% upside to consensus target, providing a fundamental floor for the thesis.")
    elif upside < -5:
        bear.append(f"Analyst price target is below the current price — sell-side consensus does not support the current valuation.")

    # ── Short interest ───────────────────────────────────────
    if short > 18:
        bear.append(f"{short:.0f}% of the float is sold short — heavy short interest creates persistent headwinds and downside pressure on any weakness, but also a potential short-squeeze catalyst on positive news.")
    elif short > 10:
        bear.append(f"Moderate short interest at {short:.0f}% of float. Bears are positioned; any earnings beat or guidance raise could force covering and accelerate any rally.")

    # ── Profitability ─────────────────────────────────────────
    if margin > 20:
        bull.append(f"Net margin of {margin:.0f}% — highly profitable business with strong pricing power and competitive moat. Earnings quality is high.")
    elif margin < 0:
        bear.append(f"Currently loss-making (net margin {margin:.0f}%). The bull case relies on a path to profitability — any delay increases dilution risk.")

    if roe > 25:
        bull.append(f"ROE of {roe:.0f}% — management is generating exceptional returns on shareholder capital, a hallmark of quality compounding businesses.")

    # ── Volatility ────────────────────────────────────────────
    if beta > 2.2:
        bear.append(f"Beta of {beta:.1f} means {ticker} typically moves {beta:.1f}× the broader market — amplifies gains but also drawdowns. Size positions accordingly.")
    if atr > 6:
        bear.append(f"Daily ATR of {atr:.1f}% of price — the shares swing widely within a single day, so short-term price moves are less informative about the business.")

    # ── Overview paragraph ────────────────────────────────────
    strat_label = strategy.split(" ", 1)[1] if " " in strategy else strategy
    overview = (
        f"{ticker} is a {conv_w} {strat_label.lower()} pick scoring {score:.0f}/100. "
    )
    if chg_1m > 8:
        overview += f"The stock has gained {chg_1m:.1f}% over the past month and {chg_3m:.1f}% over three months, confirming strong near-term momentum. "
    elif chg_1m < -8:
        overview += f"The stock has fallen {abs(chg_1m):.1f}% over the past month, a large move relative to its own recent history. "
    elif abs(chg_1m) <= 8:
        overview += f"Price action over the past month has been measured ({chg_1m:+.1f}%), with the setup building quietly. "
    overview += (
        f"Its recent trading range implies typical downside of about {stop_p:.1f}% from the current price, "
        f"which is a measure of how volatile the shares have been."
    )

    # ── Strategy-specific note ────────────────────────────────
    strat_notes = {
        "Momentum": (
            f"**What the price is doing.** The share price is in a sustained uptrend, sitting above both its 20-day "
            f"and 50-day averages. It changed {chg_1w:+.1f}% over the past week on {vol:.1f}× its normal trading volume, "
            f"which means unusually heavy activity. "
            f"**What to monitor:** momentum characteristics historically weaken when a stock falls back below its "
            f"20-day average, when RSI drops under 45, or when volume dries up. Strong momentum describes past price "
            f"movement — it says nothing about the quality of the underlying business."
        ),
        "Oversold": (
            f"**What the price is doing.** The shares have sold off sharply, and RSI of {rsi:.0f} places them in "
            f"territory historically described as oversold — meaning the fall has been rapid relative to the stock's "
            f"own history. "
            f"**What to monitor:** whether the underlying financials are holding up. A falling price with stable "
            f"revenue and margins is a different situation from a falling price that reflects a deteriorating "
            f"business, and this signal alone cannot tell the two apart."
        ),
        "Growth": (
            f"**What the fundamentals show.** Reported growth or analyst estimates have shifted recently. "
            f"Analyst consensus price targets sit {upside:.0f}% above the current price — this reflects other "
            f"analysts' published estimates, not a forecast from this platform, and such targets are frequently wrong. "
            f"**What to monitor:** the next earnings release and any guidance update, which are the events most "
            f"likely to confirm or contradict these expectations."
        ),
        "Consolidating": (
            f"**What the price is doing.** The shares are trading in a narrow range at {pct_rng:.0f}% of their "
            f"52-week high, with lower-than-usual volatility. Narrow ranges historically resolve into larger moves, "
            f"but they resolve in both directions. "
            f"**What to monitor:** whether the range breaks on above-average volume. Ranges that break on thin "
            f"volume frequently reverse."
        ),
    }
    strategy_note = strat_notes.get(strategy, "Monitor price movement relative to key moving averages.")

    # ── Risk characteristics ──────────────────────────────────
    # Describes how the shares have behaved. Deliberately contains no entry,
    # exit, target or position-size guidance -- see the language policy note at
    # the top of this module.
    risk_note = (
        f"Typical downside range: about {stop_p:.1f}% below the current price, based on recent volatility. "
        f"Analyst consensus targets imply {_v('reward_pct'):.1f}% upside, which is other analysts' published "
        f"estimates rather than a forecast from this platform. "
    )
    if beta > 1.5:
        risk_note += (
            f"Beta of {beta:.1f} means the shares have historically moved more than the wider market — "
            f"roughly {beta:.1f}% for every 1% market move, in both directions."
        )
    elif beta < 0.8:
        risk_note += (
            f"Beta of {beta:.1f} means the shares have historically moved less than the wider market."
        )
    else:
        risk_note += f"Beta of {beta:.1f} means the shares have broadly tracked the wider market."

    return {
        "overview":       overview,
        "bull_factors":   bull[:5],
        "bear_factors":   bear[:4],
        "strategy_note":  strategy_note,
        "risk_note":      risk_note,
    }


def metric_card(label, value, col, hot=False):
    """A single figure with its label.

    Deliberately not a card: a bordered tile per metric was the dominant reason
    the interface read as a template. Label above value, hairline below, and the
    grid does the grouping.
    """
    col.markdown(
        f'<div style="padding:0.15rem 0 0.7rem 0;border-bottom:1px solid {theme.RULE};">'
        f'<div style="font-size:0.66rem;letter-spacing:0.08em;text-transform:uppercase;'
        f'color:{theme.FAINT};margin-bottom:0.25rem;">{label}</div>'
        f'<div style="font-size:1.15rem;font-weight:600;color:{theme.INK};line-height:1.1;'
        f'font-variant-numeric:tabular-nums;">{value}</div></div>',
        unsafe_allow_html=True,
    )


# ── Sidebar ──────────────────────────────────────────────────
st.sidebar.markdown("""
<div style="padding:0.2rem 0.55rem 1.1rem 0.55rem;">
    <div style="font-size:0.95rem; font-weight:620; color:#e8eaef; letter-spacing:-0.01em;">
        Investor Square</div>
    <div style="font-size:0.62rem; font-weight:500; letter-spacing:0.16em;
                text-transform:uppercase; color:#7b8394; margin-top:0.2rem;">Equity research</div>
</div>
""", unsafe_allow_html=True)

page = st.sidebar.radio(
    "Navigate",
    ["Overview", "Company", "Discover", "Screener",
     "Compare", "Watchlist", "UK Investor"],
    index=0,
    label_visibility="collapsed",
)

# Discover groups the three discovery views. Choosing between them is a
# secondary control, not a top-level destination.
_discover_tab = "All companies"
if page == "Discover":
    st.sidebar.markdown("---")
    _discover_tab = st.sidebar.radio(
        "View", ["All companies", "Hidden Gems", "Momentum"], index=0,
    )

# ════════════════════════════════════════════════════════════
# PAGE — HOME
# ════════════════════════════════════════════════════════════
if page == "Overview":

    _hour = datetime.now().hour
    _greeting = "Good morning" if _hour < 12 else ("Good afternoon" if _hour < 18 else "Good evening")
    theme.page_header(_greeting, "What stands out in the market today.")

    # ── Market snapshot ──────────────────────────────────────
    theme.section("Market snapshot")

    @st.cache_data(persist="disk", show_spinner=False)
    def _market_overview(bucket: int) -> list:
        indices = [("^GSPC", "S&P 500"), ("^IXIC", "NASDAQ"), ("^FTSE", "FTSE 100")]
        prices = _batch_prices(tuple(t for t, _ in indices), period="1mo", interval="1d")
        out = []
        for sym, label in indices:
            hist = prices.get(sym)
            if hist is None or hist.empty or len(hist["Close"].dropna()) < 2:
                out.append({"label": label, "last": None, "day": None})
                continue
            closes = hist["Close"].dropna()
            out.append({"label": label, "last": float(closes.iloc[-1]),
                        "day": (float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100})
        return out

    try:
        overview = _market_overview(_bucket())
    except Exception:  # noqa: BLE001
        overview = []

    if not overview or all(m["last"] is None for m in overview):
        st.caption("Market data unavailable.")
    else:
        cells = []
        for m in overview:
            if m["last"] is None:
                cells.append(f'<div class="bs-score"><div class="bs-score-label">{m["label"]}</div>'
                             f'<div class="bs-score-value na">Unavailable</div></div>')
                continue
            cls = "bs-pos" if m["day"] >= 0 else "bs-neg"
            cells.append(
                f'<div class="bs-score" style="min-width:130px;">'
                f'<div class="bs-score-label">{m["label"]}</div>'
                f'<div class="bs-score-value">{m["last"]:,.0f}</div>'
                f'<div class="bs-row-r {cls}">{m["day"]:+.2f}%</div></div>'
            )
        st.markdown(f'<div class="bs-scores">{"".join(cells)}</div>', unsafe_allow_html=True)
        st.caption("Index levels may be delayed. Shown for context.")

    # ── Discover ─────────────────────────────────────────────
    theme.section("Discover")

    @st.cache_data(persist="disk", show_spinner=False)
    def _overview_picks(bucket: int) -> dict:
        """A short list per theme, scored with the standard engine."""
        tickers = get_universe("all_curated")[:110]
        prices = _batch_prices(tuple(tickers), period="3mo", interval="1d")
        valid = tuple(t for t in tickers if t in prices and not prices[t].empty) or tuple(tickers)
        infos = _batch_info(valid)
        scored = []
        for t in valid:
            info = infos.get(t) or {}
            if not info.get("shortName"):
                continue
            sc = score_company(t, _fundamentals_from_info(info, prices.get(t)), info.get("sector"))
            if sc.overall is None:
                continue
            scored.append((sc, info.get("shortName", t)[:30]))

        def top(key, reason, n=4):
            ranked = sorted(
                (x for x in scored if x[0].categories[key].available),
                key=lambda x: x[0].categories[key].score, reverse=True)[:n]
            return [{"ticker": sc.ticker, "name": nm, "score": sc.overall,
                     "detail": f"{sc.categories[key].label} {sc.categories[key].score:.0f}",
                     "reason": reason} for sc, nm in ranked]

        return {
            "High quality": top("quality", "Strong profitability and margins"),
            "Strong momentum": top("momentum", "Share price has risen recently"),
            "Modestly valued": top("valuation", "Trading on lower multiples"),
        }

    # Loads on arrival rather than behind a button: an overview that shows
    # nothing until clicked fails at being an overview. The result is cached to
    # disk per six-hour bucket, so only the first visit in a window pays for it.
    with st.spinner(""):
        try:
            picks = _overview_picks(_bucket())
        except Exception:  # noqa: BLE001
            picks = {}
    if not picks:
        st.caption("Unable to load lists right now.")
    else:
        cols = st.columns(3, gap="large")
        for col, (heading, rows) in zip(cols, picks.items()):
            with col:
                st.markdown(f'<div class="bs-eyebrow">{heading}</div>', unsafe_allow_html=True)
                if not rows:
                    st.caption("Nothing met the threshold.")
                    continue
                st.markdown("".join(
                    f'<div class="bs-row">'
                    f'<div class="bs-row-t">{r["ticker"]}</div>'
                    f'<div><div class="bs-row-n">{r["name"]}</div>'
                    f'<div class="bs-row-r">{r["detail"]}</div></div>'
                    f'<div class="bs-row-s">{r["score"]:.0f}</div></div>'
                    for r in rows
                ), unsafe_allow_html=True)
                st.caption(rows[0]["reason"])
        st.caption(
            "Highest scoring on our methodology within a curated universe. "
            "Not recommendations."
        )

    # ── Watchlist beside recently viewed ─────────────────────
    wl_col, recent_col = st.columns([1.6, 1], gap="large")
    with wl_col:
        theme.section("Watchlist")
    try:
        wl = _watchlist()
        entries = wl.all()
    except Exception:  # noqa: BLE001
        entries = []

    with wl_col:
        if not entries:
            st.caption("Nothing saved. Add companies from the Company page.")
        else:
            rows_html = []
            for entry in entries[:10]:
                snap = None
                try:
                    snap = _score_history().latest(entry.ticker)
                except Exception:  # noqa: BLE001
                    pass
                score_txt = "—" if snap is None or snap.overall is None else f"{snap.overall:.0f}"
                rows_html.append(
                    f'<div class="bs-row"><div class="bs-row-t">{entry.ticker}</div>'
                    f'<div class="bs-row-n">{(entry.name or "")[:40]}</div>'
                    f'<div class="bs-row-s">{score_txt}</div></div>'
                )
            st.markdown("".join(rows_html), unsafe_allow_html=True)
            if len(entries) > 10:
                st.caption(f"And {len(entries) - 10} more.")

    with recent_col:
        theme.section("Recently viewed")
        try:
            recent = _watchlist().recent(limit=10)
        except Exception:  # noqa: BLE001
            recent = []
        if recent:
            st.markdown(" ".join(f'<span class="bs-tag">{e.ticker}</span>' for e in recent),
                        unsafe_allow_html=True)
        else:
            st.caption("Companies you look at will appear here.")

    # ── One concept ──────────────────────────────────────────
    theme.section("Briefly")
    LESSONS = [
        ("What a P/E ratio tells you",
         "The P/E ratio is the share price divided by profit per share over the last year. "
         "A P/E of 20 means paying £20 for every £1 of annual profit. A high figure can "
         "reflect growth expectations, or simply optimism already priced in. It is most "
         "useful compared with companies in the same industry."),
        ("Price movement and business quality are different things",
         "A company can rise sharply while its revenue shrinks, and a strong business can "
         "go nowhere for years. That is why the two are scored separately here and never "
         "combined into one figure."),
        ("What a Stocks and Shares ISA does",
         "An ISA is a wrapper around investments, not an investment. Gains and dividends "
         "inside one are free of UK capital gains and dividend tax. It does not protect "
         "against losses."),
        ("Why missing data matters",
         "If a company has not reported a figure, no score can honestly be calculated from "
         "it. Treating a missing number as zero can make a company with no reported debt "
         "look debt-free. Here it is shown as unavailable and lowers confidence."),
        ("What diversification does",
         "Holding several unrelated investments limits the damage any one can do. It does "
         "not protect against a fall in the whole market, and several companies in one "
         "industry are less diversified than they look."),
    ]
    _title, _body = LESSONS[date.today().toordinal() % len(LESSONS)]
    st.markdown(
        f'<div style="font-size:0.875rem;font-weight:600;color:{theme.INK};">{_title}</div>'
        f'<div style="font-size:0.85rem;color:{theme.MUTED};line-height:1.62;margin-top:0.3rem;'
        f'max-width:72ch;">{_body}</div>',
        unsafe_allow_html=True,
    )

    theme.hairline()
    st.caption(
        f"Methodology v{SCORING_VERSION}. {PROVIDER_NAME}; prices may be delayed."
        + ("" if PROVIDER_IS_LICENSED else " Not licensed for commercial redistribution.")
    )
    st.caption(
        "Research and education only. Not financial advice, and not a recommendation to "
        "buy or sell."
    )



elif page == "Company":

    theme.page_header(
        "Company research",
        "Search a company to see what its reported figures show.",
    )

    query = _company_picker()

    if not query:
        st.caption("Choose a company, or type a ticker to look one up.")
        st.stop()

    st.session_state["_co_last"] = query
    with st.spinner(""):
        res = _research(query, _bucket())

    if "error" in res:
        st.error(res["error"])
        st.stop()

    try:
        _score_history().record(res["score"])
    except Exception:  # noqa: BLE001 - history is secondary to the research
        pass

    score, expl = res["score"], res["explanation"]
    cur = {"USD": "$", "GBP": "£", "GBp": "p", "EUR": "€"}.get(res["currency"], "")

    # ── Identity line ────────────────────────────────────────
    hist = res.get("history")
    day_change = None
    if hist is not None and not hist.empty and len(hist["Close"].dropna()) > 1:
        closes = hist["Close"].dropna()
        day_change = (float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100

    meta = " · ".join(x for x in (query, res["sector"], res["industry"]) if x)
    price_html = ""
    if res["price"]:
        chg = ""
        if day_change is not None:
            cls = "bs-pos" if day_change >= 0 else "bs-neg"
            chg = f' <span class="{cls}" style="font-size:0.9rem;">{day_change:+.2f}%</span>'
        price_html = (f'<div style="text-align:right;"><span style="font-size:1.35rem;'
                      f'font-weight:600;color:{theme.INK};font-variant-numeric:tabular-nums;">'
                      f'{cur}{res["price"]:,.2f}</span>{chg}</div>')

    left, right = st.columns([3, 1])
    with left:
        st.markdown(
            f'<div style="font-size:1.4rem;font-weight:620;color:{theme.INK};'
            f'letter-spacing:-0.018em;line-height:1.2;">{res["name"]}</div>'
            f'<div style="font-size:0.78rem;color:{theme.FAINT};margin:0.15rem 0 0.9rem 0;">{meta}</div>',
            unsafe_allow_html=True,
        )
    with right:
        if price_html:
            st.markdown(price_html, unsafe_allow_html=True)

    # Watchlist control, deliberately quiet
    try:
        _wl = _watchlist()
        _wl.record_view(query, res["name"])
        in_list = _wl.contains(query)
        if st.button("Remove from watchlist" if in_list else "Add to watchlist",
                     key=f"_wl_{query}"):
            _wl.remove(query) if in_list else _wl.add(query, res["name"])
            st.rerun()
    except Exception:  # noqa: BLE001
        pass

    theme.hairline()

    # ── Scores, one row ──────────────────────────────────────
    conf_note = f"{score.confidence.title()} confidence · {score.coverage:.0%} of figures available"
    if score.available:
        st.markdown(
            f'<div class="bs-eyebrow" style="margin-bottom:0.35rem;">Research score</div>'
            f'<div style="display:flex;align-items:baseline;gap:0.7rem;margin-bottom:1.4rem;">'
            f'<span style="font-size:2rem;font-weight:620;color:{theme.INK};line-height:1;'
            f'font-variant-numeric:tabular-nums;">{score.overall:.0f}</span>'
            f'<span style="font-size:0.85rem;color:{theme.FAINT};">out of 100</span>'
            f'<span style="font-size:0.78rem;color:{theme.MUTED};margin-left:auto;">{conf_note}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="font-size:0.9rem;color:{theme.MUTED};margin-bottom:1rem;">'
            f'No overall score — too few figures were available to score this company fairly.</div>',
            unsafe_allow_html=True,
        )

    theme.score_row([
        (c.label, c.score if c.available else None) for c in score.categories.values()
    ])

    # ── Summary alongside the numbers it describes ───────────
    sum_col, snap_col = st.columns([1.25, 1], gap="large")
    with sum_col:
        theme.section("Summary")
        st.markdown(f'<div style="font-size:0.9rem;color:{theme.MUTED};line-height:1.62;">'
                    f'{expl["summary"]}</div>', unsafe_allow_html=True)
    with snap_col:
        theme.section("Financial snapshot")
        _snapshot = []
        for _cat in score.categories.values():
            for _m in _cat.metrics:
                _h = GLOSSARY.get(_m.field)
                if not _h:
                    continue
                _snapshot.append((_h["label"],
                                  format_value(_m.field, _m.raw) if _m.available else None))
        theme.stat_grid(_snapshot[:8])

    # ── Strengths / risks ────────────────────────────────────
    def _trim(items):
        out = []
        for item in items:
            head = item.split("—")[0].strip().strip("*").strip()
            tail = item.split("—", 1)[1].strip() if "—" in item else ""
            first = tail.split(".")[0].strip()
            out.append(f"<b>{head}</b>{' — ' + first + '.' if first else ''}")
        return out

    st.markdown("<div style='height:1.6rem;'></div>", unsafe_allow_html=True)
    theme.two_column_list("Key strengths", _trim(expl["strengths"][:4]),
                          "Risks to monitor", _trim(expl["concerns"][:4]))

    if len(_snapshot) > 8:
        theme.section("Further measures")
        theme.stat_grid(_snapshot[8:])

    # ── Deeper detail, behind a click ────────────────────────
    st.markdown("<div style='height:1.4rem;'></div>", unsafe_allow_html=True)
    with st.expander("What these measures mean"):
        for cat in score.categories.values():
            for metric in cat.metrics:
                help_ = GLOSSARY.get(metric.field)
                if not help_:
                    continue
                direction = ("Higher is generally better." if help_["better"] == "higher"
                             else "Lower is generally better.")
                value = format_value(metric.field, metric.raw) if metric.available else "Not reported"
                st.markdown(
                    f'<div style="padding:0.55rem 0;border-bottom:1px solid {theme.RULE};">'
                    f'<span style="font-size:0.83rem;font-weight:550;color:{theme.INK};">'
                    f'{help_["label"]}</span>'
                    f'<span style="font-size:0.83rem;color:{theme.MUTED};float:right;">{value}</span>'
                    f'<div style="font-size:0.79rem;color:{theme.MUTED};margin-top:0.2rem;'
                    f'max-width:70ch;">{help_["means"]} {help_["matters"]} {direction}</div></div>',
                    unsafe_allow_html=True,
                )

    if expl["could_change"]:
        with st.expander("What could change the score"):
            for item in expl["could_change"]:
                st.markdown(f'<div style="font-size:0.845rem;color:{theme.MUTED};'
                            f'margin-bottom:0.4rem;">{item}</div>', unsafe_allow_html=True)

    if expl["unavailable"]:
        with st.expander(f"{len(expl['unavailable'])} measures unavailable"):
            for line in expl["unavailable"]:
                st.markdown(f'<div style="font-size:0.82rem;color:{theme.MUTED};">{line}</div>',
                            unsafe_allow_html=True)

    # ── Price ────────────────────────────────────────────────
    if hist is not None and not hist.empty:
        theme.section("Share price, 12 months")
        fig_co = go.Figure(go.Scatter(
            x=hist.index, y=hist["Close"], mode="lines",
            line=dict(color=theme.INK, width=1.4), hovertemplate="%{y:.2f}<extra></extra>",
        ))
        fig_co.update_layout(**theme.chart_layout_quiet(height=200))
        st.plotly_chart(fig_co, width="stretch", config={"displayModeBar": False})

    # ── Score history ────────────────────────────────────────
    theme.section("Score history")
    try:
        store = _score_history()
        snapshots = store.history(query)
    except Exception:  # noqa: BLE001
        snapshots = []

    if len(snapshots) < 2:
        st.caption(
            "Tracking began today. Earlier scores are not shown because they cannot be "
            "recalculated — the data source provides only current figures."
        )
    else:
        cells = [("Today", snapshots[-1].overall, None)]
        for days, label in ((30, "30 days"), (90, "90 days"), (365, "1 year")):
            snap = store.nearest(query, days, tolerance_days=max(7, days // 5))
            cells.append((label, snap.overall if snap else None,
                          f"{snap.age_days}d ago" if snap else None))
        st.markdown(
            '<div class="bs-scores">' + "".join(
                f'<div class="bs-score"><div class="bs-score-label">{label}</div>'
                + (f'<div class="bs-score-value">{value:.0f}</div>' if value is not None
                   else '<div class="bs-score-value na">Not tracked</div>')
                + (f'<div class="bs-row-r">{note}</div>' if note else "")
                + '</div>'
                for label, value, note in cells
            ) + '</div>', unsafe_allow_html=True)

        earlier = store.nearest(query, 30, tolerance_days=400) or snapshots[0]
        change = describe_change(snapshots[-1], earlier)
        st.markdown(f'<div style="font-size:0.845rem;color:{theme.MUTED};margin-top:0.9rem;'
                    f'max-width:70ch;">{change["summary"]}</div>', unsafe_allow_html=True)

    theme.hairline()
    st.caption(
        f"Methodology v{SCORING_VERSION}. {PROVIDER_NAME}, retrieved "
        f"{res['fetched_at']:%d %b %Y}. Prices may be delayed."
        + ("" if PROVIDER_IS_LICENSED else
           " Not licensed for commercial redistribution.")
    )
    st.caption(
        "Research and education only. Not financial advice, and not a recommendation to "
        "buy or sell. Scores describe reported figures and past prices, not future returns."
    )



elif page == "Compare":

    st.title("Compare companies")
    st.caption(
        f"Put 2 to {MAX_COMPANIES} companies side by side. This shows how they differ on "
        f"the measures we score — it does not say which one to pick, because that depends "
        f"on what you are looking for."
    )

    _directory = _company_directory(_bucket())
    _options = sorted(_directory) or ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"]
    picked = st.multiselect(
        "Companies to compare", _options,
        default=[t for t in ("AAPL", "MSFT") if t in _options][:2],
        format_func=lambda t: f"{t} — {_directory[t]}" if t in _directory else t,
        max_selections=MAX_COMPANIES,
        label_visibility="collapsed",
        help="Type to filter by company name or ticker.",
    )

    if len(picked) < 2:
        st.info("Enter at least two tickers to compare.")
        st.stop()
    if len(picked) > MAX_COMPANIES:
        st.warning(f"Comparing the first {MAX_COMPANIES}: {', '.join(picked[:MAX_COMPANIES])}.")
        picked = picked[:MAX_COMPANIES]

    with st.spinner(f"Looking up {', '.join(picked)}…"):
        results, failed = [], []
        for tk in picked:
            res = _research(tk, _bucket())
            if "error" in res:
                failed.append((tk, res["error"]))
            else:
                results.append((tk, res))

    for tk, err in failed:
        st.error(f"{tk}: {err}")

    if len(results) < 2:
        st.warning("At least two companies with usable data are needed to compare.")
        st.stop()

    scores = [r["score"] for _, r in results]
    names  = {tk: r["name"] for tk, r in results}
    comp   = compare(scores, names)

    # ── Overall row ──────────────────────────────────────────
    st.markdown("---")
    cols = st.columns(len(comp.tickers))
    for col, tk in zip(cols, comp.tickers):
        overall = comp.overall[tk]
        conf = comp.confidence[tk]
        conf_colour = {"high": "#2f6b4f", "moderate": "#8d7434", "low": "#9b3b3b"}[conf]
        col.markdown(
            f'<div style="background:#ffffff;border:1px solid #e7e4dd;border-top:3px solid #8d7434;'
            f'border-radius:3px;padding:0.9rem 1rem;">'
            f'<div style="font-size:0.85rem;font-weight:700;color:#12161f;">{names[tk]}</div>'
            f'<div style="font-size:0.66rem;color:#9aa1ad;margin-bottom:0.4rem;">{tk}</div>'
            + (f'<div style="font-size:2rem;font-weight:800;color:#12161f;line-height:1;">'
               f'{overall:.0f}<span style="font-size:0.8rem;font-weight:400;color:#9aa1ad;">'
               f' / 100</span></div>' if overall is not None else
               '<div style="font-size:0.95rem;color:#9aa1ad;">Not scored</div>')
            + f'<div style="font-size:0.7rem;color:{conf_colour};font-weight:600;">'
              f'{conf.title()} confidence</div></div>',
            unsafe_allow_html=True,
        )

    # ── In plain English ─────────────────────────────────────
    st.markdown("---")
    st.markdown("##### In plain English")
    st.markdown(comp.summary)

    # ── Category table ───────────────────────────────────────
    st.markdown("##### Score by category")
    table = []
    for row in comp.rows:
        entry = {"Measure": row.label}
        for tk in comp.tickers:
            value = row.scores[tk]
            entry[tk] = "—" if value is None else f"{value:.0f}"
        entry["Notes"] = (row.note if row.note else
                          (f"{row.leader} scores highest" if row.leader else ""))
        table.append(entry)
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    st.caption(
        f"A gap smaller than {MIN_MEANINGFUL_GAP:.0f} points is treated as too close to "
        f"separate — the underlying figures carry more noise than that."
    )

    # ── Bars ─────────────────────────────────────────────────
    chart_rows = [
        {"Measure": row.label, "Company": names[tk], "Score": row.scores[tk]}
        for row in comp.rows for tk in comp.tickers if row.scores[tk] is not None
    ]
    if chart_rows:
        cdf = pd.DataFrame(chart_rows)
        fig_cmp = go.Figure()
        for i, tk in enumerate(comp.tickers):
            sub = cdf[cdf["Company"] == names[tk]]
            fig_cmp.add_trace(go.Bar(name=names[tk], x=sub["Measure"], y=sub["Score"],
                                     marker_color=PALETTE[i % len(PALETTE)]))
        fig_cmp.update_layout(**chart_layout(height=320, barmode="group",
                                             yaxis_range=[0, 100]))
        st.plotly_chart(fig_cmp, width="stretch")

    # ── Caveats ──────────────────────────────────────────────
    if comp.caveats:
        st.markdown("##### Worth knowing")
        for caveat in comp.caveats:
            st.markdown(f"<div style='font-size:0.85rem;color:#5f6672;margin-bottom:0.3rem;'>"
                        f"• {caveat}</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.caption(
        "This is research and education, not financial advice, and nothing here is a "
        "recommendation to buy or sell any investment. Scores describe reported figures "
        "and past price behaviour; they are not predictions."
    )


# ════════════════════════════════════════════════════════════
# PAGE 1 — MOMENTUM
# ════════════════════════════════════════════════════════════
elif page == "Discover" and _discover_tab == "Momentum":

    st.title("Momentum")
    st.caption("Ranked by a composite momentum score: price change, volume surge, RSI momentum, analyst sentiment, and short-term trend strength.")

    # Which universe to scan
    st.sidebar.markdown("### Momentum settings")
    hot_universe = st.sidebar.selectbox(
        "Scan universe",
        ["tech", "ai", "all_curated", "nasdaq100"],
        format_func=lambda x: {
            "tech": "Tech & Mega Caps",
            "ai": "AI & ML",
            "all_curated": "All Curated (~130)",
            "nasdaq100": "NASDAQ 100",
        }[x],
        index=0,
    )
    hot_top_n = st.sidebar.slider("Show top N", 5, 20, 10)
    refresh_hot = st.sidebar.button("Refresh momentum scan", type="primary")

    # Cache key: refresh when button pressed or universe changes
    cache_key = f"hot_{hot_universe}"
    if refresh_hot or cache_key not in st.session_state:
        st.session_state[cache_key] = None

    if _scan_gate(cache_key, "Run momentum scan", forced=refresh_hot,
                  note="Scans your chosen universe for momentum. Pulls live data for "
                       "up to a few hundred tickers, so it takes a moment."):
        tickers_to_scan = get_universe(hot_universe)
        tickers_tuple   = tuple(tickers_to_scan)

        with st.spinner(f"Analysing {len(tickers_to_scan)} tickers for momentum..."):
            prog = st.progress(0, text="Downloading price data (batch)...")
            all_prices = _batch_prices(tickers_tuple, period="1mo", interval="1d")
            valid_tup  = tuple(t for t in tickers_to_scan if t in all_prices and not all_prices[t].empty) or tuple(tickers_to_scan)
            prog.progress(0.5, text=f"Fetching analyst data for {len(valid_tup)} active stocks...")
            all_info   = _batch_info(valid_tup)
            prog.progress(0.9, text="Computing scores...")

            rows = []
            for ticker in tickers_to_scan:
                try:
                    hist = all_prices.get(ticker)
                    if hist is None or len(hist) < 6:
                        continue

                    closes = hist["Close"]
                    volumes = hist["Volume"]

                    price_now   = float(closes.iloc[-1])
                    price_1w    = float(closes.iloc[-6])  if len(closes) >= 6  else None
                    price_1m    = float(closes.iloc[0])
                    vol_now     = float(volumes.iloc[-5:].mean())
                    vol_1m_avg  = float(volumes.mean())

                    week_chg    = ((price_now - price_1w) / price_1w * 100) if price_1w else 0
                    month_chg   = (price_now - price_1m) / price_1m * 100
                    vol_surge   = vol_now / vol_1m_avg if vol_1m_avg > 0 else 1.0

                    delta  = closes.diff().dropna()
                    gain   = delta.clip(lower=0).rolling(14).mean()
                    loss   = (-delta.clip(upper=0)).rolling(14).mean()
                    rs     = gain / loss.replace(0, float("nan"))
                    rsi    = float((100 - 100 / (1 + rs)).iloc[-1]) if not rs.empty else 50.0
                    rsi    = rsi if not pd.isna(rsi) else 50.0

                    sma20  = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else price_now
                    vs_sma = (price_now - sma20) / sma20 * 100

                    info = all_info.get(ticker, {})

                    # Price momentum and business quality are computed
                    # separately and never averaged. See services/momentum.py.
                    research = score_company(
                        ticker, _fundamentals_from_info(info, hist), info.get("sector"),
                    )
                    mom = assess_momentum(
                        ticker, info.get("shortName", ticker)[:32],
                        {"chg_1w": week_chg, "chg_1m": month_chg,
                         "vs_sma20": vs_sma, "vol_surge": vol_surge},
                        research,
                    )

                    rows.append({
                        "ticker":        ticker,
                        "name":          info.get("shortName", ticker)[:32],
                        "price":         round(price_now, 2),
                        "week_chg":      round(week_chg, 2),
                        "month_chg":     round(month_chg, 2),
                        "vol_surge":     round(vol_surge, 2),
                        "rsi":           round(rsi, 1),
                        "vs_sma20":      round(vs_sma, 2),
                        "momentum":      mom.momentum,
                        "fundamentals":  mom.fundamentals,
                        "fund_conf":     mom.fundamentals_confidence,
                        "profile":       mom.profile_label,
                        "profile_key":   mom.profile_key,
                        "profile_note":  mom.profile_note,
                    })
                except Exception:
                    continue

            prog.empty()
            if not rows:
                st.warning("No data returned. Check your tickers and try again.")
                st.stop()
            hot_df = (pd.DataFrame(rows).dropna(subset=["momentum"])
                        .sort_values("momentum", ascending=False).reset_index(drop=True))
            st.session_state[cache_key] = hot_df

    hot_df = st.session_state.get(cache_key)

    if hot_df is None:
        st.stop()  # gate is showing its own prompt — nothing to render yet
    if hot_df.empty:
        st.error(
            "The scan finished but no stocks came back. This is usually a temporary "
            "data-provider hiccup — press Refresh momentum scan to try again, or "
            "pick a different universe in the sidebar."
        )
        st.stop()

    top_hot = hot_df.head(hot_top_n)

    # ── Summary cards ────────────────────────────────────────
    st.markdown("### Largest recent price moves")
    st.caption(
        "Two separate scores. **Momentum** describes what the share price has done. "
        "**Business** is the research score for the company itself. A stock can move "
        "sharply while the business behind it scores poorly — so they are never combined."
    )
    cols = st.columns(5)
    for i, row in top_hot.head(5).iterrows():
        arrow = "▲" if row["week_chg"] >= 0 else "▼"
        col_cls = "up" if row["week_chg"] >= 0 else "down"
        fund = row["fundamentals"]
        fund_txt = "—" if pd.isna(fund) else f"{fund:.0f}"
        fund_col = ("#9aa1ad" if pd.isna(fund)
                    else "#2f6b4f" if fund >= 60 else "#9b3b3b" if fund < 45 else "#8d7434")
        cols[i % 5].markdown(
            f'<div class="bs-panel">'
            f'<h3>{row["ticker"]}</h3>'
            f'<p><span class="{col_cls}">{arrow} {row["week_chg"]:+.1f}%</span></p>'
            f'<div style="display:flex;gap:0.9rem;margin:0.3rem 0 0.35rem;">'
            f'  <div><div style="font-size:0.58rem;letter-spacing:0.08em;color:#9aa1ad;">MOMENTUM</div>'
            f'  <b style="font-size:1rem;color:#8d7434;">{row["momentum"]:.0f}</b></div>'
            f'  <div><div style="font-size:0.58rem;letter-spacing:0.08em;color:#9aa1ad;">BUSINESS</div>'
            f'  <b style="font-size:1rem;color:{fund_col};">{fund_txt}</b></div>'
            f'</div>'
            f'<small style="color:#5f6672;font-size:0.66rem;">{row["profile"]}</small>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Flag the case the old single score hid entirely.
    momentum_only = top_hot[top_hot["profile_key"] == "momentum_only"]
    if not momentum_only.empty:
        st.warning(
            f"**{len(momentum_only)} of these have risen on price alone.** "
            f"{', '.join(momentum_only['ticker'].tolist())} score poorly on the business "
            f"measures despite the price move. Price movement on its own says nothing "
            f"about the quality of a company."
        )
    unknown = top_hot[top_hot["profile_key"] == "momentum_unknown"]
    if not unknown.empty:
        st.info(
            f"**{len(unknown)} could not be assessed as businesses** "
            f"({', '.join(unknown['ticker'].tolist())}) — too little financial data was "
            f"available. Their momentum figure describes the price only."
        )

    st.markdown("---")

    # ── Full ranked table ────────────────────────────────────
    st.markdown(f"### Full top {hot_top_n} ranking")

    display_hot = top_hot.copy()
    display_hot.insert(0, "Rank", range(1, len(display_hot) + 1))
    display_hot = display_hot.rename(columns={
        "ticker": "Ticker", "price": "Price ($)", "week_chg": "Week %",
        "month_chg": "Month %", "vol_surge": "Vol Surge",
        "rsi": "RSI (14)", "vs_sma20": "vs SMA20 %",
        "momentum": "Momentum", "fundamentals": "Business", "profile": "Profile",
    })

    st.dataframe(display_hot, hide_index=True, width="stretch")

    # ── Price chart for selected stock ───────────────────────
    st.markdown("---")
    st.markdown("### Price chart")
    chart_ticker = st.selectbox("Select stock", top_hot["ticker"].tolist(), index=0)

    try:
        hist = yf.Ticker(chart_ticker).history(period="3mo", interval="1d", auto_adjust=True)
        if not hist.empty:
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=hist.index,
                open=hist["Open"], high=hist["High"],
                low=hist["Low"],   close=hist["Close"],
                name=chart_ticker,
                increasing_line_color="#2f6b4f",
                decreasing_line_color="#9b3b3b",
            ))
            sma20 = hist["Close"].rolling(20).mean()
            fig.add_trace(go.Scatter(x=hist.index, y=sma20, name="SMA 20",
                                     line=dict(color=GOLD, width=1.5, dash="dot")))
            fig.update_layout(**chart_layout(
                height=420, xaxis_rangeslider_visible=False,
                title=dict(text=f"{chart_ticker} — 3 months", font=dict(size=12, color=CHART_TEXT)),
            ))
            st.plotly_chart(fig, width="stretch")
    except Exception as e:
        st.warning(f"Could not load chart for {chart_ticker}: {e}")

    # ── Volume chart ─────────────────────────────────────────
    try:
        hist = yf.Ticker(chart_ticker).history(period="1mo", interval="1d", auto_adjust=True)
        if not hist.empty:
            avg_vol = hist["Volume"].mean()
            colors = [GOLD if v > avg_vol else "#e7e4dd" for v in hist["Volume"]]
            fig2 = go.Figure(go.Bar(
                x=hist.index, y=hist["Volume"],
                marker_color=colors, name="Volume",
            ))
            fig2.add_hline(y=avg_vol, line_dash="dot", line_color=GOLD,
                           annotation_text="30d avg", annotation_position="right")
            fig2.update_layout(**chart_layout(
                height=200, showlegend=False,
                title=dict(text=f"{chart_ticker} — Volume (gold = above 30d avg)", font=dict(size=11, color=CHART_TEXT)),
            ))
            st.plotly_chart(fig2, width="stretch")
    except Exception:
        pass

    # ── Score breakdown ───────────────────────────────────────
    st.markdown("---")
    st.markdown("### What is driving the momentum score")
    st.caption("Price and volume only. Analyst opinion and company fundamentals are deliberately excluded from this score.")
    score_components = ["week_chg", "month_chg", "vol_surge", "vs_sma20"]
    comp_labels      = ["Week change %", "Month change %", "Volume surge", "vs SMA20 %"]

    fig3 = go.Figure()
    colors3 = PALETTE
    for i, (col, label) in enumerate(zip(score_components, comp_labels)):
        if col in top_hot.columns:
            fig3.add_trace(go.Bar(
                name=label, x=top_hot["ticker"], y=top_hot[col],
                marker_color=colors3[i],
            ))
    fig3.update_layout(**chart_layout(barmode="group", height=360))
    st.plotly_chart(fig3, width="stretch")


# ════════════════════════════════════════════════════════════
# PAGE 2 — HIDDEN GEMS
# ════════════════════════════════════════════════════════════
elif page == "Discover" and _discover_tab == "Hidden Gems":

    st.title("Hidden Gems")
    st.caption(
        "Sound businesses, reasonably priced, that relatively few analysts follow. "
        "Every company here has passed all five tests below — being overlooked on its own "
        "never qualifies a company."
    )

    with st.expander("How a company qualifies — the full methodology"):
        st.markdown(
            f"A company must pass **every** one of these tests. They are checked "
            f"independently; a strong result in one never compensates for failing another.\n"
        )
        for i, gate in enumerate(GEM_GATES, 1):
            st.markdown(f"**{i}. {gate.label}** — {gate.why}")
        st.markdown(
            f"\nQualifying companies are then ranked by **research score**, not by how "
            f"obscure they are. Being less well known is a qualifying condition, never a merit.\n\n"
            f"**What this cannot tell you:** passing these tests does not mean a company is "
            f"undervalued. It means its reported figures look sound and its valuation multiples "
            f"are not stretched. A low valuation can equally reflect a risk the figures do not "
            f"yet show.\n\n"
            f"*Methodology v{GEM_METHODOLOGY_VERSION}, scoring v{SCORING_VERSION}.*"
        )

    st.sidebar.markdown("### Hidden Gems settings")
    gem_universe = st.sidebar.selectbox(
        "Scan universe",
        ["broad", "all_curated", "tech", "ai", "growth", "nasdaq100"],
        format_func=lambda x: {
            "broad": "Broad Market (~300)",
            "tech": "Tech & Mega Caps",
            "ai": "AI & ML",
            "growth": "High Growth (~50)",
            "all_curated": "All Curated (~130)",
            "nasdaq100": "NASDAQ 100",
        }[x],
        index=0,
    )
    gem_top_n   = st.sidebar.slider("Show top N gems", 5, 25, 10)
    refresh_gem = st.sidebar.button("Find Hidden Gems", type="primary")

    st.sidebar.markdown("### Coverage threshold")
    st.sidebar.caption(
        "How thin analyst coverage must be for a company to count as overlooked."
    )
    gem_max_analysts = st.sidebar.slider("Max analysts covering", 3, 30, MAX_ANALYSTS)

    gem_cache_key = f"gems_{gem_universe}_{gem_max_analysts}"
    if refresh_gem or gem_cache_key not in st.session_state:
        st.session_state[gem_cache_key] = None

    if _scan_gate(gem_cache_key, "Find Hidden Gems", forced=refresh_gem,
                  note="Screens for sound businesses that few analysts follow. "
                       "Pulls live data for several hundred tickers."):
        scan_tickers  = get_universe(gem_universe)
        tickers_tuple = tuple(scan_tickers)

        with st.spinner(f"Assessing {len(scan_tickers)} companies…"):
            prog = st.progress(0, text="Downloading price data (batch)...")
            gem_prices = _batch_prices(tickers_tuple, period="1mo", interval="1d")
            valid_tup  = tuple(t for t in scan_tickers if t in gem_prices and not gem_prices[t].empty) or tuple(scan_tickers)
            prog.progress(0.5, text=f"Fetching fundamentals for {len(valid_tup)} companies...")
            all_info = _batch_info(valid_tup)
            prog.progress(0.9, text="Applying methodology...")

            assessed = []
            for ticker in valid_tup:
                info = all_info.get(ticker) or {}
                if not info.get("shortName"):
                    continue
                hist = gem_prices.get(ticker)
                fundamentals = _fundamentals_from_info(info, hist)
                result = assess_gem(
                    ticker, info.get("shortName", ticker)[:32],
                    fundamentals, info.get("sector"),
                    max_analysts=gem_max_analysts,
                )
                assessed.append(result)
            prog.empty()

        st.session_state[gem_cache_key] = assessed

    assessed = st.session_state.get(gem_cache_key)
    if assessed is None:
        st.stop()
    if not assessed:
        st.warning(
            "No data came back for this universe. This is usually a temporary "
            "data-provider issue — press Find Hidden Gems to try again."
        )
        st.stop()

    gems = sorted([r for r in assessed if r.qualifies],
                  key=lambda r: r.rank_score, reverse=True)

    # ── Funnel: show how many companies each gate removed ────
    st.markdown("---")
    f1, f2, f3, f4 = st.columns(4)
    metric_card("Companies assessed", str(len(assessed)),                       f1)
    metric_card("Passed every test",  str(len(gems)),                           f2)
    scored = [r for r in assessed if r.score.overall is not None]
    metric_card("Could be scored",    str(len(scored)),                         f3)
    metric_card("Best research score",
                f"{gems[0].rank_score:.0f}" if gems else "—",                   f4)

    with st.expander("Where companies dropped out"):
        counts = {g.label: 0 for g in GEM_GATES}
        unknown_counts = {g.label: 0 for g in GEM_GATES}
        for r in assessed:
            for label, _ in r.failed:
                counts[label] = counts.get(label, 0) + 1
            for label, _ in r.unknown:
                unknown_counts[label] = unknown_counts.get(label, 0) + 1
        st.dataframe(
            pd.DataFrame([
                {"Test": g.label,
                 "Failed it": counts.get(g.label, 0),
                 "Could not be checked": unknown_counts.get(g.label, 0)}
                for g in GEM_GATES
            ]),
            hide_index=True, width="stretch",
        )
        st.caption(
            "A company that could not be checked on a test does not pass it. Missing data "
            "counts as unknown, never as a pass."
        )

    if not gems:
        st.info(
            "No company in this universe passed every test. That is a legitimate result — "
            "the methodology is deliberately strict, and being overlooked alone does not qualify "
            "a company. Try a broader universe, or open the panel above to see which test "
            "removed the most companies."
        )
        st.stop()

    # ── The gems ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown(f"### {min(gem_top_n, len(gems))} companies that passed every test")
    st.caption("Ranked by research score. Order does not reflect how overlooked a company is.")

    for r in gems[:gem_top_n]:
        sc = r.score
        with st.container():
            head, score_col = st.columns([3, 1])
            with head:
                st.markdown(
                    f'<div style="font-size:1.05rem;font-weight:700;color:#12161f;">'
                    f'{r.ticker} <span style="font-weight:400;color:#5f6672;font-size:0.85rem;">'
                    f'{r.name}</span></div>',
                    unsafe_allow_html=True,
                )
            with score_col:
                st.markdown(
                    f'<div style="text-align:right;font-size:1.3rem;font-weight:800;color:#8d7434;">'
                    f'{sc.overall:.0f}<span style="font-size:0.75rem;font-weight:400;color:#9aa1ad;">'
                    f' / 100</span></div>'
                    f'<div style="text-align:right;font-size:0.66rem;color:#9aa1ad;">'
                    f'{sc.confidence} confidence</div>',
                    unsafe_allow_html=True,
                )

            st.markdown("**Why this company appears**")
            for label, evidence in r.passed:
                st.markdown(
                    f'<div style="font-size:0.83rem;color:#5f6672;margin-bottom:0.2rem;">'
                    f'<span style="color:#2f6b4f;">✓</span> <b>{label}</b> — {evidence}</div>',
                    unsafe_allow_html=True,
                )
            if r.unknown:
                st.caption(
                    "Not checked: " + "; ".join(f"{label} ({ev})" for label, ev in r.unknown)
                )
            st.markdown("---")

    st.caption(
        "This is research and education, not financial advice, and nothing here is a "
        "recommendation to buy or sell any investment. Passing these tests does not mean a "
        "company is undervalued or that its share price will rise. Investing carries risk and "
        "you may get back less than you put in."
    )


elif page == "Watchlist":

    st.title("Holdings Review")
    st.caption(
        "Enter companies you hold to see what has changed in their reported figures: "
        "deteriorating fundamentals, stretched valuations, weakening momentum and "
        "balance-sheet strain. This is research on what the numbers show, not guidance "
        "on what to do about it."
    )

    st.sidebar.markdown("### Your holdings")
    sell_input = st.sidebar.text_area(
        "Enter tickers you own",
        value="AAPL, MSFT, NVDA, TSLA, META",
        height=120,
        help="Comma or newline separated",
    )
    for sep in ["\n", " "]:
        sell_input = sell_input.replace(sep, ",")
    sell_tickers = [t.strip().upper() for t in sell_input.split(",") if t.strip()]

    st.sidebar.markdown("### Factor weights")
    sw_val   = st.sidebar.slider("Overvaluation",          0, 50, 25)
    sw_fund  = st.sidebar.slider("Fundamental decline",    0, 50, 35)
    sw_bal   = st.sidebar.slider("Balance sheet stress",   0, 50, 20)
    sw_mkt   = st.sidebar.slider("Market / momentum",      0, 50, 20)

    refresh_sell = st.sidebar.button("Review my holdings", type="primary")

    sell_cache = f"sell_{'_'.join(sell_tickers)}"
    if refresh_sell or sell_cache not in st.session_state:
        st.session_state[sell_cache] = None

    if _scan_gate(sell_cache, "Analyse my holdings", forced=refresh_sell,
                  note="Checks your holdings for warning signs. Pulls live data for "
                       "each ticker you listed in the sidebar."):
        rows = []
        tickers_tuple_sell = tuple(sell_tickers)
        prog = st.progress(0, text="Downloading price data (batch)...")
        sell_all_prices = _batch_prices(tickers_tuple_sell, period="3mo", interval="1d")
        valid_sell_tup  = tuple(t for t in sell_tickers if t in sell_all_prices and not sell_all_prices[t].empty) or tuple(sell_tickers)
        prog.progress(0.45, text=f"Fetching fundamentals for {len(valid_sell_tup)} stocks...")
        sell_all_info   = _batch_info(valid_sell_tup)
        prog.progress(0.85, text="Assessing each holding...")

        for ticker in sell_tickers:
            try:
                info = sell_all_info.get(ticker, {})
                if not info:
                    continue

                def _f(k, d=None):
                    v = info.get(k)
                    try: return float(v) if v is not None else d
                    except: return d

                # ── Valuation overstretch ──────────────────────────────
                pe         = _f("trailingPE")
                forward_pe = _f("forwardPE")
                peg        = _f("pegRatio")
                ps         = _f("priceToSalesTrailing12Months")
                ev_ebitda  = _f("enterpriseToEbitda")
                price_52h  = _f("fiftyTwoWeekHigh")
                price_now  = _f("currentPrice") or _f("regularMarketPrice")

                # Warning scores: HIGHER means more to look at. A missing input
                # must therefore be None, not 0 -- a 0 here reads as "no concern".
                pe_warn    = max(0, min(100, (pe  - 15) / 55 * 100)) if pe  is not None else None
                peg_warn   = max(0, min(100, (peg - 1)  / 2  * 100)) if peg is not None else None
                ps_warn    = max(0, min(100, (ps  - 3)  / 17 * 100)) if ps  is not None else None
                if price_52h and price_now:
                    near_52h_warn = max(0, min(100, ((price_now / price_52h * 100) - 70) / 30 * 100))
                else:
                    near_52h_warn = None

                val_warn, val_cov = _weighted_known([
                    (pe_warn, 0.35), (peg_warn, 0.30), (ps_warn, 0.20), (near_52h_warn, 0.15),
                ])

                # ── Fundamental deterioration ──────────────────────────
                def _pct(field):
                    raw = _f(field)
                    return raw * 100 if raw is not None else None

                rev_growth   = _pct("revenueGrowth")
                earn_growth  = _pct("earningsGrowth")
                net_margin   = _pct("profitMargins")
                op_margin    = _pct("operatingMargins")
                gross_margin = _pct("grossMargins")
                roe          = _pct("returnOnEquity")

                rev_warn    = (max(0, min(100, (-rev_growth + 5) / 30 * 100))
                               if rev_growth is not None else None)
                earn_warn   = (max(0, min(100, (-earn_growth + 10) / 40 * 100))
                               if earn_growth is not None else None)
                margin_warn = (max(0, min(100, (15 - net_margin) / 25 * 100)) if net_margin is not None
                               and net_margin < 15 else (0 if net_margin is not None else None))
                roe_warn    = (max(0, min(100, (15 - roe) / 20 * 100)) if roe is not None
                               and roe < 15 else (0 if roe is not None else None))

                fund_warn, fund_cov = _weighted_known([
                    (rev_warn, 0.35), (earn_warn, 0.30), (margin_warn, 0.20), (roe_warn, 0.15),
                ])

                # ── Balance sheet stress ───────────────────────────────
                de_raw      = _f("debtToEquity")
                de          = de_raw / 100 if de_raw is not None else None
                curr_ratio  = _f("currentRatio")
                fcf         = _f("freeCashflow")
                market_cap  = _f("marketCap")
                fcf_yield   = (fcf / market_cap * 100) if fcf and market_cap else None

                # No debt figure is not the same as no debt.
                de_warn  = max(0, min(100, (de - 0.5) / 2 * 100)) if de is not None else None
                cr_warn  = (max(0, min(100, (1.5 - curr_ratio) / 1.5 * 100))
                            if curr_ratio is not None else None)
                if fcf is None:
                    fcf_warn = None
                elif fcf < 0:
                    fcf_warn = 80
                elif fcf_yield is not None:
                    fcf_warn = 40 if fcf_yield < 1 else 0
                else:
                    fcf_warn = 0

                bal_warn, bal_cov = _weighted_known([
                    (de_warn, 0.40), (fcf_warn, 0.35), (cr_warn, 0.25),
                ])

                # ── Market / momentum warnings ─────────────────────────
                hist  = sell_all_prices.get(ticker)
                rsi_val, vs_sma50, vs_sma200 = 50.0, 0.0, 0.0
                if hist is not None and len(hist) >= 14:
                    closes   = hist["Close"]
                    delta    = closes.diff().dropna()
                    gain     = delta.clip(lower=0).rolling(14).mean()
                    loss     = (-delta.clip(upper=0)).rolling(14).mean()
                    rs       = gain / loss.replace(0, float("nan"))
                    rsi_val  = float((100 - 100 / (1 + rs)).iloc[-1]) if not rs.empty else 50
                    rsi_val  = rsi_val if not pd.isna(rsi_val) else 50.0
                    if len(closes) >= 50:
                        sma50   = float(closes.rolling(50).mean().iloc[-1])
                        vs_sma50 = (float(closes.iloc[-1]) - sma50) / sma50 * 100
                    if len(closes) >= 60:
                        sma200  = float(closes.rolling(min(200, len(closes))).mean().iloc[-1])
                        vs_sma200 = (float(closes.iloc[-1]) - sma200) / sma200 * 100

                # RSI > 75 = overbought, < 30 = may be broken
                rsi_warn      = max(0, min(100, (rsi_val - 65) / 30 * 100))
                # Far below key moving averages
                sma50_warn    = max(0, min(100, (-vs_sma50  + 2) / 15 * 100)) if vs_sma50  < 0 else 0
                sma200_warn   = max(0, min(100, (-vs_sma200 + 2) / 20 * 100)) if vs_sma200 < 0 else 0

                # Insider selling — use heldPercentInsiders as fast proxy
                # Low insider ownership + high short interest = disposal pressure
                insider_sell_warn = 50.0
                held_ins  = _f("heldPercentInsiders", None)
                _short_hr = _f("shortPercentOfFloat")
                short_pct = _short_hr * 100 if _short_hr is not None else None
                if held_ins is not None:
                    # Low insider ownership is a mild sell signal
                    # With short interest unknown, the warning rests on insider
                    # ownership alone -- the unknown component contributes
                    # nothing rather than being invented.
                    insider_sell_warn = max(0, min(100, (0.05 - held_ins) / 0.05 * 50
                                                        + (short_pct or 0) * 2))

                # Analyst rec worsening (3 = hold, 4-5 = underperform/sell)
                rec_mean   = _f("recommendationMean")
                rec_warn   = max(0, min(100, ((rec_mean or 3) - 2) / 3 * 100)) if rec_mean else 50

                mkt_warn   = rsi_warn * 0.25 + sma50_warn * 0.20 + sma200_warn * 0.20 + insider_sell_warn * 0.20 + rec_warn * 0.15

                # ── Composite attention score ─────────────────────────
                # Categories that could not be assessed are excluded and the
                # remaining weights renormalised, so a holding with missing data
                # is reported as less certain rather than as less concerning.
                sell_score, data_cov = _weighted_known([
                    (val_warn, sw_val), (fund_warn, sw_fund),
                    (bal_warn, sw_bal), (mkt_warn, sw_mkt),
                ])
                if sell_score is None:
                    continue  # nothing measurable; do not invent a verdict

                overall_cov = data_cov * (
                    (val_cov * sw_val + fund_cov * sw_fund + bal_cov * sw_bal + 1.0 * sw_mkt)
                    / ((sw_val + sw_fund + sw_bal + sw_mkt) or 1)
                )
                confidence = ("high" if overall_cov >= 0.85 else
                              "moderate" if overall_cov >= 0.60 else "low")

                if sell_score >= 62:   verdict, verdict_cls = "Several factors to review", "fail-badge"
                elif sell_score >= 40: verdict, verdict_cls = "Some factors to review",    "warn-badge"
                else:                  verdict, verdict_cls = "Few factors flagged",        "pass-badge"

                research = score_company(
                    ticker, _fundamentals_from_info(info, hist), info.get("sector"),
                )
                try:
                    _score_history().record(research)
                except Exception:  # noqa: BLE001 - history is secondary
                    pass

                rows.append({
                    "ticker":          ticker,
                    "research_score":  research.overall,
                    "research_conf":   research.confidence,
                    "name":            info.get("shortName", ticker)[:28],
                    "sell_score":      round(sell_score,   1),
                    "val_warn":        round(val_warn,  1) if val_warn  is not None else None,
                    "fund_warn":       round(fund_warn, 1) if fund_warn is not None else None,
                    "bal_warn":        round(bal_warn,  1) if bal_warn  is not None else None,
                    "mkt_warn":        round(mkt_warn,  1) if mkt_warn  is not None else None,
                    "data_coverage":   round(overall_cov * 100, 0),
                    "confidence":      confidence,
                    "verdict":         verdict,
                    "verdict_cls":     verdict_cls,
                    # Raw metrics
                    "pe":              round(pe, 1)         if pe         else None,
                    "peg":             round(peg, 2)        if peg        else None,
                    "ps":              round(ps, 2)         if ps         else None,
                    "rev_growth":      round(rev_growth, 1),
                    "earn_growth":     round(earn_growth, 1),
                    "net_margin":      round(net_margin, 1),
                    "roe":             round(roe, 1),
                    "de_ratio":        round(de, 2)         if de         else None,
                    "fcf_yield":       round(fcf_yield, 1)  if fcf_yield  else None,
                    "rsi":             round(rsi_val, 1),
                    "vs_sma50":        round(vs_sma50, 1),
                    "vs_sma200":       round(vs_sma200, 1),
                    "insider_sell":    round(insider_sell_warn, 0),
                    "rec_mean":        round(rec_mean, 1)   if rec_mean   else None,
                    "near_52h_pct":    (round(price_now / price_52h * 100, 1)
                                        if price_52h and price_now else None),
                })

            except Exception:
                continue

        prog.empty()
        if not rows:
            st.warning("No data returned. Check your tickers and try again.")
            st.stop()
        sell_df = pd.DataFrame(rows).sort_values("sell_score", ascending=False).reset_index(drop=True)
        st.session_state[sell_cache] = sell_df

    sell_df = st.session_state.get(sell_cache)

    if sell_df is None:
        st.stop()  # gate is showing its own prompt — nothing to render yet
    if sell_df.empty:
        st.warning(
            "No data came back for those tickers. Check the symbols in the sidebar "
            "(US tickers work best), then press Review my holdings to try again."
        )
        st.stop()

    # ── What has changed since we last looked ────────────────────
    # Uses only recorded snapshots. Nothing is reconstructed: a past score
    # cannot be recomputed from today's figures.
    st.markdown("### What has changed")
    try:
        _hist_store = _score_history()
        changes = []
        for _, row in sell_df.iterrows():
            tk = row["ticker"]
            latest = _hist_store.latest(tk)
            if latest is None:
                continue
            earlier = (_hist_store.nearest(tk, 30, tolerance_days=20)
                       or _hist_store.nearest(tk, 90, tolerance_days=60))
            if earlier is None or earlier.taken_on == latest.taken_on:
                continue
            changes.append((tk, describe_change(latest, earlier)))
    except Exception:  # noqa: BLE001
        changes = []

    if not changes:
        st.info(
            "**Nothing to compare yet.** Research scores for these companies were recorded "
            "today. Once there are snapshots from an earlier date, this section will show "
            "how each score has moved and which measures drove it. Earlier scores cannot be "
            "calculated retrospectively — the data source only provides today's figures."
        )
    else:
        for tk, change in changes:
            if not change["comparable"]:
                st.caption(f"**{tk}** — {change['summary']}")
                continue
            arrow = "▲" if change["delta"] > 0 else ("▼" if change["delta"] < 0 else "▬")
            colour = ("#2f6b4f" if change["delta"] > 0
                      else "#9b3b3b" if change["delta"] < 0 else "#9aa1ad")
            st.markdown(
                f"<div style='margin-bottom:0.45rem;font-size:0.9rem;color:#5f6672;'>"
                f"<b>{tk}</b> <span style='color:{colour};font-weight:700;'>{arrow} "
                f"{change['delta']:+.0f}</span> — {change['summary']}</div>",
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ── Review cards ─────────────────────────────────────────────
    st.markdown("### At a glance")
    cols = st.columns(len(sell_df))
    for i, (_, row) in enumerate(sell_df.iterrows()):
        score = row["sell_score"]
        if score >= 62:
            bg, border = "#fff5f5", "#9b3b3b"
        elif score >= 40:
            bg, border = "#fffbeb", "#8d7434"
        else:
            bg, border = "#f0fdf4", "#2f6b4f"
        cols[i].markdown(
            f'<div style="background:{bg}; border:1px solid {border}; border-top:3px solid {border}; '
            f'border-radius:3px; padding:0.85rem 1rem;">'
            f'<div style="font-size:0.85rem; font-weight:700; color:#12161f;">{row["ticker"]}</div>'
            f'<div style="font-size:1.6rem; font-weight:800; color:{border}; line-height:1.1; margin:0.2rem 0;">{score:.0f}</div>'
            f'<div style="font-size:0.68rem; color:#5f6672; text-transform:uppercase; letter-spacing:0.07em;">Factors flagged</div>'
            f'<div style="font-size:0.75rem; font-weight:600; color:{border}; margin-top:0.35rem;">{row["verdict"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── Factors-flagged bar chart ────────────────────────────────
    st.markdown("### Where the concerns come from")
    fig_sell = go.Figure()
    comp_cols  = ["val_warn", "fund_warn", "bal_warn", "mkt_warn"]
    comp_names = ["Overvaluation", "Fundamental Decline", "Balance Sheet Stress", "Market/Momentum"]
    comp_clrs  = ["#9b3b3b", "#8d7434", "#7c3aed", "#2563eb"]

    for col, name, clr in zip(comp_cols, comp_names, comp_clrs):
        fig_sell.add_trace(go.Bar(
            name=name, x=sell_df["ticker"], y=sell_df[col],
            marker_color=clr, opacity=0.85,
        ))

    # Threshold lines
    fig_sell.add_hline(y=62, line_dash="dash", line_color="#9b3b3b", line_width=1.2,
                       annotation_text="Several factors flagged", annotation_position="right",
                       annotation_font=dict(color="#9b3b3b", size=10))
    fig_sell.add_hline(y=40, line_dash="dot",  line_color="#8d7434", line_width=1,
                       annotation_text="Watch zone", annotation_position="right",
                       annotation_font=dict(color="#8d7434", size=10))

    fig_sell.update_layout(**chart_layout(
        barmode="group", height=380,
        yaxis_range=[0, 105],
        yaxis_title="Factors flagged (0 = none, 100 = many)",
    ))
    st.plotly_chart(fig_sell, width="stretch")

    # ── Full data table ──────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Full signal table")

    table_cols = ["ticker", "name", "sell_score", "verdict",
                  "pe", "peg", "rev_growth", "earn_growth",
                  "net_margin", "roe", "de_ratio", "fcf_yield",
                  "rsi", "vs_sma50", "vs_sma200",
                  "insider_sell", "near_52h_pct"]
    rename_sell = {
        "ticker": "Ticker", "name": "Company",
        "sell_score": "Factors flagged", "verdict": "Summary",
        "pe": "P/E", "peg": "PEG",
        "rev_growth": "Rev Gr%", "earn_growth": "EPS Gr%",
        "net_margin": "Net Mgn%", "roe": "ROE%",
        "de_ratio": "D/E", "fcf_yield": "FCF Yld%",
        "rsi": "RSI", "vs_sma50": "vs SMA50%", "vs_sma200": "vs SMA200%",
        "insider_sell": "Insider selling%", "near_52h_pct": "% of 52W high",
    }
    avail_sell = [c for c in table_cols if c in sell_df.columns]
    st.dataframe(
        sell_df[avail_sell].rename(columns=rename_sell),
        hide_index=True, width="stretch",
    )

    # ── Deep dive ───────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### What the numbers show for each holding")
    sel_sell = st.selectbox("Select a holding", sell_df["ticker"].tolist(), key="sell_select")
    s = sell_df[sell_df["ticker"] == sel_sell].iloc[0]

    c1, c2, c3, c4 = st.columns(4)

    def warn_badge(score):
        # A category that could not be assessed must say so, not show a
        # reassuring green badge built from data we never had.
        if score is None or pd.isna(score):
            return '<span class="warn-badge">Not assessed — data unavailable</span>'
        if score >= 62: return f'<span style="color:#9b3b3b;">High ({score:.0f})</span>'
        if score >= 40: return f'<span style="color:#8a6a2f;">Medium ({score:.0f})</span>'
        return f'<span style="color:#2f6b4f;">Low ({score:.0f})</span>'

    with c1:
        st.markdown("**Overvaluation**")
        st.markdown(warn_badge(s["val_warn"]), unsafe_allow_html=True)
        st.write(f"P/E: **{s['pe']}**" if s["pe"] else "P/E: **—**")
        st.write(f"PEG: **{s['peg']}**" if s["peg"] else "PEG: **—**")
        st.write(f"P/S: **{s['ps']}**" if s["ps"] else "P/S: **—**")
        pct_52 = s["near_52h_pct"]
        if pct_52:
            cls = "fail-badge" if pct_52 > 90 else "warn-badge" if pct_52 > 75 else "pass-badge"
            st.markdown(f'% of 52W high: <span class="{cls}"><b>{pct_52:.0f}%</b></span>', unsafe_allow_html=True)

    with c2:
        st.markdown("**Fundamental decline**")
        st.markdown(warn_badge(s["fund_warn"]), unsafe_allow_html=True)
        rg = s["rev_growth"]
        eg = s["earn_growth"]
        rg_cls = "pass-badge" if rg > 5 else "warn-badge" if rg > 0 else "fail-badge"
        eg_cls = "pass-badge" if eg > 5 else "warn-badge" if eg > 0 else "fail-badge"
        st.markdown(f'Revenue growth: <span class="{rg_cls}"><b>{rg:+.1f}%</b></span>', unsafe_allow_html=True)
        st.markdown(f'Earnings growth: <span class="{eg_cls}"><b>{eg:+.1f}%</b></span>', unsafe_allow_html=True)
        nm = s["net_margin"]
        nm_cls = "pass-badge" if nm > 10 else "warn-badge" if nm > 0 else "fail-badge"
        st.markdown(f'Net margin: <span class="{nm_cls}"><b>{nm:.1f}%</b></span>', unsafe_allow_html=True)
        st.write(f"ROE: **{s['roe']:.1f}%**")

    with c3:
        st.markdown("**Balance sheet stress**")
        st.markdown(warn_badge(s["bal_warn"]), unsafe_allow_html=True)
        de = s["de_ratio"]
        de_cls = "pass-badge" if (de or 0) < 0.5 else "warn-badge" if (de or 0) < 1.5 else "fail-badge"
        st.markdown(f'D/E ratio: <span class="{de_cls}"><b>{de}</b></span>', unsafe_allow_html=True) if de else st.write("D/E: **—**")
        fy = s["fcf_yield"]
        if fy is not None:
            fy_cls = "pass-badge" if fy > 3 else "warn-badge" if fy > 0 else "fail-badge"
            st.markdown(f'FCF yield: <span class="{fy_cls}"><b>{fy:.1f}%</b></span>', unsafe_allow_html=True)
        else:
            st.write("FCF yield: **—**")

    with c4:
        st.markdown("**Market signals**")
        st.markdown(warn_badge(s["mkt_warn"]), unsafe_allow_html=True)
        rsi = s["rsi"]
        rsi_cls = "fail-badge" if rsi > 75 else "warn-badge" if rsi > 65 else "pass-badge" if rsi > 30 else "fail-badge"
        st.markdown(f'RSI (14): <span class="{rsi_cls}"><b>{rsi:.0f}</b></span>', unsafe_allow_html=True)
        sma50 = s["vs_sma50"]
        sma50_cls = "pass-badge" if sma50 > 0 else "fail-badge"
        st.markdown(f'vs SMA50: <span class="{sma50_cls}"><b>{sma50:+.1f}%</b></span>', unsafe_allow_html=True)
        sma200 = s["vs_sma200"]
        sma200_cls = "pass-badge" if sma200 > 0 else "fail-badge"
        st.markdown(f'vs SMA200: <span class="{sma200_cls}"><b>{sma200:+.1f}%</b></span>', unsafe_allow_html=True)
        ins = s["insider_sell"]
        ins_cls = "fail-badge" if ins > 70 else "warn-badge" if ins > 50 else "pass-badge"
        st.markdown(f'Insider selling: <span class="{ins_cls}"><b>{ins:.0f}%</b></span>', unsafe_allow_html=True)
        rec = s["rec_mean"]
        if rec:
            rec_label = {1:"Strong Buy",2:"Buy",3:"Hold",4:"Underperform",5:"Sell"}.get(round(rec), f"{rec:.1f}")
            rec_cls   = "pass-badge" if rec <= 2 else "warn-badge" if rec <= 3 else "fail-badge"
            st.markdown(f'Analyst view: <span class="{rec_cls}"><b>{rec_label}</b></span>', unsafe_allow_html=True)

    # ── Price chart for selected holding ────────────────────────
    st.markdown("---")
    st.markdown(f"### {sel_sell} — 6-month price chart")
    try:
        hist6 = yf.Ticker(sel_sell).history(period="6mo", interval="1d", auto_adjust=True)
        if not hist6.empty:
            closes = hist6["Close"]
            sma50_line  = closes.rolling(50).mean()
            sma200_line = closes.rolling(min(200, len(closes))).mean()

            fig_price = go.Figure()
            fig_price.add_trace(go.Scatter(
                x=hist6.index, y=closes, name="Price",
                line=dict(color="#12161f", width=1.8),
                fill="tozeroy", fillcolor="rgba(184,150,12,0.07)",
            ))
            fig_price.add_trace(go.Scatter(
                x=hist6.index, y=sma50_line, name="SMA 50",
                line=dict(color=GOLD, width=1.4, dash="dot"),
            ))
            fig_price.add_trace(go.Scatter(
                x=hist6.index, y=sma200_line, name="SMA 200",
                line=dict(color="#9b3b3b", width=1.4, dash="dash"),
            ))
            fig_price.update_layout(**chart_layout(
                height=340,
                title=dict(text=f"{sel_sell} vs SMA 50 & SMA 200", font=dict(size=12, color=CHART_TEXT)),
            ))
            st.plotly_chart(fig_price, width="stretch")
    except Exception:
        pass

    # ── Disclaimer ───────────────────────────────────────────────
    st.markdown("---")
    st.caption(
        "Holdings Review is a research tool, not financial advice. "
        "A high sell score means the data warrants a closer look — not an automatic exit. "
        "Always consider your own tax situation and time horizon, and seek independent advice if you need it."
    )

    st.download_button(
        "⬇ Download sell analysis as CSV",
        data=sell_df.to_csv(index=False),
        file_name=f"sell_watch_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )


# ════════════════════════════════════════════════════════════
# PAGE 4 — SCREENER
# ════════════════════════════════════════════════════════════
elif page == "Screener":
    st.title("Screener")

    st.sidebar.markdown("### Mode")
    screen_mode = st.sidebar.radio(
        "What to screen on",
        ["Research scores", "Classic filters"],
        index=0,
        help="Research scores filters on the five scored categories. Classic filters "
             "uses the older raw-metric thresholds from the YAML configs.",
    )

    # ════════════════════════════════════════════════════════
    # SCORE-BASED SCREEN
    # ════════════════════════════════════════════════════════
    if screen_mode == "Research scores":
        st.caption(
            "Filter companies on the five scored categories. Every preset is a set of "
            "stated thresholds — open one to see exactly what it filters for, and what "
            "it cannot tell you."
        )

        sc_universe = st.sidebar.selectbox(
            "Universe",
            ["broad", "all_curated", "tech", "ai", "growth", "nasdaq100"],
            format_func=lambda x: {
                "broad": "Broad Market (~300)", "tech": "Tech & Mega Caps",
                "ai": "AI & ML", "growth": "High Growth (~50)",
                "all_curated": "All Curated (~130)", "nasdaq100": "NASDAQ 100",
            }[x],
            index=0,
        )
        preset_key = st.sidebar.selectbox(
            "Preset",
            ["custom"] + [p.key for p in PRESETS],
            format_func=lambda k: "Custom thresholds" if k == "custom" else PRESETS_BY_KEY[k].label,
            index=1,
        )

        custom_limits = {}
        if preset_key == "custom":
            st.sidebar.markdown("### Thresholds")
            for key, label in FILTERABLE.items():
                custom_limits[key] = st.sidebar.slider(label, 0, 100, (0, 100), key=f"_scr_{key}")
            custom_limits = {k: v for k, v in custom_limits.items() if v != (0, 100)}

        sc_refresh = st.sidebar.button("Run screen", type="primary")
        sc_key = f"screen_{sc_universe}"
        if sc_refresh or sc_key not in st.session_state:
            st.session_state[sc_key] = None

        if _scan_gate(sc_key, "Run screen", forced=sc_refresh,
                      note="Scores every company in the universe, then applies your "
                           "filters. Pulls live data for several hundred tickers."):
            tickers = get_universe(sc_universe)
            with st.spinner(f"Scoring {len(tickers)} companies…"):
                prog = st.progress(0, text="Downloading price data (batch)...")
                prices = _batch_prices(tuple(tickers), period="3mo", interval="1d")
                valid = tuple(t for t in tickers if t in prices and not prices[t].empty) or tuple(tickers)
                prog.progress(0.5, text=f"Fetching fundamentals for {len(valid)}...")
                infos = _batch_info(valid)
                prog.progress(0.9, text="Scoring...")
                scored = []
                for t in valid:
                    info = infos.get(t) or {}
                    if not info.get("shortName"):
                        continue
                    sc = score_company(t, _fundamentals_from_info(info, prices.get(t)),
                                       info.get("sector"))
                    scored.append((sc, info.get("shortName", t)[:32], info.get("sector")))
                prog.empty()
            st.session_state[sc_key] = scored

        scored = st.session_state.get(sc_key)
        if scored is None:
            st.stop()
        if not scored:
            st.warning("No data came back. Press Run screen to try again.")
            st.stop()

        names = {sc.ticker: nm for sc, nm, _ in scored}
        sectors = {sc.ticker: sec for sc, _, sec in scored}
        all_scores = [sc for sc, _, _ in scored]

        if preset_key == "custom":
            result = screen(all_scores, custom_limits)
            active_preset = None
            st.markdown("##### Custom thresholds")
            st.caption(", ".join(f"{FILTERABLE[k]} {v[0]}–{v[1]}"
                                 for k, v in custom_limits.items()) or "No thresholds set.")
        else:
            result, active_preset = apply_preset(all_scores, preset_key)
            st.markdown(f"##### {active_preset.label}")
            st.markdown(active_preset.description)
            st.caption(f"**Filters:** {active_preset.describe()}.")
            st.warning(f"**What this does not tell you:** {active_preset.caveat}")

        m1, m2, m3, m4 = st.columns(4)
        metric_card("Companies scored", str(result.total - result.unscorable), m1)
        metric_card("Passed filters",   str(len(result.passed)),                m2)
        metric_card("Pass rate",        f"{result.pass_rate:.0%}",              m3)
        metric_card("Not scoreable",    str(result.unscorable),                 m4)

        if result.removed_by:
            with st.expander("Where companies dropped out"):
                st.dataframe(
                    pd.DataFrame([{"Filter": k, "Removed": v}
                                  for k, v in result.removed_by.items()]),
                    hide_index=True, width="stretch",
                )
                st.caption(
                    "A company whose category could not be scored does not pass a filter "
                    "on that category — unknown is not a pass."
                )

        if not result.passed:
            st.info(
                "No company passed every filter. That is a real result, not an error — "
                "try a broader universe or looser thresholds, and see the panel above for "
                "which filter removed the most."
            )
            st.stop()

        st.markdown("---")
        st.markdown(f"### {len(result.passed)} companies passed")
        rows = []
        for sc in result.passed:
            row = {"Ticker": sc.ticker, "Company": names.get(sc.ticker, sc.ticker),
                   "Sector": sectors.get(sc.ticker) or "—",
                   "Research": f"{sc.overall:.0f}", "Confidence": sc.confidence}
            for key, label in FILTERABLE.items():
                if key == "overall":
                    continue
                cat = sc.categories.get(key)
                row[label] = "—" if not (cat and cat.available) else f"{cat.score:.0f}"
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     height=min(640, 36 * len(rows) + 40))
        st.caption(
            f"Scoring methodology v{SCORING_VERSION}. A dash means that measure could not "
            f"be scored for that company."
        )
        st.markdown("---")
        st.caption(
            "This is research and education, not financial advice, and nothing here is "
            "a recommendation to buy or sell any investment. Passing a screen means a "
            "company's reported figures match a pattern — nothing more."
        )
        st.stop()

    # ════════════════════════════════════════════════════════
    # CLASSIC FILTERS (raw metric thresholds from YAML)
    # ════════════════════════════════════════════════════════
    st.caption("Raw-metric thresholds from the YAML configs.")
    mode = st.sidebar.radio(
        "Screening mode",
        ["Long-term investing", "Swing trading prep"],
        index=0,
    )
    config_file = "config_longterm.yaml" if mode == "Long-term investing" else "config_swing.yaml"
    config_path = CONFIGS_DIR / config_file

    st.sidebar.markdown("### Ticker input")
    input_method = st.sidebar.radio(
        "How to select stocks",
        ["Enter tickers", "Predefined universe", "Upload file"],
        index=0,
    )

    tickers = []
    if input_method == "Enter tickers":
        ticker_input = st.sidebar.text_area(
            "Tickers (comma or newline separated)",
            value="AAPL, MSFT, GOOG, META, AMZN, NVDA, V, JNJ, PG, UNH",
            height=100,
        )
        for sep in [",", "\n"]:
            ticker_input = ticker_input.replace(sep, ",")
        tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]

    elif input_method == "Predefined universe":
        universe_options = {
            "tech":        "Tech & Mega Caps (~60)",
            "ai":          "AI & Machine Learning (~55)",
            "space":       "Space & Defence (~50)",
            "growth":      "High Growth (~50)",
            "all_curated": "All Curated (~130)",
            "nasdaq100":   "NASDAQ 100",
            "sp500":       "S&P 500",
            "ftse100":     "FTSE 100",
        }
        universe = st.sidebar.selectbox(
            "Select universe",
            list(universe_options.keys()),
            format_func=lambda x: universe_options[x],
        )
        if st.sidebar.button("Load universe"):
            with st.spinner(f"Loading {universe}..."):
                loaded = get_universe(universe)
                st.session_state["loaded_tickers"] = loaded
                st.sidebar.success(f"Loaded {len(loaded)} tickers")
        tickers = st.session_state.get("loaded_tickers", [])

    elif input_method == "Upload file":
        uploaded = st.sidebar.file_uploader("Upload CSV or TXT", type=["csv", "txt"])
        if uploaded:
            import tempfile, os
            with tempfile.NamedTemporaryFile(delete=False, suffix=uploaded.name) as tmp:
                tmp.write(uploaded.read())
                tmp_path = tmp.name
            tickers = load_tickers_from_file(tmp_path)
            os.unlink(tmp_path)
            st.sidebar.success(f"Loaded {len(tickers)} tickers")

    st.sidebar.markdown("### Options")
    use_cache  = st.sidebar.checkbox("Use cache (24h)", value=True)
    show_failed = st.sidebar.checkbox("Show filtered-out stocks", value=False)
    top_n      = st.sidebar.slider("Top results to show", 5, 50, 20)

    st.sidebar.markdown("### Quick filter overrides")
    config = load_config(str(config_path))
    if mode == "Long-term investing":
        pe_max     = st.sidebar.slider("Max P/E",           10, 60,  25)
        roe_min    = st.sidebar.slider("Min ROE %",          0, 40,  15)
        de_max     = st.sidebar.slider("Max D/E",          0.0, 5.0, 1.0, step=0.1)
        margin_min = st.sidebar.slider("Min net margin %",   0, 30,   5)
        config["filters"].update({
            "pe_trailing":    {"enabled": True, "max": pe_max,     "on_missing": "pass"},
            "roe":            {"enabled": True, "min": roe_min,    "on_missing": "pass"},
            "debt_to_equity": {"enabled": True, "max": de_max,     "on_missing": "pass"},
            "net_margin":     {"enabled": True, "min": margin_min, "on_missing": "pass"},
        })
    else:
        margin_min = st.sidebar.slider("Min net margin %", -10, 20, 0)
        de_max     = st.sidebar.slider("Max D/E",          0.5, 5.0, 2.0, step=0.1)
        config["filters"].update({
            "net_margin":     {"enabled": True, "min": margin_min, "on_missing": "fail"},
            "debt_to_equity": {"enabled": True, "max": de_max,     "on_missing": "pass"},
        })

    # ── Main area ────────────────────────────────────────────
    if not tickers:
        st.info("Enter tickers in the sidebar or load a predefined universe, then hit **Run Screener**.")
        st.stop()

    if st.button("▶  Run Screener", type="primary", width="stretch"):
        st.session_state["screen_run"] = True
        st.session_state["screen_results"] = None

    if not st.session_state.get("screen_run"):
        st.info(f"Ready to screen **{len(tickers)}** tickers in **{mode}** mode.")
        st.stop()

    # Run (or show cached results)
    if st.session_state.get("screen_results") is None:
        fetcher = YFinanceFetcher(cache_expiry_hours=24 if use_cache else 0)
        prog = st.progress(0, text="Fetching data...")
        all_metrics, failed_tickers = [], []

        for i, ticker in enumerate(tickers):
            prog.progress((i + 1) / len(tickers), text=f"Fetching {ticker}... ({i+1}/{len(tickers)})")
            data = fetcher.fetch_ticker_data(ticker)
            if not data.get("info"):
                failed_tickers.append(ticker)
                continue
            try:
                all_metrics.append(calculate_all_metrics(ticker, data))
            except Exception:
                failed_tickers.append(ticker)

        prog.empty()
        if failed_tickers:
            st.warning(f"{len(failed_tickers)} tickers failed: {', '.join(failed_tickers[:15])}")

        passed, failed_screen = screen_batch(all_metrics, config)
        results = score_batch(passed, config)
        st.session_state["screen_results"] = results
        st.session_state["screen_failed"]  = failed_screen
        st.session_state["screen_all"]     = all_metrics

    results       = st.session_state["screen_results"]
    failed_screen = st.session_state.get("screen_failed", [])

    # ── Summary cards ─────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    metric_card("Tickers screened",  str(len(st.session_state.get("screen_all", []))), c1)
    metric_card("Passed filters",    str(len(results)), c2)
    avg_score = f"{sum(r['composite_score'] for r in results)/len(results):.1f}" if results else "—"
    metric_card("Avg composite score", avg_score, c3)
    avg_data  = f"{sum(r['data_completeness'] for r in results)/len(results):.0f}%" if results else "—"
    metric_card("Avg data coverage", avg_data, c4)

    st.markdown("---")

    if not results:
        st.error("No stocks passed all filters. Loosen the thresholds in the sidebar and re-run.")
        st.stop()

    df = pd.DataFrame(results)

    # ── Tabs ─────────────────────────────────────────────────
    tab1, tab2, tab3 = st.tabs(["Results table", "Score breakdown", "Stock deep dive"])

    with tab1:
        st.markdown(f"**Top {min(top_n, len(results))} results — ranked by composite score**")
        display_cols = [
            "ticker", "name", "composite_score",
            "pe_trailing", "pe_forward", "roe", "roa", "roic",
            "gross_margin", "operating_margin", "net_margin",
            "debt_to_equity", "current_ratio", "interest_coverage",
            "revenue_growth_1y", "eps_growth_1y",
            "fcf_yield", "fcf_margin",
            "dividend_yield", "payout_ratio",
            "fcf_positive_3y", "no_dilution_3y", "data_completeness",
        ]
        avail = [c for c in display_cols if c in df.columns]
        rename_map = {
            "ticker": "Ticker", "name": "Name", "composite_score": "Score",
            "pe_trailing": "P/E", "pe_forward": "Fwd P/E", "roe": "ROE%",
            "roa": "ROA%", "roic": "ROIC%", "gross_margin": "Gross%",
            "operating_margin": "Op Mgn%", "net_margin": "Net Mgn%",
            "debt_to_equity": "D/E", "current_ratio": "Curr Ratio",
            "interest_coverage": "Int Cov", "revenue_growth_1y": "Rev Gr%",
            "eps_growth_1y": "EPS Gr%", "fcf_yield": "FCF Yld%",
            "fcf_margin": "FCF Mgn%", "dividend_yield": "Div Yld%",
            "payout_ratio": "Payout%", "fcf_positive_3y": "FCF+3Y",
            "no_dilution_3y": "No Dilute", "data_completeness": "Data%",
        }
        disp = df[avail].head(top_n).rename(columns=rename_map)
        st.dataframe(disp, hide_index=True, width="stretch",
                     height=min(700, 35 * len(disp) + 40))

        csv_data = df[avail].to_csv(index=False)
        st.download_button(
            "⬇ Download CSV", data=csv_data,
            file_name=f"screen_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )

        if show_failed and failed_screen:
            st.markdown("**Filtered-out stocks**")
            fail_rows = [{"Ticker": e["ticker"],
                          "Reason": "; ".join(f"{k}: {v['reason']}"
                                              for k, v in e["failed_filters"].items())}
                         for e in failed_screen]
            st.dataframe(pd.DataFrame(fail_rows), hide_index=True, width="stretch")

    with tab2:
        score_cols = {
            "score_profitability":    "Profitability",
            "score_financial_health": "Financial Health",
            "score_valuation":        "Valuation",
            "score_growth":           "Growth",
            "score_cash_flow":        "Cash Flow",
        }
        avail_sc = {k: v for k, v in score_cols.items() if k in df.columns}
        if avail_sc:
            chart_df = df.head(min(top_n, 20))
            fig = go.Figure()
            colors = PALETTE
            for i, (col, label) in enumerate(avail_sc.items()):
                fig.add_trace(go.Bar(
                    name=label, x=chart_df["ticker"], y=chart_df[col],
                    marker_color=colors[i % len(colors)],
                ))
            fig.update_layout(**chart_layout(barmode="group", height=400))
            st.plotly_chart(fig, width="stretch")

    with tab3:
        sel = st.selectbox("Select stock", [r["ticker"] for r in results])
        stock = next(r for r in results if r["ticker"] == sel)

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Valuation**")
            for k, l in [("pe_trailing","P/E (trailing)"),("pe_forward","P/E (forward)"),
                          ("pb","P/B"),("ps","P/S"),("ev_ebitda","EV/EBITDA"),("peg","PEG")]:
                v = stock.get(k)
                st.write(f"{l}: **{v:.2f}**" if v is not None else f"{l}: **—**")
        with c2:
            st.markdown("**Profitability**")
            for k, l in [("roe","ROE"),("roa","ROA"),("roic","ROIC"),
                          ("gross_margin","Gross margin"),("operating_margin","Op margin"),("net_margin","Net margin")]:
                v = stock.get(k)
                st.write(f"{l}: **{v:.1f}%**" if v is not None else f"{l}: **—**")
        with c3:
            st.markdown("**Financial health**")
            for k, l in [("debt_to_equity","D/E"),("current_ratio","Current ratio"),
                          ("quick_ratio","Quick ratio"),("interest_coverage","Interest coverage"),
                          ("net_debt_to_ebitda","Net debt/EBITDA")]:
                v = stock.get(k)
                st.write(f"{l}: **{v:.2f}**" if v is not None else f"{l}: **—**")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Growth**")
            for k, l in [("revenue_growth_1y","Revenue 1Y"),("revenue_growth_3y_cagr","Revenue 3Y CAGR"),
                          ("revenue_growth_5y_cagr","Revenue 5Y CAGR"),("eps_growth_1y","EPS 1Y"),
                          ("eps_growth_3y_cagr","EPS 3Y CAGR")]:
                v = stock.get(k)
                st.write(f"{l}: **{v:.1f}%**" if v is not None else f"{l}: **—**")
        with c2:
            st.markdown("**Cash flow**")
            fcf = stock.get("fcf")
            if fcf is not None:
                st.write(f"FCF: **${fcf/1e9:.2f}B**" if abs(fcf) >= 1e9 else f"FCF: **${fcf/1e6:.0f}M**")
            else:
                st.write("FCF: **—**")
            for k, l in [("fcf_yield","FCF yield"),("fcf_margin","FCF margin")]:
                v = stock.get(k)
                st.write(f"{l}: **{v:.1f}%**" if v is not None else f"{l}: **—**")
        with c3:
            st.markdown("**Quality flags**")
            for flag_key, label in [("fcf_positive_3y","FCF positive 3Y"),("no_dilution_3y","No dilution 3Y")]:
                v = stock.get(flag_key)
                if v is True:
                    st.markdown(f'{label}: <span class="pass-badge">PASS</span>', unsafe_allow_html=True)
                elif v is False:
                    st.markdown(f'{label}: <span class="fail-badge">FAIL</span>', unsafe_allow_html=True)
                else:
                    st.markdown(f'{label}: <span class="warn-badge">NO DATA</span>', unsafe_allow_html=True)
            st.write(f"Data completeness: **{stock.get('data_completeness', 0):.0f}%**")
            st.write(f"Composite score: **{stock.get('composite_score', 0):.1f} / 100**")


# ════════════════════════════════════════════════════════════
# PAGE 5 — T212 ISA
# ════════════════════════════════════════════════════════════
elif page == "UK Investor":

    st.title("UK Investor")

    # ISA benefit banner
    st.markdown("""
    <div style="background:#ffffff; border:1px solid #e7e4dd; border-left:4px solid #8d7434;
                border-radius:3px; padding:0.85rem 1.2rem; margin-bottom:1.2rem;">
        <div style="font-size:0.8rem; font-weight:700; color:#8d7434; letter-spacing:0.1em;
                    text-transform:uppercase; margin-bottom:0.3rem;">🇬🇧 Stocks & Shares ISA</div>
        <div style="font-size:0.85rem; color:#5f6672; line-height:1.5;">
            All gains and dividends earned inside a Stocks & Shares ISA are
            <strong>completely tax-free</strong> — no Capital Gains Tax, no dividend income tax.
            This page screens stocks that are available on Trading 212's ISA platform.
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Sidebar ──────────────────────────────────────────────
    st.sidebar.markdown("### UK Investor settings")

    t212_sector = st.sidebar.selectbox(
        "Sector filter",
        ["All T212 stocks", "US Tech & AI", "US Finance", "US Healthcare", "US Consumer",
         "US Industrials", "UK Listed (.L)"],
        index=0,
    )

    t212_mode = st.sidebar.radio(
        "Analysis mode",
        ["Top Movers", "Best Opportunities", "Screen"],
        index=0,
    )

    t212_top_n = st.sidebar.slider("Show top N", 5, 25, 12)
    refresh_t212 = st.sidebar.button("Refresh Data", type="primary")

    # ── Sector filtering ─────────────────────────────────────
    ALL_T212 = get_trading212_isa()

    SECTOR_MAP = {
        "All T212 stocks":    ALL_T212,
        "US Tech & AI":       [t for t in ALL_T212 if t in [
            "AAPL","MSFT","GOOG","AMZN","META","NVDA","TSLA",
            "AMD","INTC","AVGO","QCOM","TXN","MU","MRVL","ARM","TSM","ASML","LRCX","KLAC","AMAT","ON","SMCI",
            "CRM","ORCL","ADBE","NOW","SNOW","PLTR","DDOG","NET","ZS","CRWD","PANW","FTNT","MDB","WDAY","HUBS","TWLO","OKTA",
            "AI","PATH","SOUN","BBAI","IONQ","RGTI",
            "NFLX","SHOP","UBER","ABNB","SPOT","RBLX","TTD","PINS","SNAP","DUOL",
            "ANET","DELL","CFLT","DT","IOT","MNDY","TOST",
        ]],
        "US Finance":         [t for t in ALL_T212 if t in [
            "V","MA","PYPL","SQ","COIN","AFRM","SOFI","HOOD",
            "JPM","BAC","WFC","GS","MS","C","AXP","COF","BLK","SCHW",
        ]],
        "US Healthcare":      [t for t in ALL_T212 if t in [
            "JNJ","UNH","LLY","ABBV","PFE","MRK","AMGN","GILD","ISRG",
            "DXCM","VEEV","REGN","HIMS","RXRX",
        ]],
        "US Consumer":        [t for t in ALL_T212 if t in [
            "WMT","COST","PG","KO","PEP","MCD","SBUX","NKE","TGT","HD","LOW","CELH","DUOL",
            "T","VZ","CMCSA","DIS",
        ]],
        "US Industrials":     [t for t in ALL_T212 if t in [
            "GE","CAT","HON","BA","RTX","LMT","NOC","GD","DE","HWM","TDG",
            "RKLB","AXON","KTOS","ASTS","XOM","CVX","COP","SLB","EOG",
            "O","PLD","AMT","EQIX",
        ]],
        "UK Listed (.L)":     [t for t in ALL_T212 if t.endswith(".L")],
    }

    t212_tickers = SECTOR_MAP.get(t212_sector, ALL_T212)

    st.caption(
        f"Showing **{t212_sector}** — {len(t212_tickers)} stocks available. "
        f"Data from yfinance (cached 24h)."
    )

    # ══════════════════════════════════════════════════════════
    # MODE A — TOP MOVERS (momentum)
    # ══════════════════════════════════════════════════════════
    if t212_mode == "Top Movers":

        t212_cache_key = f"t212_hot_{t212_sector}"
        if refresh_t212 or t212_cache_key not in st.session_state:
            st.session_state[t212_cache_key] = None

        if st.session_state[t212_cache_key] is None:
            with st.spinner(f"Analysing {len(t212_tickers)} T212 stocks for momentum..."):
                t212_tup   = tuple(t212_tickers)
                prog = st.progress(0, text="Batch downloading prices...")
                t212_prices = _batch_prices(t212_tup, period="1mo", interval="1d")
                prog.progress(0.5, text="Fetching analyst data (parallel)...")
                t212_infos  = _batch_info(t212_tup)
                prog.progress(0.85, text="Computing scores...")

                rows = []
                for ticker in t212_tickers:
                    try:
                        hist = t212_prices.get(ticker)
                        if hist is None or len(hist) < 6:
                            continue

                        closes  = hist["Close"]
                        volumes = hist["Volume"]
                        price_now  = float(closes.iloc[-1])
                        price_1w   = float(closes.iloc[-6]) if len(closes) >= 6 else None
                        price_1m   = float(closes.iloc[0])
                        vol_now    = float(volumes.iloc[-5:].mean())
                        vol_avg    = float(volumes.mean())

                        week_chg   = ((price_now - price_1w) / price_1w * 100) if price_1w else 0
                        month_chg  = (price_now - price_1m) / price_1m * 100
                        vol_surge  = vol_now / vol_avg if vol_avg > 0 else 1.0

                        delta = closes.diff().dropna()
                        gain  = delta.clip(lower=0).rolling(14).mean()
                        loss  = (-delta.clip(upper=0)).rolling(14).mean()
                        rs    = gain / loss.replace(0, float("nan"))
                        rsi   = float((100 - 100 / (1 + rs)).iloc[-1]) if not rs.empty else 50.0
                        rsi   = rsi if not pd.isna(rsi) else 50.0

                        sma20  = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else price_now
                        vs_sma = (price_now - sma20) / sma20 * 100

                        info  = t212_infos.get(ticker, {})
                        rec   = info.get("recommendationMean")
                        analyst_score = max(0, min(100, (5 - float(rec)) / 4 * 100)) if rec else 50.0

                        week_score = min(100, max(0, (week_chg + 10) / 30 * 100))
                        vol_score  = min(100, max(0, (vol_surge - 0.5) / 2.5 * 100))
                        rsi_score  = min(100, max(0, (rsi - 30) / 50 * 100))
                        sma_score  = min(100, max(0, (vs_sma + 5) / 20 * 100))
                        hot_score  = (
                            week_score * 0.35 + vol_score * 0.20 +
                            rsi_score  * 0.20 + sma_score * 0.15 +
                            analyst_score * 0.10
                        )

                        currency = "£" if ticker.endswith(".L") else "$"
                        rows.append({
                            "ticker":    ticker, "currency":  currency,
                            "price":     round(price_now, 2),
                            "week_chg":  round(week_chg, 2),
                            "month_chg": round(month_chg, 2),
                            "vol_surge": round(vol_surge, 2),
                            "rsi":       round(rsi, 1),
                            "vs_sma20":  round(vs_sma, 2),
                            "hot_score": round(hot_score, 1),
                        })
                    except Exception:
                        continue

                prog.empty()
                if not rows:
                    st.warning("No data returned. Check your tickers and try again.")
                    st.stop()
                df_hot = pd.DataFrame(rows).sort_values("hot_score", ascending=False).reset_index(drop=True)
                st.session_state[t212_cache_key] = df_hot

        df_hot = st.session_state[t212_cache_key]

        if df_hot is None or df_hot.empty:
            st.error(
                "No data came back for this list. This is usually a temporary "
                "data-provider hiccup — refresh the page to try again."
            )
            st.stop()

        top_hot = df_hot.head(t212_top_n)

        # Hero cards
        st.markdown("### This week's top movers")
        cols = st.columns(min(5, len(top_hot)))
        for i, (_, row) in enumerate(top_hot.head(5).iterrows()):
            arrow = "▲" if row["week_chg"] >= 0 else "▼"
            cls   = "up" if row["week_chg"] >= 0 else "down"
            cols[i % 5].markdown(
                f'<div class="bs-panel">'
                f'<h3>{row["ticker"]}</h3>'
                f'<p><span class="{cls}">{arrow} {row["week_chg"]:+.1f}%</span></p>'
                f'<small style="color:#a0a0b0">Score: {row["hot_score"]:.0f} &nbsp;|&nbsp; '
                f'RSI: {row["rsi"]:.0f} &nbsp;|&nbsp; Vol: {row["vol_surge"]:.1f}x</small>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("---")
        st.markdown(f"### Full top {t212_top_n}")

        display_df = top_hot.copy()
        display_df.insert(0, "Rank", range(1, len(display_df) + 1))
        display_df = display_df.rename(columns={
            "ticker": "Ticker", "currency": "CCY", "price": "Price",
            "week_chg": "Week %", "month_chg": "Month %",
            "vol_surge": "Vol Surge", "rsi": "RSI (14)",
            "vs_sma20": "vs SMA20%", "hot_score": "Score",
        })
        st.dataframe(display_df, hide_index=True, width="stretch")

        # Chart
        st.markdown("---")
        st.markdown("### Price chart")
        chart_t = st.selectbox("Select stock", top_hot["ticker"].tolist(), key="t212_hot_chart")
        try:
            hist = yf.Ticker(chart_t).history(period="3mo", interval="1d", auto_adjust=True)
            if not hist.empty:
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=hist.index, open=hist["Open"], high=hist["High"],
                    low=hist["Low"], close=hist["Close"], name=chart_t,
                    increasing_line_color="#2f6b4f", decreasing_line_color="#9b3b3b",
                ))
                fig.add_trace(go.Scatter(
                    x=hist.index, y=hist["Close"].rolling(20).mean(),
                    name="SMA 20", line=dict(color=GOLD, width=1.5, dash="dot"),
                ))
                fig.update_layout(**chart_layout(
                    height=400, xaxis_rangeslider_visible=False,
                    title=dict(text=f"{chart_t} — 3 months", font=dict(size=12, color=CHART_TEXT)),
                ))
                st.plotly_chart(fig, width="stretch")
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════
    # MODE B — BEST OPPORTUNITIES (fundamental value score)
    # ══════════════════════════════════════════════════════════
    elif t212_mode == "Best Opportunities":

        st.markdown(
            "Ranks T212 stocks by a combined **value + growth + quality** score — "
            "helping you find the strongest ISA candidates right now."
        )

        t212_opp_key = f"t212_opp_{t212_sector}"
        if refresh_t212 or t212_opp_key not in st.session_state:
            st.session_state[t212_opp_key] = None

        if st.session_state[t212_opp_key] is None:
            with st.spinner(f"Scoring {len(t212_tickers)} T212 stocks..."):
                t212_opp_tup = tuple(t212_tickers)
                rows = []
                prog = st.progress(0, text="Fetching fundamentals (parallel)...")
                t212_opp_info = _batch_info(t212_opp_tup)
                prog.progress(0.7, text="Computing opportunity scores...")

                for ticker in t212_tickers:
                    try:
                        info = t212_opp_info.get(ticker, {})
                        if not info or info.get("regularMarketPrice") is None:
                            continue

                        def _f(key, default=None):
                            v = info.get(key)
                            try:
                                return float(v) if v is not None else default
                            except (TypeError, ValueError):
                                return default

                        pe          = _f("trailingPE")
                        forward_pe  = _f("forwardPE")
                        peg         = _f("pegRatio")
                        ps          = _f("priceToSalesTrailing12Months")
                        pb          = _f("priceToBook")

                        roe         = (_f("returnOnEquity") or 0) * 100
                        net_margin  = (_f("profitMargins")  or 0) * 100
                        op_margin   = (_f("operatingMargins") or 0) * 100

                        rev_growth  = (_f("revenueGrowth")  or 0) * 100
                        earn_growth = (_f("earningsGrowth") or 0) * 100

                        _de_raw     = _f("debtToEquity")
                        de_ratio    = _de_raw / 100 if _de_raw is not None else None
                        curr_ratio  = _f("currentRatio")
                        fcf         = _f("freeCashflow")
                        market_cap  = _f("marketCap")
                        fcf_yield   = (fcf / market_cap * 100) if fcf and market_cap and market_cap > 0 else None

                        div_yield   = (_f("dividendYield") or 0) * 100

                        # ── Score components ───────────────────
                        # Value (cheap = high score)
                        peg_s  = max(0, min(100, (3 - (peg or 3)) / 3 * 100))
                        pe_s   = max(0, min(100, (40 - (pe or 40)) / 35 * 100))
                        fcf_s  = max(0, min(100, (fcf_yield or 0) / 10 * 100))
                        val_s  = peg_s * 0.45 + pe_s * 0.30 + fcf_s * 0.25

                        # Growth
                        rev_s  = max(0, min(100, (rev_growth + 5)     / 55 * 100))
                        earn_s = max(0, min(100, (earn_growth + 10)   / 60 * 100))
                        grow_s = rev_s * 0.55 + earn_s * 0.45

                        # Quality / profitability
                        roe_s  = max(0, min(100, roe / 40 * 100))
                        mgn_s  = max(0, min(100, net_margin / 30 * 100))
                        qual_s = roe_s * 0.50 + mgn_s * 0.50

                        # Health
                        # Missing debt data is unknown, not "no debt". Excluded from
                        # the health score rather than scored as perfect.
                        de_s   = (max(0, min(100, (2 - de_ratio) / 2 * 100))
                                  if de_ratio is not None else None)
                        fcf_h  = 80 if (fcf or 0) > 0 else 20
                        hlth_s, _hlth_cov = _weighted_known([(de_s, 0.55), (fcf_h, 0.45)])
                        if hlth_s is None:
                            hlth_s = 50.0   # nothing measurable; neutral, never favourable

                        # Overall ISA score (value 30, growth 30, quality 25, health 15)
                        isa_score = val_s * 0.30 + grow_s * 0.30 + qual_s * 0.25 + hlth_s * 0.15

                        currency = "£" if ticker.endswith(".L") else "$"

                        rows.append({
                            "ticker":      ticker,
                            "name":        info.get("shortName", ticker)[:28],
                            "currency":    currency,
                            "isa_score":   round(isa_score, 1),
                            "val_score":   round(val_s,     1),
                            "grow_score":  round(grow_s,    1),
                            "qual_score":  round(qual_s,    1),
                            "hlth_score":  round(hlth_s,    1),
                            "pe":          round(pe, 1)          if pe          else None,
                            "peg":         round(peg, 2)         if peg         else None,
                            "forward_pe":  round(forward_pe, 1)  if forward_pe  else None,
                            "ps":          round(ps, 2)          if ps          else None,
                            "pb":          round(pb, 2)          if pb          else None,
                            "fcf_yield":   round(fcf_yield, 1)   if fcf_yield   else None,
                            "rev_growth":  round(rev_growth, 1),
                            "earn_growth": round(earn_growth, 1),
                            "net_margin":  round(net_margin, 1),
                            "roe":         round(roe, 1),
                            "de_ratio":    round(de_ratio, 2)    if de_ratio is not None else None,
                            "div_yield":   round(div_yield, 2)   if div_yield   else None,
                        })
                    except Exception:
                        continue

                prog.empty()
                if not rows:
                    st.warning("No data returned. Check your tickers and try again.")
                    st.stop()
                opp_df = pd.DataFrame(rows).sort_values("isa_score", ascending=False).reset_index(drop=True)
                st.session_state[t212_opp_key] = opp_df

        opp_df = st.session_state[t212_opp_key]

        if opp_df is None or opp_df.empty:
            st.error(
                "No data came back for this list. This is usually a temporary "
                "data-provider hiccup — refresh the page to try again."
            )
            st.stop()

        top_opp = opp_df.head(t212_top_n)

        # Hero cards
        st.markdown("### Top ISA opportunities")
        cols = st.columns(min(5, len(top_opp)))
        for i, (_, row) in enumerate(top_opp.head(5).iterrows()):
            peg_str = f"PEG {row['peg']:.2f}" if row["peg"] else f"P/E {row['pe']}" if row["pe"] else "—"
            cols[i % 5].markdown(
                f'<div class="bs-panel">'
                f'<h3>{row["ticker"]}</h3>'
                f'<p style="font-size:1.4rem">{row["isa_score"]:.0f}'
                f'<small style="font-size:0.8rem;color:#a0a0b0"> / 100</small></p>'
                f'<small style="color:#a0a0b0">{peg_str} &nbsp;·&nbsp; '
                f'Rev {row["rev_growth"]:+.0f}%</small>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("---")

        # Full table
        col_rename_opp = {
            "ticker": "Ticker", "name": "Company", "currency": "CCY",
            "isa_score": "🇬🇧 ISA Score",
            "val_score": "Value", "grow_score": "Growth",
            "qual_score": "Quality", "hlth_score": "Health",
            "pe": "P/E", "peg": "PEG", "forward_pe": "Fwd P/E",
            "ps": "P/S", "pb": "P/B", "fcf_yield": "FCF Yld%",
            "rev_growth": "Rev Gr%", "earn_growth": "EPS Gr%",
            "net_margin": "Net Mgn%", "roe": "ROE%",
            "de_ratio": "D/E", "div_yield": "Div Yld%",
        }
        disp_opp = top_opp.rename(columns=col_rename_opp)
        disp_opp.insert(0, "Rank", range(1, len(disp_opp) + 1))
        st.dataframe(disp_opp, hide_index=True, width="stretch",
                     height=min(700, 36 * len(disp_opp) + 40))

        # Score breakdown chart
        st.markdown("---")
        st.markdown("### ISA score breakdown")
        fig_opp = go.Figure()
        for col, name, clr in [
            ("val_score", "Value",      PALETTE[0]),
            ("grow_score","Growth",     PALETTE[1]),
            ("qual_score","Quality",    PALETTE[2]),
            ("hlth_score","Health",     PALETTE[3]),
        ]:
            fig_opp.add_trace(go.Bar(
                name=name, x=top_opp["ticker"], y=top_opp[col],
                marker_color=clr,
            ))
        fig_opp.update_layout(**chart_layout(barmode="group", height=360, yaxis_range=[0, 100]))
        st.plotly_chart(fig_opp, width="stretch")

        st.download_button(
            "⬇ Download T212 opportunities as CSV",
            data=opp_df.to_csv(index=False),
            file_name=f"t212_isa_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )

    # ══════════════════════════════════════════════════════════
    # MODE C — FULL SCREENER (within T212 universe)
    # ══════════════════════════════════════════════════════════
    elif t212_mode == "Screen":

        st.markdown(
            "Run a full fundamental screen — using the same long-term or swing filters — "
            "but restricted to stocks available on Trading 212's ISA platform."
        )

        st.sidebar.markdown("### Screening mode")
        t212_screen_mode = st.sidebar.radio(
            "Config", ["Long-term", "Swing"], index=0, key="t212_screen_mode",
        )
        config_file = "config_longterm.yaml" if t212_screen_mode == "Long-term" else "config_swing.yaml"
        config_path = CONFIGS_DIR / config_file
        config      = load_config(str(config_path))

        if t212_screen_mode == "Long-term":
            pe_max     = st.sidebar.slider("Max P/E",         10, 60,  25, key="t212_pe")
            roe_min    = st.sidebar.slider("Min ROE %",         0, 40,  15, key="t212_roe")
            de_max     = st.sidebar.slider("Max D/E",         0.0, 5.0, 1.0, step=0.1, key="t212_de")
            margin_min = st.sidebar.slider("Min net margin %",  0, 30,   5, key="t212_mgn")
            config["filters"].update({
                "pe_trailing":    {"enabled": True, "max": pe_max,     "on_missing": "pass"},
                "roe":            {"enabled": True, "min": roe_min,    "on_missing": "pass"},
                "debt_to_equity": {"enabled": True, "max": de_max,     "on_missing": "pass"},
                "net_margin":     {"enabled": True, "min": margin_min, "on_missing": "pass"},
            })
        else:
            margin_min = st.sidebar.slider("Min net margin %", -10, 20, 0, key="t212_swing_mgn")
            de_max     = st.sidebar.slider("Max D/E",          0.5, 5.0, 2.0, step=0.1, key="t212_swing_de")
            config["filters"].update({
                "net_margin":     {"enabled": True, "min": margin_min, "on_missing": "fail"},
                "debt_to_equity": {"enabled": True, "max": de_max,     "on_missing": "pass"},
            })

        st.info(
            f"Ready to screen **{len(t212_tickers)} T212 stocks** in **{t212_screen_mode}** mode. "
            f"Hit **Run Screen** to start."
        )

        if st.button("▶  Run T212 Screen", type="primary", width="stretch", key="t212_run"):
            st.session_state["t212_screen_run"] = True
            st.session_state["t212_screen_results"] = None

        if not st.session_state.get("t212_screen_run"):
            st.stop()

        if st.session_state.get("t212_screen_results") is None:
            fetcher = YFinanceFetcher(cache_expiry_hours=24)
            prog = st.progress(0, text="Fetching T212 data...")
            all_metrics, failed = [], []

            for i, ticker in enumerate(t212_tickers):
                prog.progress((i + 1) / len(t212_tickers), text=f"Fetching {ticker}... ({i+1}/{len(t212_tickers)})")
                data = fetcher.fetch_ticker_data(ticker)
                if not data.get("info"):
                    failed.append(ticker)
                    continue
                try:
                    all_metrics.append(calculate_all_metrics(ticker, data))
                except Exception:
                    failed.append(ticker)

            prog.empty()
            if failed:
                st.warning(f"{len(failed)} tickers failed: {', '.join(failed[:15])}")

            passed, failed_screen = screen_batch(all_metrics, config)
            results = score_batch(passed, config)
            st.session_state["t212_screen_results"] = results
            st.session_state["t212_screen_all"]     = all_metrics

        results   = st.session_state["t212_screen_results"]
        all_mets  = st.session_state.get("t212_screen_all", [])

        # Summary cards
        c1, c2, c3, c4 = st.columns(4)
        metric_card("T212 stocks screened", str(len(all_mets)), c1)
        metric_card("Passed filters",       str(len(results)),  c2)
        avg_s = f"{sum(r['composite_score'] for r in results)/len(results):.1f}" if results else "—"
        metric_card("Avg score", avg_s, c3)
        uk_count = sum(1 for r in results if r["ticker"].endswith(".L"))
        metric_card("UK (.L) stocks passed", str(uk_count), c4)

        st.markdown("---")

        if not results:
            st.error("No stocks passed. Try loosening the filters.")
            st.stop()

        df_res = pd.DataFrame(results)
        display_cols = [
            "ticker", "name", "composite_score",
            "pe_trailing", "roe", "net_margin", "operating_margin",
            "debt_to_equity", "current_ratio",
            "revenue_growth_1y", "eps_growth_1y",
            "fcf_yield", "dividend_yield",
            "fcf_positive_3y", "data_completeness",
        ]
        avail = [c for c in display_cols if c in df_res.columns]
        rename_t212 = {
            "ticker": "Ticker", "name": "Name", "composite_score": "Score",
            "pe_trailing": "P/E", "roe": "ROE%", "net_margin": "Net Mgn%",
            "operating_margin": "Op Mgn%", "debt_to_equity": "D/E",
            "current_ratio": "Curr Ratio", "revenue_growth_1y": "Rev Gr%",
            "eps_growth_1y": "EPS Gr%", "fcf_yield": "FCF Yld%",
            "dividend_yield": "Div Yld%", "fcf_positive_3y": "FCF+3Y",
            "data_completeness": "Data%",
        }
        disp_res = df_res[avail].head(t212_top_n).rename(columns=rename_t212)
        disp_res.insert(0, "Rank", range(1, len(disp_res) + 1))
        st.dataframe(disp_res, hide_index=True, width="stretch",
                     height=min(700, 35 * len(disp_res) + 40))

        st.download_button(
            "⬇ Download T212 screen as CSV",
            data=df_res[avail].to_csv(index=False),
            file_name=f"t212_screen_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )

        # Score chart
        st.markdown("---")
        st.markdown("### Score breakdown")
        sc_cols  = ["score_profitability","score_financial_health","score_valuation","score_growth","score_cash_flow"]
        sc_names = ["Profitability","Financial Health","Valuation","Growth","Cash Flow"]
        avail_sc = [(c, n) for c, n in zip(sc_cols, sc_names) if c in df_res.columns]
        if avail_sc:
            fig_sc = go.Figure()
            for i, (col, nm) in enumerate(avail_sc):
                fig_sc.add_trace(go.Bar(
                    name=nm, x=df_res["ticker"].head(t212_top_n), y=df_res[col].head(t212_top_n),
                    marker_color=PALETTE[i % len(PALETTE)],
                ))
            fig_sc.update_layout(**chart_layout(barmode="group", height=380))
            st.plotly_chart(fig_sc, width="stretch")

    # ── ISA disclaimer ───────────────────────────────────────
    st.markdown("---")
    st.caption(
        "This tool provides data-driven analysis only — not financial advice. "
        "ISA allowances, tax rules and T212 instrument availability are subject to change. "
        "Always verify stock availability directly in the Trading 212 app before investing."
    )


# ════════════════════════════════════════════════════════════
# PAGE 6 — HEDGE FUND ENGINE
# ════════════════════════════════════════════════════════════
elif page == "Discover" and _discover_tab == "All companies":

    st.markdown("""
    <div style="padding:1.4rem 1.8rem 1.2rem; background:#ffffff; border:1px solid #e7e4dd;
                border-left:5px solid #8d7434; border-radius:4px; margin-bottom:1.4rem;">
        <div style="font-size:1.9rem; font-weight:800; color:#12161f; letter-spacing:-0.02em; line-height:1.1;">
            📊 Stock Research
        </div>
        <div style="font-size:0.88rem; color:#5f6672; margin-top:0.5rem; line-height:1.6; max-width:780px;">
            Scores every company in your chosen universe and explains, in plain English, what its numbers
            show. Each company gets a research score, a written summary of its strengths and risks, its
            risk characteristics, and the profile that best describes it —
            <b>Momentum</b>, <b>Oversold</b>, <b>Growth</b>, or <b>Consolidating</b>.
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Sidebar ──────────────────────────────────────────────
    st.sidebar.markdown("### Research settings")

    hf_universe = st.sidebar.selectbox(
        "Scan universe",
        ["broad", "all_curated", "tech", "ai", "growth", "nasdaq100", "t212"],
        format_func=lambda x: {
            "broad":       "Broad Market (~300)",
            "all_curated": "All Curated (~130)",
            "tech":        "Tech & Mega Caps (~60)",
            "ai":          "AI & Machine Learning (~55)",
            "growth":      "High Growth (~50)",
            "nasdaq100":   "NASDAQ 100",
            "t212":        "T212 ISA (~145)",
        }[x],
        index=0,
    )

    hf_top_n = st.sidebar.slider("Picks per strategy", 3, 20, 6)

    st.sidebar.markdown("### Risk profile")
    risk_profile = st.sidebar.radio(
        "My risk appetite",
        ["Conservative", "Balanced", "Aggressive"],
        index=1,
    )
    portfolio_size = st.sidebar.number_input(
        "Portfolio size (£/$)",
        min_value=500, max_value=500_000, value=10_000, step=500,
    )

    refresh_hf = st.sidebar.button("Run research scan", type="primary")

    # Risk profile → position sizing caps
    risk_caps = {
        "Conservative": {"high": 0.05, "med": 0.025, "low": 0.01},
        "Balanced":      {"high": 0.10, "med": 0.05,  "low": 0.025},
        "Aggressive":    {"high": 0.15, "med": 0.08,  "low": 0.04},
    }[risk_profile]

    hf_cache_key = f"hf_{hf_universe}"
    if refresh_hf or hf_cache_key not in st.session_state:
        st.session_state[hf_cache_key] = None

    # ── Data fetch & scoring ──────────────────────────────────
    if _scan_gate(hf_cache_key, "Run research scan", forced=refresh_hf,
                  note="Scores and explains every company in your chosen universe. "
                       "This is the heaviest scan in the app — up to ~360 tickers."):
        scan_tickers  = get_universe(hf_universe)
        hf_tup        = tuple(scan_tickers)

        with st.spinner(f"Running hedge fund scan on {len(scan_tickers)} stocks..."):
            rows = []
            prog = st.progress(0, text="Downloading price data (batch)...")

            # ── Pass 1: prices for all tickers ────────────────────
            hf_prices = _batch_prices(hf_tup, period="3mo", interval="1d")

            # ── Pass 2: only fetch fundamentals for tickers with
            #            valid price history — skips dead/delisted tickers
            valid_tickers = tuple(
                t for t in scan_tickers
                if t in hf_prices and len(hf_prices[t]) >= 20
            )
            prog.progress(0.40, text=f"Fetching fundamentals for {len(valid_tickers)} active stocks (parallel)...")
            hf_info = _batch_info(valid_tickers)
            prog.progress(0.85, text="Computing strategy scores...")

            for ticker in scan_tickers:
                try:
                    info   = hf_info.get(ticker, {})
                    if not info or info.get("regularMarketPrice") is None:
                        continue

                    hist3m = hf_prices.get(ticker)
                    if hist3m is None or len(hist3m) < 20:
                        continue

                    # ── Helpers ───────────────────────────────
                    def _f(key, d=None):
                        v = info.get(key)
                        try:    return float(v) if v is not None else d
                        except: return d

                    closes  = hist3m["Close"]
                    volumes = hist3m["Volume"]
                    price   = float(closes.iloc[-1])

                    # ── Price momentum signals ─────────────────
                    p1w  = float(closes.iloc[-6])  if len(closes) >= 6  else price
                    p1m  = float(closes.iloc[-22]) if len(closes) >= 22 else float(closes.iloc[0])
                    p3m  = float(closes.iloc[0])

                    chg_1w  = (price - p1w)  / p1w  * 100
                    chg_1m  = (price - p1m)  / p1m  * 100
                    chg_3m  = (price - p3m)  / p3m  * 100

                    # ── Moving averages ────────────────────────
                    sma20  = float(closes.rolling(20).mean().iloc[-1])
                    sma50  = float(closes.rolling(min(50, len(closes))).mean().iloc[-1])
                    vs_20  = (price - sma20) / sma20 * 100
                    vs_50  = (price - sma50) / sma50 * 100
                    # MA alignment: sma20 > sma50 = uptrend
                    ma_aligned = sma20 > sma50

                    # ── RSI (14) ───────────────────────────────
                    delta  = closes.diff().dropna()
                    gain   = delta.clip(lower=0).rolling(14).mean()
                    loss   = (-delta.clip(upper=0)).rolling(14).mean()
                    rs     = gain / loss.replace(0, float("nan"))
                    rsi    = float((100 - 100 / (1 + rs)).iloc[-1]) if not rs.empty else 50.0
                    rsi    = rsi if not pd.isna(rsi) else 50.0

                    # ── Volume analysis ────────────────────────
                    vol_5d   = float(volumes.iloc[-5:].mean())
                    vol_20d  = float(volumes.iloc[-20:].mean())
                    vol_surge = vol_5d / vol_20d if vol_20d > 0 else 1.0

                    # ── ATR (14d) — volatility / position sizing
                    high = hist3m["High"]
                    low  = hist3m["Low"]
                    tr   = pd.concat([
                        high - low,
                        (high - closes.shift(1)).abs(),
                        (low  - closes.shift(1)).abs(),
                    ], axis=1).max(axis=1)
                    atr    = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else price * 0.02
                    atr_pct = atr / price * 100   # ATR as % of price

                    # ── 52-week positioning ────────────────────
                    w52_high = _f("fiftyTwoWeekHigh")
                    w52_low  = _f("fiftyTwoWeekLow")
                    if w52_high and w52_low and w52_high > w52_low:
                        pct_range = (price - w52_low) / (w52_high - w52_low) * 100  # 0=52wk low, 100=52wk high
                    else:
                        pct_range = 50.0

                    # ── Fundamentals ───────────────────────────
                    rev_growth  = (_f("revenueGrowth")  or 0) * 100
                    earn_growth = (_f("earningsGrowth") or 0) * 100
                    net_margin  = (_f("profitMargins")  or 0) * 100
                    roe         = (_f("returnOnEquity") or 0) * 100
                    pe          = _f("trailingPE")
                    forward_pe  = _f("forwardPE")
                    beta        = _f("beta", 1.0)
                    market_cap  = _f("marketCap", 0)
                    fcf         = _f("freeCashflow", 0)
                    _short_raw  = _f("shortPercentOfFloat")
                    short_pct   = _short_raw * 100 if _short_raw is not None else None

                    # ── Analyst data ───────────────────────────
                    rec_mean    = _f("recommendationMean")  # 1=strong buy, 5=sell
                    analyst_score = max(0, min(100, (5 - (rec_mean or 3)) / 4 * 100))
                    n_analysts  = _f("numberOfAnalystOpinions", 0)

                    # Target price vs current
                    target_price = _f("targetMeanPrice")
                    upside       = ((target_price - price) / price * 100) if target_price and price else None

                    name = info.get("shortName", ticker)[:28]

                    # ══════════════════════════════════════════
                    # STRATEGY SCORING
                    # ══════════════════════════════════════════

                    # ── 1. MOMENTUM ROCKET ─────────────────────
                    # Sweet spot: RSI 52-72, above both MAs, volume expanding,
                    # positive 1w & 1m, MAs aligned upward
                    rsi_mom = min(100, max(0, 100 - abs(rsi - 62) * 4))  # peak at RSI=62
                    ma_mom  = (
                        (30 if vs_20 > 0 else 0) +
                        (30 if vs_50 > 0 else 0) +
                        (40 if ma_aligned else 0)
                    )
                    chg_mom = min(100, max(0, (chg_1w * 3 + chg_1m) / 4 * 5 + 50))
                    vol_mom = min(100, max(0, (vol_surge - 0.8) / 1.2 * 100))
                    momentum_score = (
                        rsi_mom   * 0.25 +
                        ma_mom    * 0.30 +
                        chg_mom   * 0.25 +
                        vol_mom   * 0.20
                    )

                    # ── 2. BOUNCE CANDIDATE ────────────────────
                    # Oversold RSI (<38), stock above SMA50 long-term (intact bull trend),
                    # strong fundamentals, near support
                    rsi_bounce   = min(100, max(0, (45 - rsi) / 20 * 100))  # high score when RSI very low
                    trend_intact = min(100, max(0, (vs_50 + 20) / 25 * 100))  # not too far below SMA50
                    fund_bounce  = min(100, max(0, net_margin / 20 * 100)) * 0.5 + \
                                   min(100, max(0, roe / 30 * 100)) * 0.5
                    support_prox = min(100, max(0, 100 - pct_range))  # closer to 52wk low = more bounce potential
                    bounce_score = (
                        rsi_bounce   * 0.35 +
                        trend_intact * 0.25 +
                        fund_bounce  * 0.25 +
                        support_prox * 0.15
                    )

                    # ── 3. GROWTH CATALYST ─────────────────────
                    # Revenue + earnings accelerating, analyst upgrades, PE compression,
                    # positive upside to price target
                    rev_cat    = min(100, max(0, (rev_growth + 5)  / 60 * 100))
                    earn_cat   = min(100, max(0, (earn_growth + 5) / 65 * 100))
                    upsid_cat  = min(100, max(0, (upside or 0)     / 40 * 100)) if upside else 40
                    pe_comp    = 0.0
                    if pe and forward_pe and forward_pe > 0 and forward_pe < pe:
                        pe_comp = min(100, (pe - forward_pe) / pe * 300)
                    analyst_cat = analyst_score
                    catalyst_score = (
                        rev_cat     * 0.25 +
                        earn_cat    * 0.25 +
                        upsid_cat   * 0.20 +
                        pe_comp     * 0.15 +
                        analyst_cat * 0.15
                    )

                    # ── 4. BREAKOUT WATCH ──────────────────────
                    # Near 52W high (85-97%), low recent volatility (coiling),
                    # volume starting to expand, above all MAs
                    near_high   = min(100, max(0, 100 - abs(pct_range - 91) * 3.5))  # sweet spot 85-97%
                    vol_coiling = min(100, max(0, (1.5 - atr_pct) / 1.5 * 100))       # low ATR = coiling
                    vol_expand  = min(100, max(0, (vol_surge - 0.9) / 1.1 * 100))
                    above_mas   = (50 if vs_20 > 0 else 0) + (50 if vs_50 > 0 else 0)
                    breakout_score = (
                        near_high   * 0.35 +
                        vol_coiling * 0.25 +
                        vol_expand  * 0.20 +
                        above_mas   * 0.20
                    )

                    # ── OVERALL RESEARCH SCORE ─────────────────
                    best_score = max(momentum_score, bounce_score, catalyst_score, breakout_score)

                    # Primary strategy classification
                    scores_map = {
                        "Momentum":  momentum_score,
                        "Oversold":    bounce_score,
                        "Growth":  catalyst_score,
                        "Consolidating":  breakout_score,
                    }
                    primary_strategy = max(scores_map, key=scores_map.get)

                    # Secondary strategy if close to primary
                    sorted_strats = sorted(scores_map.items(), key=lambda x: x[1], reverse=True)
                    secondary_strategy = sorted_strats[1][0] if sorted_strats[1][1] > sorted_strats[0][1] * 0.80 else None

                    # ── RISK METRICS ───────────────────────────
                    beta_risk    = min(100, max(0, abs((beta or 1.0) - 1.3) * 30))  # ideal beta ~1.3 for short-term
                    vol_risk     = min(100, atr_pct / 8 * 100)                       # ATR > 8% is high risk
                    short_risk   = (min(100, short_pct / 20 * 100)
                                    if short_pct is not None else None)
                    # An unknown short interest is excluded, not scored as zero
                    # risk -- absent data must never make a company look safer.
                    risk_score, risk_cov = _weighted_known([
                        (beta_risk, 0.40), (vol_risk, 0.35), (short_risk, 0.25),
                    ])
                    if risk_score is None:
                        risk_score = vol_risk
                    # risk_score 0=low risk, 100=very high risk

                    # ── POSITION SIZING ────────────────────────
                    score_band_tier = (
                        "high" if best_score >= 72 else
                        "med"  if best_score >= 58 else
                        "low"
                    )
                    pos_pct   = risk_caps[score_band_tier]
                    # Reduce if risk is elevated
                    if risk_score > 65:
                        pos_pct *= 0.6
                    elif risk_score > 45:
                        pos_pct *= 0.8
                    pos_value = portfolio_size * pos_pct

                    # ── STOP LOSS & TARGET ─────────────────────
                    # Stop: 1.5x ATR below current price (or SMA20 whichever is closer)
                    stop_atr  = price - 1.5 * atr
                    stop_sma  = sma20 * 0.98
                    stop_loss = max(stop_atr, stop_sma)   # take the higher (tighter) stop
                    stop_pct  = (price - stop_loss) / price * 100

                    # Target: analyst consensus or 2x ATR extension upward
                    if target_price and target_price > price:
                        target = target_price
                    else:
                        target = price + 2.5 * atr
                    reward_pct = (target - price) / price * 100
                    rr_ratio   = reward_pct / stop_pct if stop_pct > 0 else 0

                    rows.append({
                        "ticker":             ticker,
                        "name":               name,
                        "best_score":         round(best_score, 1),
                        "primary_strategy":   primary_strategy,
                        "secondary_strategy": secondary_strategy or "",
                        "momentum_score":     round(momentum_score,  1),
                        "bounce_score":       round(bounce_score,    1),
                        "catalyst_score":     round(catalyst_score,  1),
                        "breakout_score":     round(breakout_score,  1),
                        "score_band":         score_band_tier,
                        "risk_score":         round(risk_score, 1),
                        # Price data
                        "price":              round(price, 2),
                        "chg_1w":             round(chg_1w, 2),
                        "chg_1m":             round(chg_1m, 2),
                        "chg_3m":             round(chg_3m, 2),
                        "rsi":                round(rsi, 1),
                        "vol_surge":          round(vol_surge, 2),
                        "vs_sma20":           round(vs_20, 2),
                        "vs_sma50":           round(vs_50, 2),
                        "atr_pct":            round(atr_pct, 2),
                        "pct_52w_range":      round(pct_range, 1),
                        "beta":               round(beta, 2) if beta else None,
                        "short_pct":          round(short_pct, 1) if short_pct is not None else None,
                        # Fundamentals
                        "rev_growth":         round(rev_growth, 1),
                        "earn_growth":        round(earn_growth, 1),
                        "net_margin":         round(net_margin, 1),
                        "roe":                round(roe, 1),
                        "pe":                 round(pe, 1)    if pe    else None,
                        "forward_pe":         round(forward_pe, 1) if forward_pe else None,
                        "upside":             round(upside, 1) if upside else None,
                        # Position management
                        "pos_pct":            round(pos_pct * 100, 1),
                        "pos_value":          round(pos_value, 0),
                        "stop_loss":          round(stop_loss, 2),
                        "stop_pct":           round(stop_pct, 1),
                        "target":             round(target, 2),
                        "reward_pct":         round(reward_pct, 1),
                        "rr_ratio":           round(rr_ratio, 2),
                        "market_cap":         market_cap,
                    })

                except Exception:
                    continue

            prog.empty()

            hf_df = pd.DataFrame(rows)
            hf_df = hf_df.sort_values("best_score", ascending=False).reset_index(drop=True)
            st.session_state[hf_cache_key] = hf_df

    hf_df = st.session_state.get(hf_cache_key)

    if hf_df is None:
        st.stop()  # gate is showing its own prompt — nothing to render yet
    if hf_df.empty:
        st.error(
            "The scan finished but no stocks came back. This is usually a temporary "
            "data-provider hiccup — press Run research scan to try again, or pick a "
            "smaller universe in the sidebar."
        )
        st.stop()

    # ── FUND OVERVIEW CARDS ───────────────────────────────────
    total_picks   = len(hf_df[hf_df["best_score"] >= 55])
    high_scoring     = len(hf_df[hf_df["score_band"] == "high"])
    avg_volatility = hf_df[hf_df["atr_pct"] > 0]["atr_pct"].mean()
    avg_research_score= hf_df["best_score"].mean()

    c1, c2, c3, c4, c5 = st.columns(5)
    metric_card("Stocks scanned",     str(len(hf_df)),             c1)
    metric_card("High scoring",       str(high_scoring),              c2)
    metric_card("Avg research score", f"{avg_research_score:.1f}",     c3)
    metric_card("Avg volatility",     f"{avg_volatility:.1f}%" if not pd.isna(avg_volatility) else "—", c4)
    metric_card("Risk profile",       risk_profile,                c5)

    st.markdown("---")

    # ── STRATEGY TABS ─────────────────────────────────────────
    tab_all, tab_mom, tab_bnc, tab_cat, tab_brk, tab_risk, tab_returns = st.tabs([
        "All companies", "Momentum", "Oversold", "Growth", "Consolidating", "Risk", "Scenarios",
    ])

    # ── Helper: render a strategy card grid ───────────────────
    def render_pick_cards(df_strat, score_col, n=6):
        top = df_strat.head(n)
        cols = st.columns(min(3, len(top)))
        for idx, (_, row) in enumerate(top.iterrows()):
            col = cols[idx % 3]
            score = row[score_col]
            conv  = row["score_band"]
            stop  = row["stop_pct"]
            win_p = row["best_score"] / 100
            exp_r = win_p * (row["reward_pct"] or 0) - (1 - win_p) * (row["stop_pct"] or 0)

            border_clr = "#2f6b4f" if conv=="high" else "#8d7434" if conv=="med" else "#5f6672"
            badge = "HIGH" if conv=="high" else "MED" if conv=="med" else "LOW"
            sec   = f'<span style="font-size:0.72rem;color:#9aa1ad;">&nbsp;+{row["secondary_strategy"]}</span>' if row.get("secondary_strategy") else ""

            snap   = generate_hf_summary(row.to_dict())
            bull1  = snap["bull_factors"][0][:95] + "…" if snap["bull_factors"] and len(snap["bull_factors"][0]) > 95 else (snap["bull_factors"][0] if snap["bull_factors"] else "")
            bear1  = snap["bear_factors"][0][:85] + "…" if snap["bear_factors"] and len(snap["bear_factors"][0]) > 85 else (snap["bear_factors"][0] if snap["bear_factors"] else "")

            col.markdown(
                f'<div style="background:#ffffff; border:1px solid #e7e4dd; '
                f'border-top:4px solid {border_clr}; border-radius:4px; '
                f'padding:1.2rem 1.3rem 1.1rem; margin-bottom:0.8rem; '
                f'">'

                # Header row
                f'<div style="display:flex; justify-content:space-between; align-items:start; margin-bottom:0.2rem;">'
                f'  <div style="font-size:1.15rem; font-weight:800; color:#8d7434; letter-spacing:0.01em;">{row["ticker"]}{sec}</div>'
                f'  <div style="font-size:0.68rem; font-weight:700; color:{border_clr}; letter-spacing:0.07em;">{badge}</div>'
                f'</div>'
                f'<div style="font-size:0.76rem; color:#5f6672; margin-bottom:0.6rem;">{row["name"]} &nbsp;·&nbsp; {row["primary_strategy"]}</div>'

                # Score
                f'<div style="font-size:2rem; font-weight:900; color:#12161f; line-height:1; margin-bottom:0.5rem;">{score:.0f}'
                f'  <span style="font-size:0.8rem; font-weight:400; color:#9aa1ad;">/ 100</span>'
                f'</div>'

                # Metrics grid
                f'<div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:0.3rem; font-size:0.78rem; color:#5f6672; margin-bottom:0.5rem;">'
                f'  <div><span style="color:#9aa1ad;font-size:0.68rem;">PRICE</span><br><b>${row["price"]:.2f}</b></div>'
                f'  <div><span style="color:#9aa1ad;font-size:0.68rem;">WEEK</span><br><b style="color:{"#2f6b4f" if row["chg_1w"]>=0 else "#9b3b3b"}">{row["chg_1w"]:+.1f}%</b></div>'
                f'  <div><span style="color:#9aa1ad;font-size:0.68rem;">RSI</span><br><b>{row["rsi"]:.0f}</b></div>'
                f'  <div><span style="color:#9aa1ad;font-size:0.68rem;">TYP. DOWNSIDE</span><br><b style="color:#5f6672">−{stop:.1f}%</b></div>'
                f'  <div><span style="color:#9aa1ad;font-size:0.68rem;">VOLATILITY</span><br><b style="color:#5f6672">{row["atr_pct"]:.1f}%</b></div>'
                f'  <div><span style="color:#9aa1ad;font-size:0.68rem;">EXP. RET</span><br><b style="color:{"#2f6b4f" if exp_r>=0 else "#9b3b3b"}">{exp_r:+.1f}%</b></div>'
                f'</div>'

                # Bull snippet
                + (f'<div style="padding:0.4rem 0.6rem; background:#f0fdf4; border-left:2px solid #2f6b4f; '
                   f'border-radius:0 4px 4px 0; font-size:0.74rem; color:#1e4a2f; line-height:1.4; margin-bottom:0.3rem;">'
                   f'▲ {bull1}</div>' if bull1 else "")

                # Bear snippet
                + (f'<div style="padding:0.4rem 0.6rem; background:#fff5f5; border-left:2px solid #9b3b3b; '
                   f'border-radius:0 4px 4px 0; font-size:0.74rem; color:#4a1e1e; line-height:1.4; margin-bottom:0.5rem;">'
                   f'▼ {bear1}</div>' if bear1 else "")

                + f'<div style="padding:0.38rem 0.6rem; background:#f8fafc; border-radius:4px; '
                f'font-size:0.74rem; color:#5f6672;">'
                f'  📊 Beta <b>{row["beta"]:.2f}</b> &nbsp;·&nbsp; Analyst upside <b>{row["reward_pct"]:+.0f}%</b>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── Helper: full table for a strategy ─────────────────────
    def render_strategy_table(df_strat, score_col, label):
        top = df_strat.head(hf_top_n * 2)
        disp = top[[
            "ticker", "name", score_col, "score_band",
            "price", "chg_1w", "chg_1m", "rsi", "vol_surge",
            "vs_sma20", "beta", "atr_pct", "pct_52w_range",
            "stop_pct", "reward_pct",
            "rev_growth", "earn_growth", "net_margin", "upside",
        ]].copy()
        disp.insert(0, "Rank", range(1, len(disp) + 1))
        rename = {
            "ticker": "Ticker", "name": "Company", score_col: f"📊 {label} Score",
            "score_band": "Score band", "price": "Price",
            "chg_1w": "Week %", "chg_1m": "Month %", "rsi": "RSI",
            "vol_surge": "Vol Surge", "vs_sma20": "vs SMA20%",
            "beta": "Beta", "atr_pct": "ATR%", "pct_52w_range": "52W Range%",
            "stop_pct": "Typical downside%",
            "reward_pct": "Analyst upside%",
            "rev_growth": "Rev Gr%", "earn_growth": "EPS Gr%",
            "net_margin": "Net Mgn%", "upside": "Analyst Upside%",
        }
        st.dataframe(
            disp.rename(columns=rename),
            hide_index=True, width="stretch",
            height=min(600, 36 * len(disp) + 40),
        )

    # ════════════════════════════════
    with tab_all:
        st.markdown(f"### Top {hf_top_n * 2} picks across all strategies")
        st.markdown(
            "Ranked by overall research score. Each company is tagged with the profile that best "
            "describes its current characteristics, and a secondary profile where relevant."
        )
        top_all = hf_df.head(hf_top_n * 2)

        cols = st.columns(3)
        for idx, (_, row) in enumerate(top_all.head(6).iterrows()):
            col = cols[idx % 3]
            conv  = row["score_band"]
            border_clr = "#2f6b4f" if conv=="high" else "#8d7434" if conv=="med" else "#5f6672"
            badge = "HIGH" if conv=="high" else "MED" if conv=="med" else "LOW"
            sec   = f' <span style="font-size:0.7rem;color:#9aa1ad;">+{row["secondary_strategy"]}</span>' if row.get("secondary_strategy") else ""
            # Quick one-liner from the summary engine
            snap  = generate_hf_summary(row.to_dict())
            bull1 = snap["bull_factors"][0] if snap["bull_factors"] else ""
            bull1_short = bull1[:90] + "…" if len(bull1) > 90 else bull1
            col.markdown(
                f'<div style="background:#ffffff; border:1px solid #e7e4dd; '
                f'border-top:3px solid {border_clr}; border-radius:3px; '
                f'padding:1rem 1.1rem 0.9rem; margin-bottom:0.6rem; '
                f'">'
                f'<div style="display:flex; justify-content:space-between;">'
                f'  <span style="font-size:1rem; font-weight:700; color:#8d7434;">{row["ticker"]}</span>'
                f'  <span style="font-size:0.65rem; font-weight:600; color:{border_clr};">{badge}</span>'
                f'</div>'
                f'<div style="font-size:0.7rem; color:#9aa1ad; margin:0.1rem 0 0.4rem;">'
                f'  {row["primary_strategy"]}{sec}'
                f'</div>'
                f'<div style="font-size:1.6rem; font-weight:800; color:#12161f; line-height:1;">'
                f'  {row["best_score"]:.0f}<span style="font-size:0.75rem;font-weight:400;color:#9aa1ad;"> / 100</span>'
                f'</div>'
                f'<hr style="border:none;border-top:1px solid #f0f4fa;margin:0.45rem 0;">'
                f'<div style="font-size:0.75rem; color:#5f6672; display:grid; grid-template-columns:1fr 1fr; gap:0.2rem;">'
                f'  <div>${row["price"]:.2f}</div>'
                f'  <div style="color:{"#2f6b4f" if row["chg_1w"]>=0 else "#9b3b3b"}">{row["chg_1w"]:+.1f}% wk</div>'
                f'  <div>Typical swing: <b style="color:#5f6672">{row["atr_pct"]:.1f}%</b></div>'
                f'  <div>Beta: <b style="color:#5f6672">{row["beta"]:.2f}</b></div>'
                f'</div>'
                + (f'<div style="margin-top:0.5rem; padding:0.4rem 0.5rem; background:#f8fafc; '
                   f'border-radius:4px; font-size:0.72rem; color:#5f6672; line-height:1.4; '
                   f'border-left:2px solid #2f6b4f;">'
                   f'▲ {bull1_short}'
                   f'</div>' if bull1 else "")
                + f'<div style="margin-top:0.4rem;font-size:0.72rem;color:#9aa1ad;">Volatility {row["atr_pct"]:.1f}% · Beta {row["beta"]:.2f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("---")
        render_strategy_table(hf_df, "best_score", "Research")

        # Strategy distribution chart
        st.markdown("---")
        st.markdown("### Strategy distribution")
        strat_counts = hf_df.head(50)["primary_strategy"].value_counts()
        fig_dist = go.Figure(go.Pie(
            labels=strat_counts.index.tolist(),
            values=strat_counts.values.tolist(),
            hole=0.55,
            marker_colors=[GOLD, "#2563eb", "#2f6b4f", "#7c3aed"],
            textinfo="label+percent",
            textfont=dict(size=12),
        ))
        fig_dist.update_layout(**chart_layout(
            height=320, showlegend=False,
            title=dict(text="Primary strategy breakdown (top 50 picks)", font=dict(size=11, color=CHART_TEXT)),
        ))
        st.plotly_chart(fig_dist, width="stretch")

        # ── Deep Dive panel ──────────────────────────────────────
        st.markdown("---")
        st.markdown("### Stock deep dive")
        st.markdown("Select any stock from the scan to get a detailed narrative breakdown.")

        dive_ticker = st.selectbox(
            "Select stock to analyse",
            hf_df["ticker"].tolist(),
            key="hf_deep_dive_sel",
        )
        dive_row = hf_df[hf_df["ticker"] == dive_ticker].iloc[0].to_dict()
        summary  = generate_hf_summary(dive_row)

        # ── Header strip ─────────────────────────────────────────
        strat      = dive_row.get("primary_strategy", "")
        sec_strat  = dive_row.get("secondary_strategy", "")
        conv       = dive_row.get("score_band", "med")
        conv_clr   = "#2f6b4f" if conv=="high" else "#8d7434" if conv=="med" else "#5f6672"
        conv_lbl   = {"high":"HIGH SCORE","med":"MID SCORE","low":"LOW SCORE"}.get(conv,"")

        st.markdown(
            f'<div style="background:#ffffff; border:1px solid #e7e4dd; border-radius:4px; '
            f'padding:1.2rem 1.5rem; margin-bottom:1rem;">'
            f'<div style="display:flex; justify-content:space-between; align-items:start; flex-wrap:wrap; gap:0.5rem;">'
            f'  <div>'
            f'    <div style="font-size:1.6rem; font-weight:800; color:#8d7434; line-height:1;">{dive_ticker}</div>'
            f'    <div style="font-size:0.82rem; color:#5f6672; margin-top:0.2rem;">{dive_row.get("name","")}</div>'
            f'  </div>'
            f'  <div style="text-align:right;">'
            f'    <div style="font-size:0.72rem; font-weight:700; color:{conv_clr}; letter-spacing:0.1em; '
            f'text-transform:uppercase;">{conv_lbl}</div>'
            f'    <div style="font-size:0.78rem; color:#5f6672; margin-top:0.2rem;">'
            f'      {strat}{("  ·  " + sec_strat) if sec_strat else ""}'
            f'    </div>'
            f'  </div>'
            f'</div>'
            f'<hr style="border:none; border-top:1px solid #f0f4fa; margin:0.8rem 0;">'
            f'<p style="font-size:0.88rem; color:#5f6672; line-height:1.65; margin:0;">{summary["overview"]}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Bull / Bear columns ──────────────────────────────────
        col_bull, col_bear = st.columns(2)

        with col_bull:
            st.markdown(
                '<div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:3px; padding:1rem 1.2rem;">'
                '<div style="font-size:0.75rem; font-weight:700; color:#2f6b4f; letter-spacing:0.1em; '
                'text-transform:uppercase; margin-bottom:0.6rem;">📈 Bull Case</div>',
                unsafe_allow_html=True,
            )
            if summary["bull_factors"]:
                for pt in summary["bull_factors"]:
                    st.markdown(
                        f'<div style="display:flex; gap:0.5rem; margin-bottom:0.5rem; font-size:0.83rem; color:#1e3a2f;">'
                        f'  <span style="color:#2f6b4f; flex-shrink:0; font-weight:700; margin-top:0.05rem;">▲</span>'
                        f'  <span style="line-height:1.5;">{pt}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown('<p style="font-size:0.83rem; color:#5f6672;">No strong bull signals identified.</p>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        with col_bear:
            st.markdown(
                '<div style="background:#fff5f5; border:1px solid #fecaca; border-radius:3px; padding:1rem 1.2rem;">'
                '<div style="font-size:0.75rem; font-weight:700; color:#9b3b3b; letter-spacing:0.1em; '
                'text-transform:uppercase; margin-bottom:0.6rem;">📉 Bear Case & Risks</div>',
                unsafe_allow_html=True,
            )
            if summary["bear_factors"]:
                for pt in summary["bear_factors"]:
                    st.markdown(
                        f'<div style="display:flex; gap:0.5rem; margin-bottom:0.5rem; font-size:0.83rem; color:#3b1a1a;">'
                        f'  <span style="color:#9b3b3b; flex-shrink:0; font-weight:700; margin-top:0.05rem;">▼</span>'
                        f'  <span style="line-height:1.5;">{pt}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown('<p style="font-size:0.83rem; color:#5f6672;">No major risk flags on this metric set.</p>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        # ── Strategy note ────────────────────────────────────────
        st.markdown(
            f'<div style="background:#fffbeb; border:1px solid #fde68a; border-radius:3px; '
            f'padding:0.9rem 1.2rem; margin-top:0.8rem;">'
            f'<div style="font-size:0.75rem; font-weight:700; color:#92400e; letter-spacing:0.1em; '
            f'text-transform:uppercase; margin-bottom:0.4rem;">🎯 Strategy rationale</div>'
            f'<p style="font-size:0.85rem; color:#78350f; line-height:1.6; margin:0;">{summary["strategy_note"]}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Risk management box ───────────────────────────────────
        st.markdown(
            f'<div style="background:#f8fafc; border:1px solid #e7e4dd; border-radius:3px; '
            f'padding:0.9rem 1.2rem; margin-top:0.7rem;">'
            f'<div style="font-size:0.75rem; font-weight:700; color:#5f6672; letter-spacing:0.1em; '
            f'text-transform:uppercase; margin-bottom:0.4rem;">⚖️ Risk management</div>'
            f'<p style="font-size:0.84rem; color:#5f6672; line-height:1.6; margin:0;">{summary["risk_note"]}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Key metrics grid ─────────────────────────────────────
        st.markdown("<div style='margin-top:1rem;'></div>", unsafe_allow_html=True)
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        def _kpi(col, label, value, good_thresh=None, bad_thresh=None, fmt="{:.1f}", suffix=""):
            try:
                val_str = (fmt + suffix).format(value) if value is not None else "—"
            except Exception:
                val_str = str(value)
            col.markdown(
                f'<div style="background:#ffffff; border:1px solid #e7e4dd; border-top:3px solid #8d7434; '
                f'border-radius:5px; padding:0.7rem 0.9rem; text-align:center;">'
                f'<div style="font-size:0.62rem; color:#9aa1ad; text-transform:uppercase; letter-spacing:0.1em;">{label}</div>'
                f'<div style="font-size:1.2rem; font-weight:800; color:#12161f; margin-top:0.2rem;">{val_str}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        _kpi(m1, "Price",       dive_row.get("price"),      fmt="${:.2f}", suffix="")
        _kpi(m2, "Week chg",    dive_row.get("chg_1w"),     fmt="{:+.1f}", suffix="%")
        _kpi(m3, "RSI (14)",    dive_row.get("rsi"),        fmt="{:.0f}")
        _kpi(m4, "Beta",        dive_row.get("beta"),       fmt="{:.2f}")
        _kpi(m5, "Typical low", dive_row.get("stop_loss"),  fmt="${:.2f}", suffix="")
        _kpi(m6, "Beta",        dive_row.get("beta"),       fmt="{:.2f}", suffix="")

        n1, n2, n3, n4, n5, n6 = st.columns(6)
        _kpi(n1, "Rev growth",  dive_row.get("rev_growth"),   fmt="{:+.1f}", suffix="%")
        _kpi(n2, "EPS growth",  dive_row.get("earn_growth"),  fmt="{:+.1f}", suffix="%")
        _kpi(n3, "Net margin",  dive_row.get("net_margin"),   fmt="{:.1f}",  suffix="%")
        _kpi(n4, "P/E (trail)", dive_row.get("pe"),           fmt="{:.1f}",  suffix="×")
        _kpi(n5, "Fwd P/E",     dive_row.get("forward_pe"),   fmt="{:.1f}",  suffix="×")
        _kpi(n6, "Analyst ↑",   dive_row.get("upside"),       fmt="{:+.1f}", suffix="%")

        # ── Price chart ───────────────────────────────────────────
        st.markdown("<div style='margin-top:1rem;'></div>", unsafe_allow_html=True)
        try:
            chart_hist = yf.Ticker(dive_ticker).history(period="6mo", interval="1d", auto_adjust=True)
            if not chart_hist.empty:
                closes_c   = chart_hist["Close"]
                sma20_c    = closes_c.rolling(20).mean()
                sma50_c    = closes_c.rolling(50).mean()

                fig_dive = go.Figure()
                fig_dive.add_trace(go.Candlestick(
                    x=chart_hist.index,
                    open=chart_hist["Open"], high=chart_hist["High"],
                    low=chart_hist["Low"],   close=chart_hist["Close"],
                    name=dive_ticker,
                    increasing_line_color="#2f6b4f",
                    decreasing_line_color="#9b3b3b",
                    increasing_fillcolor="#2f6b4f",
                    decreasing_fillcolor="#9b3b3b",
                ))
                fig_dive.add_trace(go.Scatter(
                    x=chart_hist.index, y=sma20_c, name="SMA 20",
                    line=dict(color=GOLD, width=1.5, dash="dot"),
                ))
                fig_dive.add_trace(go.Scatter(
                    x=chart_hist.index, y=sma50_c, name="SMA 50",
                    line=dict(color="#2563eb", width=1.5, dash="dash"),
                ))
                # Typical downside band
                stop_val = dive_row.get("stop_loss")
                if stop_val:
                    fig_dive.add_hline(
                        y=stop_val, line_dash="dash", line_color="#9b3b3b", line_width=1.2,
                        annotation_text=f"Typical downside ${stop_val:.2f}",
                        annotation_position="right",
                        annotation_font=dict(color="#9b3b3b", size=10),
                    )
                # Target line
                target_val = dive_row.get("target")
                if target_val:
                    fig_dive.add_hline(
                        y=target_val, line_dash="dot", line_color="#2f6b4f", line_width=1.2,
                        annotation_text=f"Target ${target_val:.2f}",
                        annotation_position="right",
                        annotation_font=dict(color="#2f6b4f", size=10),
                    )

                # Volume subplot
                vol_colors = [
                    "#2f6b4f" if chart_hist["Close"].iloc[i] >= chart_hist["Open"].iloc[i]
                    else "#9b3b3b"
                    for i in range(len(chart_hist))
                ]
                fig_dive.add_trace(go.Bar(
                    x=chart_hist.index, y=chart_hist["Volume"],
                    name="Volume", marker_color=vol_colors, opacity=0.4,
                    yaxis="y2",
                ))

                fig_dive.update_layout(**chart_layout(
                    height=440,
                    xaxis_rangeslider_visible=False,
                    yaxis=dict(gridcolor=CHART_GRID, linecolor=CHART_GRID, zeroline=False, domain=[0.25, 1]),
                    yaxis2=dict(domain=[0, 0.20], showgrid=False, showticklabels=False),
                    title=dict(
                        text=f"{dive_ticker} — 6 months  |  SMA20 (gold)  ·  SMA50 (blue)  |  Typical downside (red)  ·  Analyst target (green)",
                        font=dict(size=11, color=CHART_TEXT),
                    ),
                ))
                st.plotly_chart(fig_dive, width="stretch")
        except Exception:
            st.info("Chart unavailable — data may be loading. Try switching tickers.")

    # ════════════════════════════════
    with tab_mom:
        st.markdown("### Momentum")
        st.markdown(
            "Stocks in a **strong, confirmed uptrend** — above both moving averages, "
            "RSI in the continuation sweet spot (52–72), and volume expanding. "
            "Best held for **1–3 weeks** riding the trend."
        )
        mom_df = hf_df.sort_values("momentum_score", ascending=False)
        render_pick_cards(mom_df, "momentum_score", n=hf_top_n)
        st.markdown("---")
        render_strategy_table(mom_df, "momentum_score", "Momentum")

        st.markdown("---")
        st.markdown("### Momentum score components — top picks")
        top_mom = mom_df.head(min(hf_top_n, 10))
        fig_mom = go.Figure()
        for col, name, clr in [
            ("chg_1w",    "Week Change%", PALETTE[0]),
            ("vol_surge", "Vol Surge",    PALETTE[1]),
            ("vs_sma20",  "vs SMA20%",    PALETTE[2]),
        ]:
            fig_mom.add_trace(go.Bar(name=name, x=top_mom["ticker"], y=top_mom[col], marker_color=clr))
        fig_mom.update_layout(**chart_layout(barmode="group", height=320))
        st.plotly_chart(fig_mom, width="stretch")

    # ════════════════════════════════
    with tab_bnc:
        st.markdown("### Oversold")
        st.markdown(
            "Stocks that are **temporarily oversold** — RSI below 38, but sitting in a "
            "broader uptrend with solid fundamentals. The bull thesis is intact; "
            "this is a dip. Best held for **3–10 days** targeting a mean-reversion bounce."
        )
        bnc_df = hf_df[hf_df["rsi"] < 45].sort_values("bounce_score", ascending=False)
        if bnc_df.empty:
            st.info("No strongly oversold stocks found right now. The market may be in a broad uptrend. Try refreshing or expanding the universe.")
        else:
            render_pick_cards(bnc_df, "bounce_score", n=hf_top_n)
            st.markdown("---")
            render_strategy_table(bnc_df, "bounce_score", "Bounce")

            # RSI visualisation
            st.markdown("---")
            st.markdown("### RSI levels — bounce candidates")
            top_bnc = bnc_df.head(min(hf_top_n, 12))
            fig_rsi = go.Figure()
            bar_clrs = [GOLD if r < 30 else "#2563eb" for r in top_bnc["rsi"]]
            fig_rsi.add_trace(go.Bar(x=top_bnc["ticker"], y=top_bnc["rsi"], marker_color=bar_clrs, name="RSI (14)"))
            fig_rsi.add_hline(y=30, line_dash="dash", line_color="#9b3b3b", line_width=1.2,
                              annotation_text="Oversold (30)", annotation_position="right",
                              annotation_font=dict(color="#9b3b3b", size=10))
            fig_rsi.add_hline(y=45, line_dash="dot", line_color="#8d7434", line_width=1,
                              annotation_text="Oversold threshold (45)", annotation_position="right",
                              annotation_font=dict(color="#8d7434", size=10))
            fig_rsi.update_layout(**chart_layout(height=300, yaxis_range=[0, 60], showlegend=False,
                                                  title=dict(text="RSI (gold = extreme oversold)", font=dict(size=11, color=CHART_TEXT))))
            st.plotly_chart(fig_rsi, width="stretch")

    # ════════════════════════════════
    with tab_cat:
        st.markdown("### Growth")
        st.markdown(
            "Stocks with **accelerating fundamentals** — revenue beating expectations, "
            "earnings growing fast, analyst price targets well above current price. "
            "A catalyst is building; best held for **2–4 weeks** into or around earnings."
        )
        cat_df = hf_df.sort_values("catalyst_score", ascending=False)
        render_pick_cards(cat_df, "catalyst_score", n=hf_top_n)
        st.markdown("---")
        render_strategy_table(cat_df, "catalyst_score", "Catalyst")

        # Analyst upside vs research score
        st.markdown("---")
        st.markdown("### Analyst upside vs catalyst score")
        top_cat = cat_df[cat_df["upside"].notna()].head(min(hf_top_n * 2, 20))
        if not top_cat.empty:
            fig_cat = go.Figure(go.Scatter(
                x=top_cat["catalyst_score"],
                y=top_cat["upside"],
                mode="markers+text",
                text=top_cat["ticker"],
                textposition="top center",
                textfont=dict(size=9, color=CHART_TEXT),
                marker=dict(
                    size=top_cat["earn_growth"].clip(5, 60) / 3,
                    color=top_cat["catalyst_score"],
                    colorscale=[[0, "#e7e4dd"], [0.5, GOLD], [1, "#2f6b4f"]],
                    showscale=True,
                    colorbar=dict(title="Catalyst Score", thickness=10, len=0.6),
                    line=dict(color="#ffffff", width=1),
                ),
            ))
            fig_cat.update_layout(**chart_layout(
                height=380,
                xaxis_title="Catalyst Score",
                yaxis_title="Analyst Upside %",
                title=dict(text="Bubble size = earnings growth rate", font=dict(size=11, color=CHART_TEXT)),
            ))
            st.plotly_chart(fig_cat, width="stretch")

    # ════════════════════════════════
    with tab_brk:
        st.markdown("### Consolidating")
        st.markdown(
            "Stocks **coiling near their 52-week high** with low volatility and starting "
            "volume expansion — classic pre-breakout setup. Best held for "
            "**1–2 weeks** once the breakout is confirmed with a volume surge."
        )
        brk_df = hf_df[hf_df["pct_52w_range"] >= 75].sort_values("breakout_score", ascending=False)
        if brk_df.empty:
            st.info("No pre-breakout setups found right now. Try a broader universe.")
        else:
            render_pick_cards(brk_df, "breakout_score", n=hf_top_n)
            st.markdown("---")
            render_strategy_table(brk_df, "breakout_score", "Breakout")

            # 52-week range chart
            st.markdown("---")
            st.markdown("### Position in 52-week range (85–97% = ideal breakout zone)")
            top_brk = brk_df.head(min(hf_top_n * 2, 15))
            bar_clrs_brk = [
                "#2f6b4f" if 85 <= r <= 97 else GOLD if r > 75 else "#e7e4dd"
                for r in top_brk["pct_52w_range"]
            ]
            fig_brk = go.Figure(go.Bar(
                x=top_brk["ticker"], y=top_brk["pct_52w_range"],
                marker_color=bar_clrs_brk, name="52W Range %",
            ))
            fig_brk.add_hrect(y0=85, y1=97, fillcolor="rgba(22,163,74,0.07)",
                               line_width=0, annotation_text="Ideal breakout zone",
                               annotation_position="right",
                               annotation_font=dict(color="#2f6b4f", size=10))
            fig_brk.update_layout(**chart_layout(height=300, yaxis_range=[60, 105], showlegend=False,
                                                  title=dict(text="Green = ideal breakout zone (85–97%)", font=dict(size=11, color=CHART_TEXT))))
            st.plotly_chart(fig_brk, width="stretch")

    # ════════════════════════════════
    with tab_risk:
        st.markdown("### Risk characteristics")
        st.markdown(
            "How volatile these companies have been, and how far their shares have historically moved. "
            "These are descriptions of past behaviour — they are not guidance on what to buy, how much to "
            "hold, or when to exit."
        )

        # Volatility vs analyst expectations. Deliberately NOT a risk/reward
        # map with target lines: that framing presents a trade to execute.
        top_risk = hf_df[hf_df["atr_pct"] > 0].head(40)
        fig_rr = go.Figure(go.Scatter(
            x=top_risk["atr_pct"],
            y=top_risk["beta"],
            mode="markers+text",
            text=top_risk["ticker"],
            textposition="top center",
            textfont=dict(size=9, color=CHART_TEXT),
            marker=dict(
                size=12,
                color=top_risk["best_score"],
                colorscale=[[0, "#e7e4dd"], [0.5, GOLD], [1, "#2f6b4f"]],
                showscale=True,
                colorbar=dict(title="Research<br>score", thickness=10, len=0.6),
                line=dict(color="#ffffff", width=1),
            ),
        ))
        fig_rr.add_hline(y=1.0, line_dash="dot", line_color="#9aa1ad",
                         annotation_text="Moves with the market (beta 1.0)",
                         annotation_font=dict(color="#9aa1ad", size=10))
        fig_rr.update_layout(**chart_layout(
            height=420,
            xaxis_title="Daily volatility (ATR as % of price)",
            yaxis_title="Beta (sensitivity to the wider market)",
            title=dict(text="Further right = larger daily swings. Higher up = amplifies market moves.",
                       font=dict(size=11, color=CHART_TEXT)),
        ))
        st.plotly_chart(fig_rr, width="stretch")
        st.caption(
            "Beta above 1.0 means the shares have historically moved more than the market in both "
            "directions. ATR is the average daily price range — a higher figure means larger day-to-day swings."
        )

        st.markdown("---")
        st.markdown("### Risk characteristics table")

        pos_cols = [
            "ticker", "name", "best_score", "primary_strategy", "score_band",
            "risk_score", "beta", "atr_pct", "short_pct",
            "price", "stop_pct", "reward_pct",
        ]
        avail_pos = [c for c in pos_cols if c in hf_df.columns]
        rename_pos = {
            "ticker": "Ticker", "name": "Company",
            "best_score": "Research score", "primary_strategy": "Profile",
            "score_band": "Band", "risk_score": "Risk score",
            "beta": "Beta", "atr_pct": "Volatility%", "short_pct": "Short%",
            "price": "Price $", "stop_pct": "Typical downside%",
            "reward_pct": "Analyst upside%",
        }
        st.dataframe(
            hf_df[avail_pos].head(hf_top_n * 2).rename(columns=rename_pos),
            hide_index=True, width="stretch",
            height=min(700, 36 * hf_top_n * 2 + 40),
        )

        # Risk distribution
        st.markdown("---")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### Risk score distribution")
            fig_risk_hist = go.Figure(go.Histogram(
                x=hf_df["risk_score"], nbinsx=20,
                marker_color=GOLD, opacity=0.75,
                name="Risk score",
            ))
            fig_risk_hist.add_vline(x=45, line_dash="dot", line_color="#8d7434",
                                    annotation_text="Moderate risk", annotation_font=dict(color="#8d7434", size=10))
            fig_risk_hist.add_vline(x=65, line_dash="dash", line_color="#9b3b3b",
                                    annotation_text="High risk", annotation_font=dict(color="#9b3b3b", size=10))
            fig_risk_hist.update_layout(**chart_layout(height=280, showlegend=False,
                                                         xaxis_title="Risk score (0=low, 100=high)"))
            st.plotly_chart(fig_risk_hist, width="stretch")

        with c2:
            st.markdown("### Beta distribution")
            valid_beta = hf_df["beta"].dropna()
            fig_beta = go.Figure(go.Histogram(
                x=valid_beta, nbinsx=20,
                marker_color="#2563eb", opacity=0.75,
                name="Beta",
            ))
            fig_beta.add_vline(x=1.0, line_dash="dot",  line_color="#5f6672",
                               annotation_text="Market (β=1)", annotation_font=dict(color="#5f6672", size=10))
            fig_beta.add_vline(x=1.5, line_dash="dash", line_color=GOLD,
                               annotation_text="β=1.5", annotation_font=dict(color=GOLD, size=10))
            fig_beta.update_layout(**chart_layout(height=280, showlegend=False, xaxis_title="Beta"))
            st.plotly_chart(fig_beta, width="stretch")

        st.markdown("---")
        st.download_button(
            "⬇ Download full fund scan as CSV",
            data=hf_df.to_csv(index=False),
            file_name=f"hedge_fund_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )

    # ════════════════════════════════
    with tab_returns:
        st.markdown("### Return scenarios")
        st.markdown(
            "Filter by profile and score band, pick your companies, set a holding period, "
            "and see projected returns adjust in real time — including compounding across "
            "multiple rotations. Bull returns scale with hold time; stops are fixed."
        )

        # Reference hold periods per strategy (weeks) — used to time-scale bull returns.
        # These represent the "full expected move" window for each strategy type.
        STRATEGY_REF_HOLD: dict[str, float] = {
            "Momentum":  4.0,   # ~1 month momentum burst
            "Oversold":    3.0,   # ~3-week mean-reversion snap
            "Growth":  8.0,   # ~2-month catalyst play
            "Consolidating":  5.0,   # ~5-week breakout development
        }

        # ── Step 1: Filter controls ───────────────────────────────
        st.markdown("#### 1 · Filter the candidate pool")
        f1, f2 = st.columns(2)
        with f1:
            strat_filter = st.multiselect(
                "Strategy types to include",
                options=["Momentum", "Oversold", "Growth", "Consolidating"],
                default=["Momentum", "Oversold", "Growth", "Consolidating"],
                key="ret_strat_filter",
            )
        with f2:
            conv_filter = st.multiselect(
                "Score bands to include",
                options=["high", "med", "low"],
                default=["high", "med", "low"],
                key="ret_conv_filter",
            )

        # Build filtered candidate pool
        _cand = hf_df[
            hf_df["primary_strategy"].isin(strat_filter) &
            hf_df["score_band"].isin(conv_filter)
        ].copy()

        if _cand.empty:
            st.warning("No companies match the selected profile / score band filters. Adjust the filters above.")
            st.stop()

        # Stock picker — label shows strategy + score so user knows what they're picking
        _cand_labels = {
            row["ticker"]: f"{row['ticker']}  ·  {row['primary_strategy']}  ·  score {row['best_score']:.0f}  ·  {row['score_band']}"
            for _, row in _cand.iterrows()
        }
        default_tickers = list(_cand_labels.keys())[:min(10, len(_cand_labels))]
        selected_tickers = st.multiselect(
            "Stocks to include in projection",
            options=list(_cand_labels.keys()),
            default=default_tickers,
            format_func=lambda t: _cand_labels[t],
            key="ret_stock_picker",
        )

        if not selected_tickers:
            st.info("Select at least one stock above to see projections.")
            st.stop()

        sel_df = _cand[_cand["ticker"].isin(selected_tickers)].copy()

        # ── Step 2: Projection inputs ─────────────────────────────
        st.markdown("---")
        st.markdown("#### 2 · Set projection parameters")
        pi1, pi2, pi3 = st.columns(3)
        with pi1:
            invest_total = st.number_input(
                "Initial investment (£/$)", min_value=100, max_value=1_000_000,
                value=int(portfolio_size), step=100, key="ret_invest",
            )
        with pi2:
            hold_weeks = st.slider(
                "Hold period per rotation (weeks)", 1, 16, 3, key="ret_weeks",
                help="Bull return scales proportionally to this vs the strategy's reference hold period.",
            )
        with pi3:
            n_rotations = st.slider(
                "Number of rotations", 1, 20, 6, key="ret_rots",
                help="How many times you re-deploy capital in succession (compounding chart).",
            )

        # ── Step 3: Allocation method ─────────────────────────────
        st.markdown("---")
        st.markdown("#### 3 · Allocation method")
        alloc_method = st.radio(
            "How to split capital across stocks",
            options=["Score weighted (auto)", "Equal weight", "Custom weighting"],
            horizontal=True,
            key="ret_alloc_method",
        )

        n_sel = len(sel_df)

        if alloc_method == "Score weighted (auto)":
            _conv_w = {"high": 3, "med": 2, "low": 1}
            sel_df["_w"] = sel_df["score_band"].map(_conv_w).fillna(1)
            sel_df["alloc_pct"] = sel_df["_w"] / sel_df["_w"].sum() * 100
        elif alloc_method == "Equal weight":
            sel_df["alloc_pct"] = 100.0 / n_sel
        else:  # Custom
            # Seed with equal weight; user edits
            sel_df["alloc_pct"] = (sel_df.get("pos_pct", pd.Series([100.0 / n_sel] * n_sel, index=sel_df.index))
                                   .clip(0.1))
            # Normalise seed
            sel_df["alloc_pct"] = sel_df["alloc_pct"] / sel_df["alloc_pct"].sum() * 100

            edit_df = sel_df[["ticker", "primary_strategy", "score_band", "alloc_pct"]].copy()
            edit_df = edit_df.rename(columns={
                "ticker": "Ticker", "primary_strategy": "Strategy",
                "score_band": "Tier", "alloc_pct": "Allocation %",
            })
            st.caption("Edit the **Allocation %** column — values will be re-normalised automatically.")
            edited = st.data_editor(
                edit_df,
                column_config={
                    "Allocation %": st.column_config.NumberColumn(
                        "Allocation %", min_value=0.1, max_value=100.0, step=0.5, format="%.1f %%"
                    ),
                    "Ticker":   st.column_config.TextColumn(disabled=True),
                    "Strategy": st.column_config.TextColumn(disabled=True),
                    "Tier":     st.column_config.TextColumn(disabled=True),
                },
                hide_index=True,
                width="stretch",
                key="ret_custom_alloc",
            )
            # Write normalised pct back to sel_df
            raw_pcts = edited["Allocation %"].values
            total_raw = raw_pcts.sum()
            sel_df["alloc_pct"] = (raw_pcts / total_raw * 100) if total_raw > 0 else (100.0 / n_sel)

        # Dollar allocation
        sel_df["adj_alloc"] = sel_df["alloc_pct"] / 100 * invest_total

        # ── Step 4: Time-adjusted return calculations ─────────────
        sel_df["ref_hold"]   = sel_df["primary_strategy"].map(STRATEGY_REF_HOLD).fillna(2.5)
        # Scale bull return proportionally to hold time vs reference window.
        # Clip: minimum 0.1× (very short holds), maximum 3.0× (very long holds).
        # This ensures 6w vs 13w always produces meaningfully different projections.
        sel_df["time_scale"] = (hold_weeks / sel_df["ref_hold"]).clip(0.1, 3.0)

        sel_df["win_prob"]  = (sel_df["best_score"] / 100 * 0.45 + 0.40).clip(0.40, 0.85)
        # Bull scales with hold time; bear (stop) is fixed
        sel_df["bull_ret"]  = (sel_df["reward_pct"].fillna(10) / 100) * sel_df["time_scale"]
        sel_df["bear_ret"]  = -(sel_df["stop_pct"].fillna(5) / 100)
        sel_df["base_ret"]  = (
            sel_df["win_prob"] * sel_df["bull_ret"] +
            (1 - sel_df["win_prob"]) * sel_df["bear_ret"]
        )

        sel_df["bull_value"]  = sel_df["adj_alloc"] * (1 + sel_df["bull_ret"])
        sel_df["base_value"]  = sel_df["adj_alloc"] * (1 + sel_df["base_ret"])
        sel_df["bear_value"]  = sel_df["adj_alloc"] * (1 + sel_df["bear_ret"])
        sel_df["bull_profit"] = sel_df["bull_value"] - sel_df["adj_alloc"]
        sel_df["base_profit"] = sel_df["base_value"] - sel_df["adj_alloc"]
        sel_df["bear_profit"] = sel_df["bear_value"] - sel_df["adj_alloc"]

        # Portfolio totals
        port_invested = sel_df["adj_alloc"].sum()
        port_bull     = sel_df["bull_value"].sum()
        port_base     = sel_df["base_value"].sum()
        port_bear     = sel_df["bear_value"].sum()
        port_bull_pct = (port_bull - port_invested) / port_invested * 100
        port_base_pct = (port_base - port_invested) / port_invested * 100
        port_bear_pct = (port_bear - port_invested) / port_invested * 100

        # ── Scenario headline cards ───────────────────────────────
        st.markdown("---")
        st.markdown(f"#### Portfolio projection — {hold_weeks}-week hold · {n_sel} positions")
        sc1, sc2, sc3, sc4 = st.columns(4)

        sc1.markdown(
            f'<div style="background:#ffffff; border:1px solid #e7e4dd; border-top:4px solid #5f6672; '
            f'border-radius:4px; padding:1.1rem 1.3rem;">'
            f'<div style="font-size:0.68rem; color:#9aa1ad; text-transform:uppercase; letter-spacing:0.1em;">Invested</div>'
            f'<div style="font-size:1.8rem; font-weight:900; color:#12161f; margin:0.2rem 0;">${invest_total:,.0f}</div>'
            f'<div style="font-size:0.78rem; color:#5f6672;">{n_sel} positions · {alloc_method}</div>'
            f'</div>', unsafe_allow_html=True,
        )
        sc2.markdown(
            f'<div style="background:#f0fdf4; border:1px solid #bbf7d0; border-top:4px solid #2f6b4f; '
            f'border-radius:4px; padding:1.1rem 1.3rem;">'
            f'<div style="font-size:0.68rem; color:#2f6b4f; text-transform:uppercase; letter-spacing:0.1em; font-weight:700;">🐂 Bull Case</div>'
            f'<div style="font-size:1.8rem; font-weight:900; color:#2f6b4f; margin:0.2rem 0;">${port_bull:,.0f}</div>'
            f'<div style="font-size:0.9rem; font-weight:700; color:#2f6b4f;">+{port_bull_pct:.1f}%'
            f'  <span style="font-size:0.75rem; font-weight:400; color:#4ade80;">(+${port_bull-port_invested:,.0f})</span></div>'
            f'</div>', unsafe_allow_html=True,
        )
        sc3.markdown(
            f'<div style="background:#fffbeb; border:1px solid #fde68a; border-top:4px solid #8d7434; '
            f'border-radius:4px; padding:1.1rem 1.3rem;">'
            f'<div style="font-size:0.68rem; color:#92400e; text-transform:uppercase; letter-spacing:0.1em; font-weight:700;">📊 Base Case</div>'
            f'<div style="font-size:1.8rem; font-weight:900; color:#8d7434; margin:0.2rem 0;">${port_base:,.0f}</div>'
            f'<div style="font-size:0.9rem; font-weight:700; color:#8d7434;">{port_base_pct:+.1f}%'
            f'  <span style="font-size:0.75rem; font-weight:400; color:#d97706;">(${port_base-port_invested:+,.0f})</span></div>'
            f'</div>', unsafe_allow_html=True,
        )
        sc4.markdown(
            f'<div style="background:#fff5f5; border:1px solid #fecaca; border-top:4px solid #9b3b3b; '
            f'border-radius:4px; padding:1.1rem 1.3rem;">'
            f'<div style="font-size:0.68rem; color:#9b3b3b; text-transform:uppercase; letter-spacing:0.1em; font-weight:700;">🐻 Bear Case</div>'
            f'<div style="font-size:1.8rem; font-weight:900; color:#9b3b3b; margin:0.2rem 0;">${port_bear:,.0f}</div>'
            f'<div style="font-size:0.9rem; font-weight:700; color:#9b3b3b;">{port_bear_pct:+.1f}%'
            f'  <span style="font-size:0.75rem; font-weight:400; color:#f87171;">(${port_bear-port_invested:+,.0f})</span></div>'
            f'</div>', unsafe_allow_html=True,
        )
        st.markdown(
            f"<p style='font-size:0.8rem; color:#9aa1ad; margin-top:0.5rem;'>"
            f"Bull returns are scaled to your {hold_weeks}-week hold (reference periods: "
            + ", ".join(f"{k} {v}w" for k, v in STRATEGY_REF_HOLD.items()) +
            f"). Downside estimates are based on volatility and do not vary with holding period.</p>",
            unsafe_allow_html=True,
        )

        # ── Per-stock waterfall chart ─────────────────────────────
        st.markdown("---")
        st.markdown("### Per-stock projected profit / loss")

        fig_proj = go.Figure()
        fig_proj.add_trace(go.Bar(
            name="Bull",
            x=sel_df["ticker"],
            y=sel_df["bull_profit"].round(0),
            marker_color="#2f6b4f", opacity=0.85,
            text=sel_df["bull_profit"].apply(lambda x: f"+${x:,.0f}"),
            textposition="outside", textfont=dict(size=9, color="#2f6b4f"),
        ))
        fig_proj.add_trace(go.Bar(
            name="Base",
            x=sel_df["ticker"],
            y=sel_df["base_profit"].round(0),
            marker_color=GOLD, opacity=0.85,
            text=sel_df["base_profit"].apply(lambda x: f"${x:+,.0f}"),
            textposition="outside", textfont=dict(size=9, color="#8d7434"),
        ))
        fig_proj.add_trace(go.Bar(
            name="Bear",
            x=sel_df["ticker"],
            y=sel_df["bear_profit"].round(0),
            marker_color="#9b3b3b", opacity=0.75,
            text=sel_df["bear_profit"].apply(lambda x: f"${x:+,.0f}"),
            textposition="outside", textfont=dict(size=9, color="#9b3b3b"),
        ))
        fig_proj.add_hline(y=0, line_color="#e7e4dd", line_width=1)
        fig_proj.update_layout(**chart_layout(
            barmode="group", height=400,
            yaxis_title="Projected profit / loss ($)",
            title=dict(text=f"Per-stock P&L — {hold_weeks}-week hold", font=dict(size=11, color=CHART_TEXT)),
        ))
        st.plotly_chart(fig_proj, width="stretch")

        # ── Detailed table ────────────────────────────────────────
        st.markdown("---")
        st.markdown("### Full projection table")

        proj_disp = sel_df[[
            "ticker", "name", "primary_strategy", "score_band", "ref_hold", "time_scale",
            "adj_alloc", "win_prob",
            "bull_ret", "base_ret", "bear_ret",
            "bull_value", "base_value", "bear_value",
            "bull_profit", "base_profit", "bear_profit",
        ]].copy()
        proj_disp["win_prob"]   = (proj_disp["win_prob"]  * 100).round(0).astype(int).astype(str) + "%"
        proj_disp["time_scale"] = proj_disp["time_scale"].apply(lambda x: f"{x:.2f}×")
        proj_disp["ref_hold"]   = proj_disp["ref_hold"].apply(lambda x: f"{x:.1f}w")
        proj_disp["bull_ret"]   = proj_disp["bull_ret"].apply(lambda x: f"{x*100:+.1f}%")
        proj_disp["base_ret"]   = proj_disp["base_ret"].apply(lambda x: f"{x*100:+.1f}%")
        proj_disp["bear_ret"]   = proj_disp["bear_ret"].apply(lambda x: f"{x*100:+.1f}%")
        for c in ["adj_alloc","bull_value","base_value","bear_value","bull_profit","base_profit","bear_profit"]:
            proj_disp[c] = proj_disp[c].apply(lambda x: f"${x:,.0f}")

        proj_disp = proj_disp.rename(columns={
            "ticker":"Ticker","name":"Company","primary_strategy":"Strategy","score_band":"Tier",
            "ref_hold":"Ref Hold","time_scale":"Time Scale",
            "adj_alloc":"Invested","win_prob":"Win Prob",
            "bull_ret":"Bull Ret%","base_ret":"Base Ret%","bear_ret":"Bear Ret%",
            "bull_value":"Bull Value","base_value":"Base Value","bear_value":"Bear Value",
            "bull_profit":"Bull P&L","base_profit":"Base P&L","bear_profit":"Bear P&L",
        })
        proj_disp.insert(0, "#", range(1, len(proj_disp) + 1))
        st.dataframe(proj_disp, hide_index=True, width="stretch",
                     height=min(600, 36 * len(proj_disp) + 40))

        # ── Compounding projection ────────────────────────────────
        st.markdown("---")
        st.markdown("### Compounding scenario")
        st.markdown(
            f"Reinvesting the full portfolio after each **{hold_weeks}-week** rotation, "
            f"running **{n_rotations} rotations** in total. "
            f"Each rotation uses the same time-adjusted base-case return ({port_base_pct:+.1f}% per rotation)."
        )

        rot_bull = port_bull_pct / 100
        rot_base = port_base_pct / 100
        rot_bear = port_bear_pct / 100

        rotation_labels = ["Start"] + [f"R{r+1}" for r in range(n_rotations)]
        bull_values_c   = [invest_total]
        base_values_c   = [invest_total]
        bear_values_c   = [invest_total]

        for r in range(n_rotations):
            bull_values_c.append(bull_values_c[-1] * (1 + rot_bull))
            base_values_c.append(base_values_c[-1] * (1 + rot_base))
            bear_values_c.append(bear_values_c[-1] * (1 + rot_bear))

        fig_compound = go.Figure()
        fig_compound.add_trace(go.Scatter(
            x=rotation_labels, y=bull_values_c, name="Bull",
            line=dict(color="#2f6b4f", width=2.5),
            fill="tozeroy", fillcolor="rgba(22,163,74,0.07)",
            mode="lines+markers", marker=dict(size=7, color="#2f6b4f"),
        ))
        fig_compound.add_trace(go.Scatter(
            x=rotation_labels, y=base_values_c, name="Base",
            line=dict(color=GOLD, width=2.5),
            fill="tozeroy", fillcolor="rgba(184,150,12,0.07)",
            mode="lines+markers", marker=dict(size=7, color=GOLD),
        ))
        fig_compound.add_trace(go.Scatter(
            x=rotation_labels, y=bear_values_c, name="Bear",
            line=dict(color="#9b3b3b", width=2, dash="dot"),
            mode="lines+markers", marker=dict(size=6, color="#9b3b3b"),
        ))
        fig_compound.add_hline(
            y=invest_total, line_dash="dot", line_color="#9aa1ad", line_width=1,
            annotation_text="Starting capital", annotation_position="right",
            annotation_font=dict(color="#9aa1ad", size=10),
        )
        fig_compound.update_layout(**chart_layout(
            height=400,
            yaxis_title="Portfolio value ($)",
            yaxis_tickprefix="$", yaxis_tickformat=",.0f",
            title=dict(
                text=f"{n_rotations} rotations × {hold_weeks}-week holds — starting ${invest_total:,.0f}",
                font=dict(size=11, color=CHART_TEXT),
            ),
        ))
        st.plotly_chart(fig_compound, width="stretch")

        # End state summary cards
        e1, e2, e3 = st.columns(3)
        final_bull_pct = (bull_values_c[-1] - invest_total) / invest_total * 100
        final_base_pct = (base_values_c[-1] - invest_total) / invest_total * 100
        final_bear_pct = (bear_values_c[-1] - invest_total) / invest_total * 100

        total_weeks = hold_weeks * n_rotations
        for col, label, val, pct, bg, bc in [
            (e1, f"Bull · {n_rotations}×", bull_values_c[-1], final_bull_pct, "#f0fdf4", "#2f6b4f"),
            (e2, f"Base · {n_rotations}×", base_values_c[-1], final_base_pct, "#fffbeb", "#8d7434"),
            (e3, f"Bear · {n_rotations}×", bear_values_c[-1], final_bear_pct, "#fff5f5", "#9b3b3b"),
        ]:
            col.markdown(
                f'<div style="background:{bg}; border:1px solid #e7e4dd; border-top:4px solid {bc}; '
                f'border-radius:4px; padding:1rem 1.2rem; text-align:center;">'
                f'<div style="font-size:0.72rem; color:{bc}; font-weight:700; text-transform:uppercase; letter-spacing:0.1em;">{label}</div>'
                f'<div style="font-size:2rem; font-weight:900; color:{bc}; margin:0.3rem 0;">${val:,.0f}</div>'
                f'<div style="font-size:0.9rem; font-weight:700; color:{bc};">{pct:+.1f}% total return</div>'
                f'<div style="font-size:0.75rem; color:#5f6672; margin-top:0.2rem;">'
                f'${val - invest_total:+,.0f} over {total_weeks}w ({n_rotations} rotations)</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            "<p style='font-size:0.78rem; color:#9aa1ad; margin-top:0.8rem;'>"
            "Bull returns are scaled by your hold period relative to each strategy's reference window. "
            "Bear/stop returns are fixed regardless of hold time. "
            "Compounding assumes the same per-rotation return each cycle. "
            "Scenario likelihood is derived from the research score (40%–85%). "
            "Illustrative only — not financial advice."
            "</p>",
            unsafe_allow_html=True,
        )

    # ── Disclaimer ───────────────────────────────────────────
    st.markdown("---")
    st.caption(
        "This is a research and education tool, not financial advice, and nothing here is a "
        "recommendation to buy or sell any investment. Scores describe a company's reported figures "
        "and past price behaviour; they are not predictions. Analyst upside figures are other "
        "analysts' published estimates, not forecasts from this platform. Investing carries risk and "
        "you may get back less than you put in. Consider taking independent financial advice."
    )
