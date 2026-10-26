# QuantEdge Phase Two: report

**Question:** does any new, precisely defined hypothesis beat, out of sample and net of costs, (1) equal-weight
buy-and-hold of the same stocks, (2) the market ETF and (3) cash?

**Answer: no candidate showed a defensible edge.**
- Two hypothesis–market pairs were **rejected** outright.
- Three were **inconclusive**: positive point estimates against the equal-weight universe, but intervals that
  include zero and Deflated Sharpe ratios below 0.95.
- In robustness diagnostics, the inconclusive results' advantage disappears once the 1–3 stocks we know with
  hindsight became winners are removed.
- Nothing qualified for the locked holdout, so the holdout remains untouched.

The plan was written and committed before any run: [RESEARCH-PHASE2-PLAN.md](RESEARCH-PHASE2-PLAN.md)
(commit `7404f1e`). The supporting code was committed before any run too (`e7bc8da`).

## 1. Hypotheses (as pre-registered)

| | Rule | Variants | Rationale | Falsified if |
|---|---|---|---|---|
| **H1** Cross-sectional momentum | Rank 20 stocks by return *t−L → t−21*; hold top 5 equal-weight; rebalance every 21 sessions; no absolute or regime filter | L = 126, 252 | Under-reaction, slow diffusion (Jegadeesh & Titman 1993) | OOS excess vs equal-weight universe ≤ 0, or fails 2× costs |
| **H2** Short-term reversal | Rank by return over the last *L* sessions; hold bottom 5; rebalance every 5 sessions; exit when no longer bottom 5; no stops | L = 5, 10 | Over-reaction / liquidity provision (Lehmann 1990) | Doesn't beat the equal-weight universe OOS net of costs, or fails 2× costs |
| **H3** Regime-conditioned momentum | H1, but hold only when the ETF is above its 200-session average, else cash | L = 126, 252 | Momentum crashes cluster in bear-market rebounds (Daniel & Moskowitz 2016) | Doesn't improve Sharpe-above-cash or max drawdown vs H1 OOS, or fails the universe test |

## 2. Data, periods, settings

- **Data:** Yahoo daily bars, adjusted for splits and dividends, 25 Oct 2016 → 23 Oct 2026. `us_large` (20 US) and `uk_large` (20 UK). Every symbol has a full history.
- **Development data:** before 3 Feb 2025.
  - Train / validation split 75/25 after warm-up (momentum: ~Nov 2017 → Apr 2023, then Apr 2023 → Jan 2025).
  - Walk-forward: 756-session in-sample, 126-session out-of-sample, rolling, development data only.
- **Holdout (locked):** 3 Feb 2025 → 23 Oct 2026. **Not used**, because no candidate passed.
- **Costs:**
  - 5 bps spread (2.5 per side) + 5 bps slippage per side
  - 0.5% UK stamp duty on buys
  - every run repeated at 0×/2×/3× trading frictions
- **Cash:** FRED DTB3 (US) / SONIA (UK). Sharpe, PSR, DSR and intervals are all above cash.
- **Other settings:**
  - five equal slots
  - next-open execution
  - seeded tie-break, now also used for the rebalance engine's buy order
  - block-bootstrap 95% intervals
  - Deflated Sharpe with trials = distinct parameter sets on that strategy, market and universe (earlier momentum runs count)

## 3. Results

### Walk-forward out-of-sample (the primary evidence)

