# Deploying QuantEdge (always-on)

> **Status:** the Docker files in this repository have **not been run yet**. Docker isn't installed on
> the development Mac, so the first deployment is also the first test. What *has* been checked
> offline: the compose file parses and every variable it requires is in `.env.example`; every table
> and the price upsert compile for Postgres; stored JSON is strictly valid for Postgres. Expect small
> fixes on the first run. QuantEdge is **not** running 24/7 anywhere until you complete the steps below and the
> checks in "Verify" pass.

## What has to run, and when

| Service | Runs | Why |
|---|---|---|
| Postgres | 24/7 | Prices, experiments, signals, agent logs, paper accounts |
| Worker (`python -m quantedge.worker`) | 24/7 process; jobs follow the exchange calendars | Everything scheduled |
| Dashboard (Streamlit) | 24/7, or start it only when you want to look | The UI |
| Caddy | 24/7 | HTTPS certificate + password in front of the dashboard |

The worker's jobs (times are exchange-local; holidays and half-days come from the exchange calendars):

| Job | When | Notes |
|---|---|---|
| UK daily bars | LSE trading days, 17:15 London | Then signal scan, then UK daily paper accounts step |
| US daily bars | NYSE trading days, 16:30 New York | Then signal scan, then US daily paper accounts step |
| US intraday bars | every 5 min, 09:30–16:10 New York | 5-minute bars + intraday paper accounts |
| Health & freshness | every 5 min | Opens and resolves alerts; provider pinged at most every 30 min |
| AI research cycle | weekdays 23:00 London | Only if AI is configured; skipped when nothing has changed |

If the worker was down, a missed daily refresh is made up on restart (up to 5 days back), and a missed
AI cycle up to 18 hours back. A crashed worker's half-finished runs are marked failed on the next start,
and a second worker refuses to start while one is alive.

## Recommended hosting: one small VPS

A single small Linux server running `docker compose` is the cheapest dependable option:

* **Hetzner Cloud CX22** (2 vCPU, 4 GB RAM): about €4–5/month. DigitalOcean or Linode 2 GB
  droplets (~$12/month) work the same way.
* Managed alternative: Render or Railway (web + worker + Postgres) at roughly $20–30/month: less
  server admin, but more expensive.

Monthly running cost estimate:

| Item | Minimum | With paid data + AI |
|---|---|---|
| Server | ≈ €5 | ≈ €5 |
| Market data | $0 (Yahoo, personal use) | $29.99 (EODHD EOD+intraday) |
| AI agents | $0 (disabled) | your cap, e.g. $10 (`QE_LLM_MONTHLY_BUDGET_USD`) |
| Domain (optional) | ~$1 | ~$1 |

## Pre-flight checklist (on your Mac, before deploying)

* `python3 -m pytest tests -q` passes.
* `python3 -m quantedge.cli health` returns 0 with real data.
* You've decided whether the server uses Yahoo (personal use, keep it password-protected) or EODHD.
* You have: a server with SSH key access, a domain name, and a password for the dashboard.
* If enabling AI: an Anthropic API key and the monthly cap you're comfortable with.

## Steps

1. **Create the server** (Ubuntu 24.04, your SSH key, no password logins). Open only ports 22, 80, 443
   in the provider's firewall.
2. **Install Docker**: `curl -fsSL https://get.docker.com | sh`, then log out and back in.
3. **Get the code** onto the server (`git clone` the branch, or `rsync` the project folder; the image
   only needs `quantedge/` and the two requirements files).
4. **DNS:** point an A record (e.g. `quantedge.yourdomain.com`) at the server's IP.
5. **Configure:** `cp .env.example .env` and set at least:
   * `POSTGRES_PASSWORD`: long random string
   * `QE_DOMAIN`: the DNS name from step 4
   * `QE_BASIC_AUTH_USER` and `QE_BASIC_AUTH_HASH`: generate the hash with
     `docker run --rm caddy:2-alpine caddy hash-password --plaintext 'your-password'`
     and write every `$` in it as `$$` in `.env`
   * optional: `ANTHROPIC_API_KEY` + `QE_LLM_PROVIDER=anthropic`, `EODHD_API_KEY` + `QE_DATA_PROVIDER=eodhd`,
     `QE_ALERT_WEBHOOK_URL` (Slack/Discord/ntfy-style webhook for alerts)
   * `chmod 600 .env`. It holds secrets and is git-ignored.
