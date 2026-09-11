### Self-hosted job monitor.

Polls official career/ATS feeds for a configured list of companies, stores
normalized software-engineering vacancies, and sends a Telegram alert when a
**new** posting matches deterministic rules.

## What it does

You maintain a YAML list of target companies. On a timer (default 10 minutes)
the process:

1. Loads enabled companies from PostgreSQL
2. Fetches published jobs from each company's ATS
3. Normalizes them into a common `SourceJob` shape
4. Reconciles against stored jobs
5. Scores newly discovered jobs with keyword/location rules
6. Sends a Telegram message for jobs above the alert threshold

The first successful fetch for a company is a **baseline**: current vacancies
are stored but not alerted. Subsequent polls alert only for jobs that were not
seen before.

## Architecture

```
Company registry  →  ATS adapter  →  raw jobs
        →  normalization  →  persistence / reconciliation
        →  matching  →  Telegram notification
```

The running process is a FastAPI app plus an in-process APScheduler job. There
is no Redis, Celery, or extra worker.

Package layout:


| Path                | Responsibility                         |
| ------------------- | -------------------------------------- |
| `app/companies`     | YAML import into the company table     |
| `app/sources`       | ATS adapters and HTTP helpers          |
| `app/jobs`          | Normalization and reconciliation       |
| `app/matching`      | Deterministic scoring                  |
| `app/notifications` | Telegram delivery + notification state |
| `app/scheduler`     | Poll orchestration                     |
| `app/api`           | Health / read-only observability API   |




## Supported ATSs


| `source_type` | Identifier                                                 | Endpoint used                                                                                          |
| ------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `greenhouse`  | Job board token (`boards.greenhouse.io/{token}`)           | Public Job Board API `GET /v1/boards/{token}/jobs?content=true` (no pagination; descriptions included) |
| `lever`       | Site slug (`jobs.lever.co/{slug}`). EU boards: `eu/{slug}` | Official Postings API `GET /v0/postings/{site}?mode=json` with `skip`/`limit` pagination               |
| `ashby`       | Job board name (`jobs.ashbyhq.com/{name}`)                 | Official posting-api `GET /posting-api/job-board/{name}`                                               |
| `workday`     | `host/tenant/site`                                         | Public CXS JSON API `POST /wday/cxs/{tenant}/{site}/jobs` with `limit=20`                              |


Workday is not a single slug. Copy host, tenant, and site from a real careers
URL, for example:

```
https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
source_identifier: nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite
```

Workday list responses do not include descriptions or real timestamps. Newly
discovered Workday jobs are enriched with a follow-up detail GET so description
keywords can match. `postedOn` strings such as "Posted Yesterday" are ignored.

## Local setup

Python 3.12+, PostgreSQL, and a virtualenv:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Point DATABASE_URL at local Postgres, e.g.
# DATABASE_URL=postgresql+asyncpg://jobalert:jobalert@localhost:5432/jobalert
make migrate
make import-companies
make dev
```



## Environment variables

See `.env.example`. Important values:

- `DATABASE_URL` — SQLAlchemy async URL (`postgresql+asyncpg://...`). Required.
- `POLL_INTERVAL_SECONDS` — default `600`
- `COMPANY_CONCURRENCY` — parallel company fetches, default `8`
- `MISS_THRESHOLD` — consecutive successful polls a job must be missing before it is marked inactive (default `2`)
- `NOTIFICATIONS_ENABLED` — `true` to send Telegram messages
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — required when notifications are enabled
- `MATCHING_CONFIG_PATH` / `COMPANIES_CONFIG_PATH`

Configuration fails fast with a readable error when required values are missing.

## Database migrations

Alembic is the schema source of truth. `create_all()` is not used at runtime.

```bash
make migrate
# equivalent: alembic upgrade head
```

Docker Compose runs `alembic upgrade head` before starting uvicorn. If the
migration fails, the container exits.

## Adding companies

Edit `config/companies.yaml` and import:

```bash
make import-companies
# python -m app.cli import-companies --file config/companies.yaml
```

The import is an idempotent upsert on `(source_type, source_identifier)`.
Companies not listed in the file are left untouched (never deleted). Changing
`enabled: false` disables polling for that company. The bundled examples start
disabled; set `enabled: true` for the companies you want to monitor.

Example:

```yaml
companies:
  - name: Example
    website: https://example.com
    careers_url: https://boards.greenhouse.io/example
    source_type: greenhouse
    source_identifier: example
    priority: high
```

`priority` may be `high` / `medium` / `low` (or an integer). Higher values are
polled first.

