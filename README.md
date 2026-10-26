> **QuantEdge**, the UK/US quantitative research and paper-trading platform, lives in `quantedge/`.
> Start with [docs/quantedge/README.md](docs/quantedge/README.md). This README covers the original stock screener.

# Fundamental Stock Screener

A Python-based tool for screening stocks by fundamental metrics. Two modes:
- **Long-term investing**: find quality compounders with strong balance sheets and reasonable valuations
- **Swing trading prep**: filter out structurally broken companies before layering technical analysis

## Install

```bash
cd ~/investing
pip3 install ".[dashboard]"
```

Then open a new terminal (or run `source ~/.zshrc`) so the `screener` command is on your PATH.

## Usage

### CLI

```bash
# Screen with the long-term config
screener -c longterm -u sp500
screener -c longterm -t AAPL,MSFT,GOOG,JNJ,V

# Screen with the swing trading config
screener -c swing -t AAPL,MSFT,NVDA,META,AMD

# See WHY each stock passes or fails every filter
screener -c longterm -u nasdaq100 --dry-run

# Load tickers from a file
screener -c swing -t my_watchlist.csv

# Force fresh data (ignore cache)
screener -c swing -t AAPL --no-cache
```

If `screener` isn't found, use `python3 -m screener.cli` instead, or run `./run_screener.sh`.

### Interactive Dashboard

```bash
screener --dashboard
```

Opens a web UI at `http://localhost:8501` where you can:
- Switch between long-term and swing modes
- Enter tickers or load a full index (S&P 500, NASDAQ 100, FTSE 100)
- Adjust filter thresholds with sliders in real time
- View ranked results in a sortable table
- Drill into any stock's full metric breakdown
- See a grouped bar chart of category scores
- Download results as CSV

## CLI Flags

| Flag | Description |
|------|-------------|
| `-c`, `--config` | Config name (`longterm`, `swing`) or path to a custom YAML |
| `-t`, `--tickers` | Comma-separated tickers OR path to CSV/text file |
| `-u`, `--universe` | Predefined universe: `sp500`, `nasdaq100`, `ftse100` |
| `--top` | Number of results in terminal summary (default: 20) |
| `--dry-run` | Show per-filter pass/fail detail for every stock |
| `-v`, `--verbose` | Debug logging |
| `--no-cache` | Ignore cached data, fetch fresh |
| `--dashboard` | Launch the interactive web dashboard |

## Ticker Input

You can provide tickers four ways:
1. **Inline**: `-t AAPL,MSFT,GOOG`
2. **Text file** (one per line): `-t watchlist.txt`
3. **CSV** (with `ticker` or `symbol` column): `-t portfolio.csv`
4. **Universe**: `-u sp500`

## Config Files

Built-in configs live in `screener/configs/`. To create a custom config, copy one and point to it:

```bash
cp screener/configs/config_longterm.yaml my_value_screen.yaml
# edit my_value_screen.yaml
screener -c my_value_screen.yaml -u sp500
```

### Filters

Pass/fail gates. A stock must pass ALL enabled filters.

```yaml
filters:
  pe_trailing:
    enabled: true      # toggle on/off
    max: 25            # upper bound
    on_missing: pass   # "pass" or "fail" when data unavailable

  roe:
    enabled: true
    min: 15            # lower bound

  fcf_positive_3y:
    enabled: true
    must_be_true: true  # for boolean quality flags
```

### Scoring Weights

Controls how the 0-100 composite score is calculated.

```yaml
scoring:
  weights:
    profitability: 0.30
    financial_health: 0.25
    valuation: 0.20
    growth: 0.15
    cash_flow: 0.10
```

## Metrics Reference

### Valuation
| Metric | Description |
|--------|-------------|
| `pe_trailing` | Price / trailing 12-month earnings. Lower = cheaper. |
| `pe_forward` | Price / estimated next-year earnings. |
| `pb` | Price / book value. Below 1 = trading below asset value. |
| `ps` | Price / trailing 12-month revenue. |
| `ev_ebitda` | Enterprise value / EBITDA. Accounts for debt, better than P/E for cross-company comparison. |
| `peg` | P/E divided by earnings growth rate. Below 1 = cheap relative to growth. |

