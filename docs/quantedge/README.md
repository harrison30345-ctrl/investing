# QuantEdge: launch guide

QuantEdge is a research and paper-trading platform for UK and US equities. **There is no real-money
execution anywhere in this code**, and no broker connection. Design and decisions:
[ARCHITECTURE.md](ARCHITECTURE.md). Running it always-on in the cloud: [DEPLOY.md](DEPLOY.md).

## 1. First run (about 5 minutes)

```bash
cd ~/investing
pip3 install --user -r requirements.txt
python3 -m quantedge.cli refresh --core                # indices, benchmark ETFs, watchlist
python3 -m quantedge.cli refresh --universe us_large   # 20 US large caps, 10 years of daily bars
python3 -m quantedge.cli refresh --universe uk_large   # 20 UK large caps
python3 -m quantedge.cli refresh --intraday            # US 5-minute bars (Yahoo keeps ~60 days)
python3 -m quantedge.cli dashboard                     # open http://localhost:8503
```

No account or key is needed: the default data source is Yahoo Finance (free, **delayed**, personal use
only). A `.env` file is optional; copy `.env.example` to `.env` only to change settings.

To try it without the internet: put `QE_DATA_PROVIDER=demo` in `.env` (or the environment). Demo prices
are synthetic, stored separately from real data, and every page shows a DEMO DATA banner.

## 2. The research loop

1. **Explore on train.** Strategy Lab → pick a strategy and universe → *Run backtest*, or *Sweep one
   parameter* to see how sensitive the result is. Only the first 60% of history is used.
2. **Check on validation.** Backtests → select the run → *Re-test on validation period*. Same rules,
   parameters and costs on the next 20% of history, which the run has never seen.
3. **Read the verdict.** Reports groups runs by configuration and says, in plain English, whether the
   result held up, was mixed, inconclusive, degraded or failed. Each run is compared with the market ETF
   *and* with simply buying and holding the same stocks, with 95% confidence intervals, a haircut for
   how many variants were tried, a cost-sensitivity curve and a breakdown by market regime.
   *Walk-forward* (Strategy Lab run mode or CLI) re-picks a parameter every fold on past data only.
4. **(Optional) Final test.** The last 20% is locked. Unlock it once, at the end; every use is counted.
5. **Paper trade.** Paper Trading → *Start a paper account* from a validated run, with your approval.
   It trades only bars that arrive from then on, with simulated money.
6. **(Optional) AI review.** AI Research → *Run research cycle*. Agents critique the evidence and
   propose experiments; every number they cite is checked against the database.

A good backtest is a hypothesis that survived one test, not evidence it will make money.

## 3. Keeping it running

| What | Command | Notes |
|---|---|---|
| Background worker | `python3 -m quantedge.worker` | Refreshes data after each close, polls 5-minute bars in the US session, steps paper accounts, runs health checks (and the AI cycle if enabled). Ctrl-C to stop. |
| One job now | `python3 -m quantedge.cli worker --run us_eod_refresh` | Also `uk_eod_refresh`, `us_intraday_poll`, `health_check`, `research_cycle` |
| Health | `python3 -m quantedge.cli health` | Exit code 0 ok, 1 warnings, 2 errors |
| Always on | see [DEPLOY.md](DEPLOY.md) | Cloud server (recommended) or the launchd template for this Mac |

The top bar shows **Worker online** while the worker is running, plus an **Alerts** count when a check
fails (details in Settings → System health).

## 4. Command reference

```bash
python3 -m quantedge.cli refresh --core | --universe us_large | --symbols AAPL,SHEL.L | --intraday
python3 -m quantedge.cli backtest ema_pullback --universe us_large [--period validation] [--params '{"fast": 10}']
python3 -m quantedge.cli sweep xs_momentum lookback 63,126,252 --universe us_large
python3 -m quantedge.cli walkforward ema_pullback fast 10,20,30 --universe us_large   # re-chosen each fold, out-of-sample
python3 -m quantedge.cli validate 12                  # re-test experiment #12 on validation
python3 -m quantedge.cli report [--ids 12,13]         # plain-English reports (Markdown)
python3 -m quantedge.cli scan                         # record daily signal observations
python3 -m quantedge.cli paper list | step | create --experiment 13 --approve | pause 1 | resume 1 | close 1
python3 -m quantedge.cli agents estimate | run | status | approve 7 --note "…" | decline 7
python3 -m quantedge.cli status                       # what's stored and how fresh
python3 -m pytest tests -q                            # full test suite (~3 minutes)
```

