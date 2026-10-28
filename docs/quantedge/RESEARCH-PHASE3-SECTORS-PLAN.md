# QuantEdge Phase Three: pre-registered plan for sector-ETF momentum

**Status: APPROVED by the user on 28 Oct 2026, as written (only this status line changed after approval).**

_Original status at drafting:_ **DRAFT AWAITING APPROVAL. Nothing has been run.** No sector-ETF prices have been fetched or
inspected, and no code has been changed for this study. Once approved, the plan is frozen: any
later deviation is reported in the results document, not edited in here.

Written 27 Oct 2026. Follows [RESEARCH-PHASE2-REPORT.md](RESEARCH-PHASE2-REPORT.md), which found that
single-stock momentum results on today's large caps are explained by survivorship (NVDA/LLY/AVGO;
RR.L/GLEN.L).

## 1. Question
Does cross-sectional momentum **across US sector ETFs** beat, out of sample and net of costs:
1. equal-weight buy-and-hold of the **same sector ETFs**,
2. **SPY** (total return),
3. **cash** at the US 3-month T-bill rate (FRED DTB3)?

"No" is an acceptable answer.

**Why sectors.**
- **Less survivorship.** The nine original Select Sector SPDR funds all launched in December 1998 and none
  has closed, so a fixed list of funds doesn't drop failures the way a list of today's large companies does.
- **Longer history.** They give ~26 years of data, including the 2000–02 and 2008 bear markets that the
  Phase Two window lacked.

**Why it might work.** Industry momentum is documented and partly explains stock-level momentum
(Moskowitz & Grinblatt 1999). Economic shocks diffuse slowly across industries.

**Why it might not.**
- Nine assets is a very thin cross-section.
- The effect has been reported to weaken after publication.
- Sector funds are themselves rebuilt at GICS reclassifications (see §7).

## 2. Universe (fixed now)
**The nine original Select Sector SPDRs:** XLB (materials), XLE (energy), XLF (financials), XLI (industrials),
XLK (technology), XLP (consumer staples), XLU (utilities), XLV (health care), XLY (consumer discretionary).

- **Excluded:** XLRE (launched 2015) and XLC (launched 2018), so that membership is constant over the whole
  history and never chosen with hindsight.
- **Market:** US. **Benchmarks:** SPY (total return, adjusted close) and the equal-weight buy-and-hold of
  these nine.

## 3. Periods (fixed now)

| Period | Dates |
|---|---|
| Data requested | earliest Yahoo daily history for the nine ETFs and SPY (expected from late Dec 1998) → latest available |
| Indicator warm-up | first ~257 sessions (12-month look-back + 5) |
| **Development** | end of warm-up → **31 Jan 2025** |
| Train / validation | development split chronologically: **first 75% train, last 25% validation** (expected ≈ 2000–2019 / 2019 – Jan 2025) |
| Walk-forward | development only; 756-session in-sample, 126-session out-of-sample, rolling (≈ 40 folds) |
| **Holdout (locked)** | **3 Feb 2025 → latest available.** The same holdout as Phase Two. Used **once**, and only if every criterion in §6 passes |

**Holdout status, verified on 27 Oct 2026 before writing this plan:**
- no daily-strategy experiment has an end date on or after 3 Feb 2025
- no experiment has ever used a test period
- no sector-ETF data is stored

Known prior exposure: the audit printed SPY's total return and maximum drawdown for 4 Dec 2024 → 23 Oct 2026
(benchmark level only). General public knowledge of how sectors performed (e.g. technology's strength since
2010) can't be removed and is acknowledged as a limitation.

## 4. Strategy and variants (fixed now)
**Rule:**
- At each rebalance close, rank the nine ETFs by total return from *t−L* to *t−21* (skip the most recent month).
- Hold the **top 3** equal-weight (1/3 each) from the next session's open.
- Rebalance every **21** sessions.
- No absolute-momentum filter and no regime filter.
- Existing strategy `xs_momentum` with `top_n=3`, `skip=21`, `rebalance_days=21`, `abs_filter=False`,
  `regime_filter=False`.

**Variants (the only ones tested): L = 126 (6-1) and L = 252 (12-1).**

**Settings, all project defaults:**
- **Costs:** 5 bps full spread (2.5 per side) + 5 bps slippage per side, no commission, no stamp duty (US).
  Fund expense ratios are already inside adjusted prices. Each run is also recorded at 0×/2×/3× trading frictions.
- **Cash:** uninvested cash earns DTB3; Sharpe, PSR, Deflated Sharpe and Sharpe intervals are measured above cash.
- **Execution and sizing:** next-open execution; rebalance drift tolerance 25%; seeded tie-break for buy order.
- **Statistics:** 95% block-bootstrap intervals (2,000 resamples, 20-day blocks). Deflated Sharpe trials =
  distinct parameter sets recorded for `xs_momentum` on these nine symbols, so the count starts fresh for this
  universe (§7 discusses the wider count).

