# QuantEdge — Architecture & Phase 1 Proposal

QuantEdge is a research and paper-trading platform for UK and US equities. It
does **not** place real orders. This document records what was found in the
repository, the decisions taken, and why.

## 1. What already existed (and what happens to it)

| Existing code | Status | Decision |
|---|---|---|
| `screener/` + `services/` + `dashboard.py` | Working fundamental screener (Streamlit, light theme) with tests | **Kept untouched.** QuantEdge is a new package alongside it. It reuses Streamlit and the same libraries, but not the screener's scoring, which uses today's fundamentals and so can't be backtested honestly. |
| `backtest.py` | Ad-hoc scoring backtest | Kept. Its bias notes (survivorship, no costs) shaped the new engine's design. |
| `trading/` | Separate, git-ignored "Trading Arena" project | Not imported (it's not in this repository's history). Ideas reused: the paper/live safety gate and causal momentum scoring. |
| MNQ 20/50 EMA futures research | **Not found anywhere on disk** | Nothing to preserve. A `futures/` module is reserved for it. Stock assumptions (stamp duty, share sizing, LSE/NYSE calendars) are not to be applied to futures. |

## 2. Framework decision: Streamlit, with a custom design system

The brief says to keep Streamlit if it already works, and it does. Streamlit 1.50 provides:
multi-page navigation (`st.navigation`), sortable/virtualised tables
(`st.dataframe` + column config with sparklines), segmented controls, and
Plotly charts. The terminal look comes from one injected stylesheet plus a
Plotly template (`quantedge/ui/`), not from per-page styling.

