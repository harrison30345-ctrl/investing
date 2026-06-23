#!/usr/bin/env python3
"""
crypto_screener.py — Crypto early-opportunity screener.

Uses only the CoinGecko FREE public API (no key required).
Four signals:
  1. Trending coins      (social/search momentum — often leads price)
  2. Recently listed     (coins first seen on CoinGecko in the last 30 days)
  3. Low-cap momentum    (Brian Jung style: small cap + strong 7d run + liquidity)
  4. Coinbase watchlist  (cross-reference against a Coinbase asset list)

Run:  python3 crypto_screener.py
Deps: pip install requests rich pandas
"""

import os
import time
import datetime as dt

import requests
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# NOTE: the real CoinGecko free API lives at api.coingecko.com (the
# "geckoapi.com" host in the original spec does not resolve). Using the
# correct base URL so the tool actually works with no API key.
BASE_URL = "https://api.coingecko.com/api/v3"
# Public Coinbase Exchange currencies list (no key, returns ~500 listed assets).
# The original assets.coinbase.com CDN 403s automated clients, so we use the
# documented public exchange endpoint instead.
COINBASE_ASSETS_URL = "https://api.exchange.coinbase.com/currencies"

SLEEP_BETWEEN_CALLS = 1.5      # be polite to the free rate limit
MAX_RETRIES = 2
RETRY_SLEEP = 3

STABLECOIN_MARKERS = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "FRAX", "USDD"}

# Some hosts (e.g. Coinbase's asset CDN) 403 the default python-requests UA.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")

console = Console()


# ---------------------------------------------------------------------------
# HTTP helper with retry + rate-limit sleep
# ---------------------------------------------------------------------------
def get_json(url, params=None):
    """GET with up to MAX_RETRIES retries. Returns parsed JSON or None on failure."""
    attempt = 0
    while attempt <= MAX_RETRIES:
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            time.sleep(SLEEP_BETWEEN_CALLS)   # rate-limit cushion after every call
            return data
        except Exception as exc:  # noqa: BLE001 — we want to catch everything and retry
            attempt += 1
            if attempt > MAX_RETRIES:
                console.print(f"[yellow]⚠ Request failed ({url}): {exc}[/yellow]")
                return None
            console.print(
                f"[dim]Retry {attempt}/{MAX_RETRIES} for {url} after error: {exc}[/dim]"
            )
            time.sleep(RETRY_SLEEP)
    return None


def is_stablecoin(name, symbol):
    name_u = (name or "").upper()
    sym_u = (symbol or "").upper()
    if sym_u in STABLECOIN_MARKERS:
        return True
    return any(marker in name_u or marker in sym_u for marker in STABLECOIN_MARKERS)


# ---------------------------------------------------------------------------
# Section 1 — Trending coins
# ---------------------------------------------------------------------------
def fetch_trending():
    """Top trending coins by CoinGecko search volume."""
    data = get_json(f"{BASE_URL}/search/trending")
    if not data:
        return []
    rows = []
    for entry in data.get("coins", [])[:7]:
        item = entry.get("item", {})
        rows.append(
            {
                "section": "trending",
                "name": item.get("name"),
                "symbol": (item.get("symbol") or "").upper(),
                "market_cap_rank": item.get("market_cap_rank"),
                "price_btc": item.get("price_btc"),
            }
        )
    return rows


def render_trending(rows):
    table = Table(title="🔥 Trending Coins (top 7)", title_style="bold yellow", header_style="yellow")
    table.add_column("Name", style="yellow")
    table.add_column("Symbol", style="yellow")
    table.add_column("Mkt Cap Rank", justify="right", style="yellow")
    table.add_column("Price (BTC)", justify="right", style="yellow")
    for r in rows:
        rank = str(r["market_cap_rank"]) if r["market_cap_rank"] is not None else "—"
        price_btc = f"{r['price_btc']:.10f}" if isinstance(r["price_btc"], (int, float)) else "—"
        table.add_row(r["name"] or "—", r["symbol"] or "—", rank, price_btc)
    console.print(table)


