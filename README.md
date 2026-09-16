https://github.com/user-attachments/assets/8c3b5ba0-7c3a-4a48-9e2d-266edb219193
# NeuroOS

**External executive function for neurodivergent builders and shop owners.**

Not an ADHD planner. Not a wellness app. Not a generated desktop.

NeuroOS is a local-first protocol engine for the work a brain drops under load: sequencing, working memory, energy accounting, and re-entry after interruption.

The test is not a star count. The test is whether `neuro-os recover` still knows what you were doing after a restart.

NeuroOS includes a hardened single-host Docker deployment profile. Read [PRODUCTION.md](PRODUCTION.md) before exposing it to users; a real launch still requires host-specific TLS, DNS, backup, monitoring, and secret-management setup.

---

## The Tuesday loop

```text
register → morning → start → pause → recover → shutdown
```

| Command | What it does |
|---------|----------------|
| `morning` | Plan the day into energy-matched blocks from open loops |
| `start` | Mark a task in progress and optionally save the first action |
| `pause` | Leave a durable next action, notes, file/line, and resources |
| `recover` | Print the exact saved resume step — one sentence, no questions |
| `shutdown` | Close the day and prep tomorrow from real state |
| `tasks` | List tasks by status or energy |
| `admin` | Process due admin items |
| `serve` | Run the API |

If a feature does not serve that loop, it does not belong in v0.

---

## Status

Working now:

- PostgreSQL is the source of truth for API and worker
- Alembic owns schema and runs before app services in Compose
- Protocol failures are recorded as failures — no silent mock plans
- Agent tools use validated JSON arguments
- `start` / `pause` / `recover` persist user-authored context across restarts
- Tasks created by a protocol point at the exact run that created them
- Daily morning plans are idempotent per user timezone
- Authenticated run traces at `GET /protocols/runs/{run_id}` (no raw prompts or tool payloads stored)

Not done yet:

- Calendar / inbox provider adapters
- Managed hosted deployment and operational monitoring

---

## Quick start

Requires **Python 3.12+**, **Docker**, and at least one model key in `.env`.

```bash
git clone https://github.com/seanebones-lang/NeuroOS.git
cd NeuroOS

cp .env.example .env
# Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY
# Change SECRET_KEY before anything but local play

docker compose up -d --build
curl http://127.0.0.1:8011/health
```

Compose maps the API to **8011** by default (`NEURO_OS_PORT` overrides). Docs: http://127.0.0.1:8011/docs

```bash
docker compose exec api neuro-os register --email you@example.com
docker compose exec api neuro-os morning --email you@example.com
docker compose exec api neuro-os tasks --email you@example.com
```

Start work, leave a breadcrumb, come back:

```bash
docker compose exec api neuro-os start TASK_UUID --email you@example.com \
  --next-action "Open the implementation and add the validation branch"

docker compose exec api neuro-os pause TASK_UUID --email you@example.com \
  --next-action "Add the invalid-input test" \
  --working-notes "Happy path passes" \
  --file tests/test_feature.py --line 42 \
  --resource docs/design.md

docker compose exec api neuro-os recover --email you@example.com --task-id TASK_UUID
docker compose exec api neuro-os shutdown --email you@example.com
```

Morning retries the same local date return the original run. Pass `--idempotency-key` only when you intend a second plan.

### Local install (no Compose for the app process)

```bash
docker compose up -d postgres redis
pip install -e ".[dev]"
alembic upgrade head
neuro-os register
neuro-os serve          # http://127.0.0.1:8000
```

Point `DATABASE_URL` at Postgres (`postgresql+asyncpg://neuro:neuro@localhost:5432/neuro`). Do not mix a local SQLite file with the Compose database.

### Production configuration

