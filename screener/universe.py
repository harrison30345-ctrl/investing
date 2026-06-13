"""
Universe helpers — pull constituent lists for common indices.
Also handles loading tickers from CSV/text files.
"""

import logging
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def load_tickers_from_file(filepath: str) -> list[str]:
    """
    Load tickers from a CSV or text file.
    Supports:
        - One ticker per line (.txt)
        - CSV with a 'ticker' or 'symbol' column, or first column
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Ticker file not found: {filepath}")

    suffix = path.suffix.lower()

    if suffix == ".csv":
        df = pd.read_csv(filepath)
        # Try common column names
        for col_name in ["ticker", "Ticker", "symbol", "Symbol", "TICKER", "SYMBOL"]:
            if col_name in df.columns:
                return df[col_name].dropna().str.strip().tolist()
        # Fall back to first column
        return df.iloc[:, 0].dropna().astype(str).str.strip().tolist()
    else:
        # Text file: one ticker per line
        with open(filepath, "r") as f:
            return [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]


def get_sp500() -> list[str]:
    """Fetch current S&P 500 constituents from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"id": "constituents"})
        if table is None:
            # Fallback: first wikitable
            table = soup.find("table", {"class": "wikitable"})
        df = pd.read_html(str(table))[0]
        # Column is usually "Symbol"
        for col in ["Symbol", "Ticker", "Ticker symbol"]:
            if col in df.columns:
                tickers = df[col].str.strip().str.replace(".", "-", regex=False).tolist()
                logger.info(f"Loaded {len(tickers)} S&P 500 tickers")
                return tickers
        logger.error("Could not find symbol column in S&P 500 table")
        return []
    except Exception as e:
        logger.error(f"Failed to fetch S&P 500 list: {e}")
        return []


def get_nasdaq100() -> list[str]:
    """Fetch current NASDAQ 100 constituents from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
        # Find the table with ticker/symbol column
        for table in tables:
            for col in ["Ticker", "Symbol", "Ticker symbol"]:
                if col in table.columns:
                    tickers = table[col].str.strip().str.replace(".", "-", regex=False).tolist()
                    logger.info(f"Loaded {len(tickers)} NASDAQ 100 tickers")
                    return tickers
        logger.error("Could not find ticker column in NASDAQ 100 tables")
        return []
    except Exception as e:
        logger.error(f"Failed to fetch NASDAQ 100 list: {e}")
        return []


def get_ftse100() -> list[str]:
    """Fetch current FTSE 100 constituents from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/FTSE_100_Index"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
        for table in tables:
            # FTSE uses "Ticker" or "EPIC" column; tickers need .L suffix for yfinance
            for col in ["Ticker", "EPIC", "Symbol", "Company"]:
                if col in table.columns and col != "Company":
                    tickers = table[col].str.strip().tolist()
                    # Add .L suffix for London Stock Exchange
                    tickers = [f"{t}.L" for t in tickers if isinstance(t, str) and t]
                    logger.info(f"Loaded {len(tickers)} FTSE 100 tickers")
                    return tickers
        logger.error("Could not find ticker column in FTSE 100 tables")
        return []
    except Exception as e:
        logger.error(f"Failed to fetch FTSE 100 list: {e}")
        return []


# ── Curated thematic lists ───────────────────────────────────

def get_tech_mega() -> list[str]:
    """Big tech + major tech companies (~60 tickers)."""
    return [
        # Mega caps
        "AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA",
        # Semiconductors
        "AMD", "INTC", "AVGO", "QCOM", "TXN", "MU", "MRVL", "ON", "LRCX", "KLAC", "AMAT", "ASML", "ARM", "TSM",
        # Software / Cloud
        "CRM", "ORCL", "ADBE", "NOW", "SNOW", "PLTR", "DDOG", "NET", "ZS", "CRWD", "PANW", "FTNT", "MDB",
        # Internet / Digital
        "NFLX", "SHOP", "SQ", "PYPL", "UBER", "ABNB", "SNAP", "PINS", "RBLX", "SPOT", "TTD",
        # Hardware / Enterprise
        "DELL", "HPE", "IBM", "CSCO", "ANET",
        # Fintech
        "V", "MA", "FIS", "COIN",
        # Other notable tech
        "SMCI", "DOCS", "HUBS", "TWLO", "ZM", "OKTA",
    ]


