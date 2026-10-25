#!/usr/bin/env python3
"""
crypto_dd.py — Crypto due-diligence scanner (auto-discover mode).

Surfaces NEWER, LOW-CAP tokens and scores each on the *objective* signals that
correlate with a legitimate, capable team — so you can spend your research time
on the few that aren't obvious junk.

It does NOT tell you what to buy, and it does NOT judge how "smart" a team is.
There is no free, reliable data source for that. What it CAN measure:

  • Real development activity   (GitHub: does the team actually ship code?)
  • Dilution / unlock risk      (market cap vs fully-diluted valuation)
  • Liquidity sanity            (volume vs market cap — real interest? wash?)
  • Legitimacy basics           (website, whitepaper, audit, project age, links)

LIMITATION (read this): the single strongest automatable proxy for "good team"
is *which reputable VCs funded them* — top funds do months of team diligence you
can't replicate. That data (DeFiLlama's raises API) is now paywalled, so this
tool can't include it for free. Treat strong backers, found manually, as a big
plus on top of whatever this surfaces.

Run:  python3 crypto_dd.py
Deps: pip install requests rich pandas

NOT FINANCIAL ADVICE. Newer low-cap tokens are the highest-risk part of the
market. This is a research filter, not a recommendation engine.
"""

import os
import time
import datetime as dt

import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# Reuse the rate-limited, retrying HTTP helper from the screener.
import crypto_screener as cs

console = Console()

CG = cs.BASE_URL
GITHUB_API = "https://api.github.com/repos"
OUTPUT_DIR = cs.OUTPUT_DIR

# Candidate universe: small but real. Tweak to taste.
MIN_MCAP = 3_000_000
MAX_MCAP = 250_000_000
MAX_CANDIDATES = 12          # each costs 2 API calls; the free tier rate-limits hard
CG_CALL_SPACING = 4.0       # extra seconds between per-coin CoinGecko detail calls

NOW = dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# 1. Discover candidates
# ---------------------------------------------------------------------------
def discover_candidates():
    """
    Build a candidate pool of newer, low-cap coins:
      - CoinGecko 'recently_added' category (newest listings), plus
      - low-cap coins by volume (small but liquid/real)
    Deduped, trimmed to MAX_CANDIDATES.
    """
    pool = {}

    recent = cs.get_json(
        f"{CG}/coins/markets",
        params={
            "vs_currency": "usd",
            "category": "recently_added",
            "order": "market_cap_desc",
            "per_page": 50,
            "page": 1,
            "sparkline": "false",
        },
    ) or []

    by_vol = cs.get_json(
        f"{CG}/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 250,
            "page": 1,
            "sparkline": "false",
        },
    ) or []

    for c in (recent if isinstance(recent, list) else []) + (by_vol if isinstance(by_vol, list) else []):
        mcap = c.get("market_cap") or 0
        if not (MIN_MCAP <= mcap <= MAX_MCAP):
            continue
        if cs.is_stablecoin(c.get("name"), c.get("symbol")):
            continue
        cid = c.get("id")
        if cid and cid not in pool:
            pool[cid] = c

    # Prefer the smallest caps (most "early"), cap the count.
    candidates = sorted(pool.values(), key=lambda c: c.get("market_cap") or 0)
    return candidates[:MAX_CANDIDATES]