| | Exp | Net | EW universe | ETF | Cash | Excess vs EW (95% CI, /yr) | Sharpe > cash [CI] | ETF Sharpe | PSR | DSR | Max DD (ETF) | Turnover/yr | Trades | Costs (% cap.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 US | #60 | +203.4% | +172.3% | +87.4% | +12.1% | [−11.0%, +20.3%] | 1.10 [0.17, 1.97] | 0.87 | 0.99 | 0.77 | −20.6% (−21.7%) | 5.6× | 87 | 2.7% |
| H1 UK | #75 | +120.6% | +75.5% | +40.7% | +10.8% | [−3.7%, +18.1%] | 0.93 [0.14, 1.65] | 0.51 | 0.97 | 0.71 | −15.1% (−11.9%) | 6.3× | 85 | 11.3% |
| H2 US | #65 | +154.3% | +276.1% | +101.2% | +12.9% | [−18.2%, +6.3%] | 0.71 [−0.04, 1.53] | 0.66 | 0.94 | 0.76 | −38.3% (−33.7%) | 42.6× | 781 | 24.6% |
| H2 UK | #80 | **−67.9%** | +37.6% | +12.8% | +11.4% | [−38.3%, −17.6%] | −0.80 [−1.51, −0.11] | 0.10 | 0.04 | 0.00 | −69.1% (−34.8%) | 110.3× | 717 | 89.0% |
| H3 US | #70 | +182.8% | +172.3% | +87.4% | +12.1% | [−14.2%, +19.4%] | 1.12 [0.16, 2.01] | 0.87 | 0.99 | 0.73 | −24.7% (−21.7%) | 5.2× | 76 | 2.4% |
| H3 UK | #85 | +43.9% | +75.5% | +40.7% | +10.8% | [−14.7%, +7.8%] | 0.45 [−0.23, 1.14] | 0.51 | 0.81 | 0.27 | −16.1% (−11.9%) | 8.2× | 90 | 11.6% |

Walk-forward windows: momentum OOS ≈ Nov 2020 → Jan 2025 (8 folds); reversal ≈ Nov 2019 → Jan 2025 (10 folds).
Variants chosen per fold:
- H1/H3 US: 126, 126, then 252 ×6
- H1 UK: 126, then 252 ×7
- H2 US: mixed 5/10
- H2 UK: mostly 10

### Train and validation (net of costs)

| Exp | Hyp | Mkt | L | Period | Window | Net | EW | ETF | Cash | Sharpe>cash [CI] | DSR | Max DD | Turnover | Trades | Net at 0× / 2× / 3× costs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| #56 | H1 | US | 126 | train | 2017-11-01→2023-04-06 | +318.5% | +220.7% | +74.6% | +8.2% | 0.98 [0.16, 1.81] | 0.83 | −39.1% | 7.4× | 103 | +325.9 / +301.2 / +289.3% |
| #57 | H1 | US | 126 | validation | 2023-04-10→2025-01-31 | +168.4% | +90.4% | +50.5% | +9.5% | 1.87 [0.67, 3.12] | 0.89 | −17.1% | 8.8× | 39 | +171.1 / +165.6 / +162.3% |
| #58 | H1 | US | 252 | train | 2017-11-01→2023-04-06 | +356.3% | +220.7% | +74.6% | +8.2% | 1.01 [0.22, 1.81] | 0.83 | −36.5% | 5.0× | 67 | +359.0 / +346.9 / +336.7% |
| #59 | H1 | US | 252 | validation | 2023-04-10→2025-01-31 | +162.3% | +90.4% | +50.5% | +9.5% | 1.85 [0.72, 3.10] | 0.86 | −16.7% | 4.9× | 24 | +164.2 / +163.1 / +159.1% |
| #71 | H1 | UK | 126 | train | 2017-10-31→2023-04-11 | +0.2% | +21.3% | +3.0% | +4.1% | 0.08 [−0.69, 0.82] | 0.19 | −38.7% | 8.2× | 113 | +3.6 / −3.1 / −5.5% |
| #72 | H1 | UK | 126 | validation | 2023-04-12→2025-01-31 | +37.3% | +25.7% | +10.2% | +9.5% | 0.97 [−0.42, 2.17] | 0.60 | −9.9% | 6.9× | 32 | +38.6 / +36.1 / +34.9% |
| #73 | H1 | UK | 252 | train | 2017-10-31→2023-04-11 | +10.7% | +21.3% | +3.0% | +4.1% | 0.16 [−0.50, 0.81] | 0.21 | −26.4% | 6.0× | 83 | +13.8 / +8.7 / +7.8% |
| #74 | H1 | UK | 252 | validation | 2023-04-12→2025-01-31 | +50.3% | +25.7% | +10.2% | +9.5% | 1.31 [−0.05, 2.70] | 0.71 | −13.9% | 4.9× | 23 | +51.3 / +49.4 / +48.5% |
| #61 | H2 | US | 5 | train | 2016-11-15→2023-01-10 | +274.0% | +289.6% | +99.9% | +7.8% | 0.86 [0.10, 1.67] | 0.98 | −34.7% | 74.8× | 1150 | +425.8 / +164.8 / +87.6% |
| #62 | H2 | US | 5 | validation | 2023-01-11→2025-01-31 | +74.4% | +130.1% | +56.5% | +10.7% | 1.17 [0.11, 2.44] | 0.95 | −14.9% | 71.0× | 364 | +94.4 / +56.5 / +40.4% |
| #63 | H2 | US | 10 | train | 2016-11-15→2023-01-10 | +192.9% | +289.6% | +99.9% | +7.8% | 0.72 [0.01, 1.54] | 0.89 | −37.9% | 53.4× | 819 | +274.7 / +129.0 / +78.9% |
| #64 | H2 | US | 10 | validation | 2023-01-11→2025-01-31 | +91.8% | +130.1% | +56.5% | +10.7% | 1.46 [0.55, 2.60] | 0.94 | −13.4% | 53.3× | 269 | +107.9 / +77.1 / +63.5% |
| #76 | H2 | UK | 5 | train | 2016-11-15→2023-01-12 | −55.5% | +41.8% | +13.0% | +3.4% | −0.45 [−1.18, 0.19] | 0.13 | −67.3% | 76.2× | 1167 | −36.8 / −68.7 / −78.0% |
| #77 | H2 | UK | 5 | validation | 2023-01-13→2025-01-31 | −23.9% | +33.4% | +10.3% | +10.5% | −1.04 [−2.08, −0.01] | 0.06 | −28.1% | 76.1× | 390 | −14.5 / −32.4 / −39.8% |
| #78 | H2 | UK | 10 | train | 2016-11-15→2023-01-12 | −42.1% | +41.8% | +13.0% | +3.4% | −0.26 [−0.92, 0.32] | 0.12 | −54.4% | 52.9× | 811 | −26.1 / −54.6 / −64.4% |
| #79 | H2 | UK | 10 | validation | 2023-01-13→2025-01-31 | −8.9% | +33.4% | +10.3% | +10.5% | −0.55 [−1.78, 0.46] | 0.09 | −18.9% | 54.3× | 278 | −1.0 / −16.3 / −23.0% |
| #66 | H3 | US | 126 | train | 2017-11-01→2023-04-06 | +168.7% | +220.7% | +74.6% | +8.2% | 0.80 [−0.05, 1.65] | 0.63 | −30.4% | 6.5× | 96 | +176.2 / +162.4 / +153.7% |
| #67 | H3 | US | 126 | validation | 2023-04-10→2025-01-31 | +168.4% | +90.4% | +50.5% | +9.5% | 1.87 [0.67, 3.12] | 0.84 | −17.1% | 8.8× | 39 | +171.1 / +165.6 / +162.3% |
| #68 | H3 | US | 252 | train | 2017-11-01→2023-04-06 | +168.7% | +220.7% | +74.6% | +8.2% | 0.80 [−0.01, 1.63] | 0.61 | −33.2% | 5.1× | 74 | +174.4 / +163.2 / +157.4% |
| #69 | H3 | US | 252 | validation | 2023-04-10→2025-01-31 | +162.3% | +90.4% | +50.5% | +9.5% | 1.85 [0.72, 3.10] | 0.82 | −16.7% | 4.9× | 24 | +164.2 / +163.1 / +159.1% |
| #81 | H3 | UK | 126 | train | 2017-10-31→2023-04-11 | +0.5% | +21.3% | +3.0% | +4.1% | 0.05 [−0.65, 0.67] | 0.10 | −18.5% | 7.0× | 96 | +3.4 / −2.3 / −4.6% |
| #82 | H3 | UK | 126 | validation | 2023-04-12→2025-01-31 | +4.3% | +25.7% | +10.2% | +9.5% | −0.18 [−1.40, 0.70] | 0.05 | −10.1% | 5.4× | 25 | +5.1 / +3.5 / +2.8% |
| #83 | H3 | UK | 252 | train | 2017-10-31→2023-04-11 | −0.5% | +21.3% | +3.0% | +4.1% | 0.03 [−0.63, 0.63] | 0.08 | −21.0% | 5.8× | 79 | +2.1 / −2.7 / −4.7% |
| #84 | H3 | UK | 252 | validation | 2023-04-12→2025-01-31 | +5.2% | +25.7% | +10.2% | +9.5% | −0.13 [−1.33, 0.86] | 0.05 | −13.9% | 4.9× | 22 | +5.9 / +4.6 / +3.9% |

H3 US validation equals H1 US validation exactly (#67 = #57, #69 = #59): the S&P was above its 200-day
average throughout that window, so the regime filter never acted.

## 4. Pre-registered criteria

| | C1 OOS vs EW (point & CI) | C2 beats ETF | C3 beats cash | C4 DSR ≥ 0.95 | C5 validation vs EW at 1× and 2× | C6 ≥ 30 trades | **Verdict** |
|---|---|---|---|---|---|---|---|
| H1 US | ✗ (point +, CI includes 0) | ✓ | ✓ | ✗ (0.77) | ✓ | ✓ | **Inconclusive** |
| H1 UK | ✗ (point +, CI includes 0) | ✓ | ✓ | ✗ (0.71) | ✓ | ✓ | **Inconclusive** |
| H2 US | ✗ (point −) | ✓ | ✗ (PSR 0.94) | ✗ | ✗ | ✓ | **Rejected** |
| H2 UK | ✗ (point −, CI < 0) | ✗ | ✗ | ✗ | ✗ | ✓ | **Rejected** |
| H3 US | ✗ (point +, CI includes 0) | ✓ | ✓ | ✗ (0.73) | ✓ | ✓ | **Inconclusive** |
| H3 UK | ✗ (point −) | ✗ | ✗ | ✗ | ✗ | ✓ | **Rejected** |

No candidate reached C7 or the holdout.

**H3's specific claim** (better risk than H1):
- **US:** Sharpe above cash 1.12 vs 1.10, i.e. no meaningful change, and **worse** drawdown (−24.7% vs −20.6%).
- **UK:** worse on both.
- The regime filter added nothing measurable.

## 5. Robustness checks (not pre-registered; can only downgrade)

The three inconclusive results depend on a few names:

| Walk-forward | Top contributors to P&L |
|---|---|
| H1 US #60 | NVDA 31%, LLY 19%, AVGO 19% |
| H3 US #70 | NVDA 31%, AVGO 20%, LLY 18% |
| H1 UK #75 | RR.L 32%, GLEN.L 30%, BA.L 13% |

Re-running each walk-forward without those names (the equal-weight baseline is recomputed on the reduced list):

| Diagnostic | Exp | Net | EW (reduced) | Excess vs EW 95% CI /yr |
|---|---|---|---|---|
| H1 US without NVDA | #86 | +149.7% | +145.5% | [−10.7%, +15.0%] |
| H1 US without NVDA, LLY, AVGO | #87 | +116.8% | **+119.8%** | [−12.8%, +13.8%] |
| H3 US without NVDA | #88 | +143.0% | **+145.5%** | [−12.9%, +15.4%] |
| H3 US without NVDA, AVGO, LLY | #89 | +108.7% | **+119.8%** | [−14.8%, +14.4%] |
| H1 UK without RR.L | #90 | +65.6% | +57.4% | [−7.8%, +13.0%] |
| H1 UK without RR.L, GLEN.L, BA.L | #91 | **+2.2%** | **+46.1%** | [−18.9%, +1.9%] |

Removing one to three names, all chosen with hindsight, erases or reverses the advantage over buying the same
stocks. That's what survivorship-driven momentum looks like: the universe is today's large caps, so it contains
the stocks that went on to win, and momentum loads on them.

The diagnostics' Deflated Sharpe values (0.89–0.98 in the records) aren't comparable to the main runs: their
trial count restarts because the symbol set differs. They were **not** used for any verdict.

## 6. Bias and assumption checks

| Issue | Finding |
|---|---|
| Look-ahead in signals / ranking / regime | Rankings use closes ≤ *t*; the regime uses the ETF's SMA200 at *t*; trades at *t+1* open. Covered by tests (`test_phase2.py`, `test_no_lookahead.py`) |
| **Look-ahead in universe selection** | **Present and unfixable with free data.** Universes are today's large caps; demonstrated in §5 |
| Overlapping positions | None. One position per name; holding periods are rebalance-to-rebalance and don't overlap |
| Universe composition | 20 names, top/bottom 5 = 25%; a thin cross-section that's sensitive to single names (§5) |
| Corporate actions | Adjusted closes, so rankings and returns are total return. Dividend withholding tax isn't modelled |
| Missing data | None: every symbol has a full history; validation checks pass |
| Execution | Next-open fills, 7.5 bps per side + UK stamp duty. Momentum is cheap (US costs 2.7% of capital over 4 years); reversal isn't (US 24.6%; UK 89%, mostly stamp duty, as anticipated) |
| Cash | Uninvested cash earns the short rate; H3's cash periods aren't penalised |
| Data contamination | (a) SPY's overall return for most of the holdout was printed during the audit; (b) 12-1 momentum (abs filter on) was inspected on this validation window before, so **H1/H3 validation wasn't fresh**, and C5 passes for them should be discounted; (c) intraday runs touched Aug–Sep 2026 |

## 7. Multiple testing
- **Phase Two:** 12 variant configurations × train/validation (24 runs), 6 walk-forwards, and 6 diagnostics.
  That's 36 experiments (#56–#91), all recorded, none failed.
- **Decisions:** 6 hypothesis–market tests. A Bonferroni-style correction across them would require ~99.2%
  intervals. Moot, since none passed at 95%.
- **Deflated Sharpe trial counts:** they include every earlier momentum parameter set recorded on the same
  universe (6–11 trials). Earlier work that wasn't recorded (the in-memory audit diagnostics) isn't counted, so
  the true trial count is somewhat higher.

## 8. Deviations from the plan
1. Added the §5 leave-top-contributors diagnostics. They weren't pre-registered. Step 10 of the brief requires
   bias checks; they were used only to qualify, never to promote.
2. No other deviations: parameters, periods, costs and criteria were applied as written.

## 9. Verdicts

| Candidate | Verdict | Why |
|---|---|---|
| H1 momentum, US | **Inconclusive**, and not worth further testing on this universe | Beats ETF and cash; point estimate beats EW, but the interval includes zero and DSR is 0.77. The edge vanishes without NVDA/LLY/AVGO |
| H1 momentum, UK | **Inconclusive**, and not worth further testing on this universe | Same pattern; without RR.L/GLEN.L/BA.L it's +2% vs +46% |
| H2 reversal, US | **Rejected** | Lags EW out of sample (+154% vs +276%); cost-fragile (validation 2× costs ≈ EW) |
| H2 reversal, UK | **Rejected** | Loses money (−68%), significantly worse than EW; stamp duty makes weekly turnover impossible |
| H3 regime momentum, US | **Inconclusive**; the regime claim isn't supported | No better than H1 on risk; same survivorship dependence |
| H3 regime momentum, UK | **Rejected** | Lags EW and ETF |

**No candidate is worth further independent testing.** The locked holdout (3 Feb 2025 → 23 Oct 2026)
**remains untouched** and available for a future pre-registered hypothesis.

## 10. Recommended next research step
The binding constraint is the **universe**, not the strategy. With today's survivors, any cross-sectional rule
that leans towards winners will look good, and the §5 test shows that's what happened. Options, in order:
1. **Survivorship-light universe, free:** cross-sectional momentum across **ETFs that existed throughout**
   (US sector SPDRs XLK, XLF, XLE, XLV, XLI, XLP, XLY, XLU, XLB and one or two later sector funds; or
   country ETFs). These are funds, not companies, so they don't drop out by failing. Pre-register it, use the
   same machinery and baselines (EW of the ETFs, SPY, cash), and keep the same holdout.
2. **Point-in-time constituents, paid:** historical index membership including delisted companies (EODHD
   delisted data, Norgate). That's the only way to test single-stock cross-sectional hypotheses honestly. It
   needs a purchase decision.
3. **Don't tune** H1–H3 further on these universes. Any variant that "works" would be another draw from the same
   biased sample.

## 11. Files, tests, experiments
- **Code** (`e7bc8da`):
  - `quantedge/strategies/reversal.py` (new)
  - `backtest/runner.py` (calendar holdout via `holdout_start`)
  - `backtest/splits.py` (`Split.holdout_start`)
  - `backtest/walkforward.py` (holdout support)
  - `backtest/engine.py` (cash benchmark metrics; seeded rebalance buy order)
  - `backtest/portfolio.py` (`tie_break_priority`)
  - `reports.py` (holdout runs grouped separately)
  - `strategies/__init__.py`
  - `tests/quantedge/test_phase2.py` (7 tests)
- **Docs:** `RESEARCH-PHASE2-PLAN.md` (`7404f1e`) and this report.
- **Tests:** full suite **492 passed** after the code changes, before any experiment.
- **Experiments:** #56–#85 (pre-registered, source `phase2`) and #86–#91 (diagnostics, source `phase2-diagnostic`).
- **Database:** rows added only; no schema change; nothing deleted.
- **Unrelated:** `screener/dashboard.py` and `screener/universe.py` have uncommitted changes, made by someone else
  at 23:35–23:38 on 26 Oct. They weren't touched or committed by this work.