def get_ai_leaders() -> list[str]:
    """AI and machine learning focused companies (~55 tickers)."""
    return [
        # AI infrastructure — chips
        "NVDA", "AMD", "INTC", "AVGO", "QCOM", "MRVL", "ARM", "TSM", "ASML", "AMAT", "LRCX", "MU", "SMCI",
        # AI infrastructure — cloud / compute
        "MSFT", "GOOG", "AMZN", "META", "ORCL", "IBM",
        # AI software / platforms
        "PLTR", "AI", "SNOW", "DDOG", "MDB", "ESTC", "PATH", "BBAI",
        # AI cybersecurity
        "CRWD", "PANW", "ZS", "FTNT", "S",
        # AI in enterprise / SaaS
        "CRM", "NOW", "ADBE", "HUBS", "WDAY",
        # AI robotics / autonomous
        "TSLA", "ISRG", "TER", "ROKU",
        # AI edge / vision
        "ON", "CDNS", "SNPS",
        # AI healthcare
        "VEEV", "DXCM", "RXRX", "INCY",
        # AI data / analytics
        "TTD", "VERX", "DT", "CFLT",
        # AI networking
        "ANET", "CSCO", "NET",
        # Smaller AI pure-plays
        "SOUN", "IREN", "RGTI", "IONQ",
    ]


def get_space_defence() -> list[str]:
    """Space, aerospace, and defence companies (~50 tickers)."""
    return [
        # Pure-play space
        "RKLB", "ASTS", "LUNR", "RDW", "MNTS", "BKSY", "SPIR", "PL", "IRDM",
        # Space + defence primes
        "LMT", "RTX", "NOC", "GD", "BA", "LHX",
        # Space-adjacent / satellite
        "VSAT", "GSAT",
        # Defence tech
        "PLTR", "LDOS", "BAH", "CACI", "SAIC", "KTOS", "MRCY",
        # Aerospace suppliers
        "HWM", "TDG", "HEI", "AXON", "SPR", "ERJ", "TXT",
        # Drones / autonomous
        "AVAV", "JOBY", "ACHR",
        # Propulsion / engines
        "GE", "HII",
        # Defence electronics / sensors
        "FLIR", "DRS", "TATT",
        # Space infrastructure / materials
        "AJRD", "RPTX",
        # ETF proxies (for reference — these won't screen like stocks)
        # Broader defence
        "CW", "MOG-A", "WWD", "ESLT",
        # Nuclear / energy for space
        "OKLO", "NNE", "SMR", "LEU",
    ]


def get_growth_50() -> list[str]:
    """High-growth companies across sectors (~50 tickers)."""
    return [
        # Tech growth
        "NVDA", "PLTR", "CRWD", "NET", "DDOG", "SNOW", "MDB", "SHOP", "TTD", "ARM",
        # AI growth
        "SMCI", "SOUN", "AI", "PATH", "ESTC",
        # Fintech
        "SQ", "COIN", "AFRM", "SOFI", "HOOD",
        # Consumer / digital
        "ABNB", "UBER", "RBLX", "SPOT", "DUOL", "CELH",
        # Healthcare growth
        "ISRG", "DXCM", "VEEV", "RXRX", "HIMS",
        # Energy transition
        "ENPH", "FSLR", "PLUG", "RUN",
        # Space / defence growth
        "RKLB", "AXON", "JOBY", "ASTS",
        # Industrials growth
        "TOST", "MNDY", "IOT",
        # Biotech growth
        "CRSP", "NTLA", "BEAM",
        # Misc high-growth
        "ANET", "FTNT", "PANW", "ZS", "CFLT", "DT",
    ]


