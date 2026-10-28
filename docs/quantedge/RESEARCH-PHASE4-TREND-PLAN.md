# QuantEdge Phase Four: pre-registered plan for a 10-month SPY trend filter

**Dated 28 Oct 2026. Committed before any strategy run. Immutable from commit:** any later deviation is
reported in the results document, never edited in here. No run of this strategy exists when this is
committed. Engineering (Part A) is frozen at `a1ab29c`.

## 1. Hypothesis and falsification
**Primary hypothesis.** Holding SPY only while its month-end price is above its 10-month simple moving
average, and cash otherwise, **reduces maximum drawdown and improves risk-adjusted return (Sharpe above
cash)** relative to SPY buy-and-hold, net of costs, out of sample.

**It is falsified (verdict "rejected") if, on the walk-forward out-of-sample result, any one of these holds:**
- the Sharpe-above-cash difference (strategy − SPY) is ≤ 0 (point estimate)
- the strategy's maximum drawdown is not shallower than SPY's (point estimate)
- the Sharpe advantage disappears at 3× trading costs (C3 below fails)

Lower drawdown on its own is not the hypothesis: holding cash part of the time lowers drawdown almost
by construction. The claim being tested is lower drawdown **and** better risk-adjusted return.

## 2. Universe, instrument and prices
- **Instrument:** SPY (SPDR S&P 500 ETF), US market, Yahoo daily bars stored in the project database
  (1998-12-01 onward; validated in Phase Three; 2025-01-09 is a closure override, not a gap).
- **Universe:** `["SPY"]` only. **Cash:** uninvested cash earns FRED DTB3 (3-month T-bill), daily, as in all
  experiments since the audit.
- **Price series and dividends:** dividend- and split-adjusted prices (Yahoo adjusted close; the engine's
  adjusted OHLC). Returns therefore include dividends, reinvested at the ex-date close.
- **Signal series:** the same adjusted close. This makes the signal a total-return trend signal. It is still
  point-in-time: later dividends rescale every earlier price by the same factor, so the ratio of a month-end
  close to its trailing average is unchanged by them. **Interpretation note:** the brief says "closing
  price". The adjusted close is used because it is what the engine trades and values. A raw-price
  variant is **not** run (it would be a second trial).

## 3. The rule (exact)
- **Month-end** = the last NYSE session of a calendar month (trading calendar, with the documented closure
  overrides). Known in advance; no price information is used to identify it.
- At each month-end close *t*: let *P* be the adjusted close at *t*, and *SMA10* the mean of the adjusted closes
  on the last 10 month-ends up to and including *t*.
  - If **P > SMA10** (strictly): hold SPY for the next month.
  - Otherwise: hold cash for the next month.
- **Execution:** the existing convention. Signals use the close of *t*; orders fill at the next session's open
  (the first session of the new month), with costs.
