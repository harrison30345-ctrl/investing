# QuantEdge Phase Four: engineering hardening and the 10-month SPY trend filter

**Pre-registered verdict: REJECTED.** On 22 years of walk-forward out-of-sample data (Dec 2002 → Dec 2024):
- **Drawdown:** the 10-month trend filter **halved SPY's maximum drawdown** (−25.2% vs −53.7%; C1 passes).
- **Risk-adjusted return:** it did **not** improve. Sharpe above cash was 0.572 vs SPY's 0.568, a
  difference of +0.005 with a 95% interval of −0.32 to +0.34.
- **Costs:** the tiny Sharpe edge reverses at 3× costs (0.514 vs 0.568). That is a pre-registered
  falsification condition.
- **Return given up:** 2.8 percentage points of CAGR a year (8.15% vs 10.93%). That's "materially lower" by
  the plan's own definition.
- **Where the benefit came from:** essentially all of it is from 2008. After 2010, both remaining thirds favour
  buy-and-hold.

The holdout stays locked. This is research, not advice.

**Commits** (branch `research-phase2`, local only, nothing pushed):

| Commit | What |
|---|---|
| `a1ab29c` | Part A engineering (no strategy runs) |
| `f4c26ac` | Pre-registration [RESEARCH-PHASE4-TREND-PLAN.md](RESEARCH-PHASE4-TREND-PLAN.md), committed before any run |
| `19b5d07` | Part B engineering (plan §4), frozen before the first run |
| `ebd4022` | Walk-forward warm-up fix (deviation 1 below), made before any result was viewed |

**Experiments:** #97 (train), #98 (validation), #100 (walk-forward, the pre-registered run); #99 (walk-forward
with the wrong warm-up, kept on record).

## Part A: engineering summary (`a1ab29c`)
No schema change. No stored experiment was modified: the stored #92 and #95 configurations were re-run in
memory and reproduce every stored metric exactly.

| # | Change | Where | Tests |
|---|---|---|---|
| 1 | Universe types (current stocks / ETF / point-in-time) with matching bias notes. The ETF note says bias is lower, not zero. Reports add the type note to older runs and keep their stored notes | `universe.py`, `backtest/runner.py`, `reports.py` | `test_phase4_engineering.py` |
| 2 | Walk-forward cost curve: stitched out-of-sample re-run at 0×/2×/3× frictions, fold choices fixed at baseline costs; labelled `result_basis` / `cost_curve_basis`. A test confirms costs never change signal timing | `backtest/walkforward.py` | same |
| 3 | Calendar closure overrides with sources (2025-01-09). The library calendar is unchanged (`overrides=False`). A bar on an override day is a validation error and is dropped before the engine, so no price or fill can exist on a closed day; genuine gaps are still flagged | `markets.py`, `data/validation.py`, `backtest/runner.py` | same |
| 4 | Project-wide trial accounting, computed from records and shown next to the per-universe DSR count, which is unchanged. Counting rules are in the module docstring | `trials.py`, `reports.py`, `app/views.py`, CLI and Reports page | same |
| 5 | Secondary monthly-rebalanced equal-weight baseline (`universe_monthly_*`), reported only; verdicts unchanged. New runs only. For older experiments, any figure is exploratory | `backtest/engine.py`, `reports.py`, `app/views.py` | same |

**Trial count, corrected.** Under the documented counting rules (a walk-forward counts every candidate it chose
between), `xs_momentum` had **29** distinct configurations by #96. The Phase Three report said 23 because it
didn't expand the audit walk-forward's four look-back candidates. The Phase Three report is left as written.
Project-wide now: **43 configurations across 7 strategy families** (94 experiments; timing and demo runs excluded).

## Part B engineering (`19b5d07`, then `ebd4022`)
The engine could not run the rule faithfully as it stood: it rebalanced every *N* sessions from the window
start, not at month-ends. Changes, all specified in the plan's §4 before any run:
- **Month-end hook:** `RebalanceStrategy.rebalance_due`. The default is unchanged, and stored momentum
  results reproduce exactly.
- **The strategy:** `trend_sma10m`, with the length fixed at 10.
- **Fixed-rule walk-forward.**
- **Paired block-bootstrap interval** for the Sharpe difference.
- **Strategy trade labels.**

One old test asserted that a single-value walk-forward is rejected. It was updated to test the remaining guards.