def get_broad_market() -> list[str]:
    """
    Broad cross-sector US stock universe (~300 tickers).
    Combines all curated thematic lists with expanded coverage across
    healthcare, financials, consumer, industrials, energy, and emerging growth.
    """
    # ── Mega / large cap tech ──────────────────────────────────────────
    mega = [
        "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "BRK-B", "LLY",
    ]
    # ── Semiconductors ─────────────────────────────────────────────────
    semis = [
        "AMD", "INTC", "AVGO", "QCOM", "TXN", "MU", "MRVL", "ARM", "TSM", "ASML",
        "LRCX", "KLAC", "AMAT", "ON", "SMCI", "WOLF", "SWKS", "MPWR", "ENTG", "ONTO",
        "COHR", "ALGM", "NXPI", "ADI", "MCHP",
    ]
    # ── Cloud, SaaS & Software ─────────────────────────────────────────
    software = [
        "CRM", "ORCL", "ADBE", "NOW", "SNOW", "PLTR", "DDOG", "NET", "ZS", "CRWD",
        "PANW", "FTNT", "MDB", "WDAY", "HUBS", "TWLO", "OKTA", "GTLB", "BILL", "COUP",
        "SMAR", "BOX", "DOCN", "WIX", "FROG", "APPN", "NCNO", "TASK", "PCTY", "PAYC",
        "ESTC", "DT", "CFLT", "DOMO", "ALRM",
    ]
    # ── AI pure-plays ──────────────────────────────────────────────────
    ai_pure = [
        "AI", "PATH", "SOUN", "BBAI", "IONQ", "RGTI", "QUBT", "ARQQ", "IREN", "CORZ",
        "HUT", "BTBT",
    ]
    # ── Internet & digital media ───────────────────────────────────────
    internet = [
        "NFLX", "SHOP", "UBER", "ABNB", "SPOT", "RBLX", "TTD", "PINS", "SNAP", "DUOL",
        "LYFT", "DASH", "ETSY", "EBAY", "IAC", "ZG", "TRIP", "BKNG", "EXPE", "CARG",
        "YELP", "ANGI",
    ]
    # ── Fintech & payments ─────────────────────────────────────────────
    fintech = [
        "V", "MA", "PYPL", "SQ", "COIN", "AFRM", "SOFI", "HOOD", "NU", "RELY",
        "ADYEY", "FIS", "FISV", "GPN", "WEX", "PAYO", "FLYW", "STEP",
    ]
    # ── US banks & traditional finance ────────────────────────────────
    banks = [
        "JPM", "BAC", "WFC", "GS", "MS", "C", "AXP", "COF", "BLK", "SCHW",
        "USB", "PNC", "TFC", "MTB", "FITB", "RF", "CFG", "HBAN", "KEY", "STT",
        "BX", "KKR", "APO", "ARES", "CG", "IVZ", "TROW", "AMG",
    ]
    # ── Healthcare & pharma ────────────────────────────────────────────
    health = [
        "JNJ", "UNH", "LLY", "ABBV", "PFE", "MRK", "AMGN", "GILD", "ISRG", "DXCM",
        "VEEV", "REGN", "HIMS", "RXRX", "TMO", "DHR", "ABT", "MDT", "BSX", "EW",
        "ZBH", "SYK", "HOLX", "PODD", "INSP", "AXNX", "NVCR", "MRUS",
        "MRNA", "BNTX", "NVAX", "SGEN", "ALNY", "BMRN", "RARE",
    ]
    # ── Biotech growth ─────────────────────────────────────────────────
    biotech = [
        "CRSP", "NTLA", "BEAM", "EDIT", "ARKG", "FATE", "KYMR", "LEGN",
        "INCY", "EXAS", "NTRA", "TDOC", "ACAD", "IONS", "PTGX",
    ]
    # ── Consumer discretionary & retail ───────────────────────────────
    consumer = [
        "AMZN", "WMT", "COST", "TGT", "HD", "LOW", "NKE", "LULU", "RH", "CPRI",
        "TPR", "PVH", "RL", "ANF", "AEO", "URBN", "ROST", "TJX", "BURL", "GPS",
        "DG", "DLTR", "FIVE", "OLLI", "BJ",
    ]
    # ── Consumer staples ──────────────────────────────────────────────
    staples = [
        "PG", "KO", "PEP", "MCD", "SBUX", "CMG", "YUM", "QSR", "DPZ", "WEN",
        "MNST", "CELH", "KHC", "GIS", "K", "SJM", "MKC", "CHD", "CLX", "CL",
    ]
    # ── Energy ────────────────────────────────────────────────────────
    energy = [
        "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "VLO", "MPC", "OXY", "DVN",
        "FANG", "HES", "APA", "MRO", "HAL", "BKR", "NOV", "WHD",
        "ENPH", "FSLR", "PLUG", "RUN", "ARRY", "NOVA", "SEDG",
    ]
    # ── Industrials & aerospace ───────────────────────────────────────
    industrials = [
        "GE", "CAT", "HON", "BA", "RTX", "LMT", "NOC", "GD", "DE", "HWM", "TDG",
        "CTAS", "RSG", "WM", "ROK", "EMR", "ETN", "IR", "PH", "ITW", "MMM",
        "XYL", "XYLD", "GNRC", "CARR", "OTIS", "TXT", "HII", "CW",
    ]
    # ── Space & defence growth ────────────────────────────────────────
    space_def = [
        "RKLB", "AXON", "KTOS", "ASTS", "MRCY", "LDOS", "BAH", "CACI", "SAIC",
        "AVAV", "JOBY", "ACHR", "LILM", "EVEX",
    ]
    # ── Communications & media ────────────────────────────────────────
    comms = [
        "T", "VZ", "CMCSA", "DIS", "PARA", "WBD", "FOXA", "NWSA", "NYT",
        "SIRI", "LSXMA", "CHTR",
    ]
    # ── Real estate ───────────────────────────────────────────────────
    realestate = [
        "O", "PLD", "AMT", "EQIX", "CCI", "SBAC", "VICI", "SPG", "EXR", "PSA",
        "ARE", "VTR", "WELL", "DLR", "NNN",
    ]
    # ── Materials & mining ────────────────────────────────────────────
    materials = [
        "FCX", "NEM", "GOLD", "AEM", "WPM", "PAAS", "AA", "ALB", "MP", "LTHM",
        "SQM", "LAC", "PLL", "ALTM",
    ]
    # ── Emerging / speculative growth ─────────────────────────────────
    emerging = [
        "TOST", "MNDY", "IOT", "CFLT", "ANET", "DELL", "SMCI", "GTLB", "CELH",
        "HIMS", "ASTS", "RKLB", "SOUN", "IONQ", "RGTI", "CORZ", "HUT", "MARA",
        "RIOT", "CLSK", "CIFR", "BTBT",
    ]

    seen: set[str] = set()
    result: list[str] = []
    for group in [
        mega, semis, software, ai_pure, internet, fintech, banks,
        health, biotech, consumer, staples, energy, industrials,
        space_def, comms, realestate, materials, emerging,
    ]:
        for t in group:
            if t not in seen:
                seen.add(t)
                result.append(t)
    logger.info(f"Broad market universe: {len(result)} tickers")
    return result