# ---------------------------------------------------------------------------
# 2. Enrich one coin with detail + GitHub activity
# ---------------------------------------------------------------------------
def github_pushed_at(repo_url):
    """Return (last_push_datetime, stars) for a github repo URL, or (None, None)."""
    if not repo_url or "github.com/" not in repo_url:
        return None, None
    path = repo_url.split("github.com/", 1)[1].strip("/")
    # keep owner/repo only
    parts = path.split("/")
    if len(parts) < 2:
        return None, None
    owner_repo = f"{parts[0]}/{parts[1]}"
    data = cs.get_json(f"{GITHUB_API}/{owner_repo}")
    if not isinstance(data, dict) or "pushed_at" not in data:
        return None, None
    try:
        pushed = dt.datetime.fromisoformat(data["pushed_at"].replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        pushed = None
    return pushed, data.get("stargazers_count")


def enrich(coin):
    cid = coin.get("id")
    detail = cs.get_json(
        f"{CG}/coins/{cid}",
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "true",
        },
    ) or {}

    links = detail.get("links", {}) or {}
    dev = detail.get("developer_data", {}) or {}
    md = detail.get("market_data", {}) or {}

    repos = (links.get("repos_url", {}) or {}).get("github") or []
    repo_url = repos[0] if repos else None
    homepage = next((u for u in (links.get("homepage") or []) if u), None)
    whitepaper = links.get("whitepaper") or None

    pushed_at, gh_stars = github_pushed_at(repo_url)
    stars = gh_stars if gh_stars is not None else dev.get("stars")

    mcap = (md.get("market_cap", {}) or {}).get("usd")
    fdv = (md.get("fully_diluted_valuation", {}) or {}).get("usd")
    vol = (md.get("total_volume", {}) or {}).get("usd")

    genesis = detail.get("genesis_date")
    age_days = None
    if genesis:
        try:
            g = dt.datetime.fromisoformat(genesis).replace(tzinfo=dt.timezone.utc)
            age_days = (NOW - g).days
        except (ValueError, AttributeError):
            age_days = None

    days_since_push = (NOW - pushed_at).days if pushed_at else None

    return {
        "name": coin.get("name"),
        "symbol": (coin.get("symbol") or "").upper(),
        "market_cap": mcap,
        "fdv": fdv,
        "mcap_fdv_ratio": (mcap / fdv) if (mcap and fdv) else None,
        "total_volume": vol,
        "vol_mcap_ratio": (vol / mcap) if (vol and mcap) else None,
        "has_repo": bool(repo_url),
        "repo_url": repo_url,
        "days_since_commit": days_since_push,
        "stars": stars,
        "prs_merged": dev.get("pull_requests_merged"),
        "has_website": bool(homepage),
        "has_whitepaper": bool(whitepaper),
        "age_days": age_days,
        "categories": ", ".join((detail.get("categories") or [])[:2]) or None,
    }


