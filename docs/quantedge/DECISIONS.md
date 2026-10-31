# QuantEdge architecture decisions

Decisions made by the project owner. Each one is dated, and later changes are added as new entries, never
edited in place. The roadmap is [AUTONOMOUS-SYSTEM-ROADMAP.md](AUTONOMOUS-SYSTEM-ROADMAP.md).

## D1–D8 — 31 Oct 2026 (answers to roadmap §9)

| # | Decision | What it means in code and process |
|---|---|---|
| D1 | **Broker integration code is allowed, paper-first.** No live order submission, no live credentials, no autonomous live trading in this phase. | Broker adapters may exist, but they connect only to paper endpoints. Live hosts are refused in code, not in configuration. `QE_TRADING_MODE` gains `broker_paper` when the first adapter lands; `live` is not added. No live keys are ever read. |
| D2 | **Alpaca first** for API prototyping and paper. The abstraction must also support **Interactive Brokers** (potential long-term primary). The research engine depends on neither. | `quantedge/execution/brokers/{base,sim,alpaca}.py`, with IBKR added later behind the same interface. `backtest/`, `strategies/` and `specs/` never import `execution/`, and a test enforces it. |
| D3 | **US and UK equities only.** Daily and intraday strategies stay separate. No futures, options, FX or crypto. | Spec language: a strategy declares one timeframe; mixing is rejected. The allocator keeps daily and intraday books separate. UK paper runs on our simulator until IBKR (see findings: Alpaca has no confirmed UK equities). |
| D4 | **Monthly budgets:** AI £30, market data £20, hosting £10, miscellaneous £5. Usage monitoring, alerts and hard limits. Prefer free/cheap EOD data. | AI: the existing pre-call hard cap (`agents/budget.py`), with a GBP limit, a conservative configured FX rate and alerts at 50/80/100%. Data/hosting/misc: a cost ledger of every paid service. A paid integration refuses to run unless its ledger entry fits the budget. Monthly alert. See "Spend controls" below. |
| D5 | **Agents work only within the strategy specification language.** They may propose, test, evaluate and recommend. They may not modify execution/backtest code, bypass risk controls or run arbitrary commands. Every strategy change is versioned and auditable. | Specs are data (JSON/YAML), content-hashed and versioned in the database. Agents get read-only tools plus "submit spec / request run". There is no shell and no file writes. New building blocks are human-reviewed pull requests only. |
| D6 | **`trading/`:** audit first. Keep useful broker, execution, paper and risk pieces. Separate research, broker integrations, paper execution and future live execution. No deletion or rewrite without justification. | Audit: [TRADING-DIR-AUDIT.md](TRADING-DIR-AUDIT.md). Nothing in `trading/` has been changed. Reuse is by porting into `quantedge/execution/` with tests. The Arena keeps running as is. |
| D7 | **Cloud deployment, continuously available:** scheduled research and data updates, monitoring, persistent storage, dashboard access. Avoid unnecessary continuous AI calls. | Small VM (fits £10/month) running compose: worker, dashboard, Postgres, Caddy with auth. AI runs on a schedule (e.g. daily or weekly research cycles) and on events, never in a loop. A cycle with nothing new to look at skips the model call. |
| D8 | **Live autonomy deferred** to a formal readiness review. Scope now: research and paper only. Any future live permission needs explicit human authorisation, independent validation, hard risk limits and operational safeguards. | Roadmap Phase 10 stays a document-only review. No live code path is written before it. |

## Findings that affect these decisions (31 Oct 2026)

**Alpaca**
- **Paper accounts for UK users:** paper-only accounts are open to anyone globally, so a UK user can prototype
  US-equity paper trading. Paper accounts get IEX-only real-time data.
- **Live accounts:** offered to many non-US residents depending on country. Not needed now (D1), but confirm
  with Alpaca before relying on it.
- **UK equities: not confirmed.** Alpaca's European expansion (via its FCA-authorised WealthKernel
  acquisition) started with Germany's Xetra. As of April 2026 coverage, the London Stock Exchange was
  "expected to follow". Treat UK equities on Alpaca as unavailable until shown otherwise.
- **Data cost:** free plan = IEX real-time plus historical data with a 15-minute delay on the latest bars.
  Full SIP coverage ("Algo Trader Plus") is listed at $99/month, above the £20 data budget.
- **Consequences:**
  - US paper via Alpaca (free)
  - UK paper on the internal simulator, using our validated daily bars
  - IBKR is the route to broker-side UK paper later
  - EOD research data stays on free sources (Yahoo/FRED) or a ≤ £20 EOD vendor
- **Sources:** [Alpaca paper trading docs](https://docs.alpaca.markets/us/docs/paper-trading) ·
  [Alpaca: live accounts for non-US residents](https://alpaca.markets/learn/live-trading-account-non-us) ·
  [Alpaca market data docs](https://docs.alpaca.markets/docs/about-market-data-api) ·
  [Business Wire, Apr 2026: Alpaca European equities launch](https://www.businesswire.com/news/home/20260421441080/en/) ·
  [City A.M.](https://www.cityam.com/?p=2417353)

**The Trading Arena (`trading/`) is live on this Mac**
- **Jobs:** launchd jobs `com.tradingarena.daemon` (daily cycle), `com.tradingarena.weekly` and
  `com.tradingarena.hub` (dashboard, running) are installed.
- **What it trades:** the built-in simulator only; no Alpaca keys are in use.
- **Not touched.** Retiring or migrating these jobs is your call (see the audit).

**Spend controls today**
- **AI:** there is a hard pre-call cap. `agents/budget.py` blocks any call whose worst-case cost would exceed
  the monthly or per-cycle limit, and logs it as `budget_blocked`. Health warns at 80%.
- **Gaps:**
  - limits are in USD (default $10/month), not GBP
  - alerts aren't delivered outside the dashboard
  - data, hosting and miscellaneous spend aren't tracked at all
- **Plan (Phase 0b):**
  - `QE_LLM_MONTHLY_BUDGET_GBP=30`, converted with a configured, deliberately unfavourable FX rate so the
    USD cap never exceeds £30
  - a `cost_ledger` table plus `quantedge/costs.py` (services, monthly price, start/end dates)
  - a health check per category
  - a refusal hook used by any paid data or broker client
  - alerts at 50/80/100% (stored now; emailed or sent to Telegram once Phase 9 adds delivery)

## Pending approvals (asked, not assumed)
- **Alembic** (new dependency) for schema migrations: needed before the first new table.
- **Merging `research-phase2` into `main`:** needs a push and a PR, which you'd merge on GitHub.
- **Putting `trading/` under version control** (it's `.gitignore`d today, so it has no history or backup).
