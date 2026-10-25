# QuantEdge: running it and build status

Research and paper-trading platform for UK and US equities. **No real-money
execution exists in this code.** See [ARCHITECTURE.md](ARCHITECTURE.md) for the
design, data-vendor comparison and cost estimates.

## Run locally

```bash
cd ~/investing
pip3 install --user -r requirements.txt
cp .env.example .env                                   # optional; defaults work without it
python3 -m quantedge.cli refresh --core                # indices, benchmarks, watchlist (~10 s)
python3 -m quantedge.cli refresh --universe us_large   # 20 US large caps, 10 years
python3 -m quantedge.cli refresh --universe uk_large   # 20 UK large caps
python3 -m quantedge.cli dashboard                     # http://localhost:8503
```

Other commands:

```bash
python3 -m quantedge.cli backtest ema_pullback --universe us_large --period train
python3 -m quantedge.cli backtest xs_momentum --universe uk_large --params '{"lookback": 126}'
python3 -m quantedge.cli backtest donchian_breakout --symbols AAPL,MSFT --period validation
python3 -m quantedge.cli sweep xs_momentum lookback 63,126,252 --universe us_large
python3 -m quantedge.cli validate 2   # re-test experiment #2 on the validation period
python3 -m quantedge.cli report       # plain-English reports for every configuration
python3 -m quantedge.cli status       # what is stored, how fresh
python3 -m quantedge.cli health       # can we reach the provider?
python3 -m pytest tests/quantedge -q  # QuantEdge tests
```

Offline / demo mode: `QE_DATA_PROVIDER=demo python3 -m quantedge.cli refresh --core`.
Demo rows are stored separately and the dashboard shows a DEMO DATA banner.

## Where things live

| Path | What |
|---|---|
| `~/.quantedge/quantedge.db` | SQLite database (prices, signals, experiments, logs) |
| `quantedge/data/` | providers (yahoo, eodhd, demo), validation, storage, refresh |
| `quantedge/strategies/` | deterministic rules: `ema_pullback`, `donchian_breakout`, `xs_momentum` |
| `quantedge/backtest/` | engine, costs (incl. UK stamp duty), metrics, splits, experiment runner |
| `quantedge/app/` | Streamlit dashboard; `quantedge/ui/` is the design system |

## Credentials you may supply (none required)

| Variable | When |
|---|---|
| `EODHD_API_KEY` + `QE_DATA_PROVIDER=eodhd` | licensed UK+US data ($29.99/mo plan for intraday) |
| `ANTHROPIC_API_KEY` + `QE_LLM_PROVIDER=anthropic` | Phase 6 AI agents |
| `QE_DATABASE_URL` | Postgres in the cloud |

## Build status

| Phase | Status |
|---|---|
| 1. Inspect, architecture, vendors, costs | Done ([ARCHITECTURE.md](ARCHITECTURE.md)) |
| 2. Design system + Home | Done. Agent and intraday panels show honest "not built" states |
| 3. Data providers, storage, quality checks | Done for daily bars. Yahoo verified live; EODHD adapter **not yet tested with a key**; intraday fetch exists but is not stored or scheduled |
| 4. Backtester + daily strategies | Done: signal and rebalance engines, costs, metrics, splits, experiment log, 41 tests. Walk-forward windows exist but no optimiser uses them yet |
| 5. Strategy Lab / Backtests / Reports | Done: configure and run backtests or one-parameter sweeps from the Lab (all variants share one date window), re-test on validation with the test period behind confirmation, compare up to 6 runs, deterministic plain-English reports with verdicts and Markdown export. Walk-forward optimisation not built yet |
| 6. AI agents | Not started |
| 7. Intraday strategies, worker, scheduling, health | Not started (job timetable defined in `quantedge/scheduler.py`) |
| 8. Deployment | Not started. Nothing is running 24/7 |

## Known limitations

* Universes are today's large caps: **survivorship bias** inflates backtest returns,
  especially for momentum. Every experiment records this.
* Yahoo data is unofficial, delayed, personal-use only, and not point-in-time.
* Local Python is 3.9 (end of life). Code stays 3.9-compatible; deploy on 3.11+.
* Schema is created with `create_all` plus a small add-missing-nullable-columns step; Alembic is needed before cloud deploy.
* Report verdicts are rules of thumb (e.g. validation Sharpe below half of train = "degraded"), not statistical tests.
