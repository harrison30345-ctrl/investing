# QuantEdge: handoff briefing for another AI assistant

Written 26 Oct 2026 by the Claude Code session that built QuantEdge. It covers what exists, exactly what was
done, decisions and their reasons, the current state of the user's machine, and what's left.
Facts are as of commit `e79cc17`.

---

## 1. What the project is

The user (UK-based, learning quantitative trading) asked for **QuantEdge**: a dark-mode, terminal-style
quantitative stock research platform for **UK and US equities**, with:
- up-to-date market data
- deterministic trading strategies and a backtester
- AI research agents
- paper trading
- an always-on background worker

They supplied an 8-phase brief. Hard requirements:
- **research and paper only, no real-money execution**
- never invent prices, returns, signals or agent activity
- label demo or delayed data clearly
- human approval for strategy changes, risk-limit changes and promotion to paper trading
- the Python backtester, not the LLM, is the source of truth for numbers
- record every experiment, including failures

**Status: all 8 phases are built, tested and committed. Nothing is deployed, and nothing runs 24/7.**

## 2. Where it lives

- Repo: `~/investing` (GitHub `harrison30345-ctrl/investing`). It also contains the user's older
  **fundamental stock screener** (`screener/`, `services/`, `dashboard.py`, `tests/test_*.py`), which was
  **left untouched**.