Use the production Compose profile and follow [PRODUCTION.md](PRODUCTION.md). Production startup
rejects placeholder or short `SECRET_KEY` values, `DEBUG=true`, `DATABASE_ECHO=true`, sessions longer
than 24 hours, missing model-provider credentials, and absent, wildcard, or non-HTTPS `CORS_ORIGINS`.
The profile removes source mounts, does not publish Postgres or Redis, and only binds the API to loopback
for a TLS reverse proxy. For example:

```dotenv
ENVIRONMENT=production
DEBUG=false
SECRET_KEY=replace-with-a-unique-secret-at-least-32-characters-long
CORS_ORIGINS=https://app.example.com,https://admin.example.com
OPENAI_API_KEY=replace-with-a-provider-key
ACCESS_TOKEN_EXPIRE_MINUTES=1440
```

Redis limits registration and login by client IP within a 15-minute window, and limits authenticated
protocol execution per user within an hour. Each limit is configurable with the `*_RATE_LIMIT`
variables in `.env.example`; a storage outage returns `503` so expensive endpoints are never silently
unprotected.

---

## Energy model

Every task has a cost. The schedule pays it.

| Level | Default weekday window | Belongs there |
|-------|------------------------|---------------|
| **Deep** | 6–10am | Design, code, hard client work |
| **Shallow** | mid-morning, afternoon, evening | Email, follow-ups, admin |
| **Recovery** | lunch, late afternoon | Breaks. Not a dump for invoices. |

Admin is shallow work. Recovery is a break. The default profile is Central time (`America/Chicago`).

---

## Protocols

| Protocol | Trigger | Job |
|----------|---------|-----|
| Morning | manual | Open loops → sequenced energy-matched blocks |
| Interruption recovery | manual | Exact next micro-step from saved context |
| Shutdown | end of day | Capture open loops, prep tomorrow |
| Weekly review | Friday | Energy audit + admin batch (after the daily loop has scars) |
| Admin batch | due dates | Draft recurring admin for review |
| Comms draft | manual | Draft in your voice (needs real sent samples first) |

---

## Architecture

```
src/neuro_os/
  config.py         Pydantic settings
  database.py       SQLAlchemy async engine
  models.py         User, Task, Protocol, ProtocolRun, AdminItem, …
  agent.py          Tool-calling loop
  tools.py          Protocol tools
  protocols.py      Definitions + engine
  task_service.py   Ownership, lifecycle, recovery context
  scheduler.py      Energy-aware blocks
  memory.py         Session + Redis helpers
  api.py            FastAPI
  cli.py            Typer (`neuro-os`)
  worker.py         Scheduled jobs
alembic/versions/   Schema history
tests/
docker-compose.yml  Postgres, Redis, migrate, API, worker
```

Stack: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16 + pgvector, Redis 7, Typer, OpenAI / Anthropic / Google.

---

## API notes

- Health: `GET /health`
- Auth: `POST /auth/register`, `POST /auth/login`, `GET /auth/me`
- Tasks and protocol runs are user-scoped
- Morning plans use a timezone daily idempotency key
- `GET /protocols/runs/{run_id}` — step status, provider, model, timing, bounded tool activity, errors, linked tasks

Do not send a fresh `Idempotency-Key` unless you mean to create another run.

## Verification

Run the fast SQLite-backed suite with `pytest -q -m "not integration"`. PostgreSQL API integration
tests require an isolated migrated database in `NEURO_OS_TEST_DATABASE_URL`; run them with
`pytest -q -m integration`. CI rebuilds the PostgreSQL schema through a complete downgrade and
upgrade before testing authentication, ownership, task rollback, lifecycle conflicts, and
concurrent protocol idempotency.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src/
ruff format src/
# mypy src/   # configured strict; expect work
```

---

## Philosophy

1. Start ugly and personal — one user, 30 days
2. No nagging — protocols run on demand
3. Energy-first — every task has a cost
4. Interruption is default — recovery is first-class
5. Fail visibly — no costume plans
6. Local-first — Compose on your machine

---

## License

Mozilla Public License 2.0. See [LICENSE](LICENSE).

Built by [NextEleven LLC](https://github.com/seanebones-lang).