### Profitability
| Metric | Description |
|--------|-------------|
| `roe` | Return on equity (%). How efficiently the company uses shareholder capital. >15% is strong. |
| `roa` | Return on assets (%). Efficiency of total asset base. >10% is good. |
| `roic` | Return on invested capital (%). Best profitability metric — how much return per dollar of debt + equity invested. |
| `gross_margin` | (Revenue - COGS) / Revenue. Higher = stronger pricing power. |
| `operating_margin` | Operating income / Revenue. Shows core business profitability. |
| `net_margin` | Net income / Revenue. Bottom-line profitability after everything. |

### Financial Health
| Metric | Description |
|--------|-------------|
| `debt_to_equity` | Total debt / shareholder equity. Below 1.0 = conservatively financed. |
| `current_ratio` | Current assets / current liabilities. Above 1.0 = can pay short-term obligations. |
| `quick_ratio` | Like current ratio but excludes inventory. More conservative test. |
| `interest_coverage` | EBIT / interest expense. How easily a company can service its debt. >5x is comfortable. |
| `net_debt_to_ebitda` | (Total debt - cash) / EBITDA. Below 2x is healthy. |

### Growth
| Metric | Description |
|--------|-------------|
| `revenue_growth_1y` | Year-over-year revenue growth (%). |
| `revenue_growth_3y_cagr` | 3-year compound annual revenue growth. |
| `revenue_growth_5y_cagr` | 5-year compound annual revenue growth. |
| `eps_growth_1y` | Year-over-year EPS growth. |
| `eps_growth_3y_cagr` | 3-year compound annual EPS growth. |
| `fcf_growth_1y` | Year-over-year free cash flow growth. |

### Cash Flow
| Metric | Description |
|--------|-------------|
| `fcf` | Free cash flow = operating cash flow - capex. The actual cash a business generates. |
| `fcf_yield` | FCF / market cap (%). Think of it as the "earnings yield" but for real cash. |
| `fcf_margin` | FCF / revenue (%). What % of revenue converts to free cash. |

### Dividends
| Metric | Description |
|--------|-------------|
| `dividend_yield` | Annual dividend / share price (%). |
| `payout_ratio` | Dividends paid / net income (%). Below 60% is sustainable. |
| `dividend_growth_streak` | Trailing annual dividend rate (proxy — see limitations). |

### Quality Flags
| Metric | Description |
|--------|-------------|
| `fcf_positive_3y` | True if free cash flow was positive in each of the last 3 fiscal years. |
| `no_dilution_3y` | True if share count is stable or falling over the last 3 years (allows up to 2% increase). |

## Output

- **Terminal**: top N stocks with key metrics
- **Dashboard**: full interactive table with charts and deep-dive
- **CSV**: every metric as a column, saved to `~/.screener/output/`
- **Data completeness**: a `data_completeness` column (0-100%) flags how much data was available. Low = verify manually.

## Swapping Data Sources

`data_fetcher.py` uses a `DataFetcherBase` abstract class. To use a paid API:

1. Create a new class (e.g. `FMPFetcher`) implementing `fetch_ticker_data()` and `fetch_batch()`
2. Return data in the same dict format (see `DataFetcherBase` docstring)
3. Swap the fetcher in `cli.py`

## Data Storage

| What | Where |
|------|-------|
| Cache (parquet) | `~/.screener/cache/` |
| CSV output | `~/.screener/output/` |
| Configs | `screener/configs/` (bundled with package) |

Cache expiry defaults to 24 hours (set in config YAML). Fundamentals only change quarterly, so aggressive caching is fine.

## Known Limitations

- **yfinance data gaps**: some metrics may be missing for certain stocks. Flagged via data completeness column.
- **Dividend growth streak**: yfinance doesn't provide this directly. The field is a proxy (trailing annual dividend rate).
- **FTSE 100**: tickers auto-appended with `.L` suffix. yfinance UK coverage is less reliable than US.
- **Rate limiting**: yfinance is free but can throttle. The fetcher adds 0.5s delay between requests.

## Project Structure

```
investing/
├── screener/
│   ├── __init__.py
│   ├── cli.py              # CLI entry point + screener command
│   ├── dashboard.py         # Streamlit interactive dashboard
│   ├── data_fetcher.py      # Data source abstraction + yfinance
│   ├── metrics.py           # Metric calculation from raw data
│   ├── filters.py           # Pass/fail filtering + dry-run
│   ├── scoring.py           # Composite scoring with weighted categories
│   ├── universe.py          # Index constituent loaders + file parsing
│   └── configs/
│       ├── config_longterm.yaml
│       └── config_swing.yaml
├── pyproject.toml
├── setup.cfg
├── setup.py
├── run_screener.sh          # Fallback launcher script
└── README.md
```
