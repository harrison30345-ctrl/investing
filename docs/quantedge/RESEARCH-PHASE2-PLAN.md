# QuantEdge Phase Two: pre-registered research plan

**Written 26 Oct 2026, before any Phase Two experiment was run.** Committed on the local branch
`research-phase2` so the plan's timestamp precedes the results. Any later deviation is listed in the
report (`RESEARCH-PHASE2-REPORT.md`), not edited in here.

## Question
Does any new, precisely defined hypothesis beat, out of sample and net of costs, all three of:
1. equal-weight buy-and-hold of the **same** stock universe,
2. the market ETF (SPY for US, ISF.L for UK, both total return),
3. cash at the short rate (FRED DTB3 / SONIA)?

"None does" is an acceptable answer.

## Data and periods (fixed now)

| Item | Value |
|---|---|
| Source | Yahoo daily bars already stored (adjusted for splits and dividends), 25 Oct 2016 → 23 Oct 2026 |
| Universes | `us_large` (20 US large caps), `uk_large` (20 UK large caps). Every symbol has full history (US 2,513 bars; UK 2,523–2,526) |
| **Development data** | everything before **3 Feb 2025** |
| **Holdout (locked)** | **3 Feb 2025 → 23 Oct 2026**. Used once, only for candidates that pass every development criterion below |
| Development split | after indicator warm-up: first 75% train, last 25% validation (chronological) |
| Walk-forward | development data only: 756-session in-sample, 126-session out-of-sample, rolling |

**Why 3 Feb 2025.** Recorded experiments have inspected strategy results up to 3 Dec 2024. Unrecorded audit
diagnostics (26 Oct) showed momentum results up to 8 Jan 2025. Starting the holdout on the first session of
February leaves a gap after everything inspected.

**Known contamination, disclosed up front:**
- (a) The audit printed SPY's total return and maximum drawdown for 4 Dec 2024 → 23 Oct 2026, a
  benchmark-level look at most of the holdout.
- (b) 12-month momentum with an absolute filter (`xs_momentum`, lookback 252, `abs_filter` on) has already
  been inspected on train (2017–23) and validation (2023 – Jan 2025), recorded and unrecorded. H1/H3 below
  are close relatives, so for them **the development validation window isn't fresh**. Only the holdout is a
  clean test of momentum.
- (c) The intraday experiments covered Aug–Sep 2026 on different (5-minute) data.

## Common settings (fixed now)
- **Costs:** project daily defaults. 5 bps full spread (2.5 per side) + 5 bps slippage per side, no commission,
  0.5% UK stamp duty on UK buys. Each run also records 0×/2×/3× frictions.
- **Cash:** uninvested cash earns FRED DTB3 (US) / SONIA (UK). Sharpe, PSR, DSR and Sharpe CIs are measured
  above cash.
- **Portfolio:** five equal slots of 1/5 each (unfilled slots stay in cash), long only, no leverage.
  Rebalance drift tolerance 25%. Trades execute at the next session's open after the signal close.
- **Tie-break:** the existing seeded hash (seed 0), extended to the rebalance engine's buy order. It was
  alphabetical there; this is a methodology fix, applied before any Phase Two run.
- **Statistics:** the existing block bootstrap (2,000 resamples, 20-day blocks), PSR, and Deflated Sharpe with
  trials = distinct parameter sets recorded for the strategy, market and universe. **Previous momentum trials
  count against H1/H3.**

## Hypotheses

### H1: Cross-sectional momentum (pure relative)
- **Rule:** at each rebalance close, rank the 20 stocks by total return from *t−L* to *t−21*. Hold the top 5
  equal-weight from the next open. Rebalance every 21 sessions. No absolute-momentum filter and no regime filter.
- **Variants (only these):** L = 126 (6-1) and L = 252 (12-1). Strategy `xs_momentum` with `abs_filter=False`,
  `regime_filter=False`, `skip=21`, `top_n=5`, `rebalance_days=21`.
- **Why it might work:** under-reaction to news and slow information diffusion (Jegadeesh & Titman 1993);
  one of the most replicated equity anomalies.