## Experiments and commands
Code at `19b5d07` (#97–99) and `ebd4022` (#100). Data: Yahoo SPY and sector ETFs as validated in Phase Three;
FRED DTB3.

```python
from quantedge.backtest.runner import run_experiment
from quantedge.backtest.walkforward import run_walk_forward
H = "2025-02-03"
run_experiment("trend_sma10m", {"sma_months": 10}, symbols=["SPY"], period="train", source="phase4",
               min_warmup=257, holdout_start=H)                                   # #97
run_experiment("trend_sma10m", {"sma_months": 10}, symbols=["SPY"], period="validation", source="phase4",
               min_warmup=257, holdout_start=H)                                   # #98
run_walk_forward("trend_sma10m", "sma_months", [10], symbols=["SPY"], train_sessions=756, test_sessions=126,
                 source="phase4", holdout_start=H, min_warmup=257)                # #100 (pre-registered window)
```
Evaluation reads the stored records only (no strategy runs). The script is not in the repo; every number
below comes from the stored metrics, equity points and trades of #97–100.

## Results

### Walk-forward stitched out-of-sample (#100)
12 Dec 2002 → 20 Dec 2024, 44 folds of 126 sessions, 22.0 years.

| | Trend filter | SPY buy-and-hold | Sector basket (EW B&H, chained folds) | Cash |
|---|---|---|---|---|
| Total return | +461% | +880% | +796% | +41% |
| CAGR | 8.15% | 10.93% | 10.48% | |
| Volatility | 12.3% | | 17.8% | |
| Sharpe above cash [95% CI] | **0.572** [0.21, 0.99] | **0.568** | 0.562 | |
| Max drawdown | **−25.2%** | **−53.7%** | −51.9% | |
| Time invested | 79% | 100% | 100% | |
| Turnover / yr | 1.82× (inflated by fold resets; train run: 1.01×) | | | |
| Costs | 8.0% of starting capital | | | |
| PSR / Deflated Sharpe | 0.996 / 0.984 (2 trials) | | | |

- **Paired Sharpe difference vs SPY:** +0.005, 95% CI [−0.315, +0.338].
- **Annualised excess return vs SPY:** 95% CI [−8.4%, +1.5%].
- **Monthly-rebalanced sector basket (exploratory):** +803%, Sharpe 0.560, max drawdown −52.3%.

### Train and validation (continuous; no fold artefact)
| # | Period | Strategy CAGR | SPY CAGR | Sharpe (strategy / SPY) | Sharpe diff [95% CI] | Max DD (strategy / SPY) | Invested | Rule exits |
|---|---|---|---|---|---|---|---|---|
| 97 | 8 Dec 1999 → 15 Oct 2018 | 8.02% | 5.57% | 0.566 / 0.295 | +0.27 [−0.04, +0.63] | −20.2% / −55.2% | 74% | 9 |
| 98 | 16 Oct 2018 → 31 Jan 2025 | 7.17% | 14.76% | 0.406 / 0.669 | −0.26 [−0.87, +0.35] | −24.5% / −33.7% | 77% | 8 |

Sector basket (EW B&H): train CAGR 6.71%, Sharpe 0.361, max drawdown −52.4%; validation 12.29%, 0.575, −35.6%.

### Pre-registered criteria
| | Criterion | Result | |
|---|---|---|---|
| C1 | WF max drawdown ≥ 10 pts shallower than SPY's | −25.2% vs −53.7% (28.5 pts) | pass |
| C2 | WF Sharpe diff > 0 **and** CI lower bound > 0 | +0.005, CI [−0.315, +0.338] | **FAIL** |
| C3 | C2 point estimate > 0 at 3× costs | 0.514 − 0.568 = −0.054 | **FAIL** |
| C4 | Sharpe diff > 0 in ≥ 2 of 3 thirds | +0.55, −0.38, −0.23 (1 of 3) | **FAIL** |
| C5 | Validation: Sharpe > SPY's **and** shallower drawdown | 0.406 < 0.669 (drawdown −24.5% vs −33.7% passes) | **FAIL** |
| C6 | ≥ 10 rule exits in OOS | 15 | pass |
| — | CAGR shortfall (label only) | 2.78 pts/yr > 1.5 | "materially lower CAGR" |

**Verdict: rejected**, because C3 failing is one of the plan's falsification conditions. The other two
falsification conditions were not met: the Sharpe difference is +0.005, not ≤ 0, and the drawdown is shallower.

### Where the result comes from
**C4 thirds:**

| Third | Sharpe (strategy / SPY) | Max DD (strategy / SPY) | CAGR (strategy / SPY) |
|---|---|---|---|
| Dec 2002 → Apr 2010 | 0.81 / 0.26 | −9.9% / −53.7% | 11.0% / 5.7% |
| Apr 2010 → Aug 2017 | 0.48 / 0.85 | −21.2% / −18.8% | 5.6% / 12.5% |
| Aug 2017 → Dec 2024 | 0.47 / 0.71 | −25.2% / −33.7% | 7.9% / 14.9% |

**Calendar years:**
- **Big win:** 2008, +37 pts vs SPY.
- **Biggest losses (whipsaws and late re-entry):** 2019 (−25 pts), 2010 (−18), 2023 (−16), 2015 (−9), 2016 (−8).
- **Overall:** apart from 2008, the filter never beat SPY by more than 1 point in a calendar year (best: 2013, +0.7).

**Folds:** the strategy out-returned SPY in 9 of 44 folds. The decisive one was Jun–Dec 2008 (+0.5% vs −33.6%).

**Regimes** (lagged labels; partly mechanical, because the label is close to the strategy's own signal):

| Regime | Strategy | SPY |
|---|---|---|
| Downtrend days | 4.6%/yr | 16.8%/yr |
| Uptrend days | 9.6%/yr | 11.0%/yr |
| High volatility | 8.6%/yr | 15.1%/yr |

The filter tends to be in cash during the sharp rebounds that follow sell-offs.

### Cost sensitivity (stitched OOS, fold choices fixed)
| Frictions | Net return | Sharpe | Max DD |
|---|---|---|---|
| 0× | +507% | 0.601 | −24.8% |
| 1× | +461% | 0.572 | −25.2% |
| 2× | +418% | 0.543 | −25.6% |
| 3× | +379% | 0.514 | −26.1% |

SPY's Sharpe is 0.568, so the Sharpe edge is gone between 1× and 2×.

Train (#97) at 0×/2×/3×: +333% / +321% / +314%. Validation (#98) at 0×/2×/3×: +57% / +52% / +50%.

### Uncertainty and its limits
- **Bootstrap:** 95% block bootstrap (2,000 resamples, 20-day blocks); the Sharpe difference is paired. With 15
  rule exits in 22 years, the effective sample is a handful of trend episodes. The bootstrap treats days as
  the unit and probably still overstates precision.
- **Drawdown:** a single path, with no interval. C1's pass rests largely on one event, 2008.
- **Multiple testing:**
  - The DSR with the walk-forward's 2 per-universe trials is 0.984.
  - Recomputed with the project-wide 43 configurations as a sensitivity (not a valid DSR input), it is 0.677.
  - Either way, the DSR tests Sharpe > 0, not Sharpe > SPY's, so it says nothing about the hypothesis.

## Deviations, failures and assumptions (all disclosed)
1. **Wrong walk-forward warm-up (#99).** `run_walk_forward` had no `min_warmup` option, so #99 used the
   strategy's own 230-session warm-up. Its OOS window was 4 Nov 2002 → 12 Nov 2024, not the plan's
   12 Dec 2002 → 20 Dec 2024.
   - **What was done:** this was noticed from the run's dates, before any performance figure was viewed. The
     option was added (`ebd4022`, default unchanged, tested) and the walk-forward re-run as #100. #100 is the
     pre-registered run.
   - **#99 for transparency:**
     - Sharpe 0.603 vs SPY 0.555: difference +0.048, CI [−0.25, +0.36]
     - 3× costs: Sharpe 0.548 vs SPY's 0.555 (C3 fails here too)
     - max drawdown −24.5% vs −53.7%; CAGR 8.53% vs 10.64%
   - **What #99 would have meant:** the same verdict, rejected. The month shift alone moves the Sharpe
     difference by about 0.04, and that sensitivity is itself evidence that the edge is not robust.
2. **Fold-reset artefact (pre-disclosed, plan §6).** Each fold starts and ends flat.
   - **Size:** 37 held positions were sold at a fold's last close and re-bought two sessions later. That
     missed +8.7% of SPY return in total (about 0.4%/yr) and added round-trip costs on every boundary.
   - **Effect:** this works **against** the strategy. A rough estimate of its effect on the Sharpe is +0.05
     at 1× and +0.09 at 3× costs. That's enough that C3 might pass without it, so **C3's failure is
     method-sensitive.**
   - **Why the verdict stands:** C2's interval, C4 and C5 fail regardless, and the continuous validation run
     (no artefact) lags SPY's Sharpe by 0.26. Without the artefact, the best case is "inconclusive", never
     "worth further testing".
3. **Signal on adjusted (total-return) closes**, as interpreted and stated in the plan §2. A raw-price variant
   was not run.
4. **Data:**
   - No missing observations in the development window. 2025-01-09 is an override closure, in the holdout.
   - The validation run's final decision (31 Jan 2025 close) had no fill inside the window, as intended.
5. **Test suite:** the QuantEdge suite passes on the frozen code (`ebd4022`): 181 passed. The repo-wide `tests/test_language.py`
   screener failure predates this work and is untouched. Other uncommitted work in the folder (screener,
   `services/`, theses) belongs to another session and was not touched.

## Prior exposure (restated)
This is Faber's published rule. 1999–2006 was its author's design sample, and its 2008 escape is common
knowledge. The study's single source of benefit is exactly that famous episode. That is consistent with a
rule chosen *because* it worked in 2008, not with an independently demonstrated edge.

## Recommendation
**Reject** the hypothesis that the 10-month filter improves SPY's risk-adjusted return. The holdout stays locked.

What the data does support, as a description rather than an edge:
- The filter is a **drawdown-reduction tool that costs return**: about a halved worst drawdown, for roughly
  2.8 pts/yr of CAGR and a Sharpe no better than buy-and-hold.
- Whether that trade-off is worth it is a preference question about risk, not a statistical edge, and this
  study can't settle it for you.

Suggested next steps (each would need its own pre-registered plan and your approval):
- **Stop searching for an edge with this data set.** Seven families and 43 configurations have produced none.
  The most valuable next step is probably not another signal.
- **Alternatively, ask a different question:** for example, a pre-registered risk-budgeting comparison (fixed
  60/40 or volatility-targeted SPY/T-bill). The success criteria would be about drawdown and volatility
  matching a stated preference, not about beating SPY.
- **Engineering debt:** walk-forward folds that carry positions across boundaries for fixed rules. This
  would remove the artefact in deviation 2, but it is a method change and must be decided before any future study.
