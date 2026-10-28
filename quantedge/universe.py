"""
Curated instrument catalogue.

Internal symbols follow Yahoo conventions (`SHEL.L` for London, `^FTSE` for
indices) because that's the default provider. Other providers translate
from these symbols in their adapters.

The equity lists are *today's* large caps. Any backtest over them carries
survivorship bias: companies that fell out of the index or went bust are
missing. Experiments record this caveat automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .markets import market_for_symbol


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    market: str      # "UK" | "US"
    kind: str        # "index" | "equity" | "etf"
    currency: str    # "GBX" (pence) | "USD" | "PTS" (index points)


INDICES = [
    Instrument("^FTSE", "FTSE 100", "UK", "index", "PTS"),
    Instrument("^FTMC", "FTSE 250", "UK", "index", "PTS"),
    Instrument("^GSPC", "S&P 500", "US", "index", "PTS"),
    Instrument("^IXIC", "Nasdaq Composite", "US", "index", "PTS"),
    Instrument("^DJI", "Dow Jones Industrial Avg", "US", "index", "PTS"),
]

BENCHMARKS = [
    Instrument("SPY", "SPDR S&P 500 ETF", "US", "etf", "USD"),
    Instrument("QQQ", "Invesco QQQ (Nasdaq-100) ETF", "US", "etf", "USD"),
    Instrument("ISF.L", "iShares Core FTSE 100 ETF", "UK", "etf", "GBX"),
]

_US = [
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "NVIDIA"), ("AMZN", "Amazon"),
    ("GOOGL", "Alphabet A"), ("META", "Meta Platforms"), ("AVGO", "Broadcom"),
    ("TSLA", "Tesla"), ("JPM", "JPMorgan Chase"), ("V", "Visa"), ("UNH", "UnitedHealth"),
    ("XOM", "Exxon Mobil"), ("LLY", "Eli Lilly"), ("JNJ", "Johnson & Johnson"),
    ("PG", "Procter & Gamble"), ("MA", "Mastercard"), ("HD", "Home Depot"),
    ("COST", "Costco"), ("KO", "Coca-Cola"), ("AMD", "Advanced Micro Devices"),
]

_UK = [
    ("SHEL.L", "Shell"), ("AZN.L", "AstraZeneca"), ("HSBA.L", "HSBC"),
    ("ULVR.L", "Unilever"), ("BP.L", "BP"), ("RIO.L", "Rio Tinto"),
    ("GSK.L", "GSK"), ("REL.L", "RELX"), ("BATS.L", "British American Tobacco"),
    ("LSEG.L", "London Stock Exchange Group"), ("DGE.L", "Diageo"),
    ("BARC.L", "Barclays"), ("LLOY.L", "Lloyds Banking Group"), ("RR.L", "Rolls-Royce"),
    ("NG.L", "National Grid"), ("BA.L", "BAE Systems"), ("TSCO.L", "Tesco"),
    ("GLEN.L", "Glencore"), ("VOD.L", "Vodafone"), ("AAL.L", "Anglo American"),
]

# The nine original Select Sector SPDRs (all launched Dec 1998, none closed). XLRE (2015) and
# XLC (2018) are left out so membership is constant over the whole history (Phase Three plan).
_SECTORS = [
    ("XLB", "Materials Select Sector SPDR"), ("XLE", "Energy Select Sector SPDR"),
    ("XLF", "Financial Select Sector SPDR"), ("XLI", "Industrial Select Sector SPDR"),
    ("XLK", "Technology Select Sector SPDR"), ("XLP", "Consumer Staples Select Sector SPDR"),
    ("XLU", "Utilities Select Sector SPDR"), ("XLV", "Health Care Select Sector SPDR"),
    ("XLY", "Consumer Discretionary Select Sector SPDR"),
]
SECTOR_ETFS = [Instrument(s, n, "US", "etf", "USD") for s, n in _SECTORS]

EQUITIES = [Instrument(s, n, "US", "equity", "USD") for s, n in _US] + \
           [Instrument(s, n, "UK", "equity", "GBX") for s, n in _UK]

CATALOGUE: Dict[str, Instrument] = {i.symbol: i for i in INDICES + BENCHMARKS + EQUITIES + SECTOR_ETFS}

UNIVERSES: Dict[str, List[str]] = {
    "us_large": [s for s, _ in _US],
    "uk_large": [s for s, _ in _UK],
    "us_sectors": [s for s, _ in _SECTORS],
    # Liquid US names for 5-minute research; Yahoo keeps ~60 days of 5-minute history.
    "us_intraday": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN"],
}

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "JPM", "SHEL.L", "AZN.L", "HSBA.L", "RR.L"]


def get(symbol: str) -> Instrument:
    """Catalogue entry, or a best-guess placeholder for symbols outside it."""
    symbol = symbol.strip().upper()
    if symbol in CATALOGUE:
        return CATALOGUE[symbol]
    market = market_for_symbol(symbol)
    kind = "index" if symbol.startswith("^") else "equity"
    currency = "PTS" if kind == "index" else ("GBX" if market == "UK" else "USD")
    return Instrument(symbol, symbol, market, kind, currency)


def search(query: str, market: Optional[str] = None, limit: int = 20) -> List[Instrument]:
    q = query.strip().lower()
    hits = [
        i for i in CATALOGUE.values()
        if (not market or i.market == market) and (q in i.symbol.lower() or q in i.name.lower())
    ]
    hits.sort(key=lambda i: (not i.symbol.lower().startswith(q), i.symbol))
    return hits[:limit]
