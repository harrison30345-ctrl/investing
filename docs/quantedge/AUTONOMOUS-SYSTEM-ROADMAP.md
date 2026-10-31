# QuantEdge → autonomous AI trading system: audit, target architecture and roadmap

Written 31 Oct 2026. **Planning only:** nothing in this document has been built, deployed, pushed, bought or
traded. Every decision marked **[YOU]** needs your answer before the phase that depends on it starts.

## 0. Summary
- **The plumbing is buildable, and about 40% of it already exists.** Already in place:
  - deterministic data and backtest engine
  - walk-forward, cost and uncertainty statistics
  - pre-registration discipline
  - an agent framework with budgets and human approval
  - paper accounts, worker, scheduler, health checks and alerts
  - a dark Streamlit dashboard
- **The missing 60%:**
  - a way for agents to create new strategies safely
  - a promotion gate that agents can't bypass
  - a project-wide multiple-testing budget
  - a portfolio allocator
  - an order-management and risk layer
  - broker adapters
  - always-on deployment
- **The hard problem is not the plumbing. It's that there is no edge yet.** 43 configurations across 7
  families have failed. An autonomous research loop runs far more tests than a human does. Without a strict,
  code-enforced gate and a project-wide trial budget, it will reliably "discover" strategies that are noise,
  then pay real costs trading them. So the design centre of this system is the **gate** (what must be true
  before capital moves), not the agents.
- **Recommended order:**
  1. housekeeping and decisions
  2. research memory
  3. strategy specification language
  4. promotion gate
  5. agent team v2
  6. order management + risk + paper broker
  7. portfolio manager
  8. dashboard
  9. deployment
  10. a live-readiness review, only once a strategy has passed everything, including months of paper evidence

  Live orders stay behind hard caps and human approval until that review says otherwise.

## 1. Audit: repository and Git state (31 Oct 2026)

**Branches**

| Branch | Where | Notes |
|---|---|---|
| `main` | `75b0eb3`, = origin | Phases 1–8 and the audit |
| `research-phase2` (current) | local, 9 commits ahead of origin | Phase 2–4 research (`7404f1e` … `ca53657`) plus another session's commit `3a5636b` (screener Research page + `services/`). Not merged to main |
| `quantedge` | `c42fc24`, = origin | Merged via PR #1; can be deleted on GitHub |

**Uncommitted work, all belonging to other sessions; do not touch:**
- `CLAUDE.md` (theses section)
- `screener/dashboard.py`
- `theses/`, `tests/test_theses.py`

**Not under version control:** `trading/` is excluded by `.gitignore` (line 51). It's about 2,700 lines:
- a game-themed "Trading Arena" with Alpaca and simulator brokers
- stock, crypto and betting agents, a daemon and a backtester

It has no history and no backup. Its config allows `TRADING_REQUIRE_APPROVAL=0`, which disables the human
gate on live Alpaca orders.

