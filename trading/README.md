# Trading Arena 🎮

A game-themed, multi-agent trading engine. Each agent lives in a "room" of a
game world, trades a live market with its own capital, and competes on a shared
leaderboard. **Paper-first, live-ready.**

## The world

| Agent | Room | Market | Brain |
|-------|------|--------|-------|
| 📈 Vega | The Trading Floor | US stocks | Trend-following (price > 200-day SMA) |
| 🚀 Nova | The Crypto Casino | Liquid crypto | Trend-following (price > 200-day SMA) |
| 🎲 Ace | The Sportsbook | Sports betting | Value betting + fractional Kelly |

Each trading agent runs on its own isolated paper account
(`~/.trading/sim_<name>.json`); Ace has a paper sportsbook bankroll
(`~/.trading/bets_ace.json`). Separate accounts make the leaderboard a real
competition.

### Why these strategies

Both traders use the **200-day SMA trend filter** (hold when price is above its
200-day average, else cash). Backtested on SPY 2004–2026 (through 2008, 2020,
2022): it *lags* buy-and-hold on raw return but with a **higher Sharpe (0.71 vs
0.65) and less than half the max drawdown (−25% vs −55%)** — a robust,
low-tuning risk reducer, not a return maximizer. The earlier naive momentum
strategy lost to buy-and-hold in backtest and was dropped.

Ace uses **value betting sized by fractional Kelly** — it only bets when its
probability estimate beats the book's implied odds (positive expected value),
and stakes a fraction of the Kelly-optimal amount. Its edge is being sharper
than the (imperfect, vig-charging) book. In simulation a modest edge grows the
bankroll with high variance; no edge loses to the vig — exactly like reality.
The live-odds feed is simulated; a real book needs a paid odds API.

## Run it

```bash
# Visual game hub (recommended):
streamlit run trading/hub.py

# One tick of the whole roster from the CLI:
python3 -m trading.run
python3 -m trading.run --leaderboard-only

# Autonomous — leave it running on an interval:
python3 -m trading.schedule --interval 1h
python3 -m trading.schedule --interval 30m --rounds 10   # bounded
```

## Safety: paper vs. live

Paper trading is the default and needs nothing. Real money is deliberately hard
to turn on — it requires **all** of:

1. `TRADING_MODE=live`
2. `ALPACA_API_KEY` + `ALPACA_API_SECRET` (live keys)
3. Per-order human approval at the terminal (unless `TRADING_REQUIRE_APPROVAL=0`)

No agent can place a real order unattended. See `config.py`.

## Architecture

```
trading/
  config.py          # paper/live config + the safety gate
  prices.py          # yfinance price feed (for the simulator)
  broker/
    base.py          # BrokerBase interface (Account/Position/Order)
    paper_sim.py     # built-in simulator — zero setup, persistent P&L
    alpaca.py        # Alpaca adapter (paper + live, same code path)
  agents/
    base.py          # BaseAgent + Decision
    rebalance.py     # shared "scores -> trades" logic
    signal_agent.py  # PriceSignalAgent — Vega & Nova (pluggable score fn)
    trend.py         # golden-cross + 200-day SMA signals (validated)
    momentum.py      # momentum signal (kept; underperformed in backtest)
    stock_trader.py  # legacy fundamental agent (kept, unused in roster)
    crypto_trader.py # legacy momentum agent (kept, unused in roster)
  betting/
    odds.py          # simulated sportsbook odds feed (swap for a paid API)
    book.py          # paper bankroll + bet settlement
    agent.py         # Ace — value betting + fractional Kelly
  backtest/
    data.py          # cached historical price loader
    engine.py        # point-in-time replay with costs
    metrics.py       # CAGR / Sharpe / drawdown / win rate
    run.py           # `python3 -m trading.backtest.run --strategy ...`
  roster.py          # the cast of the world (one source of truth)
  engine.py          # runs ticks, approval gate, leaderboard
  run.py             # one-shot CLI
  schedule.py        # autonomous interval runner
  hub.py             # Streamlit game hub
```

## Going live (step 4)

The `AlpacaBroker` adapter is built. To use real market fills you need a free
Alpaca account (https://alpaca.markets → Paper Trading for keys). Note: one
Alpaca key = one account, so the multi-agent leaderboard stays on the built-in
simulator; Alpaca is best for running a single agent against real fills. Wiring
the roster to Alpaca is a small change once you have keys.