## 5. Runs (all recorded, failures included)
1. **L = 126:** train, then validation.
2. **L = 252:** train, then validation.
3. **Walk-forward** choosing between L = 126 and 252 on in-sample Sharpe above cash.

That's **5 experiments**, source `phase3`.

**Reported but not used to pass:**
- the tie-break seed spread, if slots are ever contested
- the cost curve
- the regime breakdown
- the per-sector P&L contribution

## 6. Pre-registered criteria (development data only)
- **C1:** walk-forward OOS annualised excess over the **equal-weight sector basket**: point estimate > 0 **and**
  95% CI lower bound > 0.
- **C2:** walk-forward OOS net return > **SPY**, **and** Sharpe above cash > SPY's Sharpe above cash.
- **C3:** walk-forward OOS beats **cash**: Sharpe above cash > 0 and PSR ≥ 0.95.
- **C4:** walk-forward OOS **Deflated Sharpe ≥ 0.95**.
- **C5:** on **validation**, the variant chosen most often by the walk-forward beats the equal-weight basket net of
  costs, **and still does at 2× costs**.
- **C6:** at least 30 round trips in the walk-forward OOS.
- **C7 (stability):** the walk-forward OOS period divided into three equal consecutive thirds. The point estimate of
  excess over the equal-weight basket is positive in **at least two of the three**.
- **C8 (concentration, only if C1–C7 pass):** re-run the walk-forward without the single ETF that contributed most
  OOS P&L (baseline recomputed on the remaining eight). Excess over the reduced basket must stay > 0.

**Verdicts**
- **Worth further independent testing:** C1–C8 all pass. Then, and only then, run the **holdout once** (both
  variants at the walk-forward's modal choice) and report it as is, whatever it shows.
- **Rejected:** the walk-forward OOS point estimate vs the equal-weight basket is ≤ 0, **or** C5's 2× cost test fails.
- **Inconclusive:** anything else.

If the verdict isn't "worth further testing", the holdout stays locked.

## 7. Known biases and limitations, stated in advance
- **GICS reclassifications change what the funds hold:**
  - 2016: real estate moved out of financials (XLF).
  - 2018: Google, Meta, Netflix and others moved from XLK/XLY into the new XLC.

  These are structural breaks in fund composition, not look-ahead. Results around 2016 and 2018 are flagged in
  the report.
- **Thin cross-section:** nine assets, three held. Results can hinge on one or two sectors, which is why C8 exists.
- **Publication decay:** sector momentum is well known. Positive results before ~2005 and weak results after
  would fit decay rather than an enduring edge.
- **Multiple testing beyond the DSR:** the DSR counts trials on this universe only. Including the Phase Two
  momentum work, momentum has now been tested in several forms on related data. The report will state the
  total number of momentum configurations tried across the project (≈ 15 before this study) as context.
- **Data:** Yahoo, unofficial, adjusted history can be revised. Early-history data quality (1999–2001) will be
  checked with the existing validators before any experiment, and the report will list any gaps.
- **Equal-weight baseline drift:** the basket is bought once and not rebalanced (the project's standard
  definition), so it drifts towards whichever sectors grew. A monthly-rebalanced equal-weight basket would
  be a different benchmark. It's not added, to avoid choosing benchmarks after seeing results.

## 8. Engineering needed after approval (no results involved)
These change data and configuration, not strategy logic. Each gets a test.
1. **Register the universe:** add the nine ETFs to the catalogue and a `us_sectors` universe. No schema change.
2. **Back-fill long history:** the refresh only fetches *forward* from the last stored bar, so SPY's stored history
   (from Oct 2016) can't be extended today. Add a "full history" refresh option, then fetch the nine ETFs and
   SPY from 1998 (free Yahoo data). This adds price rows; nothing is deleted.
3. **Cash rates before 2000:** the FRED request starts in 2000. Widen it to 1990 so 1999–2000 cash isn't treated
   as 0%.
4. **Data check:** run the daily validators on all ten series and record the quality report before any experiment.

**Order after approval:**
1. Engineering steps above, with tests.
2. Data fetch and validation.
3. Freeze the code in a commit.
4. The 5 experiments.
5. Criteria evaluation.
6. Holdout only if earned.
7. Report.

## 9. What approval covers
- Fetching free Yahoo and FRED data (no paid data).
- The code changes in §8.
- Running exactly the 5 experiments in §5, plus C8 if reached.
- The holdout run only if C1–C8 pass.

Nothing is pushed or merged.