6. **Start:** `docker compose up -d --build`
7. **Load history once:**
   ```
   docker compose run --rm worker python -m quantedge.cli refresh --core --universe us_large
   docker compose run --rm worker python -m quantedge.cli refresh --universe uk_large
   docker compose run --rm worker python -m quantedge.cli refresh --intraday
   ```
   Your local SQLite experiments aren't copied. Re-run the backtests you care about on the server.

## Verify (before calling it "always on")

* `docker compose ps`: all four services `running`, worker and dashboard `healthy`.
* `docker compose logs -f worker`: "Worker … started", then `ran: health_check` within a minute.
* `docker compose exec worker python -m quantedge.cli health --worker`: exit code 0.
* Open `https://QE_DOMAIN`: browser asks for the password; top bar shows **Worker online**.
* After the next 17:15 London / 16:30 New York, Settings → System health shows the daily refresh `ok`.
* Optional: stop the worker (`docker compose stop worker`), wait 5 min, start it, confirm the
  "worker" alert opened and resolved.

## Day-to-day

* **Update:** `git pull && docker compose up -d --build` (the worker gets 2 minutes to finish a job).
* **Logs:** `docker compose logs --since 1h worker`
* **Backups:** nightly `pg_dump` off the box, e.g. crontab:
  `0 3 * * * docker compose -f /path/docker-compose.yml exec -T db pg_dump -U quantedge quantedge | gzip > /backups/qe-$(date +\%F).sql.gz`
  and copy `/backups` somewhere else (object storage). Test a restore once.
* **Paper accounts** keep stepping as long as the worker runs; pause or close them from the dashboard.

## Rollback

* **Bad code update:** `git checkout <previous-commit> && docker compose up -d --build`.
* **Bad data or a broken migration:** stop the app containers (`docker compose stop dashboard worker`),
  restore the latest dump (`gunzip -c qe-DATE.sql.gz | docker compose exec -T db psql -U quantedge quantedge`
  into a fresh database), then start them again.
* **Turn off AI immediately:** set `QE_LLM_PROVIDER=none` in `.env` and
  `docker compose up -d worker dashboard`. No further calls are made.

## Troubleshooting

| Symptom | Check |
|---|---|
| Browser shows a certificate error | DNS must point at the server *before* first start; `docker compose logs caddy` |
| Password prompt rejects you | Every `$` in `QE_BASIC_AUTH_HASH` must be written `$$` in `.env` |
| Worker restarts repeatedly | `docker compose logs worker`; usually the database password or a missing `.env` value |
| Worker "unhealthy" | `docker compose exec worker python -m quantedge.cli health --worker` lists the failing check |
| Data stale after the close | Yahoo can lag; check Settings → System health → Recent job runs for the refresh status |
| "Another worker is running" | Two worker containers or a local worker pointing at the same database; run only one |

## Security notes

* Only Caddy publishes ports; Postgres and Streamlit are reachable only inside the Docker network.
* All secrets live in `.env` on the server, never in git. Rotate any key that has ever been pasted
  into a chat or ticket.
* There is no broker integration and no real-money execution in the code. `QE_TRADING_MODE` accepts
  only `research` or `paper`.
* Yahoo data is for personal use. Don't expose the dashboard publicly or share it commercially on
  Yahoo data. The password in front of it is required, not optional.

## Not cloud: running the worker on this Mac

For a trial before paying for a server, run `python3 -m quantedge.worker` in a terminal, or install the
launchd template in `deploy/launchd/` (instructions inside the file). Jobs only run while the Mac is on
and awake, so missed sessions are caught up when it wakes (daily jobs), but intraday polling
during sleep is simply lost.

## Known gaps before production

* Schema changes use a minimal add-missing-columns step. Adopt Alembic before changing column types.
* The first deployment is untested (see the status note at the top).
