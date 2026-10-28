"""
Headlines, tied to companies.

WHAT THIS DOES AND DOES NOT DO
------------------------------
It fetches headlines and says which companies they mention. It does not read
them, score them, or decide whether a story is good or bad news. That restraint
is deliberate: sentiment scored off a headline is wrong often enough to be
misleading, and a confident label on a misread story is worse than no label.

The reader gets the publisher, the time and the link, and does the judging.

PROVIDER
--------
Yahoo's search endpoint, through yfinance, which is the same unlicensed
development source as the rest of the app -- see the licensing note at the top
of services/market_data.py. yfinance's per-ticker `.news` property returns
nothing on the installed version, so `yf.Search` is used instead.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

import yfinance as yf

__all__ = [
    "Headline", "for_tickers", "for_queries", "MARKET_QUERIES", "MAX_WORKERS",
    "MAX_AGE_DAYS",
]

MAX_WORKERS = 8
MAX_AGE_DAYS = 14       # older stories are history, not news

# Broad queries for market-wide news. Plain subjects, not opinions: the point is
# to surface what is being written about the economy, not to search for a view.
MARKET_QUERIES = (
    "stock market",
    "Federal Reserve interest rates",
    "inflation economy",
    "S&P 500 earnings",
)


@dataclass(frozen=True)
class Headline:
    uid: str
    title: str
    publisher: str
    url: str
    published: datetime | None = None
    tickers: tuple = ()

    @property
    def age(self) -> str:
        """How long ago, in words. 'Date unknown' rather than a guess."""
        if self.published is None:
            return "Date unknown"
        delta = datetime.now(timezone.utc) - self.published
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{max(1, int(delta.total_seconds() // 60))}m ago"
        if hours < 24:
            return f"{int(hours)}h ago"
        days = int(hours // 24)
        return "Yesterday" if days == 1 else f"{days}d ago"

    @property
    def fresh(self) -> bool:
        """Published in the last two days."""
        if self.published is None:
            return False
        return (datetime.now(timezone.utc) - self.published).total_seconds() < 172800


def _normalise(title: str) -> str:
    """A key for spotting the same story republished under a tweaked headline."""
    return re.sub(r"[^a-z0-9 ]", "", (title or "").lower()).strip()


def _base(symbol: str) -> str:
    """AAPL from AAPL, BP from BP.L -- exchange suffixes must not block a match."""
    return str(symbol or "").upper().split(".")[0].strip()


# Words that are also tickers. A headline saying "ALL eyes on the Fed" is not
# news about Allstate, and "ON" and "IT" would match almost every headline.
_TICKER_STOPWORDS = {
    "ALL", "ON", "IT", "SO", "BE", "DO", "GO", "AN", "AT", "BY", "OR", "UP",
    "NOW", "NEW", "CAN", "KEY", "RUN", "SEE", "TRUE", "OUT", "FOR", "ARE",
}


def _mentions(title: str, ticker: str, name: str | None = None) -> bool:
    """Whether a headline is genuinely about this company.

    Yahoo's search returns broad market stories for a ticker query, so the
    company cannot simply be assumed from what was searched for -- doing that
    tags an unrelated earnings story with whichever holding fetched it.
    """
    base = _base(ticker)
    if len(base) >= 2 and base not in _TICKER_STOPWORDS:
        if re.search(rf"\b{re.escape(base)}\b", title or ""):
            return True

    if name:
        # The distinctive part of the name: "Apple" from "Apple Inc.",
        # "Lloyds" from "Lloyds Banking Group plc".
        cleaned = re.sub(r"\b(inc|corp|corporation|plc|ltd|limited|group|holdings|"
                         r"company|co|sa|nv|ag|the|class [a-c])\b\.?", "",
                         name, flags=re.IGNORECASE)
        word = next((w for w in re.findall(r"[A-Za-z][A-Za-z&'-]{2,}", cleaned)), "")
        if word and re.search(rf"\b{re.escape(word)}\b", title or "", re.IGNORECASE):
            return True
    return False


def _parse(item: dict) -> Headline | None:
    title = (item.get("title") or "").strip()
    link = (item.get("link") or "").strip()
    if not title or not link:
        return None

    when = None
    stamp = item.get("providerPublishTime")
    if stamp:
        try:
            when = datetime.fromtimestamp(float(stamp), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            when = None

    related = [str(t).upper() for t in (item.get("relatedTickers") or [])
               if t and not str(t).startswith("^")]

    return Headline(
        uid=str(item.get("uuid") or link),
        title=title,
        publisher=(item.get("publisher") or "Unknown publisher").strip(),
        url=link,
        published=when,
        tickers=tuple(dict.fromkeys(related)),
    )


def _search(query: str, count: int) -> list:
    try:
        results = yf.Search(query, news_count=count, max_results=0).news or []
    except Exception:
        return []        # a failed search is an empty section, never an error page
    out = []
    for item in results:
        if isinstance(item, dict):
            parsed = _parse(item)
            if parsed is not None:
                out.append(parsed)
    return out


def _merge(batches: list) -> list:
    """Flatten, drop duplicates, newest first.

    Two keys, because the same wire story appears under different uuids with
    near-identical headlines, and under the same uuid for several tickers.
    """
    seen_uid, seen_title, merged = set(), set(), {}
    for headline in [h for batch in batches for h in batch]:
        title_key = _normalise(headline.title)
        if headline.uid in seen_uid or (title_key and title_key in seen_title):
            # Keep the one we already have, but remember it mentions this
            # company too -- that is how one story shows up under two holdings.
            existing = merged.get(headline.uid)
            if existing is not None and headline.tickers:
                tickers = tuple(dict.fromkeys(existing.tickers + headline.tickers))
                merged[headline.uid] = Headline(
                    existing.uid, existing.title, existing.publisher,
                    existing.url, existing.published, tickers,
                )
            continue
        seen_uid.add(headline.uid)
        if title_key:
            seen_title.add(title_key)
        merged[headline.uid] = headline

    return sorted(merged.values(),
                  key=lambda h: h.published or datetime.min.replace(tzinfo=timezone.utc),
                  reverse=True)


def _recent(headlines: list, max_age_days: int | None) -> list:
    if not max_age_days:
        return headlines
    cutoff = max_age_days * 86400
    now = datetime.now(timezone.utc)
    return [h for h in headlines
            if h.published is None
            or (now - h.published).total_seconds() <= cutoff]


def for_tickers(tickers, per_ticker: int = 6, names: dict | None = None,
                max_age_days: int | None = MAX_AGE_DAYS) -> list:
    """Headlines genuinely about these companies, newest first.

    Only headlines that name the company -- in Yahoo's related tickers, in the
    ticker text, or by company name -- are returned, and each one's `tickers`
    field is narrowed to the companies asked about that it actually mentions.
    Broad market stories picked up by a ticker search are dropped here; they
    belong to `for_queries`, which does not claim they are about anyone.

    Fetched in parallel: one HTTP round trip per company, and a dozen companies
    in sequence is a page that feels broken.
    """
    wanted = [str(t).strip().upper() for t in dict.fromkeys(tickers) if str(t).strip()]
    if not wanted:
        return []
    names = names or {}

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(wanted))) as pool:
        batches = list(pool.map(lambda t: _search(t, per_ticker), wanted))

    # Attribution comes from the headline text alone. Yahoo's own
    # `relatedTickers` field is far too loose to use: it lists NVDA as related
    # to a UPS earnings story, presumably from a passing mention in the body,
    # and trusting it files unrelated news under a holding. The field is kept on
    # the Headline for reference but is not evidence that a story is about a
    # company. The cost is missing stories that never name the company in the
    # headline; that is the right way round to be wrong.
    attributed = []
    for headline in _merge(batches):
        matched = [t for t in wanted
                   if _mentions(headline.title, t, names.get(t))]
        if matched:
            attributed.append(Headline(
                headline.uid, headline.title, headline.publisher, headline.url,
                headline.published, tuple(matched),
            ))
    return _recent(attributed, max_age_days)


def for_queries(queries=MARKET_QUERIES, per_query: int = 6,
                max_age_days: int | None = MAX_AGE_DAYS) -> list:
    """Headlines for broad subjects -- the economy, rates, the market itself.

    These are not attributed to any company: a story about the Fed is about the
    Fed, and tagging it with whatever ticker surfaced it would be an invention.
    """
    terms = [q for q in dict.fromkeys(queries) if q]
    if not terms:
        return []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(terms))) as pool:
        batches = list(pool.map(lambda q: _search(q, per_query), terms))
    return _recent(_merge(batches), max_age_days)
