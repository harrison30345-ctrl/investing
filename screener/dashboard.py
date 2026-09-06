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
from content.lessons import CATEGORIES, LESSONS, LESSONS_BY_KEY, lesson_for_metric
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
    page_title="The Investor Square",
    page_icon="♜",
    layout="wide",
    initial_sidebar_state="expanded",
)

theme.inject()
theme.hover_to_open_sidebar()




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
    """Ticker -> company name, for labelling the picker.

    Names are a nicety. The picker's option list is built from the universe
    itself, never from this map: an earlier version listed only tickers whose
    name had come back from the provider, so any ticker with missing info
    silently disappeared from search -- AMAT among them.
    """
    try:
        infos = _batch_info(tuple(get_universe("all_curated")))
    except Exception:  # noqa: BLE001 - the picker degrades to tickers only
        return {}
    return {t: (info or {}).get("shortName") or ""
            for t, info in infos.items() if (info or {}).get("shortName")}


def _searchable_tickers() -> list:
    """Every ticker that can be looked up, plus anything the user has viewed."""
    tickers = set()
    for universe in ("broad", "all_curated", "t212"):
        try:
            tickers.update(get_universe(universe))
        except Exception:  # noqa: BLE001
            continue
    try:
        wl = _watchlist()
        tickers.update(e.ticker for e in wl.all())
        tickers.update(e.ticker for e in wl.recent(limit=30))
    except Exception:  # noqa: BLE001
        pass
    return sorted(tickers)


def _label_for(ticker: str, directory: dict) -> str:
    name = directory.get(ticker)
    return f"{ticker} — {name}" if name else ticker


def _company_picker(default: str = "AAPL") -> str:
    """Search by ticker or company name.

    Options come from the full universe so nothing is missing, and the ticker
    leads each label so typing a symbol matches at the start of the string
    rather than somewhere in the middle of a company name.
    """
    directory = _company_directory(_bucket())
    options = _searchable_tickers()
    if not options:
        options = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]

    OTHER = "Search another ticker…"
    options = options + [OTHER]
    previous = st.session_state.get("_co_last", default)
    index = options.index(previous) if previous in options else (
        options.index(default) if default in options else 0)

    pick = st.selectbox(
        "Company", options, index=index,
        format_func=lambda t: OTHER if t == OTHER else _label_for(t, directory),
        label_visibility="collapsed",
        help="Type a ticker or company name to filter.",
    )
    if pick == OTHER:
        return st.text_input(
            "Ticker", value="", placeholder="Any ticker, for example BP.L",
            label_visibility="collapsed",
        ).strip().upper()
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
        The Investor Square</div>
    <div style="font-size:0.62rem; font-weight:500; letter-spacing:0.16em;
                text-transform:uppercase; color:#7b8394; margin-top:0.2rem;">Equity research</div>