**Other products in the repo** (preserve, don't modify): the fundamental screener (`screener/`, `dashboard.py`),
`services/` (market context, news, sensitivity: another session's screener work, read-only to us), the crypto
screener files, `moonscope/`, `bazzasheet`.

**Tests:**
- QuantEdge: 181 passing, about 3.5 minutes.
- Repo-wide: one pre-existing screener failure (`tests/test_language.py`).

**Rules in force:** CLAUDE.md says QuantEdge has "no real-money execution or broker code" and
`QE_TRADING_MODE` is only research/paper. `quantedge/config.py` enforces the mode at startup. **[YOU]** The
target system conflicts with this rule. Changing it is your decision, and the change should name exactly what is
newly allowed (e.g. "broker adapters for paper accounts" first, "live orders under hard caps" much later).

## 2. Capability map: what exists vs what's missing

| Required capability | Exists | Partial | Missing |
|---|---|---|---|
| **1. AI research** | Agent framework (`agents/`): structured outputs, budgets, LLM call log, citation checking, proposals | Agents see only stored experiments, not literature or markets. `services/market_context` + `news` exist (screener side) | Literature ingestion; market/regime monitor feeding agents; hypothesis registry; searchable research memory; failure post-mortems |
| **2. Agent team** | Trend, Momentum, Intraday, Risk Manager, Independent Validation, Research Director (`agents/roster.py`); orchestrator with queues, cycles and approval kinds | Agents can only propose experiments on **existing** strategies and parameters | Strategy-development agent that can create **new** strategies; improvement agent; portfolio-management agent; per-agent tool permissions; decision trace across agents |
| **3. Validation** | Deterministic engine, next-open fills, costs, cost curves, cash rate, universe/SPY/cash baselines, block-bootstrap CIs, PSR/DSR, walk-forward, regimes, calendar holdout, trial accounting, pre-registration docs | Pre-registration is a markdown file and a human habit, not a system object. Paper evidence isn't linked to a validation verdict | Machine-enforced promotion gate; pre-registration records (hashed, immutable); holdout-use ledger; project-wide false-discovery budget; paper-vs-backtest tracking test; fold carry-over for fixed rules (Phase 4 debt) |
| **4. Portfolio management** | Per-account capital, position caps in `SignalPortfolio` | Risk Manager agent (advisory only) | Allocator across strategies; exposure/correlation/concentration limits; strategy-level drawdown brakes; cash-reserve policy; capital ledger |
| **5. Execution** | Paper accounts (forward-only, completed bars, fills at actual prices) | `trading/broker/` has a BrokerBase, PaperSim and Alpaca adapter, but it's outside QuantEdge and unversioned | Order-management system (order state machine, idempotent IDs, partial/rejected/cancelled handling); pre-trade risk engine; position and cash reconciliation with the broker; kill switches; broker adapters in QuantEdge |
| **6. Dashboard** | Home, Markets, Signals, Watchlists, Strategy Lab, Backtests, Reports, AI Research, Paper, Settings (health) | Agent activity (AI Research page); paper results | Portfolio/equity across strategies; orders and positions blotter; risk page; strategy pipeline (gate status); market conditions page |
| **Ops** | Worker + scheduler, heartbeats, health checks, alerts table, Dockerfile + compose (never run), DEPLOY.md | Alerts are stored, not sent | Always-on host; Alembic migrations; Postgres in use; backups; external alerting (email/Telegram); secrets management; runbooks |

## 3. Existing architecture (what we build on)
```
data/providers (yahoo, eodhd, demo) → data/service (refresh, back-fill) → data/validation → db (SQLite/Postgres)
                                                                                   │
strategies/ (trend, momentum, reversal, intraday, trend_filter) ──► backtest/engine (signal + rebalance)
                                                                     ├─ costs, metrics, stats (CI, PSR, DSR)
                                                                     ├─ runner (periods, holdout, cost curve, trials)
                                                                     ├─ walkforward (stitched OOS, cost curve)
                                                                     └─ regimes
reports.py (deterministic findings/verdicts) ◄── experiments, trades, equity points
trials.py (project-wide count) · rates.py (FRED cash) · markets.py (calendars + overrides)
agents/ (llm, roster, schemas, evidence, proposals, orchestrator) → Proposal rows → human approval
paper.py (forward-only accounts; SignalPortfolio shared with backtests)
worker.py + scheduler.py (jobs) · health.py · alerts
app/ (Streamlit, screens/) + ui/ (theme, charts, components)
```

## 4. Must be preserved
1. **Determinism and single source of numbers.** The backtester (and later the paper/OMS ledger) produces every
   number. Agents cite; they never compute or recall figures.
2. **No-lookahead conventions:** signals at close, fills at next open, completed bars only, adjusted prices for
   signals, and the existing no-lookahead tests. Every new building block gets one.
3. **Every experiment recorded, failures included.** Stored records are never rewritten (reproducibility was
   verified in Phase 4).
4. **Locked holdout from 3 Feb 2025:** never used yet. It becomes a ledgered, budgeted resource (§5.4).
5. **Agents cannot approve, create accounts, change limits or move capital.** This becomes stricter, not looser.
6. **Never substitute demo data for real data.** The demo provider stays isolated.
7. **The screener, `services/`, theses, crypto screener and other sessions' work:** not modified. QuantEdge
   may *import* `services/market_context` read-only.
8. **Python 3.9 compatibility** while the local Python is 3.9. Moving to 3.11+ in a container is an option in Phase 0.

## 5. Target architecture

```
                     ┌──────────────── Research plane (LLM agents; no authority) ────────────────┐
 Sources ──► Ingest ─┤ Research · Strategy-dev · Quant-testing · Reviewer · Improvement · PM-advisor ├─► Proposals
 (papers, filings,   └───────────────────────────── reads ▲ ──────────────────────────────────────┘     │
  macro, news,                                         │                                               ▼
  market data)        Research memory (hypotheses, specs, experiments, post-mortems, full-text search)
                                                                                                      │
                     ┌──────────────── Validation plane (deterministic; the gate) ─────────────────┐ ◄┘
                     │ Spec compiler → Backtest engine → Criteria evaluator → Gate state machine      │
                     │ Trial ledger / false-discovery budget · Holdout ledger · Paper-evidence tracker│
                     └──────────────────────────────┬────────────────────────────────────────────────┘
                                                    ▼ only strategies with status ≥ PAPER / ALLOCATABLE
                     ┌──────────────── Capital plane (deterministic; hard limits) ─────────────────┐
                     │ Portfolio allocator → target weights → Pre-trade risk engine → Order intents  │
                     └──────────────────────────────┬────────────────────────────────────────────────┘
                                                    ▼
                     ┌──────────────── Execution plane ─────────────────────────────────────────────┐
                     │ OMS (order state machine) → Broker adapter (Sim / Alpaca-paper / IBKR-paper → │
                     │ live later) → Fills → Positions & cash ledger → Reconciliation → Kill switches│
                     └───────────────────────────────────────────────────────────────────────────────┘
 Ops: worker/scheduler · health · alerting (email/Telegram) · audit log · backups · dashboard (read + approve)
```

### 5.1 The principle that makes autonomy safe
**Agents propose; deterministic code disposes.** Agents write only to proposal, hypothesis and research tables. Only
the gate service may change a strategy's status. Only the allocator may produce target weights. Only the
risk engine may release orders. Each of those reads recorded evidence and fixed policy, never agent text.

Policies (criteria templates, limits, budgets) live in versioned files that agents can't write. Changing them
requires a human-approved proposal and a new version. This is what "the AI must not be able to bypass these
requirements" means in code.

### 5.2 How agents create strategies: a specification language, not free code
Today agents can only re-parameterise existing strategies. For real strategy development they need to express
new rules. **Recommended: a declarative strategy spec** (JSON/YAML) built from vetted, individually
no-lookahead-tested building blocks, compiled into the existing engine:
- universe selectors
- signals: returns, moving averages, breakouts, volatility, carry, value ratios from point-in-time fundamentals later
- transforms: rank, z-score, lag
- filters and regimes
- sizing: equal, inverse-vol, vol-target
- rebalance schedules: N sessions, month-end (the Phase 4 hook)

Agents can combine blocks freely; they can't write arbitrary Python. **[YOU]** Agent-written *code* (new blocks)
would be a pull request a human reviews and merges, never auto-loaded. This removes the biggest technical risks:
lookahead, sandbox escape and non-determinism.

### 5.3 The promotion gate (state machine, code-enforced)
`IDEA → SPECIFIED → PRE-REGISTERED → TRAIN-TESTED → VALIDATED → PAPER → ALLOCATABLE → LIVE-CANDIDATE → LIVE`

| Stage | What is required |
|---|---|
| PRE-REGISTERED | Spec + criteria template + periods stored, content-hashed, immutable, before any run |
| TRAIN-TESTED | The pre-registered runs exist and completed |
| VALIDATED | Validation + walk-forward pass the template's criteria (baselines, CIs, 2×/3× costs, stability thirds, concentration, DSR with per-universe trials) **and** the project false-discovery budget (§5.4) |
| PAPER | Automatic for VALIDATED, on a paper account (agents still can't create one; the gate does, under policy) |
| ALLOCATABLE | Minimum paper duration and trade count (e.g. ≥ 6 months and ≥ 30 trades, set per template), paper P&L consistent with the backtest's predictive distribution, no kill-switch events |
| LIVE-CANDIDATE | Only after a one-time holdout run passes; a human review |
| LIVE | Human approval, hard capital cap |

Demotion is automatic and immediate: tracking failure, drawdown brake, data fault.

### 5.4 Multiple testing at machine scale
- **Trial ledger:** every spec an agent runs is a trial, including failures (extends `trials.py`).
- **Project false-discovery budget:** a promotion's DSR is computed with the *project-wide* trial count (or an
  alpha-spending schedule), not only the per-universe count. Phase 4 showed the difference: 0.984 vs 0.677.
- **Research quotas:** each agent cycle has a trial allowance. Raising it requires a human.
- **Holdout ledger:** a budget of holdout looks (e.g. one per hypothesis family per year). The current holdout
  becomes stale as it's used. Plan for rolling holdouts: data arriving after a strategy is frozen is the
  cleanest test there is, which is what paper trading provides.

### 5.5 Portfolio management (deterministic)
- **Inputs:** ALLOCATABLE strategies; their paper and backtest return streams; policy limits.
- **Method (start simple):**
  - inverse-volatility or risk-parity across strategies
  - shrink each weight by an evidence haircut (paper duration, DSR)
  - cap per strategy, per asset class, per instrument, and on gross/net exposure
  - correlation and cluster limits
  - minimum cash reserve
  - portfolio volatility target
- **Brakes:**
  - per-strategy drawdown limit → halve, then suspend
  - portfolio drawdown limit → de-risk to cash
  - backtest-tracking failure → suspend
- **The portfolio-management agent:** advisory only. It explains allocations and proposes policy changes;
  it can't set weights.

### 5.6 Execution (broker-neutral)
- **Order intent → pre-trade risk checks:**
  - max order notional and % of average daily volume
  - per-instrument and gross exposure
  - daily loss limit
  - order rate limit
  - price-band sanity vs the last trade
  - market open and calendar
  - data freshness
  - kill-switch state
- **OMS order lifecycle:** NEW → SUBMITTED → ACKED → PARTIAL → FILLED / CANCELLED / REJECTED / EXPIRED, with
  idempotent client order IDs.
- **Broker adapter interface:** submit, cancel, status, positions, account, clock. Implementations:
  - `SimBroker`: extends `paper.py` semantics
  - `AlpacaBroker` / `IBKRBroker`: paper endpoints only, until the live review
- **Reconciliation** every cycle: internal ledger vs broker positions and cash. A mismatch halts trading and
  raises an alert.
- **Kill switches:**
  - manual
  - automatic on: stale data, broker errors over threshold, reconciliation mismatch, daily loss limit,
    abnormal spread or price gap, clock skew
- **Live mode:** a separate process and config with its own credentials, hard notional caps in code, and (until
  the review says otherwise) human approval per order or per daily batch. **[YOU]** I'll build the gates; how
  much autonomy live mode gets is your decision at the live-readiness review, not now.

### 5.7 Research plane details
- **Sources (free first):**
  - arXiv q-fin and SSRN abstracts
  - SEC EDGAR filings
  - FRED macro
  - exchange calendars
  - the existing market-context readings
  - your broker's market data

  News requires checking each source's terms. All fetched text is **untrusted data**: agents that read it never
  hold any authority (prompt-injection defence by design).
- **Research memory:**
  - tables: hypotheses, spec versions, trials, post-mortems, literature notes with citations
  - full-text search (SQLite FTS5 / Postgres tsvector); embeddings optional later
- **Market/regime monitor:** deterministic indicators (trend, volatility, breadth, rates, credit) stored daily.
  Agents read them; strategies may use them only through spec blocks with lagged values.

### 5.8 Dashboard additions
Portfolio overview (equity, P&L by strategy, exposure), Orders & Positions blotter, Risk (limits used,
correlation heatmap, brakes and kill-switch state), Strategy Pipeline (gate stage of every hypothesis, trial
budget used), Agent Activity (exists; add the decision trace), Market Conditions (regime monitor + imported
market context), Health & Alerts (exists; add external delivery status). Same dark theme and components.

## 6. External services, data and infrastructure
Prices change. Check current pricing before any purchase; nothing is bought without your approval.

| Need | Options | Notes |
|---|---|---|
| LLM | Anthropic API (agent framework already targets Claude; SDK pinned < 1.0 for Python 3.9) | No API key configured yet. The existing budget caps and LLM call log apply. Autonomous cycles cost real money every day, so set a monthly cap **[YOU]** |
| Daily market data | Yahoo (free, unofficial; unsuitable to drive real orders), EODHD (noted earlier at about $30/month), Tiingo, Polygon, broker data feeds | Execution should use the broker's or a contracted feed, not Yahoo |
| Survivorship-free history | Norgate Data, CRSP (academic), Sharadar | Needed for any stock-selection research to be credible (Phases 2–3 lesson); paid |
| Macro / filings | FRED, SEC EDGAR (free) | Already using FRED |
| Broker (paper → live) | **Interactive Brokers** (UK and US residents; robust; needs the TWS/IB Gateway process), **Alpaca** (simple REST, free paper; availability for UK residents must be verified), Trading 212 API (linked in the screener; verify order support) | **[YOU]** Choose by residency, assets and account. Start with paper on the chosen broker |
| Hosting | Small always-on VPS or cloud VM running Docker Compose (worker, dashboard, Postgres, Caddy); IB Gateway needs its own container | Docker isn't installed on this Mac; DEPLOY.md exists but has never been run |
| Alerting | Email (SMTP) or Telegram bot | Free |
| Secrets / backups | `.env` with restricted permissions → a secret manager later; nightly Postgres dumps to off-site storage | |

## 7. Phases, dependencies and size
Sizes: S ≈ 1 working session, M ≈ 2–4, L ≈ 5+. Each phase ends with tests passing and a local commit.

| # | Phase | Depends on | Size | Done when |
|---|---|---|---|---|
| 0 | Housekeeping and decisions | — | S | Decisions in §9 answered; `research-phase2` merged via PR (you merge); CLAUDE.md rules updated by you; `trading/` either versioned in its own repo or retired; Alembic introduced |
| 1 | Research memory + trial/holdout ledgers | 0 | M | Hypotheses, specs, trials and post-mortems stored and searchable; holdout ledger enforced by the runner |
| 2 | Strategy spec language + compiler | 1 | L | Existing 6 strategies re-expressed as specs and reproduce their stored results exactly; every block has a no-lookahead test |
| 3 | Promotion gate + criteria templates + false-discovery budget | 1, 2 | M | State machine with tests proving agents can't change status; Phase 3/4 studies re-evaluated through templates reproduce their verdicts |
| 4 | Agent team v2 | 1–3 | M | Six roles on structured schemas with per-role tool permissions; a full cycle runs on recorded data in dry-run (no API spend) |
| 5 | OMS + risk engine + SimBroker | 3 | L | Order state machine, pre-trade checks, ledger, reconciliation and kill switches; fault-injection tests (rejects, partial fills, outages, duplicate submits) |
| 6 | Broker adapter (paper) | 5, broker choice | M | Paper account on the chosen broker; reconciliation clean for 2 weeks; no live code path enabled |
| 7 | Portfolio allocator | 3, 5 | M | Deterministic allocation with limits and brakes; runs nightly on paper |
| 8 | Dashboard expansion | 5–7 | M | Pages in §5.8, headless render tests |
| 9 | Deployment and ops | 5–8 | M | Always-on host, Postgres, backups, external alerts, runbooks; 30 days unattended with health green |
| 10 | Live-readiness review | ≥ 1 strategy ALLOCATABLE + months of paper | S | Written review; if approved by you, live with hard caps and human approval |

Phases 1–4 (research side) and 5–6 (execution side) can run in parallel after Phase 3's interfaces are fixed.

**Realistic timeline:** the build is weeks of sessions. The binding constraint is evidence. A strategy needs
validation **and** months of paper trading before any live discussion, and so far none has passed validation.

## 8. Risks

**Financial**
- **No edge exists in what's been tested.** An automated system without an edge loses money steadily
  through costs, and faster when it's active.
- **Automated p-hacking:** agents generating hundreds of trials will find false positives. Mitigations:
  §5.3–5.4, research quotas, project-wide DSR.
- **Overfitting to known history:** agents know 2008, 2020 and 2022. Mitigations: paper/forward evidence
  required; a holdout ledger.
- **Model risk and regime change:** strategies decay. Mitigations: automatic demotion; drawdown brakes.
- **Execution risk:** slippage beyond the model, gaps, illiquidity. Mitigations: ADV limits; live costs
  feed back into the cost model.

**Operational**
- **Duplicate or runaway orders.** Mitigations: idempotent IDs, rate limits, reconciliation halts.
- **Stale or bad data driving trades.** Mitigations: freshness and quality gates already exist; make them
  pre-trade checks.
- **Outages** (host, broker, IB Gateway restarts). Mitigations: heartbeats, safe-state on restart (no orders
  until reconciled).
- **Clock and calendar errors** (Phase 4 found one). Mitigation: closure overrides, clock checks.
- **Untracked code:** `trading/` today. Mitigation: everything executable under version control.

**Security**
- **API key leakage:** keys only in env/secret store, never in the DB, logs or LLM prompts.
  Mitigations: separate paper and live keys; IP allow-listing where the broker supports it.
- **Prompt injection via research sources:** agents read untrusted text, so they hold no authority and
  have no tools that act. Proposals are validated against schemas and policy.
- **Dashboard exposure:** authentication in front of any deployed dashboard (Caddy basic-auth/OAuth). The
  approval actions are the most sensitive endpoints.
- **Agent-written code:** human-reviewed PRs only, never executed automatically (§5.2).

**Regulatory, tax and personal**
- **Your own money only.** Trading only your own money algorithmically is generally fine for a UK resident.
  Managing or pooling **other people's** money ("hedge fund") needs FCA authorisation. Get advice before that.
- **US accounts:** pattern-day-trader rules apply to margin accounts under $25k; US dividend withholding.
- **UK tax:** capital gains record-keeping. The ledger should export trade history.
- **Not advice:** this system and I don't provide investment advice. The live-allocation decision and its
  risk stay yours.

## 9. Decisions needed from you **[YOU]**
1. **Project rules:** may QuantEdge now contain broker adapters (paper first)? Update CLAUDE.md wording.
2. **Broker:** IBKR, Alpaca (if available to you) or Trading 212, and which account (ISA/GIA/US).
3. **Asset classes and markets** for the first version (suggest: US and UK equity ETFs and large caps, daily
   bars; add futures, FX or crypto later).
4. **Budgets:** monthly LLM spend cap and data budget (£0 is possible for Phases 1–8; credible stock-selection
   research needs paid survivorship-free data).
5. **Agent code:** keep agents to the spec language (recommended), or allow PR-reviewed code blocks.
6. **`trading/` (Trading Arena):** its own Git repo, fold its broker code into QuantEdge's execution layer, or retire it.
7. **Hosting:** which machine or provider runs 24/7.
8. **Live autonomy:** deferred to the Phase 10 review. Until then: paper only.

## 10. File-level roadmap
New package layout inside `quantedge/` (existing modules unchanged unless listed):

**Phase 0**
- `alembic/`, `alembic.ini` (migrations; first migration = current schema)
- `quantedge/config.py`: new modes only after your rule change (`QE_TRADING_MODE` gains `broker_paper`; `live`
  is not added until Phase 10)
- `docs/quantedge/DECISIONS.md`

**Phase 1: research memory**
- `quantedge/research/__init__.py`
- `research/models.py`: Hypothesis, SpecVersion, Trial, PostMortem, LiteratureNote, HoldoutUse (Alembic migration)
- `research/registry.py`: create/query hypotheses; immutable, hashed pre-registration
- `research/search.py`: full-text search
- `research/ledger.py`: trial and holdout ledgers (extends `quantedge/trials.py`)
- `quantedge/backtest/runner.py`: refuse a test-period run without a holdout-ledger entry
- `tests/quantedge/test_research_memory.py`

**Phase 2: strategy spec language**
- `quantedge/specs/schema.py`: JSON Schema
- `specs/blocks/` (`signals.py`, `filters.py`, `sizing.py`, `schedules.py`)
- `specs/compiler.py`: spec → `RebalanceStrategy` / `SignalStrategy`
- `specs/library/*.yaml`: the existing strategies as specs
- `tests/quantedge/test_specs_equivalence.py`: reproduces stored experiments exactly
- `tests/quantedge/test_specs_no_lookahead.py`

**Phase 3: promotion gate**
- `quantedge/gate/states.py`, `gate/criteria.py` (templates from the Phase 3/4 criteria),
  `gate/evaluator.py`, `gate/service.py` (the only writer of strategy status)
- `gate/policies/*.yaml`: versioned and human-owned
- `tests/quantedge/test_gate.py`: includes "agent code path cannot change status"

**Phase 4: agent team v2**
- `quantedge/agents/roster.py` (rewrite roles)
- `agents/tools.py`: read-only tools per role
- `agents/research_ingest.py`: arXiv/EDGAR/FRED fetchers; text marked untrusted
- `agents/strategy_dev.py`: emits specs
- `agents/review.py`, `agents/improve.py`, `agents/pm_advisor.py`
- `agents/schemas.py` (new schemas), `agents/orchestrator.py` (cycle with quotas)
- `tests/quantedge/test_agents_v2.py`: recorded-response dry runs, no network

**Phase 5: execution core**
- `quantedge/execution/models.py`: Order, Fill, Position, CashLedger, KillSwitch (migration)
- `execution/oms.py`, `execution/risk.py`, `execution/reconcile.py`, `execution/killswitch.py`
- `execution/brokers/base.py`, `execution/brokers/sim.py`
- `quantedge/paper.py`: accounts move onto the OMS + SimBroker, preserving current results
- `tests/quantedge/test_oms_faults.py`

**Phase 6: broker adapter (paper only)**
- `execution/brokers/alpaca.py` or `ibkr.py`: paper endpoints only, live hosts refused in code
- `tests/quantedge/test_broker_contract.py`: against recorded responses

**Phase 7: portfolio allocator**
- `quantedge/portfolio/allocator.py`, `portfolio/limits.py`, `portfolio/brakes.py`
- `portfolio/policies/*.yaml`
- `tests/quantedge/test_allocator.py`

**Phase 8: dashboard**
- `quantedge/app/screens/portfolio.py`, `orders.py`, `risk.py`, `pipeline.py`, `market_conditions.py`
  (imports `services/market_context` read-only)
- extend `ai_research.py`, `settings.py`
- `tests/quantedge/test_dashboard_pages.py`

**Phase 9: deployment and ops**
- `deploy/` (compose with Postgres, IB Gateway if chosen, backups), `quantedge/notify.py` (email/Telegram)
- `docs/quantedge/RUNBOOK.md`
- `quantedge/worker.py` / `scheduler.py`: new jobs for ingest, gate, allocate, execute and reconcile

**Phase 10: live-readiness review**
- `docs/quantedge/LIVE-READINESS-REVIEW.md` only. Live code paths are written after that review is approved.

## Addendum — 31 Oct 2026: decisions applied
Decisions D1–D8 are recorded in [DECISIONS.md](DECISIONS.md); the `trading/` audit is in
[TRADING-DIR-AUDIT.md](TRADING-DIR-AUDIT.md). Changes to the plan above:
- **Phase 0b (new, S): spend controls.**
  - AI: GBP limit (£30) on the existing pre-call hard cap, plus alerts at 50/80/100%.
  - Data/hosting/misc: a `cost_ledger` table, a refusal hook for paid clients, and health checks.
  - Needs Alembic, which is pending approval.
- **Phase 6:** US paper on Alpaca (free plan: IEX real-time data). UK paper on the internal `SimBroker`,
  because UK equities on Alpaca are unconfirmed. IBKR is next behind the same interface. The Alpaca port
  fixes the five defects listed in the audit.
- **Phase 2 spec language:** one timeframe per strategy (daily or intraday), never mixed. US/UK equities only.
- **Phase 4 agents:** spec-only tools. No shell, no file writes, no code changes. Spec versions are hashed and stored.
- **Phase 9:** a VM that fits £10/month. AI runs on schedules and events only, never continuously.
- **Live:** unchanged. Nothing before the Phase 10 review.