Strategies: `ema_pullback`, `donchian_breakout` (daily trend), `xs_momentum` (daily momentum),
`orb`, `vwap_continuation` (US intraday). Universes: `us_large`, `uk_large`, `us_intraday`.

## 5. Optional settings (`.env`)

| Variable | Effect |
|---|---|
| `QE_DATA_PROVIDER=eodhd` + `EODHD_API_KEY` | Licensed UK+US data ($29.99/month plan for intraday). Adapter written but **not yet tested with a real key**; check with `cli health` first |
| `QE_LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` | AI agents on Claude Opus 5.5. A cycle is ≈ $0.70 typical, ~$2 worst case; hard caps `QE_LLM_MONTHLY_BUDGET_USD` (default $10) and `QE_LLM_CYCLE_BUDGET_USD` ($2.50) |
| `QE_AGENT_AUTORUN=false` | Every agent-proposed backtest waits for your approval |
| `QE_ALERT_WEBHOOK_URL` | Alerts are also POSTed to this URL as JSON (`{"text": …}`) |
| `QE_DATABASE_URL` | Use Postgres instead of the local SQLite file |
| `QE_CASH_RATE` | `fred` (default: uninvested cash earns the US T-bill / UK SONIA rate), `zero`, or a constant like `0.04` |
| `QE_TRADING_MODE` | `research` (default) or `paper`. Anything else is refused at start-up |

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| "No stored … price data" | Run the matching `refresh` (daily, or `--intraday` for intraday strategies) |
| Top bar shows UK/US bars in red or stale | Refresh, or start the worker; Yahoo sometimes lags by an hour after the close |
| A symbol is "not found" | Yahoo uses `.L` for London (e.g. `TSCO.L`); indices start with `^` |
| Worker says another worker is running | Only one may run. If the other crashed, wait 3 minutes for its heartbeat to expire |
| Dashboard port in use | `python3 -m quantedge.cli dashboard --port 8510` |
| Quality warnings on Markets | The vendor sent inconsistent bars (e.g. high below close). They're flagged, not altered |
| "Refused: …" from the CLI | A safety rule or bad input (e.g. promoting a train-only run); the message says which |

Data lives in `~/.quantedge/quantedge.db` (SQLite). Deleting it resets everything; there's no undo.

## 7. Build status

| Phase | Status |
|---|---|
| 1. Architecture, vendors, costs | Done ([ARCHITECTURE.md](ARCHITECTURE.md)) |
| 2. Design system + Home | Done; checked at desktop and phone widths |
| 3. Data, storage, quality checks | Done: daily + 5-minute bars, exchange calendars, validation, corporate-action re-sync. Yahoo verified live; EODHD untested without a key |
| 4. Backtester + daily strategies | Done: next-open fills, stops/targets, costs incl. UK stamp duty, chronological splits, experiment log |
| 5. Strategy Lab, Backtests, Reports | Done: runs, sweeps, validation re-tests, comparison, plain-English reports |
| 6. AI agents | Done with budgets, citation checks and approvals; **never run against the live API** (no key yet) |
| 7. Intraday, paper trading, worker, health | Done locally; worker verified with a real tick. Momentum isn't supported in paper trading |
| 8. Tests + launch docs | Done: unit, end-to-end CLI and headless page-render tests; this guide and DEPLOY.md. **Not deployed: nothing is running 24/7 yet** |

## 8. Known limitations

* Universes are today's large caps: **survivorship bias** inflates backtests, especially momentum.
* Yahoo data is unofficial, delayed, personal-use only, and not point-in-time.
* Intraday history is ~60 days, so intraday results are small samples.
* Paper fills use actual prices and don't credit dividends.
* Verdicts require the edge over the stricter baseline (ETF or the same stocks bought and held) to have a 95%
  interval above zero and a Deflated Sharpe ≥ 0.95; the thresholds are conventions, not guarantees. No
  walk-forward optimiser yet. See [AUDIT-2026-10-26.md](AUDIT-2026-10-26.md).
* Agent spend is estimated at list prices; the Anthropic console is the invoice.
* Local Python is 3.9 (end of life); the Docker image uses 3.11. The Anthropic SDK is pinned below 1.0.
* Schema changes use a minimal add-missing-columns step; adopt Alembic before changing column types.
* The Docker deployment has not been run yet.