</div>
""", unsafe_allow_html=True)

nav = st.sidebar.radio(
    "Navigate",
    ["Overview", "Discover", "Screener", "Watchlist", "Learn", "UK Investor"],
    index=0,
    label_visibility="collapsed",
)

# Company and Compare are views reached from anywhere, not destinations in the
# sidebar. Searching for a company should open it wherever you happen to be,
# which is how people actually move through a research tool.
if st.session_state.get("_nav_last") != nav:
    st.session_state["_nav_last"] = nav
    st.session_state.pop("_view", None)      # changing section leaves a company

view = st.session_state.get("_view")
page = view if view in ("Company", "Compare") else nav


def _open_company(ticker: str) -> None:
    st.session_state["_view"] = "Company"
    st.session_state["_co_last"] = ticker
    st.rerun()


# ── Global search, present on every page ─────────────────────
def _global_search() -> None:
    """Search from anywhere. Selecting a company opens its research page."""
    directory = _company_directory(_bucket())
    options = _searchable_tickers()
    if not options:
        return
    BLANK = ""
    choice = st.selectbox(
        "Search stocks or tickers",
        [BLANK] + options,
        index=0,
        format_func=lambda t: ("Search stocks or tickers    Apple · AAPL · Microsoft · MSFT"
                               if t == BLANK else _label_for(t, directory)),
        label_visibility="collapsed",
        key="_global_search",
    )
    if choice and choice != st.session_state.get("_co_last"):
        _open_company(choice)


if page not in ("Company", "Compare"):
    _global_search()

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
            st.caption("Nothing saved yet. Search for a company and add it to your watchlist.")
        else:
            rows = []
            for entry in entries[:8]:
                snap = change = None
                try:
                    store = _score_history()
                    snap = store.latest(entry.ticker)
                    earlier = store.nearest(entry.ticker, 30, tolerance_days=25)
                    if snap and earlier and earlier.taken_on != snap.taken_on:
                        change = describe_change(snap, earlier)
                except Exception:  # noqa: BLE001
                    pass
                score = "—" if snap is None or snap.overall is None else f"{snap.overall:.0f}"
                reason = ""
                delta = None
                if change and change.get("comparable"):
                    delta = change["delta"]
                    if change.get("movers") and abs(delta) >= 2:
                        top = change["movers"][0]
                        reason = (f"{top['category']} "
                                  f"{'up' if top['delta'] > 0 else 'down'} "
                                  f"{abs(top['delta']):.0f}")
                rows.append({"ticker": entry.ticker, "name": (entry.name or "")[:38],
                             "score": score, "delta": delta, "reason": reason})
            theme.list_rows(rows)
            if len(entries) > 8:
                st.caption(f"And {len(entries) - 8} more.")
            st.caption("Score change is against the nearest snapshot around 30 days ago.")

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

    # ── Continue learning ────────────────────────────────────
    theme.section("Continue learning")
    # One lesson a day, drawn from the shared Learn content rather than a
    # second copy maintained here.
    _today = LESSONS[date.today().toordinal() % len(LESSONS)]
    st.markdown(
        f'<div style="font-size:0.875rem;font-weight:620;color:{theme.INK};">{_today.title}</div>'
        f'<div style="font-size:0.85rem;color:{theme.MUTED};line-height:1.62;margin-top:0.25rem;'
        f'max-width:72ch;">{_today.summary}</div>'
        f'<div style="font-size:0.74rem;color:{theme.FAINT};margin-top:0.3rem;">'
        f'{_today.minutes} min read · open Learn to continue</div>',
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

    back, search = st.columns([1, 4])
    with back:
        if st.button("Back", key="_co_back"):
            st.session_state.pop("_view", None)
            st.rerun()
    with search:
        _dir = _company_directory(_bucket())
        _opts = _searchable_tickers()
        _cur = st.session_state.get("_co_last", "AAPL")
        _idx = _opts.index(_cur) if _cur in _opts else 0
        _pick = st.selectbox(
            "Search stocks or tickers", _opts, index=_idx,
            format_func=lambda t: _label_for(t, _dir),
            label_visibility="collapsed", key="_co_search",
        )
        if _pick != _cur:
            st.session_state["_co_last"] = _pick
            st.rerun()

    query = st.session_state.get("_co_last", "AAPL")
    if not query:
        st.caption("Search for a company to begin.")
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

    # Small actions, not a toolbar.
    act1, act2, _act3 = st.columns([1, 1, 4])
    try:
        _wl = _watchlist()
        _wl.record_view(query, res["name"])
        in_list = _wl.contains(query)
        with act1:
            if st.button("Remove from watchlist" if in_list else "Add to watchlist",
                         key=f"_wl_{query}"):
                _wl.remove(query) if in_list else _wl.add(query, res["name"])
                st.rerun()
    except Exception:  # noqa: BLE001
        pass
    with act2:
        if st.button("Compare", key=f"_cmp_{query}"):
            st.session_state["_cmp_seed"] = query
            st.session_state["_view"] = "Compare"
            st.rerun()

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

    if st.button("Back", key="_cmp_back"):
        st.session_state["_view"] = "Company" if st.session_state.get("_co_last") else None
        if st.session_state.get("_view") is None:
            st.session_state.pop("_view", None)
        st.rerun()

    st.title("Compare companies")
    st.caption(
        f"Put 2 to {MAX_COMPANIES} companies side by side. This shows how they differ on "
        f"the measures we score — it does not say which one to pick, because that depends "
        f"on what you are looking for."
    )

    _directory = _company_directory(_bucket())
    _options = _searchable_tickers() or ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"]
    picked = st.multiselect(
        "Companies to compare", _options,
        default=[t for t in (st.session_state.get("_cmp_seed", "AAPL"), "MSFT")
                 if t in _options][:2],
        format_func=lambda t: _label_for(t, _directory),
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
# PAGE — LEARN
# ════════════════════════════════════════════════════════════
elif page == "Learn":

    theme.page_header(
        "Learn",
        "Short, practical explanations of the ideas this platform uses.",
    )

    opened = st.session_state.get("_lesson")
    if opened and opened in LESSONS_BY_KEY:
        lesson = LESSONS_BY_KEY[opened]
        if st.button("Back to all topics", key="_lesson_back"):
            st.session_state.pop("_lesson", None)
            st.rerun()
        st.markdown(f"## {lesson.title}")
        st.markdown(
            f'<div style="font-size:0.76rem;color:{theme.FAINT};margin:-0.3rem 0 1rem;">'
            f'{lesson.category} · {lesson.minutes} min read</div>',
            unsafe_allow_html=True,
        )
        st.markdown(lesson.body)
        st.stop()

    for category in CATEGORIES:
        items = [l for l in LESSONS if l.category == category]
        if not items:
            continue
        theme.section(category)
        cols = st.columns(2, gap="large")
        for i, lesson in enumerate(items):
            with cols[i % 2]:
                st.markdown(
                    f'<div style="padding:0.5rem 0 0.1rem;">'
                    f'<div style="font-size:0.87rem;font-weight:620;color:{theme.INK};">'
                    f'{lesson.title}</div>'
                    f'<div style="font-size:0.79rem;color:{theme.MUTED};margin-top:0.15rem;">'
                    f'{lesson.summary}</div>'
                    f'<div style="font-size:0.72rem;color:{theme.FAINT};margin-top:0.2rem;">'
                    f'{lesson.minutes} min read</div></div>',
                    unsafe_allow_html=True,
                )
                if st.button("Read", key=f"_lesson_{lesson.key}"):
                    st.session_state["_lesson"] = lesson.key
                    st.rerun()

# ════════════════════════════════════════════════════════════
# PAGE 1 — MOMENTUM
# ════════════════════════════════════════════════════════════
elif page == "Discover":

    theme.page_header(
        "Discover",
        "Every company we score, filtered by what you are looking for.",
    )

    d_universe = st.sidebar.selectbox(
        "Universe",
        ["broad", "all_curated", "tech", "ai", "growth"],
        format_func=lambda x: {
            "broad": "Broad market (~360)", "all_curated": "Curated (~145)",
            "tech": "Technology", "ai": "AI and machine learning",
            "growth": "High growth",
        }[x],
        index=1,
    )
    d_refresh = st.sidebar.button("Refresh", key="_disc_refresh")
    d_key = f"discover_{d_universe}"
    if d_refresh or d_key not in st.session_state:
        st.session_state[d_key] = None

    if _scan_gate(d_key, "Load companies", forced=d_refresh,
                  note="Scores every company in the universe. Live data, so the first "
                       "load takes a moment."):
        tickers = get_universe(d_universe)
        with st.spinner(""):
            prog = st.progress(0, text="Downloading prices…")
            prices = _batch_prices(tuple(tickers), period="3mo", interval="1d")
            valid = tuple(t for t in tickers if t in prices and not prices[t].empty) or tuple(tickers)
            prog.progress(0.5, text=f"Fetching fundamentals for {len(valid)}…")
            infos = _batch_info(valid)
            prog.progress(0.9, text="Scoring…")
            built = []
            for t in valid:
                info = infos.get(t) or {}
                if not info.get("shortName"):
                    continue
                fundamentals = _fundamentals_from_info(info, prices.get(t))
                sc = score_company(t, fundamentals, info.get("sector"))
                gem = assess_gem(t, info.get("shortName", t)[:34], fundamentals,
                                 info.get("sector"), score=sc)
                built.append({
                    "Company": info.get("shortName", t)[:34],
                    "Ticker": t,
                    "Score": sc.overall,
                    "Quality": sc.categories["quality"].score,
                    "Growth": sc.categories["growth"].score,
                    "Valuation": sc.categories["valuation"].score,
                    "Momentum": sc.categories["momentum"].score,
                    "Health": sc.categories["financial_health"].score,
                    "Sector": info.get("sector") or "—",
                    "Confidence": sc.confidence,
                    "_gem": gem.qualifies,
                })
            prog.empty()
        st.session_state[d_key] = built

    built = st.session_state.get(d_key)
    if built is None:
        st.stop()
    if not built:
        st.warning("No data came back. Press Refresh to try again.")
        st.stop()

    df = pd.DataFrame(built)

    tabs = st.tabs(["All", "High quality", "Growth", "Value", "Momentum", "Hidden gems"])
    views = [
        ("All", df, "Every company scored, highest first."),
        ("High quality", df[df["Quality"] >= 70],
         "Strongly profitable businesses. Says nothing about the price you pay."),
        ("Growth", df[df["Growth"] >= 65],
         "Revenue and earnings growing quickly. Growth is often already in the price."),
        ("Value", df[df["Valuation"] >= 70],
         "Trading on lower multiples. A low multiple is not the same as underpriced."),
        ("Momentum", df[df["Momentum"] >= 70],
         "Share price has risen recently. This describes the price, not the business."),
        ("Hidden gems", df[df["_gem"]],
         "Sound, reasonably priced businesses that few analysts follow."),
    ]

    for tab, (label, subset, note) in zip(tabs, views):
        with tab:
            st.caption(note)
            if subset.empty:
                st.info("No company in this universe meets that filter.")
                continue
            shown = (subset.drop(columns=["_gem"])
                           .sort_values("Score", ascending=False)
                           .reset_index(drop=True))
            st.dataframe(
                shown, hide_index=True, width="stretch",
                height=min(620, 36 * len(shown) + 40),
                column_config={
                    "Score": st.column_config.NumberColumn("Score", format="%d", width="small"),
                    "Quality": st.column_config.NumberColumn(format="%d", width="small"),
                    "Growth": st.column_config.NumberColumn(format="%d", width="small"),
                    "Valuation": st.column_config.NumberColumn(format="%d", width="small"),
                    "Momentum": st.column_config.NumberColumn(format="%d", width="small"),
                    "Health": st.column_config.NumberColumn(format="%d", width="small"),
                    "Ticker": st.column_config.TextColumn(width="small"),
                },
            )
            st.caption(f"{len(shown)} companies. Click a column heading to sort. "
                       f"Search any ticker above to open its research page.")

    theme.hairline()
    st.caption(
        f"Methodology v{SCORING_VERSION}. A blank cell means that measure could not be "
        f"scored for that company. Research and education only, not financial advice."
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