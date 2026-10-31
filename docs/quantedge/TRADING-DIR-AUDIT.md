# Audit of `trading/` (Trading Arena), 31 Oct 2026

Read-only audit for decision D6. **Nothing in `trading/` was changed, moved or deleted.**

## What it is
A game-themed, multi-agent paper-trading app built in July 2026, about 2,700 lines of Python.
- **Agents:**
  - Vega (US stocks) and Nova (crypto), both on a 200-day-SMA trend signal
  - Ace (sports betting)
  - Blitz, which has a state file only
- **Each agent's account:** a JSON paper-simulator account in `~/.trading/`.
- **Front end:** a neon Streamlit "hub" (port 8502).

**Status**
- **Not under version control:** `.gitignore` line 51 excludes it, so there's no history and no backup.
- **Running on this Mac via launchd:**
  - `com.tradingarena.daemon` (daily cycle)
  - `com.tradingarena.weekly` (digest plus macOS notification)
  - `com.tradingarena.hub` (dashboard; running)
- **Last log:** Nova $9,932 (−0.7%), Vega $9,836 (−1.6%), on $10,000 simulated each.
- **No broker keys are in use.** `~/.trading/odds_api_key` holds a key for The Odds API (betting feed). It was
  not read.

## Module inventory and verdict

| Module | What it does | Verdict for QuantEdge |
|---|---|---|
| `config.py` | `TRADING_MODE` paper/live; Alpaca URLs; `assert_live_allowed` banner; `TRADING_REQUIRE_APPROVAL` | **Concept reusable, implementation not.** Live is one env flag away, and `TRADING_REQUIRE_APPROVAL=0` removes human approval. That conflicts with D1/D8. QuantEdge's adapter must have no live code path at all |
| `broker/base.py` | `BrokerBase`: account, positions, price, `submit_order` (synchronous, market only); `Order` with status filled/rejected/pending_approval | **Reuse as a starting shape.** Too thin for an order-management system: no order IDs, no lifecycle, no cancel, no partial fills, no clock/calendar |
| `broker/alpaca.py` | Alpaca REST via `requests`: account, positions, latest trade, market order | **Port with fixes.** Defects: (1) maps `accepted`/`new` to **"filled"**, so an unfilled order looks filled; (2) when Alpaca hasn't filled yet, `filled_price` falls back to the **latest trade price**, which fabricates a fill price; (3) no `client_order_id`, so retries can duplicate orders; (4) no polling, cancel or partial-fill handling; (5) the same class talks to live hosts. Good: plain `requests` (no new dependency); keys only from the environment |
| `broker/paper_sim.py` | Instant fills at the yfinance latest price, **zero costs**, JSON state | **Don't reuse for research-grade paper trading.** QuantEdge's `paper.py` is stricter: completed bars only, next-open fills, costs, database state, shared with the backtester. Keep the idea of one isolated account per strategy |
| `engine.py` | Collect agent `Decision`s → approval gate → `submit_order` → log; leaderboard | **Reuse the pattern** (agents propose, the engine executes). Its gate is `input()` on a terminal, unsuitable unattended. No pre-trade risk checks beyond cash |
| `agents/base.py`, `rebalance.py` | `Decision(symbol, side, qty, reason)`; equal-weight top-N rebalancer with thresholds and stop-loss | **Superseded** by QuantEdge strategies, `SignalPortfolio` and the planned allocator |
| `agents/stock_trader.py` | Uses the **fundamental screener's** scoring as a trading brain | **Don't reuse.** It couples execution to the screener (a separate product) and has no point-in-time fundamentals, so it can't be validated |
| `agents/signal_agent.py`, `trend.py`, `momentum.py`, `crypto_trader.py` | 200-day SMA / momentum scores on yfinance history; crypto universe | **Out of scope** (crypto, D3). The SMA idea is already covered by `trend_sma10m` (Phase 4) |
| `backtest/` | Separate small backtester (fees, slippage, stop-loss, fixed cadence) | **Duplicate; retire in favour of `quantedge/backtest`.** Its README result ("200-day SMA: Sharpe 0.71 vs 0.65, max drawdown −25% vs −55%, SPY 2004–2026") was produced without cash-rate Sharpe, walk-forward or confidence intervals. QuantEdge's Phase 4 study of the closely related 10-month rule found **no** risk-adjusted improvement out of sample. The README claim shouldn't be relied on |
| `betting/` | Sports value-betting agent, odds feeds, sportsbook simulator | **Out of scope** (D3). Leave in the Arena |
| `daemon.py`, `schedule.py`, `weekly.py`, `history.py` | launchd daily cycle, interval loop, weekly digest, JSONL equity history | **Superseded** by QuantEdge's worker/scheduler/health. Keep the weekly-digest idea for alert delivery (Phase 9) |
| `hub.py`, `roster.py`, `run.py` | Game UI, agent roster, CLI | Arena-specific; leave as is |
| `prices.py` | yfinance latest price with a 60-second cache | **Don't reuse for execution** (unofficial, delayed). Execution prices come from the broker or validated bars |

## Duplications and conflicts with QuantEdge
1. **Two paper systems:** the Arena sim (instant fills, no costs) and QuantEdge `paper.py` (strict). Results
   aren't comparable.
2. **Two backtesters** with different methodologies. Only QuantEdge's carries the audit and Phase 2–4 safeguards.
3. **Safety model conflict:** the Arena allows live trading with approval switchable off. QuantEdge (D1/D8)
   allows paper only, with live absent from code.
4. **Screener coupling:** `stock_trader.py` imports `screener/`. QuantEdge must not (the screener is a
   separate product).
5. **Scheduling:** launchd on this Mac vs QuantEdge's worker (cloud target, D7). Running both indefinitely means
   two unattended systems to watch.

## Recommended separation (no code moved yet)
```
quantedge/
  research side:  data/ strategies/ specs/ backtest/ research/ agents/      (never imports execution/)
  execution/
    models.py  oms.py  risk.py  reconcile.py  killswitch.py
    brokers/
      base.py     ← shape from trading/broker/base.py, extended (IDs, lifecycle, cancel, clock)
      sim.py      ← semantics of quantedge/paper.py (completed bars, next open, costs)
      alpaca.py   ← port of trading/broker/alpaca.py with the 5 defects fixed; paper host only
      ibkr.py     ← later, same interface
    live/         ← does not exist until the Phase 10 review approves it
trading/  (Arena) unchanged; stays a separate hobby app
```

**What gets ported, with justification:**
- the `BrokerBase` shape
- the Alpaca REST calls (endpoints, headers, error capture)
- the "agents propose, engine executes" separation

Everything else is either superseded by stricter QuantEdge code or out of scope.

## Decisions for you
1. **Version control for `trading/`:** recommended as its own Git repository (it's a separate app), or
   un-ignore it here. Today, losing this disk loses it.
2. **The Arena's launchd jobs:** keep running (harmless: simulated money only), or unload them once QuantEdge
   paper trading runs in the cloud. I won't change them without your instruction.
3. **Arena live mode:** if you want it disabled permanently, the minimal change is to make `load_config()`
   reject `live`. That is a change to the Arena, so it needs your OK.