# ---------------------------------------------------------------------------
# Section 2 — Recently listed coins (last 30 days)
# ---------------------------------------------------------------------------
def fetch_recent_listings():
    """
    CoinGecko exposes a 'recently_added' market category. We pull markets ordered
    so the freshest listings surface, then keep those first seen within 30 days.
    The free /coins/markets endpoint doesn't return a listing date, so we use the
    'recently added' category which CoinGecko curates for new coins.
    """
    data = get_json(
        f"{BASE_URL}/coins/markets",
        params={
            "vs_currency": "usd",
            "category": "recently_added",
            "order": "market_cap_desc",
            "per_page": 50,
            "page": 1,
            "sparkline": "false",
        },
    )
    # Fallback: if the category isn't available, return empty so we skip cleanly.
    if not data or not isinstance(data, list):
        return []

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    rows = []
    for c in data:
        atl_date = c.get("atl_date")  # all-time-low date ≈ near listing for brand-new coins
        recent = True
        if atl_date:
            try:
                d = dt.datetime.fromisoformat(atl_date.replace("Z", "+00:00"))
                recent = d >= cutoff
            except (ValueError, AttributeError):
                recent = True
        if not recent:
            continue
        rows.append(
            {
                "section": "recent_listing",
                "name": c.get("name"),
                "symbol": (c.get("symbol") or "").upper(),
                "current_price": c.get("current_price"),
                "market_cap": c.get("market_cap"),
                "total_volume": c.get("total_volume"),
            }
        )
    return rows[:25]


def render_recent(rows):
    table = Table(title="🆕 Recently Listed (≈ last 30 days)", title_style="bold cyan", header_style="cyan")
    table.add_column("Name", style="cyan")
    table.add_column("Symbol", style="cyan")
    table.add_column("Price (USD)", justify="right", style="cyan")
    table.add_column("Market Cap", justify="right", style="cyan")
    table.add_column("24h Volume", justify="right", style="cyan")
    for r in rows:
        table.add_row(
            r["name"] or "—",
            r["symbol"] or "—",
            fmt_usd(r["current_price"]),
            fmt_usd(r["market_cap"]),
            fmt_usd(r["total_volume"]),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Section 3 — Low-cap momentum (Brian Jung style)
# ---------------------------------------------------------------------------
def fetch_momentum():
    data = get_json(
        f"{BASE_URL}/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 250,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "7d",
        },
    )
    if not data or not isinstance(data, list):
        return []

    rows = []
    for c in data:
        mcap = c.get("market_cap") or 0
        vol = c.get("total_volume") or 0
        chg7d = c.get("price_change_percentage_7d_in_currency")
        name, symbol = c.get("name"), c.get("symbol")

        if chg7d is None:
            continue
        if is_stablecoin(name, symbol):
            continue
        if not (5_000_000 <= mcap <= 300_000_000):
            continue
        if chg7d <= 15:
            continue
        if mcap == 0 or (vol / mcap) <= 0.1:
            continue

        rows.append(
            {
                "section": "momentum",
                "name": name,
                "symbol": (symbol or "").upper(),
                "current_price": c.get("current_price"),
                "market_cap": mcap,
                "total_volume": vol,
                "vol_mcap_ratio": vol / mcap,
                "price_change_percentage_7d": chg7d,
            }
        )
    rows.sort(key=lambda r: r["price_change_percentage_7d"], reverse=True)
    return rows


