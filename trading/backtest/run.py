from __future__ import annotations

"""
Backtest runner — replays a chosen strategy over history with costs and compares
it to buying-and-holding the same universe.

Examples:
    # Golden cross on SPY over 20+ years (the classic test):
    python3 -m trading.backtest.run --strategy golden_cross --universe SPY \
        --start 2004-01-01 --periods-per-year 252

    # Momentum on the crypto basket:
    python3 -m trading.backtest.run --strategy momentum --periods-per-year 365

Only price-based strategies can be backtested honestly with yfinance data. The
fundamental stock strategy needs point-in-time fundamentals, which yfinance does
not provide, so it is intentionally not offered here.
"""

import argparse
import logging
from datetime import date, timedelta

from ..agents.crypto_trader import DEFAULT_UNIVERSE
from ..agents.momentum import momentum_score
from ..agents.trend import golden_cross_score, price_vs_sma_score
from .data import load_history
from .engine import Backtester, buy_and_hold
from .metrics import Performance, compute_performance

# strategy name -> its scoring fn and sensible defaults
STRATEGIES = {
    "golden_cross": {
        "score_fn": golden_cross_score,
        "lookback": 200, "buy": 50.0, "sell": 50.0,
        "rebalance": 1, "stop": 100.0,   # the death cross is the exit; no extra stop
        "universe": ["SPY"], "start_years": 20, "ppy": 252,
    },
    "sma200": {
        "score_fn": price_vs_sma_score,
        "lookback": 200, "buy": 50.0, "sell": 50.0,
        "rebalance": 1, "stop": 100.0,
        "universe": ["SPY"], "start_years": 20, "ppy": 252,
    },
    "momentum": {
        "score_fn": momentum_score,
        "lookback": 31, "buy": 58.0, "sell": 42.0,
        "rebalance": 7, "stop": 25.0,
        "universe": DEFAULT_UNIVERSE, "start_years": 3, "ppy": 365,
    },
}


def _print_perf(label: str, perf: Performance) -> None:
    wr = f"{perf.win_rate_pct:.0f}%" if perf.win_rate_pct is not None else "—"
    print(f"\n  {label}")
    print(f"    Final equity   : ${perf.final_equity:,.0f}")
    print(f"    Total return   : {perf.total_return_pct:+.1f}%")
    print(f"    CAGR           : {perf.cagr_pct:+.1f}%")
    print(f"    Annual vol     : {perf.ann_vol_pct:.1f}%")
    print(f"    Sharpe         : {perf.sharpe}")
    print(f"    Max drawdown   : {perf.max_drawdown_pct:.1f}%")
    print(f"    Win rate       : {wr}  ({perf.num_trades} closed trades)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy backtest")
    parser.add_argument("--strategy", choices=list(STRATEGIES), default="golden_cross")
    parser.add_argument("--universe", default=None, help="comma-separated tickers")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=str(date.today()))
    parser.add_argument("--buy-threshold", type=float, default=None)
    parser.add_argument("--sell-threshold", type=float, default=None)
    parser.add_argument("--max-positions", type=int, default=None)
    parser.add_argument("--stop-loss", type=float, default=None)
    parser.add_argument("--rebalance", type=int, default=None)
    parser.add_argument("--slippage", type=float, default=5.0, help="bps per side")
    parser.add_argument("--fee", type=float, default=0.0, help="bps per trade")
    parser.add_argument("--starting-cash", type=float, default=10_000.0)
    parser.add_argument("--periods-per-year", type=int, default=None)
    parser.add_argument("--fractional", action="store_true",
                        help="allow fractional units (default for crypto)")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(message)s")

    spec = STRATEGIES[args.strategy]
    universe = ([t.strip().upper() for t in args.universe.split(",")]
                if args.universe else spec["universe"])
    start = args.start or str(date.today() - timedelta(days=spec["start_years"] * 365))
    ppy = args.periods_per_year or spec["ppy"]
    lookback = spec["lookback"]
    is_crypto = any(u.endswith("-USD") for u in universe)
    fractional = args.fractional or is_crypto
    max_positions = args.max_positions or len(universe)

    prices = load_history(universe, start, args.end, use_cache=not args.no_cache)
    if prices.empty or len(prices) < lookback + 5:
        print("Not enough price history for a backtest.")
        return

    bt = Backtester(
        prices=prices,
        score_fn=spec["score_fn"],
        buy_threshold=args.buy_threshold if args.buy_threshold is not None else spec["buy"],
        sell_threshold=args.sell_threshold if args.sell_threshold is not None else spec["sell"],
        max_positions=max_positions,
        stop_loss_pct=args.stop_loss if args.stop_loss is not None else spec["stop"],
        lookback=lookback,
        rebalance_days=args.rebalance or spec["rebalance"],
        starting_cash=args.starting_cash,
        slippage_bps=args.slippage,
        fee_bps=args.fee,
        fractional=fractional,
    )
    result = bt.run()

    strat_perf = compute_performance(
        result.equity_curve, periods_per_year=ppy,
        realized_trade_pnls=result.realized_pnls)
    bh_curve = buy_and_hold(prices, args.starting_cash, lookback)
    bh_perf = compute_performance(bh_curve, periods_per_year=ppy)

    print("\n" + "=" * 62)
    print(f"  {args.strategy.upper()} BACKTEST — "
          f"{prices.index[0].date()} → {prices.index[-1].date()}")
    print(f"  Universe: {', '.join(universe)}")
    print(f"  Rebalance every {args.rebalance or spec['rebalance']}d · "
          f"hold up to {max_positions} · slippage {args.slippage:.0f}bps")
    print("=" * 62)
    _print_perf(f"📈 {args.strategy} strategy", strat_perf)
    _print_perf("📊 Buy & hold", bh_perf)

    edge = strat_perf.total_return_pct - bh_perf.total_return_pct
    print("\n" + "-" * 62)
    verdict = "BEAT" if edge > 0 else "LAGGED"
    print(f"  Strategy {verdict} buy-and-hold by {edge:+.1f} pts total return.")
    dd_better = strat_perf.max_drawdown_pct > bh_perf.max_drawdown_pct
    print(f"  Max drawdown: {strat_perf.max_drawdown_pct:.0f}% vs "
          f"{bh_perf.max_drawdown_pct:.0f}% "
          f"({'shallower ✓' if dd_better else 'deeper'}).")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    main()