## Matching configuration

Rules live in `config/matching.yaml` and are reloaded at the start of every
poll. There is no LLM and no resume hardcoded in the application.

- Title keywords add `title_weight` each
- Description keywords add `description_weight` each
- Seniority keywords add a one-time `seniority_bonus`
- Negative keywords subtract `negative_weight` (they do **not** auto-reject unless `hard_reject_on_negative: true`)
- Location `include` / `exclude` are gates. An empty location does not fail include rules
- Jobs with `score >= alert_threshold` and at least one positive reason are notified



## Telegram setup

1. Talk to [@BotFather](https://t.me/BotFather) and create a bot. Copy the token.
2. Message the bot, then call `https://api.telegram.org/bot<token>/getUpdates` to
  read your chat id (or add the bot to a group and use the group's chat id).
3. Set in `.env`:

```
NOTIFICATIONS_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
```

Do not commit real tokens. The API and logs never echo the bot token.

Alerts look like:

```
🚀 New matching job

Company: Mollie
Role: Senior Backend Engineer
Location: Amsterdam
Match: 88/100

Why:
• title contains "backend"
• description contains "python"

Apply:
https://...
```

A Telegram failure does not roll back job ingestion. The notification row is
stored as `failed` and retried on a later poll.

## Running manually

```bash
make dev                 # API + scheduler
make poll                # one poll cycle, then exit
make import-companies
curl -s localhost:8000/health
curl -s localhost:8000/stats
curl -s localhost:8000/companies
curl -s localhost:8000/jobs
curl -X POST localhost:8000/poll
```

`POST /poll` starts a cycle if one is not already running. It uses the same
overlap lock as the scheduler.

## Docker deployment

```bash
cp .env.example .env
# Optional: set Telegram credentials and NOTIFICATIONS_ENABLED=true
docker compose up --build
```

Then import companies (the app image includes `config/`):

```bash
docker compose exec app python -m app.cli import-companies
```

The `config/` directory is bind-mounted so you can edit YAML without rebuilding.

## Polling and bootstrap semantics

- Only `enabled` companies are polled.
- One company failing (network, 404, malformed feed) does not stop the others.
- A failed fetch **does not** mark jobs inactive. That would treat ATS downtime
as "every job disappeared".
- After a **successful** fetch, jobs missing from the payload increment
`consecutive_misses`. They become `active=false` after `MISS_THRESHOLD`
consecutive misses (default 2). Seeing them again reactivates them.
- Jobs are never deleted.
- Identity is `(company_id, external_id)`, never the title.
- Duplicate rows in an ATS response are ignored after the first.
- Overlapping polls: in-process lock + `max_instances=1` + unique
`(job_id, channel)` on notifications.

**Bootstrap:** if `companies.baseline_completed_at` is null, the first
successful fetch stores every current job and sets the timestamp. No
notifications are sent. After that, only newly inserted jobs are eligible.

## Adding a new ATS adapter

1. Implement `SourceAdapter` in `app/sources/` with `fetch_jobs(company, client)`.
2. Map the vendor payload onto `SourceJob`. Raise `SourceNetworkError`,
  `SourceResponseError`, `SourceConfigError`, or `SourceAuthError` as
   appropriate. Skip individual malformed vacancies instead of failing the
   company when the rest of the feed is usable.
3. Register it in `app/sources/registry.py` (`default_adapters` +
  `SUPPORTED_SOURCE_TYPES`).
4. Add mocked HTTP tests. Do not call live ATS APIs from the unit suite.

Adapters must not write to the database or send notifications.

## Development commands

```bash
make test      # pytest
make lint      # ruff check, ruff format --check, mypy app
make format    # ruff format + fix
make migrate
make import-companies
make poll
make dev
```



## Known limitations

- Single-instance only. Duplicate in-process polls are safe; multiple machines
writing the same database are not a design goal.
- Workday needs a full `host/tenant/site` identifier. There is no generic
slug lookup, and custom domains work only when `host` is the real careers
host that serves `/wday/cxs/...`.
- Workday `limit` is hardcoded to 20. Higher values can return an empty list
with HTTP 200.
- Location matching is substring/keyword based, not visa or country inference.
- No dashboard, user accounts, LinkedIn/Indeed scraping, or LLM scoring.
- A crash after Telegram accepts a message but before the row is marked `sent`
can cause a single retry duplicate. The unique constraint still prevents
two successful rows for the same job/channel.
- Lever empty arrays are treated as "zero jobs", not "wrong slug" (wrong slugs
usually 404).
"""