- Branch **`quantedge`** (6 commits on top of `main` @ `408f058`), pushed. **PR #1**
  (https://github.com/harrison30345-ctrl/investing/pull/1) is open, ready for review, **not merged**.
- Commits:
  1. `6934e57` phases 1–4 (data layer, backtester, dashboard)
  2. `0ea23aa` phase 5 (Strategy Lab runs, comparison, reports)
  3. `c4dac15` phase 6 (AI agents)
  4. `387d1fa` phase 7, part 1 (worker, intraday, paper trading, health)
  5. `5fa41d2` phase 7, part 2 (deployment config, docs, UI fixes)
  6. `e79cc17` phase 8 (tests, fixes, launch docs)
- Code: `quantedge/` (~8,700 lines of Python). Tests: `tests/quantedge/` (~1,600 lines).
- Docs:
  - `docs/quantedge/README.md`: launch guide
  - `ARCHITECTURE.md`: design, vendor research, costs
  - `DEPLOY.md`: cloud deployment
  - this file
- `CLAUDE.md` (project instructions) has a QuantEdge section with the safety rules.
- Git-ignored, separate projects in the same folder: `trading/` (an earlier "Trading Arena" multi-agent
  paper-trading toy with launchd jobs), `moonscope/`, `bazzasheet/`. QuantEdge doesn't import them.

## 3. User's machine and environment

- macOS. **System Python 3.9.6** (`/usr/bin/python3`, end of life). Packages installed with `pip3 install --user`.
  All code is kept 3.9-compatible (`from __future__ import annotations`, `Optional[...]`, no `match`).
- Version pins forced by Python 3.9:
  - `pandas_market_calendars==4.4.2`: 5.x crashes on 3.9
  - `anthropic>=0.125,<1`: SDK 1.x needs 3.10+, and the code targets the 0.x API
- Other key packages: Streamlit 1.50, pandas 2.3, plotly 6.7, yfinance 1.2, SQLAlchemy 2.x, tenacity, python-dotenv.
- **No Docker, no Postgres, no `gh` CLI** installed. Node exists only at `~/.local/node/bin/node`.
- Database: SQLite at `~/.quantedge/quantedge.db`. Current contents:
  - **Daily bars:** 48 symbols, ~10 years each (Yahoo, real, delayed)
  - **5-minute bars:** 6 symbols (SPY, QQQ, AAPL, MSFT, NVDA, AMZN), 41 sessions each
  - about 140k price rows in total
  - **Experiments:** 24, all completed. 10 from the CLI, 8 from the dashboard, 6 "timing" runs used to check performance and refactors.
  - **Signal observations:** 258
  - **Paper accounts:** 0
  - **AI:** 0 LLM calls ever made; 1 queued (unrun) validation-agent task
  - **Alerts:** 0 open
- **No `.env` file exists.** No API keys are configured, so the AI is disabled and data comes from Yahoo.
- Dashboard preview config: `.claude/launch.json` entry `quantedge` (port 8503).

## 4. Architecture (what's in each module)

```
quantedge/
  config.py        Settings from env/.env. QE_TRADING_MODE must be research|paper or startup fails.
  markets.py       UK (XLON, Europe/London) / US (NYSE, America/New_York) calendars, session status.
                   Benchmarks: SPY (US), ISF.L (UK): total-return ETFs, so comparisons are like for like.
  universe.py      Catalogue: 5 indices, 3 ETFs (SPY, QQQ, ISF.L), 20 US + 20 UK large caps.
                   Universes: us_large, uk_large, us_intraday.
  db/              SQLAlchemy models + engine (SQLite default, Postgres via QE_DATABASE_URL).
                   create_all plus a small add-missing-nullable-columns migration (no Alembic yet).
  data/providers/  MarketDataProvider interface: yahoo (default), eodhd (written, untested w/o key),
                   demo (synthetic, stored under provider="demo" so it can never mix with real data).
  data/validation  Daily: duplicates, missing sessions vs calendar, OHLC sanity, >40% jumps, zero volume,
                   staleness. Intraday: out-of-session bars, missing 5-min bars.
  data/service.py  refresh_daily (incremental with 7-day overlap; full reload if adj_close/close ratio
                   changed → dividend/split re-adjustment), refresh_intraday.
  strategies/      Deterministic, causal, no LLM: ema_pullback, donchian_breakout (trend),
                   xs_momentum (rebalance), orb, vwap_continuation (US intraday).
  backtest/        portfolio.py SignalPortfolio (bar-by-bar rules, shared with paper trading),
                   engine.py (signal + rebalance engines), costs.py, metrics.py, splits.py, runner.py.
  signals.py       Daily signal scan → "observation" rows (never trades).
  reports.py       Deterministic plain-English verdicts per configuration.
  agents/          llm.py (provider adapter), budget.py, evidence.py, schemas.py, roster.py,
                   verify.py, proposals.py, orchestrator.py.
  paper.py         Forward-only paper accounts.
  scheduler.py     Job definitions + is_due/next_run (calendar-aware, catch-up windows).
  worker.py        Always-on loop: heartbeats, jobs, single-instance guard, crash recovery.
  health.py, alerts.py  Checks → alerts (dedupe, auto-resolve, optional webhook).
  ui/              Design tokens/CSS (theme.py), Plotly template (charts.py), components.
  app/             Streamlit: main.py (st.navigation), chrome.py (top bar), data.py (cached reads),
                   views.py (shared experiment views), actions.py, screens/*.py (10 pages).
  cli.py           Everything: refresh, backtest, sweep, validate, report, scan, agents, paper,
                   worker, health, status, dashboard.
```

## 5. What was done, phase by phase (with key decisions)

**Phase 1: inspection and architecture**
- Found the working Streamlit screener and reused the stack. The brief said to keep Streamlit if it already worked.
- The user's MNQ 20/50 EMA futures code was **not found anywhere on disk**, so there was nothing to preserve.
- Researched data vendors (Oct 2026 prices, from vendor and third-party pages):
  - **Yahoo/yfinance:** free, UK+US, delayed, unofficial, personal use only. Chosen as the default because it's the only no-key UK+US option.
  - **EODHD:** $19.99/mo EOD, $29.99/mo with intraday. The recommended paid upgrade.
  - **Massive (ex-Polygon):** US only.
  - **Alpaca:** free IEX feed, $99/mo for SIP.
  - **Twelve Data:** LSE coverage unclear.
- Cost estimate: about €5/mo minimum on a VPS; about $45–80/mo with EODHD and capped AI.

**Phase 2: design system and Home**
- Dark navy/charcoal palette with a single teal accent. Green and red are used only for direction.
- Monospaced numbers. Chart palette validated for colour-blindness and contrast on the dark background.
- The theme is passed as CLI flags rather than `.streamlit/config.toml`, so the old screener's light theme is unaffected.
- Home shows:
  - index tiles: FTSE 100/250, S&P 500, Nasdaq Composite, Dow
  - a market chart with ranges, the watchlist, recent signals
  - strategy vs benchmark, risk and drawdown
  - data health, agent and worker status, activity and schedule
- Top bar: global search (any Yahoo symbol), UK/US filter, data-freshness pills, LSE/NYSE open or closed, worker status, alerts count.
- Every panel without data shows a clear empty state. Nothing is invented.

**Phase 3: data**
- Provider abstraction, validation and storage, as described in section 4.
- Verified live against Yahoo. The checks caught real Yahoo faults, e.g. Tesco bars where high/low didn't bracket open/close.

**Phase 4: backtester**
- **Timing:** signals are evaluated on bar *t*'s close and fill at bar *t+1*'s open.
- **Stops and targets:** checked against the bar's high/low. A gap through a level fills at the open. If one bar hits both, the stop is assumed to fill first.
- **Sizing:** fixed-fractional risk (default 1%), capped at 20% of equity per position and 5 positions.
- **Costs:** half the spread plus slippage on every fill (5+5 bps default), optional commission, and **0.5% UK stamp duty on UK buys**.
- **Prices:** adjusted daily prices; UK prices converted from pence to pounds.
- **Splits:** chronological 60% train / 20% validation / 20% test, after indicator warm-up. The **test period is locked**: it needs a flag and every use is counted.
- **Metrics:** net, CAGR, benchmark, excess, Sharpe, max drawdown, volatility, win rate, profit factor, expectancy, average win/loss, exposure, turnover, costs, longest losing streak.
- **Look-ahead check:** a test recomputes signals on data truncated at many dates and asserts the past never changes.

**Phase 5: Strategy Lab, Backtests, Reports**
- **Strategy Lab:** configure parameters, sizing and costs; run a single backtest or a one-parameter sweep of up to 6 values.
- Sweep variants share one date window (`min_warmup`). That was a bug found in the browser: variants originally used different windows.
- **Backtests:** filters, comparison of up to 6 runs, "Re-test on validation" with an identical setup (`rerun_on_period`), and the test period behind a confirmation.
- **Reports:** verdicts are *Not validated*, *Failed*, *Degraded (possible overfitting)*, *Mixed* and *Held up (not proof)*. Each comes with findings and caveats and can be exported as Markdown.
- The engine was sped up about 3× with dict lookups; results were identical before and after.

**Phase 6: AI agents**
- **Agents:** Trend Research, Momentum Research, Intraday Research, Risk Manager, Independent Validation, Research Director.
- **Calls:** each agent is a single structured-output call on **`claude-opus-5-5`** (effort `high`, refusal fallbacks `fallbacks="default"` with beta header `server-side-fallback-2026-07-01`). There's no open-ended tool loop.
- **Inputs:** agents receive a deterministic **evidence pack** built from the database. The pack is stored on the task and hashed. If it hasn't changed since the agent's last run, the agent is **skipped with no call**.
- **Citation checking:** every number an agent cites (`{experiment_id, metric, value}`) is checked against the database and flagged *verified*, *unverified* or *uncited*.
- **Budget:** worst-case cost is checked *before* each call against monthly (default $10) and per-cycle ($2.50) caps.
- **Reliability:** bounded retries (2 attempts, transient errors only), timeouts, no overlapping cycles, and everything logged (`agent_tasks`, `llm_calls`, `proposals`).
- **Proposals:** validated deterministically (strategy exists, parameters in range, data present, train/validation only, validation only for configurations already run on train, no duplicates, at most 3 per agent).
- **Director:** can only select offered candidates, up to a cap (5 per cycle). Ids it invents are ignored.
- **Approval:** `strategy_change`, `risk_limit_change` and `promote_to_paper` always wait for a human. Approving records the decision only; it changes no code or limits.
- **Estimated cost:** about $0.70 per cycle typical, about $2 worst case.
- **Never run live** (no key).

**Phase 7: intraday, paper trading, worker, health, deployment**
- **Intraday:** US 5-minute bars, regular session only. ORB and VWAP strategies, always flat at the session close (enforced by both strategy and engine). Runs split by whole sessions.
- **First real intraday results** (24 training sessions; a small sample, flagged as such):
  - ORB: −2.7% over 73 trades
  - VWAP: −4.2% over 144 trades, with costs alone at 4.2% of capital
- **Paper trading:**
  - Separate tables.
  - Created only from a completed **validation/test** run with an approver name starting "human". Agent code has no path to it; a test enforces this.
  - **Forward-only:** trades only bars after approval, and only completed bars.
  - Fills at actual (unadjusted) prices; signals use adjusted prices with the stop distance rescaled; dividends aren't credited.
  - State persists between runs.
  - A test proves an account stepped bar by bar, with restarts in between, produces **identical trades to the backtest**.
  - Signal strategies only; momentum isn't supported yet.
- **Worker:** `python3 -m quantedge.worker`, ticking every 30 s. Jobs:
  - UK EOD refresh, 17:15 London on trading days
  - US EOD refresh, 16:30 New York on trading days
  - US intraday poll, every 5 minutes 09:30–16:10 New York
  - health check, every 5 minutes
  - AI research cycle, weekdays 23:00 London, only if AI is configured

  A missed refresh is caught up within 5 days; the AI cycle within 18 hours. It also writes heartbeats, refuses a second instance, and recovers from crashes. One real tick was verified: 48 symbols refreshed in 7 seconds.
- **Health and alerts:** checks for stale data (with a grace period after the close), provider reachability (rate-limited to every 30 minutes), worker heartbeat, AI budget above 80%, and paper accounts that failed to step. An alert opens once, resolves automatically, and can go to an optional webhook. `cli health` exits 0/1/2 and accepts `--fail-on error`.
- **Deployment files (never run):**
  - `Dockerfile` (Python 3.11, non-root)
  - `docker-compose.yml`: Postgres 16, dashboard, worker, and Caddy with HTTPS and basic auth. Only Caddy publishes ports.
  - `deploy/Caddyfile`
  - an optional `deploy/launchd/` plist for the Mac (not installed)

**Phase 8: tests and docs**
- **New tests:**
  - an end-to-end CLI test on a fresh database
  - a headless render test (Streamlit `AppTest`) for all 10 pages, with data and empty
  - offline Postgres compatibility tests: DDL, upsert and strict JSON
- **Fixed:**
  - the CLI showed tracebacks instead of "Refused: …" messages
  - phone layout issues
  - unused imports (pyflakes is now clean)
- **Docs:** launch guide rewritten; DEPLOY.md gained a pre-flight checklist, rollback and troubleshooting.

**Notable bugs found and fixed along the way:**
- worker job status was never saved (the record was detached from the session before flushing)
- a research cycle consumed other agents' queued work
- the Director's raw "selected" claims were shown as fact
- `$` amounts rendered as LaTeX maths
- Streamlit's −1rem markdown margin made panels overlap the next element
- a cold deep link loaded a page without the app shell, because Streamlit auto-discovers a `pages/` folder (renamed to `screens/`)
- Streamlit rejected the `%,.0f` number format
- paper and backtest results drifted slightly because paper used a shorter EMA history

## 6. Results so far (all on train unless stated; all inflated by survivorship bias)

| Strategy | Universe | Net | Benchmark | Sharpe | Max DD | Trades |
|---|---|---|---|---|---|---|
| EMA pullback | us_large, train | +106.2% | SPY +80.7% | 1.03 | −17.1% | 433 |
| Breakout + EMA | us_large, train | +154.4% | +80.7% | 1.14 | −18.1% | 133 |
| Breakout + EMA | us_large, **validation** | +65.6% | +58.1% | 1.53 (SPY 2.00) | −13.9% | 51 |
| Momentum 12-1 | us_large, train | +336.5% | +68.0% | 1.03 | −36.6% | 67 |
| Breakout + EMA | uk_large, train | −28.4% | ISF.L +0.6% | −0.37 | −38.0% | 158 |
| ORB (intraday) | us_intraday, train | −2.7% | — | — | −3.3% | 73 |
| VWAP (intraday) | us_intraday, train | −4.2% | — | — | −4.6% | 144 |

- **US breakout:** the report verdict is "Mixed": it beat the benchmark's return on validation, but not its risk-adjusted return.
- **Momentum:** results are inflated by survivorship (today's mega-caps such as NVIDIA).
- **UK breakout:** stamp duty was about 16% of capital.

## 7. Safety rules any future work must keep

- No broker or real-money execution code. `QE_TRADING_MODE` accepts only `research|paper`.
- Never substitute demo data for real data. Demo is a separate provider with a banner.
- The backtester is the only source of numbers. Agents get evidence packs and can't run the test period, fetch data, approve anything or create paper accounts.
- Paper accounts need a validated run and a named human approver. Every experiment, including failures, is recorded.
- Don't claim the system runs 24/7 until it's deployed and verified.

## 8. How to run and test

```bash
python3 -m quantedge.cli dashboard          # http://localhost:8503
python3 -m quantedge.worker                 # background scheduler
python3 -m quantedge.cli --help             # all commands
python3 -m pytest tests -q                  # 457 tests, ~2.5 min (includes the old screener's tests)
```

Gotchas:
- Streamlit doesn't hot-reload non-page modules: restart the server after editing `quantedge/ui`, `agents`, and so on.
- Page files must stay in `quantedge/app/screens/`, not `pages/`.
- Streamlit's `AppTest` mishandles single-select `segmented_control` on re-runs, so page tests render each screen in one run.

## 9. What's left

1. **Merge PR #1** into `main`.
2. **First deployment** (DEPLOY.md): the user needs a VPS (e.g. Hetzner CX22, about €5/mo), a domain and a password. The Docker stack has never been run, so expect small fixes.
3. **First live AI cycle:** the user adds `ANTHROPIC_API_KEY` and `QE_LLM_PROVIDER=anthropic` to `.env` themselves. Estimate about $0.70 and confirm with the user before spending.
4. **First paper account** from a validated run the user chooses.
5. **EODHD:** only if the user pays for it. Verify with `cli health`.
6. **Later improvements:** momentum paper trading, a walk-forward optimiser, Alembic migrations, Python 3.11+ locally.