def render_momentum(rows):
    table = Table(
        title="🚀 Low-Cap Momentum ($5M–$300M, +15% 7d, vol/mcap > 0.1)",
        title_style="bold green",
        header_style="green",
    )
    table.add_column("Name", style="green")
    table.add_column("Symbol", style="green")
    table.add_column("Price (USD)", justify="right", style="green")
    table.add_column("Market Cap", justify="right", style="green")
    table.add_column("24h Volume", justify="right", style="green")
    table.add_column("Vol/MCap", justify="right", style="green")
    table.add_column("7d %", justify="right", style="green")
    for r in rows:
        table.add_row(
            r["name"] or "—",
            r["symbol"] or "—",
            fmt_usd(r["current_price"]),
            fmt_usd(r["market_cap"]),
            fmt_usd(r["total_volume"]),
            f"{r['vol_mcap_ratio']:.2f}",
            f"[bold]{r['price_change_percentage_7d']:.1f}%[/bold]",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Section 4 — Coinbase listing watchlist / crossover
# ---------------------------------------------------------------------------
def fetch_coinbase_symbols():
    """Pull the set of asset tickers Coinbase publishes."""
    data = get_json(COINBASE_ASSETS_URL)
    symbols = set()
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                # exchange /currencies uses 'id' as the ticker (e.g. "BTC")
                sym = item.get("id") or item.get("symbol") or item.get("code")
                if sym:
                    symbols.add(str(sym).upper())
            elif isinstance(item, str):
                symbols.add(item.upper())
    elif isinstance(data, dict):
        # some payloads nest under a key
        for key in ("data", "assets", "currencies"):
            if isinstance(data.get(key), list):
                for item in data[key]:
                    if isinstance(item, dict):
                        sym = item.get("id") or item.get("symbol") or item.get("code")
                    else:
                        sym = item
                    if sym:
                        symbols.add(str(sym).upper())
    return symbols


def coinbase_crossover(coinbase_symbols, trending_rows, momentum_rows):
    """Flag trending/momentum coins whose ticker is on the Coinbase list."""
    if not coinbase_symbols:
        return []
    matches = []
    seen = set()
    for r in trending_rows + momentum_rows:
        sym = r["symbol"]
        if sym and sym in coinbase_symbols and sym not in seen:
            seen.add(sym)
            matches.append(
                {
                    "section": "coinbase_crossover",
                    "name": r["name"],
                    "symbol": sym,
                    "source": r["section"],
                }
            )
    return matches


def render_crossover(matches):
    if not matches:
        console.print(
            Panel("No Coinbase crossovers in this scan.", title="Coinbase Watchlist", style="dim")
        )
        return
    table = Table(title="⭐ HIGH PRIORITY — Coinbase Crossover", title_style="bold red", header_style="bold red")
    table.add_column("Name", style="bold red")
    table.add_column("Symbol", style="bold red")
    table.add_column("Signal Source", style="bold red")
    for m in matches:
        table.add_row(m["name"] or "—", m["symbol"], m["source"])
    console.print(Panel.fit(table, border_style="bold red"))


# ---------------------------------------------------------------------------
# Helpers + output
# ---------------------------------------------------------------------------
def fmt_usd(val):
    if val is None or val == "":
        return "—"
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "—"
    if val >= 1_000_000_000:
        return f"${val/1_000_000_000:.2f}B"
    if val >= 1_000_000:
        return f"${val/1_000_000:.2f}M"
    if val >= 1:
        return f"${val:,.2f}"
    return f"${val:.6f}"


def save_csv(all_rows):
    if not all_rows:
        console.print("[yellow]Nothing to save — all sections were empty.[/yellow]")
        return
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = dt.date.today().isoformat()
    path = os.path.join(OUTPUT_DIR, f"crypto_scan_{today}.csv")
    pd.DataFrame(all_rows).to_csv(path, index=False)
    console.print(f"[green]✔ Saved results to {path}[/green]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    console.rule("[bold]Crypto Early-Opportunity Screener[/bold]")
    all_rows = []

    # 1. Trending
    trending_rows = []
    try:
        trending_rows = fetch_trending()
        if trending_rows:
            render_trending(trending_rows)
            all_rows.extend(trending_rows)
        else:
            console.print("[yellow]⚠ Skipping Trending — no data.[/yellow]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]⚠ Trending section failed: {exc}[/yellow]")

    # 2. Recent listings
    recent_rows = []
    try:
        recent_rows = fetch_recent_listings()
        if recent_rows:
            render_recent(recent_rows)
            all_rows.extend(recent_rows)
        else:
            console.print("[yellow]⚠ Skipping Recent Listings — no data.[/yellow]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]⚠ Recent Listings section failed: {exc}[/yellow]")

    # 3. Momentum
    momentum_rows = []
    try:
        momentum_rows = fetch_momentum()
        if momentum_rows:
            render_momentum(momentum_rows)
            all_rows.extend(momentum_rows)
        else:
            console.print("[yellow]⚠ Skipping Momentum — no coins matched filters.[/yellow]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]⚠ Momentum section failed: {exc}[/yellow]")

    # 4. Coinbase crossover
    try:
        coinbase_symbols = fetch_coinbase_symbols()
        matches = coinbase_crossover(coinbase_symbols, trending_rows, momentum_rows)
        render_crossover(matches)
        all_rows.extend(matches)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]⚠ Coinbase crossover section failed: {exc}[/yellow]")

    # Save + timestamp
    save_csv(all_rows)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.print(Panel.fit(f"Scan completed: {stamp}", style="bold white"))


if __name__ == "__main__":
    main()
