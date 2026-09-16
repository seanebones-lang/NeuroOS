# NeuroOS

**External executive function for neurodivergent builders and shop owners.**

Not an ADHD planner. Not a wellness app. Not a generated desktop.

NeuroOS is a local-first protocol engine for the work a brain drops under load: sequencing, working memory, energy accounting, and re-entry after interruption.

The test is not a star count. The test is whether `neuro-os recover` still knows what you were doing after a restart.

This is a development baseline, not a production release. See [ROADMAP.md](ROADMAP.md) for gates.

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

- Local login token (CLI still prompts email + password)
- Calendar / inbox provider adapters
- Hosted UI
- Production secret / CORS / rate-limit hardening

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

---

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
