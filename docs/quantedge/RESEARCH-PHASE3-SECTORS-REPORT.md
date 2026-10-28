# QuantEdge Phase Three: sector-ETF momentum results

**Verdict: INCONCLUSIVE.** Not rejected, but not "worth further independent testing". Over 22 years of walk-forward
out-of-sample data, 3-of-9 sector momentum earned **+0.36% a year** over an equal-weight buy-and-hold of the same nine
ETFs, with a 95% interval of **−2.4% to +3.4%**. It **lagged SPY** (10.87% vs 10.96% a year), and only **one of the three
OOS thirds** was positive. Three criteria failed (C1, C2, C7), so C8 was not run and **the holdout stays locked**.

Run 28 Oct 2026 under the plan [RESEARCH-PHASE3-SECTORS-PLAN.md](RESEARCH-PHASE3-SECTORS-PLAN.md), approved as
written that day. Code frozen at `baee555` before any experiment. Experiments **#92–#96**, source `phase3`.
This is research, not advice.

## 1. Data (checked before any experiment)
Yahoo daily bars (free), back-filled with the new `refresh --since` option. FRED DTB3 cash rate, now requested from 1990.

| Series | First bar | Rows | Validator | Notes |
|---|---|---|---|---|
| XLB XLE XLF XLI XLK XLP XLU XLV XLY | 1998-12-22 | 7,005 each | pass | see below |
| SPY | 1998-12-01 | 7,020 | pass | stored history extended back from Oct 2016 (upsert; nothing deleted) |

- **2025-01-09** is flagged "missing session" on all ten. That day the market was closed for the national day of
  mourning, which the pinned calendar (pandas_market_calendars 4.4.2) doesn't know. It's a false positive, and it's in the holdout.
- **XLI:** one zero-volume day. Also, the bar for 28 Oct 2026 was fetched during the session (partial; one OHLC warning
  on XLI). Both are outside development data.