- **Sizing:** 100% target weight. Whole shares (the engine's floor), so a residual of less than one share's
  value stays in cash and earns interest. Rebalance drift tolerance as configured (25%), so no top-up trades
  while the signal is unchanged.
- **Start of any evaluation window** (train, validation, each walk-forward fold): the position is set from
  the most recent completed month-end's signal, evaluated at the window's first close and filled at the
  next open. This reproduces the rule's state; it does not add a decision the rule wouldn't make.
- Fewer than 10 month-ends of history → cash. This never happens after the warm-up below.
- Only one parameter value exists: **10 months**. No other lengths, thresholds, bands, re-entry rules or
  grids are run.

## 4. Engineering needed before any run (no results involved)
The current engine can't express the rule faithfully, so these are added, with tests, and frozen in a
commit before the first run:
1. **Month-end rebalance schedule:** a hook on rebalance strategies that decides on which sessions a
   decision is taken. The default is unchanged (every *N* sessions); existing strategies must reproduce
   stored results exactly.
2. **The strategy itself:** key `trend_sma10m`, parameter `sma_months` fixed at 10 (no other value accepted).
3. **Fixed-rule walk-forward:** walk-forward currently requires at least two candidate values. A single
   value is allowed and recorded as a fixed rule. Each fold still trades only its out-of-sample window.
4. **Paired Sharpe-difference interval:** a 95% block-bootstrap interval for (strategy Sharpe − benchmark
   Sharpe), both above cash, resampling the same blocks of days for both series.
5. **Trade labels:** strategy-specific entry and exit reasons, so rule exits can be told apart from
   end-of-window liquidations.

If any of these turns out impossible without changing the rule, the work stops and I ask for approval.

## 5. Costs, cash and benchmarks
- **Costs:** project defaults, the same as every US experiment. 5 bps full spread (2.5 per side) + 5 bps
  slippage per side; no commission; no stamp duty. SPY's expense ratio is already in the adjusted prices.
  Every run also records 0×/2×/3× trading frictions (walk-forward: stitched out-of-sample cost curve).
- **Benchmark 1 (primary): SPY buy-and-hold**, total return. This is the engine's standard benchmark curve.
  The universe baseline (equal-weight buy-and-hold of `["SPY"]`, bought at the first close with costs) is
  also reported.
- **Benchmark 2 (secondary, reported, not used to pass):** equal-weight buy-and-hold of the nine sector SPDRs
  (Phase Three universe), computed with the engine's `universe_curve` (same costs) over exactly the same
  dates. The same for the walk-forward: the per-fold baskets are chained the way the engine chains them.
- **Benchmark 3: cash** (DTB3). Sharpe ratios are measured above it.
- **Exploratory:** the monthly-rebalanced equal-weight sector basket. Context only.

## 6. Periods (fixed now; computed from the calendar and stored SPY dates, not from results)
| Period | Dates |
|---|---|
| Warm-up | 257 sessions (`min_warmup=257`, as in Phases Two and Three; ≥ 12 month-ends before the first decision) |
| Train | 8 Dec 1999 → 15 Oct 2018 |
| Validation | 16 Oct 2018 → 31 Jan 2025 |
| Walk-forward | train + validation only; 756-session in-sample, 126-session out-of-sample, rolling. 44 folds, OOS 12 Dec 2002 → 20 Dec 2024 |
| **Holdout (locked)** | **3 Feb 2025 → latest.** Used once, only if every criterion passes |

For a fixed rule nothing is chosen in-sample. The walk-forward's value is fold-level dispersion over 22
years. **Known artefact:** each fold starts and ends flat (existing method). So at each of the 43 fold
boundaries a held position is sold at the fold's last close and re-bought at the open two sessions later,
with costs. This is reported (count, cost, and the SPY return missed on those days). It is not corrected,
because correcting it would change the method after seeing results.

## 7. Runs (exactly these; all recorded, failures included)
Source `phase4`, `holdout_start="2025-02-03"`, universe `["SPY"]`, `min_warmup=257`:
1. `trend_sma10m` train
2. `trend_sma10m` validation
3. `trend_sma10m` fixed-rule walk-forward (756/126)

That's **3 experiments**. Holdout run (test period, `confirm_test`) only if the verdict is "worth further
independent testing".

## 8. Metrics and uncertainty
**Reported for every run:**
- CAGR, total return, volatility, Sharpe above cash, maximum drawdown
- time invested (exposure), turnover, transaction costs
- trades, split into rule exits and end-of-window exits
- PSR, Deflated Sharpe
- regime breakdown (existing lagged labels)
- cost curve
- comparisons with SPY, the sector basket and cash

The walk-forward adds fold-level results and C4's thirds.

**Uncertainty:** 95% block bootstrap (2,000 resamples, 20-day blocks) for the Sharpe, the excess return and
the paired Sharpe difference. **Limitations, stated now:**
- Maximum drawdown is one path and has no interval.
- The bootstrap assumes stationary returns; a trend filter's value is concentrated in a few crashes.
- The number of independent trend episodes is small (likely 10–30 switches), so the effective sample is
  far smaller than the day count.
- The regime labels (SPY vs its 200-day average, lagged) are close cousins of the strategy's own signal,
  so per-regime results are partly mechanical.

## 9. Pre-registered criteria
All on the walk-forward stitched out-of-sample result unless stated. "Sharpe" = Sharpe above cash.
- **C1 (drawdown):** strategy maximum drawdown shallower than SPY's by **≥ 10 percentage points**.
- **C2 (risk-adjusted):** Sharpe(strategy) − Sharpe(SPY) **> 0**, **and** the 95% paired bootstrap interval of the
  difference has lower bound **> 0**.
- **C3 (costs):** the C2 point estimate is still > 0 at **3×** trading frictions (walk-forward cost curve).
- **C4 (stability):** OOS divided into three equal consecutive thirds. The Sharpe-difference point estimate
  is > 0 in **at least two of three**.
- **C5 (validation):** on the validation run, Sharpe(strategy) > Sharpe(SPY) **and** maximum drawdown
  shallower than SPY's (point estimates).
- **C6 (activity):** at least **10 rule exits** (signal-driven, not end-of-window) in the OOS.

**Reported with a pre-set label, not pass/fail:** CAGR shortfall vs SPY. A shortfall of more than **1.5
percentage points a year** is called "materially lower CAGR".

**Verdicts**
- **Worth further independent testing:** C1–C6 all pass. Then, and only then, run the holdout once and report
  it as is.
- **Rejected:** any falsification condition in §1.
- **Inconclusive:** anything else. The holdout stays locked.

## 10. Multiple testing
- **New hypothesis family:** time-series trend on a single index ETF. It's a different rule from the
  cross-sectional momentum and reversal families tested so far.
- **Per-universe DSR trials** (existing method, the DSR input): train and validation = 1 distinct
  parameter set. The walk-forward record counts as its own parameter set (existing behaviour,
  conservative), so its DSR uses 2.
- **Project-wide** (Phase Four accounting): before this study, 42 distinct configurations across 6 families
  (by #96). This study adds 1 configuration and 1 family (43 / 7).
- **Sensitivity (reported, not a criterion):** the walk-forward DSR recomputed with 43 trials. It is not a valid
  DSR input (different data), only an illustration of how much the wider search would discount it.

## 11. Prior exposure and data limitations
- **The rule is famous.** It is Faber's 10-month SMA timing rule (published 2007, designed on data up to about
  2006). So 1999–2006 here was the original author's design sample, and its behaviour in 2008 is widely
  known. I (and the user) know broadly how trend filters did in 2008, 2020 and 2022. That knowledge can't
  be removed. It is why the criteria demand improvement in at least two of three thirds and at 3× costs.
- **SPY's development-period path** has been shown as a benchmark in many earlier experiments. Its
  holdout-period total return and drawdown (to 23 Oct 2026) were printed once in the audit. No strategy
  was ever run on the holdout.
- **Data:**
  - Yahoo is unofficial and not point-in-time; adjusted history can be revised.
  - Adjusted opens are derived by ratio from the close adjustment.
  - Dividends are reinvested at ex-date with no taxes.
  - DTB3 is a secondary-market yield, which an investor earns only approximately.
  - Whole shares; no market impact beyond slippage.
  - 2025-01-09 is a known closure (override), outside development data.

## 12. What approval of the brief covers
- The engineering in §4.
- The 3 runs in §7.
- The holdout run only if earned.
- Free data only. Nothing pushed or merged.
