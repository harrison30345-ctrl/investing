# Crypto Early-Opportunity Screener

A terminal tool that surfaces early-stage crypto opportunities from four
complementary signals, using only **free, no-key public APIs** (CoinGecko +
Coinbase Exchange). Results are printed as colored `rich` tables and saved to a
dated CSV.

```bash
python3 crypto_screener.py
```

> **Not financial advice.** This is a research/discovery tool. Low-cap, trending,
> and freshly listed coins are the highest-risk corner of the market. Do your own
> diligence.

---

## What each section does

### 1. Trending Coins  *(yellow)*
Pulls the top 7 coins from CoinGecko's `/search/trending` endpoint — the assets
people are searching for and viewing **right now**. Shows name, symbol, market
cap rank, and price in BTC.

**Why trending is the most predictive signal:** search and social attention tends
to *lead* price. Before a coin makes a big move, retail interest spikes —
people search it, read about it, add it to watchlists. CoinGecko's trending list
is effectively a real-time gauge of that attention. Catching a coin while it's
trending but *before* the candle prints is the whole game. It's a leading
indicator, not a lagging one like price itself.

### 2. Recently Listed  *(cyan)*
Uses CoinGecko's `recently_added` market category to find coins that first
appeared in roughly the last 30 days. Shows name, symbol, current price, market
cap, and 24h volume.

New listings are where outsized moves happen — there's no price history, thin
order books, and attention is just forming. High risk, high variance.

### 3. Low-Cap Momentum — "Brian Jung style"  *(green)*
Pulls the top 250 coins by volume from `/coins/markets` and keeps only those that
pass **all** of:

| Filter | Threshold | Why |
|---|---|---|
| Market cap | between **$5M and $300M** | big enough to be real, small enough to still run |
| 7d price change | **> 15%** | momentum is already confirmed |
| Volume / market cap | **> 0.1** | real liquidity & interest, not a dead chart |
| Stablecoins | **excluded** (USDT/USDC/DAI/BUSD…) | pegged coins don't move |

Sorted by 7d % change, descending.

### 4. Coinbase Listing Watchlist  *(bold red)*
Fetches Coinbase's public list of listed/listing assets (via the Coinbase
Exchange `currencies` endpoint) and cross-references those tickers against the
trending and momentum results. Any overlap is flagged **HIGH PRIORITY**.

**The "Coinbase effect":** when a coin gets listed on a major exchange like
Coinbase, it gains access to a huge new pool of buyers and often pops on the
news. A coin that is *already* trending or running on momentum **and** is on
Coinbase combines two tailwinds — attention plus distribution.

---

## Brian Jung's Layer 1 & listing-effect strategy

The momentum section is modeled on the approach popularized by YouTuber/investor
**Brian Jung**:

- **Hunt small/mid caps, not megacaps.** A $50M coin can 10x; a $50B coin
  realistically can't. The asymmetry lives in the $5M–$300M band — large enough
  to have survived, small enough to still have room to run.
- **Layer 1 / infrastructure focus.** New L1 blockchains and core infra
  protocols (DeFi rails, restaking, interop) are where narratives and capital
  rotate during a cycle. They tend to lead alt-season moves.
- **Confirmed momentum + liquidity.** Don't catch falling knives — wait for the
  trend to already be up (the +15% 7d filter) and require that real volume is
  backing the move (the volume/market-cap filter). A pump on no volume is a trap.
- **The listing effect.** Exchange listings (especially Coinbase) are a
  recurring, tradeable catalyst. Positioning in quality small caps *before* or
  *around* a major listing is a repeatable edge — hence Section 4 cross-references
  the Coinbase asset list against everything else the screener surfaces.

The thesis: **attention leads price, liquidity confirms it, and exchange listings
distribute it.** Each section captures one of those forces.

---

## How to run

```bash
# 1. Install dependencies
pip install requests rich pandas

# 2. Run the scan
python3 crypto_screener.py
```

No API key is required. The tool sleeps ~1.5s between API calls to stay under the
free rate limits, so a full scan takes roughly 10–20 seconds.

### Output
- Colored tables printed to the terminal (one per section).
- A timestamp of when the scan ran.
- A CSV of all results saved to `outputs/crypto_scan_YYYY-MM-DD.csv`.

### Error handling
- Each failed request retries up to **2 times** with a 3-second pause.
- If a section's API still fails, the tool prints a warning and **skips that
  section** rather than crashing — the other sections still run and save.

---

## Notes / data sources
- **CoinGecko free API** — `https://api.coingecko.com/api/v3`
  (the `geckoapi.com` host sometimes referenced does not resolve; this is the
  correct public base URL).
- **Coinbase listed assets** — `https://api.exchange.coinbase.com/currencies`
  (the `assets.coinbase.com` CDN JSON returns HTTP 403 to automated clients, so
  the public Exchange endpoint is used instead).
- "Recently listed" uses CoinGecko's `recently_added` category and the asset's
  all-time-low date as a proxy for listing recency, since the free
  `/coins/markets` endpoint does not expose an exact listing date.