# ---------------------------------------------------------------------------
# 3. Score + flag (transparent rubric, 0-100)
# ---------------------------------------------------------------------------
def score(c):
    pts, flags_red, flags_green = 0, [], []

    # --- Development activity (max 35) ---
    if c["has_repo"]:
        pts += 10
        d = c["days_since_commit"]
        if d is not None:
            if d <= 30:
                pts += 15; flags_green.append("ships code (commit <30d)")
            elif d <= 90:
                pts += 8
            elif d > 180:
                flags_red.append(f"repo stale ({d}d since commit)")
        stars = c["stars"] or 0
        if stars >= 500:
            pts += 5; flags_green.append(f"{stars} GitHub stars")
        elif stars >= 100:
            pts += 3
        if (c["prs_merged"] or 0) >= 50:
            pts += 5
    else:
        flags_red.append("no public GitHub repo")

    # --- Dilution / unlock risk (max 25) ---
    r = c["mcap_fdv_ratio"]
    if r is not None:
        if r >= 0.7:
            pts += 25; flags_green.append("low unlock overhang")
        elif r >= 0.5:
            pts += 18
        elif r >= 0.3:
            pts += 10
        elif r >= 0.15:
            pts += 4
        else:
            flags_red.append(f"heavy unlocks ahead (only {r:.0%} circulating)")
    # if FDV missing, no points either way

    # --- Legitimacy basics (max 20) ---
    if c["has_website"]:
        pts += 6
    else:
        flags_red.append("no website")
    if c["has_whitepaper"]:
        pts += 5
    if c["categories"]:
        pts += 3
    if c["age_days"] is not None:
        pts += 3
        if c["age_days"] < 30:
            flags_red.append("extremely new (<30d) — unproven")
    if c["age_days"] and c["age_days"] >= 180:
        pts += 3  # survived 6+ months

    # --- Liquidity sanity (max 20) ---
    v = c["vol_mcap_ratio"]
    if v is not None:
        if 0.02 <= v <= 2:
            pts += 20
        elif v > 2:
            pts += 8; flags_red.append(f"volume {v:.1f}x mcap — possible wash trading")
        else:
            pts += 4; flags_red.append("very thin volume")

    c["dd_score"] = min(pts, 100)
    c["red_flags"] = "; ".join(flags_red) or "—"
    c["green_flags"] = "; ".join(flags_green) or "—"
    return c


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def render(rows):
    table = Table(
        title="🔬 Due-Diligence Scan — newer low-caps, ranked by objective signals",
        title_style="bold magenta",
        header_style="magenta",
    )
    table.add_column("Score", justify="right", style="bold")
    table.add_column("Name")
    table.add_column("Sym")
    table.add_column("Mkt Cap", justify="right")
    table.add_column("Circ %", justify="right")  # mcap/fdv
    table.add_column("Last commit", justify="right")
    table.add_column("⭐", justify="right")
    table.add_column("Red flags", style="red")

    for c in rows:
        score_val = c["dd_score"]
        color = "green" if score_val >= 65 else "yellow" if score_val >= 45 else "red"
        circ = f"{c['mcap_fdv_ratio']:.0%}" if c["mcap_fdv_ratio"] is not None else "—"
        commit = f"{c['days_since_commit']}d" if c["days_since_commit"] is not None else ("none" if not c["has_repo"] else "—")
        table.add_row(
            f"[{color}]{score_val}[/{color}]",
            c["name"] or "—",
            c["symbol"] or "—",
            cs.fmt_usd(c["market_cap"]),
            circ,
            commit,
            str(c["stars"]) if c["stars"] is not None else "—",
            c["red_flags"] if c["red_flags"] != "—" else "[dim]none[/dim]",
        )
    console.print(table)


def save_csv(rows):
    if not rows:
        return
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = dt.date.today().isoformat()
    path = os.path.join(OUTPUT_DIR, f"crypto_dd_{today}.csv")
    pd.DataFrame(rows).to_csv(path, index=False)
    console.print(f"[green]✔ Saved full report to {path}[/green]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    console.rule("[bold]Crypto Due-Diligence Scanner[/bold]")
    console.print(
        Panel(
            "Scores newer low-cap tokens on dev activity, dilution, liquidity and "
            "legitimacy basics.\n[bold]Not financial advice. Not a buy list.[/bold] "
            "A high score means 'fewer obvious red flags' — not 'good investment'.\n"
            "[dim]VC-backing data (the best team proxy) is paywalled and NOT included — "
            "check backers manually.[/dim]",
            style="cyan",
        )
    )

    try:
        candidates = discover_candidates()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to discover candidates: {exc}[/red]")
        return

    if not candidates:
        console.print("[yellow]No candidates matched the cap filters.[/yellow]")
        return

    console.print(f"[dim]Found {len(candidates)} candidates. Enriching each (this takes a bit — rate limits)…[/dim]")

    rows = []
    for i, coin in enumerate(candidates, 1):
        try:
            console.print(f"[dim]  ({i}/{len(candidates)}) {coin.get('name')}…[/dim]")
            rows.append(score(enrich(coin)))
            time.sleep(CG_CALL_SPACING)   # ease off the free-tier limit between coins
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]  ⚠ Skipped {coin.get('name')}: {exc}[/yellow]")

    rows.sort(key=lambda c: c["dd_score"], reverse=True)
    render(rows)
    save_csv(rows)

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.print(Panel.fit(f"Scan completed: {stamp}", style="bold white"))
    console.print(
        "[dim]Score ≥65 = few red flags · 45–64 = mixed · <45 = avoid/dig deeper. "
        "Always verify the team and backers yourself before risking money.[/dim]"
    )


if __name__ == "__main__":
    main()