def get_all_curated() -> list[str]:
    """Combined list of all curated tickers, deduplicated (~130 unique)."""
    seen = set()
    result = []
    for fn in [get_tech_mega, get_ai_leaders, get_space_defence, get_growth_50]:
        for t in fn():
            if t not in seen:
                seen.add(t)
                result.append(t)
    return result


def get_trading212_isa() -> list[str]:
    """
    Curated selection of stocks available on Trading 212's Stocks & Shares ISA.
    Covers popular US stocks across all major sectors, plus notable UK-listed stocks
    (with .L suffix for London Stock Exchange).

    Note: T212 offers 10,000+ instruments — this is a hand-picked selection of the
    most relevant names for fundamental analysis. US stocks dominate because T212's
    US coverage is near-complete and yfinance data is most reliable there.
    """
    us_stocks = [
        # ── Mega caps ──
        "AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA", "BRK-B",
        # ── Semiconductors & AI chips ──
        "AMD", "INTC", "AVGO", "QCOM", "TXN", "MU", "MRVL", "ARM", "TSM",
        "ASML", "LRCX", "KLAC", "AMAT", "ON", "SMCI",
        # ── Cloud & Software ──
        "CRM", "ORCL", "ADBE", "NOW", "SNOW", "PLTR", "DDOG", "NET", "ZS",
        "CRWD", "PANW", "FTNT", "MDB", "WDAY", "HUBS", "TWLO", "OKTA",
        # ── AI pure-plays ──
        "AI", "PATH", "SOUN", "BBAI", "IONQ", "RGTI",
        # ── Internet & Digital Media ──
        "NFLX", "SHOP", "UBER", "ABNB", "SPOT", "RBLX", "TTD", "PINS", "SNAP", "DUOL",
        # ── Payments & Fintech ──
        "V", "MA", "PYPL", "SQ", "COIN", "AFRM", "SOFI", "HOOD",
        # ── US Banks & Finance ──
        "JPM", "BAC", "WFC", "GS", "MS", "C", "AXP", "COF", "BLK", "SCHW",
        # ── Healthcare & Pharma ──
        "JNJ", "UNH", "LLY", "ABBV", "PFE", "MRK", "AMGN", "GILD", "ISRG",
        "DXCM", "VEEV", "REGN", "HIMS", "RXRX",
        # ── Consumer staples & retail ──
        "WMT", "COST", "PG", "KO", "PEP", "MCD", "SBUX", "NKE", "TGT", "HD", "LOW",
        # ── Energy ──
        "XOM", "CVX", "COP", "SLB", "EOG",
        # ── Industrials & Aerospace ──
        "GE", "CAT", "HON", "BA", "RTX", "LMT", "NOC", "GD", "DE", "HWM", "TDG",
        # ── Space & Defence growth ──
        "RKLB", "AXON", "KTOS", "ASTS",
        # ── Communications & Media ──
        "T", "VZ", "CMCSA", "DIS", "NFLX",
        # ── Real estate ──
        "O", "PLD", "AMT", "EQIX",
        # ── High growth misc ──
        "CELH", "TOST", "MNDY", "IOT", "CFLT", "DT", "ANET", "DELL",
    ]

    uk_stocks = [
        # ── FTSE 100 — large caps ──
        "SHEL.L", "AZN.L", "HSBA.L", "BP.L", "RIO.L", "GSK.L", "ULVR.L",
        "DGE.L", "LLOY.L", "BARC.L", "NWG.L", "LGEN.L", "PRU.L",
        "VOD.L", "NG.L", "REL.L", "EXPN.L",
        # ── UK growth / tech ──
        "AUTO.L", "OCDO.L", "RR.L",
        # ── UK defence / aerospace ──
        "BA.L", "QQ.L",
        # ── UK consumer / finance ──
        "IMB.L", "BATS.L", "WISE.L",
    ]

    seen: set[str] = set()
    result: list[str] = []
    for t in us_stocks + uk_stocks:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


UNIVERSE_MAP = {
    "sp500": get_sp500,
    "nasdaq100": get_nasdaq100,
    "ftse100": get_ftse100,
    "tech": get_tech_mega,
    "ai": get_ai_leaders,
    "space": get_space_defence,
    "growth": get_growth_50,
    "all_curated": get_all_curated,
    "broad": get_broad_market,
    "t212": get_trading212_isa,
}


def get_universe(name: str) -> list[str]:
    """Get a named universe. Raises ValueError if unknown."""
    if name in UNIVERSE_MAP:
        return UNIVERSE_MAP[name]()
    raise ValueError(f"Unknown universe: {name}. Available: {list(UNIVERSE_MAP.keys())}")
