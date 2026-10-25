# Stock Screener — Claude Code Instructions

This project is a fundamental stock screener. The user interacts with it through conversation — they ask to screen stocks and you run the tool and present results.

## How to run the screener

Always run from the project directory using:
```bash
python3 -m screener.cli [flags]
```

### Common commands

```bash
# Long-term screen
python3 -m screener.cli -c longterm -t AAPL,MSFT,GOOG
python3 -m screener.cli -c longterm -u sp500
python3 -m screener.cli -c longterm -u nasdaq100 --dry-run

# Swing screen  
python3 -m screener.cli -c swing -t AAPL,MSFT,NVDA
python3 -m screener.cli -c swing -u sp500

# Custom config
python3 -m screener.cli -c /path/to/custom.yaml -t AAPL,MSFT
```

### Flags
- `-c` / `--config`: `longterm`, `swing`, or path to YAML
- `-t` / `--tickers`: comma-separated tickers or path to CSV/TXT file
- `-u` / `--universe`: `sp500`, `nasdaq100`, `ftse100`
- `--dry-run`: show why each stock passed/failed each filter
- `--top N`: show top N results (default 20)
- `--no-cache`: force fresh data fetch
- `-v`: verbose/debug logging

## How to present results

After running the screener, present results conversationally:
1. State how many tickers were screened and how many passed
2. Show the top results in a clean markdown table
3. Call out anything interesting (e.g. a stock scoring unusually high/low, data gaps)
4. If the user asked about specific stocks, comment on why they passed/failed

## Configs

Built-in configs are in `screener/configs/`. The user can ask to adjust thresholds — edit the YAML directly when they do.

- `config_longterm.yaml`: quality/value bias (PE<25, ROE>15%, D/E<1, etc.)
- `config_swing.yaml`: "not garbage" filter (profitable, D/E<2, rev growth>0, FCF>0)

## Data storage

- Cache: `~/.screener/cache/` (parquet, 24h expiry)
- Output CSVs: `~/.screener/output/`

## Key files

- `screener/cli.py` — CLI entry point
- `screener/data_fetcher.py` — yfinance data fetching + caching
- `screener/metrics.py` — all metric calculations
- `screener/filters.py` — pass/fail screening logic
- `screener/scoring.py` — composite 0-100 scoring
- `screener/universe.py` — index constituent loaders
- `screener/dashboard.py` — Streamlit web dashboard