- **Why it might not here:** with 20 mega-caps the cross-section is tiny, and the universe is selected on
  later success, so momentum may just be buying the names we know survived.
- **Falsified if:** walk-forward out-of-sample excess return over the equal-weight universe is ≤ 0, or it
  doesn't survive 2× costs.

### H2: Short-term reversal (mean reversion)
- **Rule:** at each rebalance close, rank the 20 stocks by total return over the last *L* sessions. Hold the
  bottom 5 (the biggest recent losers) equal-weight from the next open. Rebalance every 5 sessions. Exit a
  holding when it's no longer in the bottom 5 at a rebalance. No stops.
- **Variants (only these):** L = 5 (one week) and L = 10 (two weeks). New strategy `xs_reversal`, `bottom_n=5`,
  `rebalance_days=5`.
- **Why it might work:** short-horizon over-reaction, and compensation for providing liquidity to forced
  sellers (Lehmann 1990; Jegadeesh 1990).
- **Why it might not here:** the effect is documented as weak in the largest, most liquid stocks, and weekly
  turnover makes it cost-sensitive. UK stamp duty (0.5% per buy) is expected to be prohibitive.
- **Falsified if:** it doesn't beat the equal-weight universe out of sample net of costs, or fails at 2× costs.

### H3: Regime-conditioned momentum
- **Rule:** H1 exactly, but at each rebalance hold the momentum portfolio only if the market ETF's close is
  above its 200-session average. Otherwise hold cash at the short rate. Strategy `xs_momentum` with
  `regime_filter=True`; variants L = 126 and 252.
- **Rationale, specified before any regime-filtered momentum result exists in this project:** momentum
  crashes cluster in sharp rebounds after bear markets (Daniel & Moskowitz 2016). Stepping aside while the
  market is below its long-run trend should cut drawdowns more than it costs in return.
- **Falsified if:** out of sample it doesn't improve Sharpe-above-cash *or* maximum drawdown versus H1, or it
  fails the equal-weight-universe test.

## Runs (all recorded; failures included)
For each hypothesis, variant and market: one **train** run and one **validation** run. For each hypothesis and
market: one **walk-forward** choosing between the two variants on in-sample Sharpe above cash. That's
3 × 2 × 2 × 2 = 24 runs plus 3 × 2 = 6 walk-forwards = **30 experiments**.
No other parameter values will be tried. If a run fails technically, it's fixed and re-run, and both records kept.

## Pre-registered criteria (per hypothesis and market; development data only)
- **C1:** walk-forward OOS annualised excess over the **equal-weight universe**: point estimate > 0 **and** 95%
  CI lower bound > 0.
- **C2:** walk-forward OOS beats the **ETF** (net return) **and** has a higher Sharpe-above-cash than the ETF.
- **C3:** walk-forward OOS beats **cash**: Sharpe above cash > 0 with PSR ≥ 0.95.
- **C4:** walk-forward OOS **Deflated Sharpe ≥ 0.95**.
- **C5:** on **validation**, the variant chosen most often by the walk-forward beats the equal-weight universe net
  of costs, **and still does at 2× costs** (cost_x2 net return > universe return).
- **C6:** at least 30 round-trip trades in the walk-forward OOS.
- **C7 (robustness, only if C1–C6 pass):** re-run that validation without the single symbol that contributed
  most P&L. Excess over the equal-weight universe (of the reduced set) must stay > 0.

**Verdicts**
- **Worth further independent testing:** C1–C7 all pass. Only then is the locked holdout run, once, and reported as is.
- **Rejected:** the walk-forward OOS point estimate vs the equal-weight universe is ≤ 0, **or** both validation
  variants lose to the equal-weight universe, **or** C5's 2× cost test fails.
- **Inconclusive:** anything else (e.g. positive point estimates but intervals including zero, or DSR < 0.95).

If nothing passes, the holdout stays untouched for future hypotheses.

## Checks to report regardless of outcome
- overlapping positions
- survivorship and universe composition
- corporate actions
- missing data
- execution assumptions (next-open fills, costs, cash)
- the multiple-testing count