- **No missing adjusted closes.** The largest one-day moves are real: XLE −20% on 9 Mar 2020; XLK +16% on 3 Jan 2001 (the
  Fed's surprise cut); several ±12–17% days in Oct–Dec 2008.
- **Cash:** DTB3 now covers the whole window (Dec 1998: 4.4%; mid-2000: 5.6%). Before this change, 1999 cash would
  have counted as 0%.

**Periods.** After the 257-session warm-up, development runs from 30 Dec 1999 to 31 Jan 2025:
- **Train:** 30 Dec 1999 → 19 Oct 2018
- **Validation:** 22 Oct 2018 → 31 Jan 2025
- **Walk-forward OOS:** 6 Jan 2003 → 15 Jan 2025, 44 folds
- **Holdout:** 3 Feb 2025 →, not used

## 2. Results

### Train and validation
| # | Lookback | Period | Net return | EW basket | SPY | Sharpe (above cash) | EW basket Sharpe | Ann. excess vs basket [95% CI] | Trades |
|---|---|---|---|---|---|---|---|---|---|
| 92 | 126 | train | +183% | +231% | +167% | 0.31 | 0.36 | −0.9% [−4.0, +1.9] | 224 |
| 93 | 126 | validation | +102% | +111% | +142% | 0.53 | 0.59 | −0.8% [−7.2, +5.0] | 68 |
| 94 | 252 | train | +219% | +231% | +167% | 0.34 | 0.36 | −0.2% [−3.3, +3.0] | 150 |
| 95 | 252 | validation | +144% | +111% | +142% | 0.68 | 0.59 | +2.7% [−4.1, +8.7] | 52 |

### Walk-forward (#96), choosing L between 126 and 252 on in-sample Sharpe above cash
- **Choices:** L=252 in 32 folds, L=126 in 12, so the **modal variant is 252**.
- **Returns:**
  - net +867% (CAGR 10.87%)
  - EW basket +801% (10.51%)
  - SPY +885% (10.96%)
  - cash +41%
- **Sharpe above cash:** 0.566 [0.20, 0.99]. SPY 0.569; EW basket 0.563.
- **Statistics:** PSR 0.996. Deflated Sharpe 0.963 (3 trials on these nine symbols).
- **Risk and trading:**
  - max drawdown −51.4% (SPY −52.1%, basket −50.5%)
  - 295 round trips
  - costs 14.9% of starting capital over 22 years

### Criteria
| | Criterion | Result | |
|---|---|---|---|
| C1 | WF excess vs EW basket > 0 **and** CI low > 0 | +0.36%/yr, CI [−2.39%, +3.35%] | **FAIL** |
| C2 | WF net return > SPY **and** Sharpe > SPY's | 867% < 885%; 0.566 < 0.569 | **FAIL** |
| C3 | WF Sharpe above cash > 0 and PSR ≥ 0.95 | 0.566; PSR 0.996 | pass |
| C4 | WF Deflated Sharpe ≥ 0.95 | 0.963 | pass |
| C5 | Modal variant (252) beats basket on validation, also at 2× costs | +144% vs +111%; at 2×: +138% | pass |
| C6 | ≥ 30 round trips OOS | 295 | pass |
| C7 | Excess vs basket > 0 in ≥ 2 of 3 OOS thirds | −0.24%, −1.88%, +3.27%/yr (1 of 3) | **FAIL** |
| C8 | Leave out the top contributor | not run (C1–C7 didn't all pass) | — |

**Why "inconclusive" and not "rejected".** The rejection rule was a WF point estimate ≤ 0, or C5's 2× cost test failing.
Neither happened. The point estimate is positive but small, and statistically indistinguishable from zero.

**How to read C3 and C4.** These test the strategy against cash, not for an edge. A fully invested long-only basket
passes them too (SPY's Sharpe above cash over the same period is 0.569). They show that holding equities paid,
not that the momentum rule did.

**C7 thirds:**
| Third | Dates | Strategy | EW basket | SPY | Ann. excess vs basket |
|---|---|---|---|---|---|
| 1 | 6 Jan 2003 → 10 May 2010 | +71.4% | +74.2% | +55.7% | −0.24% |
| 2 | 10 May 2010 → 11 Sep 2017 | +104.8% | +131.9% | +141.2% | −1.88% |
| 3 | 11 Sep 2017 → 15 Jan 2025 | +175.5% | +122.9% | +162.3% | +3.27% |

## 3. Reported, not used to pass
- **Where the excess comes from.** All of it is from the last third, 2017–2025, and mainly from 2020 (+6.1 pts vs the
  basket), 2022 (+9.2 pts: energy) and 2023 (+5.2 pts).
- **Publication decay.** 2003–2009: −1.0%/yr vs the basket; 2010–Jan 2025: +1.0%/yr. That's the opposite of the decay
  pattern the plan warned about. But one late good stretch on nine assets is thin evidence either way.
- **Per-ETF share of walk-forward OOS P&L:**
  - XLK 26.5%, XLY 19.3%, XLI 16.1%, XLE 12.0%, XLF 9.7%, XLV 8.0%, XLP 5.6%, XLB 2.4%, XLU 0.6%
  - This is much less concentrated than Phase Two's single stocks. C8 would have removed XLK.
- **Cost curve** (net return at 0× / 1× / 2× / 3× frictions):

  | # | 0× | 1× | 2× | 3× | EW basket |
  |---|---|---|---|---|---|
  | 92 | +216% | +183% | +154% | +128% | +231% |
  | 93 | +107% | +102% | +95% | +89% | +111% |
  | 94 | +243% | +219% | +196% | +175% | +231% |
  | 95 | +150% | +144% | +138% | +133% | +111% |

  The 6-1 variant (L=126) loses to the basket in both periods **even with zero costs**. The 12-1 variant beats the
  basket before costs in train (+243% vs +231%) but not after.
- **Regimes** (WF OOS, annualised; labels lagged):

  | Regime | Strategy | SPY |
  |---|---|---|
  | Uptrend (80% of days) | 11.8% | 11.0% |
  | Downtrend | 12.8% | 16.3% |
  | High volatility | 13.5% | 14.7% |
  | Low volatility | 10.8% | 9.8% |

- **Tie-break seeds:** no contested slots (seed spread not needed; one seed).
- **GICS reclassification windows** (flagged in the plan):
  - **2016** (real estate leaves XLF, 19 Sep 2016; Jun 2016 → Mar 2017): strategy +1.8% vs basket +12.4%. 2016 was the
    strategy's worst year relative to the basket (−8.6 pts), mostly a post-election rotation it missed.
  - **2018** (XLC created, 24 Sep 2018; Jun 2018 → Mar 2019): strategy +6.7% vs basket +4.0%.
  - Neither window explains the overall result.

## 4. Multiple testing across the project
The DSR counts 3 trials on these nine symbols. Across the whole project, the database now records **23 distinct
`xs_momentum` (parameter set × universe) configurations** (10 distinct parameter sets).
- That's 21 before this study. **The plan's "≈ 15" was an underestimate**, corrected here from the database.
- At that count, a +0.36%/yr point estimate with a CI spanning ±3% is what chance alone would produce.

## 5. Deviations from the plan
1. **Plan status line** changed from "draft" to "approved" after approval. The plan's body is unchanged.
2. **Bias-note text:** experiments #92–96 carry the stock-universe note "universe is today's large caps; delisted or demoted
   companies are missing". That's wrong for these ETFs, none of which has closed. The code was frozen, so it wasn't
   edited. It's a label only; no number depends on it.
3. **No cost curve inside the walk-forward record:** the walk-forward runner doesn't compute one. Cost sensitivity is
   reported for #92–95, and C5's 2× test uses #95 as pre-registered.
4. **Full-history fetch rewrote SPY's stored Oct 2016 → today bars** at Yahoo's current adjustment basis (upsert, as the
   plan said). Earlier experiments that used SPY as a benchmark were run on the older basis. Their stored results
   are unchanged; a re-run could differ slightly in the benchmark column.

No other deviations. Exactly 5 experiments were run. All completed; none were discarded.

## 6. Holdout status
**Untouched.** Checked after the runs:
- no experiment has `used_test_set`
- no holdout-tagged experiment ends on or after 3 Feb 2025
- no Phase Three trade exits on or after 3 Feb 2025

The walk-forward's last OOS bar is 15 Jan 2025.

## 7. What this means, and options
Sector momentum on the nine SPDRs gives **no evidence of an edge** over simply holding the nine sectors, or SPY, net of
costs. The Phase Two concern (survivorship) is much weaker here, so this is a cleaner "probably no" than Phase Two's.

Reasonable next steps, each needing its own pre-registered plan and your approval:
- **Stop momentum work.** Across 23 configurations, nothing has passed. Spending the holdout on any of them would waste it.
- **A different signal family** on the same clean universe (e.g. low-volatility or trend/time-series rules on the
  sector ETFs). A new family still counts toward the multiple-testing tally.
- **Wider survivorship-free universes:** country ETFs, or a paid point-in-time constituents source. The latter needs
  approval for spend.

## 8. Files, tests, experiments
- **Code (`baee555`):**
  - `quantedge/universe.py` (`us_sectors`, `SECTOR_ETFS`)
  - `quantedge/data/service.py` (`refresh_daily(since=...)`)
  - `quantedge/data/repository.py` (`first_bar_ts`)
  - `quantedge/rates.py` (FRED start 1990; cache keyed by start)
  - `quantedge/cli.py` (`refresh --since`, `--universe us_sectors`)
- **Tests:** `tests/quantedge/test_phase3.py` (4 new). QuantEdge suite: **162 passed**.
  - The repo-wide `pytest tests` has **1 failure outside QuantEdge**: `tests/test_language.py` flags "% of portfolio" in
    `screener/dashboard.py`. That's committed screener code this work didn't touch, so it was left alone.
- **Experiments:**
  - #92 (L=126 train), #93 (L=126 validation), #94 (L=252 train), #95 (L=252 validation), #96 (walk-forward)
  - source `phase3`, `holdout_start` 2025-02-03, universe `us_sectors`