Known limits, accepted: no true fixed top bar (it's rendered at the top of every page),
and pages rerun on interaction (mitigated with `st.cache_data`). If those
limits ever bite, the backend has no Streamlit dependency, so a React front end could be built
on top of it later without touching strategy or data code.

## 3. Package layout

```
quantedge/
  config.py            env-driven settings; refuses any trading mode except research/paper
  markets.py           UK/US exchange calendars (pandas_market_calendars), session status
  universe.py          curated instrument catalogue + search
  db/                  SQLAlchemy models + engine (SQLite locally, Postgres in cloud)
  data/
    providers/         MarketDataProvider interface; yahoo, eodhd, demo implementations
    validation.py      missing sessions, duplicates, OHLC sanity, staleness, split jumps
    repository.py      persistence (upserts, reads, fetch log, freshness)
    service.py         refresh orchestration + corporate-action re-sync
  strategies/          deterministic rules — no LLM in this layer
  backtest/            engine, cost model, metrics, chronological splits, experiment runner
  signals.py           latest-bar signal observations written to the database
  scheduler.py         job definitions (+ worker loop in Phase 7)
  ui/                  design tokens, CSS, Plotly template, reusable components
  app/                 Streamlit pages
  cli.py               `python3 -m quantedge.cli …`
```

Responsibility boundaries:

* **Strategies never fetch data and never call an LLM.** They receive a price
  DataFrame and return signal columns computed only from past and current bars.
* **The backtester is the only source of numerical results.** Agents (Phase 6)
  read its stored output; they cannot write metrics.
* **Providers never fall back silently.** If the configured provider fails, the
  UI shows "unavailable". Demo data is stored under provider `demo`, so it can
  never mix with real rows.

## 4. Market data provider — research and default

Prices checked October 2026 from vendor pages and third-party summaries. Re-check them
before subscribing.

| Vendor | UK (LSE) | US | Cost | Notes |
|---|---|---|---|---|
| **Yahoo Finance via `yfinance`** | Yes (`.L`, prices in pence) | Yes | Free, no key | Unofficial scraper of Yahoo endpoints. Data is delayed (~15 min US, ~15–20 min LSE) and can break or be rate-limited without notice. Yahoo's terms allow **personal, non-commercial use only**. |
| EODHD | Yes (`.LSE`) | Yes | $19.99/mo EOD; **$29.99/mo EOD+intraday** | Licensed API, 100k calls/day, 30+ yrs EOD. Real-time streaming is on higher tiers. |
| Massive (ex-Polygon) | No | Yes | $0 EOD / $29 15-min delayed / $199 real-time | US only, so it can't be the single default. |
| Alpaca Market Data | No | Yes | Free (IEX, ~2.5% of volume) / $99 SIP | Good for US intraday paper trading later; IEX-only bars are not consolidated prices. |
| Twelve Data | Paid tiers | Free tier | from ~$29/mo | LSE plan details unclear; would need confirmation from sales. |

**Default: `yahoo`.** It's the only option that covers both markets with no
credentials, which the brief requires. It's labelled everywhere as *Delayed ·
unofficial · personal research use*.
**Recommended upgrade: EODHD EOD+Intraday ($29.99/mo)** once you want licensed,
dependable UK+US daily and intraday data. The adapter is implemented but has
not been run against a live key.
**`demo`** generates synthetic prices for offline work. It's always shown with a
"DEMO DATA" banner.

Nothing in the free stack is real-time exchange data. The UI never calls it
"live". It says "delayed".

## 5. Storage

SQLite (`~/.quantedge/quantedge.db`) for the local prototype, and Postgres in the cloud.
SQLAlchemy keeps the code identical; switch with `QE_DATABASE_URL`.
Tables: instruments, price_bars, fetch_log, quality_reports, watchlists,
experiments (every run, including failures), trades, equity_points, signals,
job_runs, heartbeats. Agent and approval tables arrive in Phase 6.

Schema creation uses `create_all` for now. Alembic migrations get added before the
first cloud deployment (Phase 8).

## 6. Backtesting principles

* Signals are computed at bar *t*'s close and fill at bar *t+1*'s open. A test
  truncates the data at every date and asserts the signals don't change.
* Stops and targets are checked against the next bars' high/low. A gap through a stop fills at
  the open. If a bar hits both stop and target, the stop is assumed to have filled first.
* Costs: half-spread + slippage on every fill, optional commission, and **0.5%
  UK stamp duty on purchases of UK shares**.
* Adjusted prices (dividends and splits) are used for trading. Benchmarks are total-return ETFs (SPY,
  ISF.L) so the comparison is like-for-like.
* Train / validation / test splits are chronological. Running on the test set
  needs an explicit flag and is recorded on the experiment.
* Known biases are stored with every experiment: survivorship (today's
  constituents), and the fact that yfinance data isn't point-in-time.

## 7. Always-on operation (Phase 7–8)

| Service | Runs | Why |
|---|---|---|
| Postgres | 24/7 | State |
| Dashboard (Streamlit) | 24/7 (or on demand) | UI |
| Worker / scheduler | 24/7 process, session-aware jobs | Refresh, signals, research |
| — UK EOD refresh | after LSE close, ~17:15 Europe/London, trading days | |
| — US EOD refresh | after NYSE close, ~16:30 America/New_York, trading days | |
| — US intraday polling | only 09:30–16:00 New York | Intraday strategies |
| — Daily research | overnight, after both refreshes | LLM agents, budget-capped |
| — Health/freshness check | every 5 min | Alerts |

**Recommended hosting:** one small VPS (e.g. Hetzner CX22, roughly €5/month) running
`docker compose` (postgres + dashboard + worker) behind Caddy with HTTPS and
basic auth. Managed alternative: Render or Railway at roughly $20–30/month for web + worker +
Postgres.

**Estimated monthly running cost**

| Item | Minimum | Recommended |
|---|---|---|
| Hosting | €5 (VPS) | €5–$30 |
| Market data | $0 (yfinance) | $29.99 (EODHD) |
| LLM agents | $0 (disabled) | hard monthly cap you set, e.g. $10–20 |
| **Total** | **≈ €5** | **≈ $45–80** |

Nothing paid is required to run locally.

## 8. Safety

`QE_TRADING_MODE` accepts only `research` (default) or `paper`. Any other value
stops the app at startup. There is no broker execution code. Promoting a
strategy to paper trading, or changing risk limits, will need a recorded human
approval (Phase 6).

## 9. AI agents (Phase 6)

```
evidence.py ─▶ deterministic JSON pack per agent (stored on the task, hashed)
      │            unchanged hash since last completed run ⇒ skip, no call
      ▼
budget.check (worst-case cost vs monthly + cycle caps) ─▶ blocked ⇒ logged, no call
      ▼
llm.AnthropicProvider (Claude Opus 5.5, structured JSON output, refusal fallbacks,
      │                bounded retries for transient errors, timeout)
      ▼
verify.py ─▶ every cited {experiment_id, metric, value} checked against the database
proposals.py ─▶ experiment proposals validated (strategy, params in range, data present,
      │          train/validation only, no duplicates); changes ⇒ awaiting_approval
      ▼
Research Director ─▶ selects ≤ N valid candidates (ids it invents or exceeds are ignored)
      ▼
backtest runner (source="agent:<name>") ─▶ normal experiment log
```

Agents are single structured calls rather than open-ended tool loops: the evidence they need
is small and known in advance, so a loop would add cost and failure modes without adding
information. The adapter is the only module that knows the vendor.

## 10. Paper trading, worker and health (Phase 7)

* `backtest/portfolio.py`: `SignalPortfolio` holds the trading rules bar by bar. The backtester drives
  it over history; paper accounts drive it over new bars and persist its state, so both follow
  identical rules (a test checks they produce identical trades).
* `paper.py`: forward-only accounts. Creation requires a completed validation/test experiment and a
  `human …` approver; agents have no code path to it.
* `scheduler.py` decides *when* (exchange calendars, catch-up windows); `worker.py` decides *what*
  (job handlers, JobRun records, heartbeats, single-instance guard, crash recovery).
* `health.py` + `alerts.py`: checks open and resolve alerts; webhook notification is optional.
* Deployment: `Dockerfile`, `docker-compose.yml` (Postgres, dashboard, worker, Caddy) and
  [DEPLOY.md](DEPLOY.md).
