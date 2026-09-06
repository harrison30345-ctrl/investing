#!/usr/bin/env python3
"""
Fundamental Stock Screener — CLI entry point.

Usage examples:
    screener --config longterm --universe sp500
    screener --config swing --tickers AAPL,MSFT,GOOG
    screener --config /path/to/custom.yaml --tickers watchlist.csv
    screener --config longterm --universe nasdaq100 --dry-run
    screener --dashboard
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from tabulate import tabulate

from screener.data_fetcher import YFinanceFetcher
from screener.metrics import calculate_all_metrics
from screener.filters import apply_filters, format_dry_run, load_config, screen_batch
from screener.scoring import score_batch
from screener.universe import get_universe, load_tickers_from_file

# Resolve paths — configs are bundled inside the package, output goes to user's home
PACKAGE_DIR = Path(__file__).parent
CONFIGS_DIR = PACKAGE_DIR / "configs"
USER_DATA_DIR = Path.home() / ".screener"
OUTPUT_DIR = USER_DATA_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Built-in config shortcuts
CONFIG_ALIASES = {
    "longterm": CONFIGS_DIR / "config_longterm.yaml",
    "long": CONFIGS_DIR / "config_longterm.yaml",
    "swing": CONFIGS_DIR / "config_swing.yaml",
}


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def resolve_config(config_arg: str) -> str:
    """Resolve a config argument to a file path. Supports aliases like 'longterm' or 'swing'."""
    if config_arg in CONFIG_ALIASES:
        return str(CONFIG_ALIASES[config_arg])
    path = Path(config_arg)
    if path.exists():
        return str(path)
    # Try looking in the configs directory
    in_configs = CONFIGS_DIR / config_arg
    if in_configs.exists():
        return str(in_configs)
    in_configs_yaml = CONFIGS_DIR / f"{config_arg}.yaml"
    if in_configs_yaml.exists():
        return str(in_configs_yaml)
    print(f"Error: config not found: {config_arg}")
    print(f"Available built-in configs: {', '.join(CONFIG_ALIASES.keys())}")
    print(f"Or provide a path to a YAML file.")
    sys.exit(1)


def resolve_tickers(args) -> list[str]:
    """Resolve ticker list from CLI arguments."""
    tickers = []

    if args.universe:
        tickers = get_universe(args.universe)
        if not tickers:
            logging.error(f"Failed to load universe: {args.universe}")
            sys.exit(1)

    if args.tickers:
        if Path(args.tickers).exists():
            tickers = load_tickers_from_file(args.tickers)
        else:
            tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    if not tickers:
        logging.error("No tickers specified. Use --universe or --tickers.")
        sys.exit(1)

    seen = set()
    unique = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    return unique


def format_terminal_summary(results: list[dict], top_n: int = 20) -> str:
    """Format a clean terminal summary of top results."""
    if not results:
        return "No stocks passed the screening filters."

    display_cols = [
        ("ticker", "Ticker"),
        ("name", "Name"),
        ("composite_score", "Score"),
        ("pe_trailing", "P/E"),
        ("roe", "ROE%"),
        ("debt_to_equity", "D/E"),
        ("net_margin", "Net Mgn%"),
        ("revenue_growth_1y", "Rev Gr%"),
        ("fcf_yield", "FCF Yld%"),
        ("data_completeness", "Data%"),
    ]

    rows = []
    for r in results[:top_n]:
        row = []
        for key, _ in display_cols:
            val = r.get(key)
            if val is None:
                row.append("—")
            elif isinstance(val, float):
                row.append(f"{val:.1f}")
            else:
                s = str(val)
                row.append(s[:25] if len(s) > 25 else s)
        rows.append(row)

    headers = [label for _, label in display_cols]
    return tabulate(rows, headers=headers, tablefmt="simple", numalign="right")


def save_results_csv(results: list[dict], config_name: str) -> str:
    """Save full results to CSV."""
    if not results:
        return ""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"screen_{config_name}_{timestamp}.csv"
    filepath = OUTPUT_DIR / filename

    col_order = [
        "ticker", "name", "composite_score",
        "score_profitability", "score_financial_health", "score_valuation",
        "score_growth", "score_cash_flow",
        "pe_trailing", "pe_forward", "pb", "ps", "ev_ebitda", "peg",
        "roe", "roa", "roic", "gross_margin", "operating_margin", "net_margin",
        "debt_to_equity", "current_ratio", "quick_ratio",
        "interest_coverage", "net_debt_to_ebitda",
        "revenue_growth_1y", "revenue_growth_3y_cagr", "revenue_growth_5y_cagr",
        "eps_growth_1y", "eps_growth_3y_cagr", "eps_growth_5y_cagr", "fcf_growth_1y",
        "fcf", "fcf_yield", "fcf_margin",
        "dividend_yield", "payout_ratio", "dividend_growth_streak",
        "fcf_positive_3y", "no_dilution_3y",
        "data_completeness",
    ]

    available_cols = [c for c in col_order if c in results[0]]
    extra_cols = [c for c in results[0].keys() if c not in col_order]
    all_cols = available_cols + extra_cols

    df = pd.DataFrame(results)[all_cols]
    df.to_csv(filepath, index=False, float_format="%.2f")
    return str(filepath)


def run_screen(args) -> list[dict]:
    """Core screening logic. Returns scored results list."""
    logger = logging.getLogger(__name__)

    config_path = resolve_config(args.config)
    logger.info(f"Loading config: {config_path}")
    config = load_config(config_path)
    config_name = Path(config_path).stem

    tickers = resolve_tickers(args)
    logger.info(f"Screening {len(tickers)} tickers")

    cache_hours = config.get("cache", {}).get("expiry_hours", 24)
    if args.no_cache:
        cache_hours = 0
    fetcher = YFinanceFetcher(cache_expiry_hours=cache_hours)

    print(f"\nFetching data for {len(tickers)} tickers...")
    raw_data = fetcher.fetch_batch(tickers)

    print("Calculating metrics...")
    all_metrics = []
    failed_tickers = []
    for ticker in tickers:
        data = raw_data.get(ticker, {})
        if not data.get("info"):
            logger.warning(f"{ticker}: no data returned, skipping")
            failed_tickers.append(ticker)
            continue
        try:
            metrics = calculate_all_metrics(ticker, data)
            all_metrics.append(metrics)
        except Exception as e:
            logger.error(f"{ticker}: metrics calculation failed — {e}")
            failed_tickers.append(ticker)

    if failed_tickers:
        print(f"\n⚠ {len(failed_tickers)} tickers failed: {', '.join(failed_tickers[:10])}")
        if len(failed_tickers) > 10:
            print(f"  ... and {len(failed_tickers) - 10} more")

    print("Applying screening filters...")
    passed, failed = screen_batch(all_metrics, config)

    if args.dry_run:
        print(format_dry_run(passed, failed, config))

    print("Scoring and ranking...")
    results = score_batch(passed, config)

    print(f"\n{'=' * 70}")
    print(f"  SCREENING RESULTS — {config_name}")
    print(f"  {len(tickers)} tickers → {len(passed)} passed filters → ranked by score")
    print(f"{'=' * 70}\n")

    if results:
        print(format_terminal_summary(results, top_n=args.top))
        csv_path = save_results_csv(results, config_name)
        print(f"\nFull results saved to: {csv_path}")
    else:
        print("No stocks passed all screening filters.")
        print("Try loosening your config thresholds or running with --dry-run to see why.")

    print()
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Fundamental Stock Screener",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  screener -c longterm -u sp500
  screener -c swing -t AAPL,MSFT,GOOG,META
  screener -c longterm -t my_watchlist.csv
  screener -c longterm -u nasdaq100 --dry-run
  screener --dashboard
        """,
    )
    parser.add_argument(
        "--config", "-c",
        help="Config name (longterm, swing) or path to YAML file",
    )
    parser.add_argument(
        "--tickers", "-t",
        help="Comma-separated tickers OR path to a CSV/text file",
    )
    parser.add_argument(
        "--universe", "-u",
        choices=["sp500", "nasdaq100", "ftse100", "tech", "ai", "space", "growth", "all_curated"],
        help="Use a predefined universe (tech, ai, space, growth, all_curated, sp500, nasdaq100, ftse100)",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Number of top results to show in terminal (default: 20)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show detailed pass/fail for each filter per stock",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Ignore cache and fetch fresh data",
    )
    parser.add_argument(
        "--dashboard", action="store_true",
        help="Launch the interactive web dashboard",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.dashboard:
        launch_dashboard()
        return

    if not args.config:
        parser.error("--config is required (use 'longterm', 'swing', or a YAML path)")

    if not args.tickers and not args.universe:
        parser.error("specify --tickers or --universe")

    run_screen(args)


def launch_dashboard():
    """Launch the Streamlit dashboard."""
    import subprocess
    dashboard_path = Path(__file__).parent / "dashboard.py"
    print("Launching dashboard at http://localhost:8501 ...")
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(dashboard_path),
                    "--server.headless", "true"], check=True)


if __name__ == "__main__":
    main()
