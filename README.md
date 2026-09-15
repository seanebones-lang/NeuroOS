# NeuroOS

**External executive function for neurodivergent builders and shop owners.**

See [ROADMAP.md](ROADMAP.md) for the current engineering priorities and release gates.

Not an ADHD planner. Not a wellness app. An operating system that takes the work your brain is bad at (sequencing, working memory, energy management, translating social/business language, follow-through after interruption, invisible admin) and makes the work your brain is good at possible.

## Core Protocols

| Protocol | Trigger | Purpose |
|----------|---------|---------|
| **Morning** | 6am / manual | Ingest calendar + inbox + open loops → 3 sequenced energy-matched blocks |
| **Interruption Recovery** | Hotkey / manual | "What was I doing?" → exact next micro-step in one sentence |
| **Shutdown** | End of day | Capture completed, open loops, prep tomorrow's 3 items + personal note |
| **Weekly Review** | Friday | Energy audit, admin batch, profile adjustment |
| **Admin Batch** | Due dates | Auto-draft invoices, taxes, licenses, follow-ups for review |
| **Comms Draft** | Manual | Draft awkward emails/DMs in your voice |

## Architecture

```
neuro-os/
├── src/neuro_os/
│   ├── config.py          # Pydantic settings
│   ├── database.py        # SQLAlchemy async engine
│   ├── models.py          # User, Task, Protocol, AdminItem, CommsTemplate
│   ├── agent.py           # Core agent loop + protocol prompts
│   ├── memory.py          # Working + long-term (Redis) memory
│   ├── scheduler.py       # Energy-aware scheduling
│   ├── protocols.py       # Protocol definitions + engine
│   ├── task_service.py     # Task ownership, lifecycle, and recovery rules
│   ├── api.py             # FastAPI REST + auth
│   └── cli.py             # Typer CLI for daily use
├── alembic/               # Migrations
├── tests/
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## Quick Start

```bash
# 1. Clone and enter
git clone https://github.com/seanebones-lang/NeuroOS.git neuro-os && cd neuro-os

# 2. Configure
cp .env.example .env
# Edit .env with your API keys (OpenAI/Anthropic/Google)

# 3. Start the database, migration, API, and worker services
docker compose up -d --build

# 4. Confirm the API is healthy
curl http://127.0.0.1:8011/health

# 5. Register user
docker compose exec api neuro-os register --email you@example.com

# 6. Morning protocol (your 6am command)
docker compose exec api neuro-os morning --email you@example.com

# 7. Start a task, then save exact context before switching away
docker compose exec api neuro-os start TASK_UUID --email you@example.com \
  --next-action "Open the implementation and add the validation branch"
docker compose exec api neuro-os pause TASK_UUID --email you@example.com \
  --next-action "Add the invalid-input test" \
  --working-notes "The happy path passes" \
  --file tests/test_feature.py --line 42 \
  --resource docs/design.md

# 8. Recover the saved action exactly, even after an API restart
docker compose exec api neuro-os recover --email you@example.com --task-id TASK_UUID

# 9. End of day
docker compose exec api neuro-os shutdown --email you@example.com
```

## API Server

```bash
neuro-os serve  # Runs on http://127.0.0.1:8000 outside Docker
# Docker Compose serves the API at http://127.0.0.1:8011
# Docs at http://127.0.0.1:8011/docs when using Docker Compose
```

Morning plans use a user-timezone daily idempotency key. Retrying the same request returns the
original run and its tasks. Send a distinct `Idempotency-Key` header only when intentionally
requesting another run.

## Energy Model

Three levels drive everything:

- **Deep** (morning 6-10am): Complex, creative, high-focus work
- **Shallow** (midday/evening): Routine, admin, communications
- **Recovery** (lunch, late afternoon): Breaks, low-cognitive tasks

The scheduler learns your actual patterns and adjusts.

## Data Model

- **User** → EnergyProfile (weekly pattern + overrides)
- **Task** → energy_level, sequence, depends_on, durable recovery context, creating run
- **Protocol** → definition (steps + AI prompts)
- **ProtocolRun** → status, timing, notes, and the exact tasks created by that execution
- **AdminItem** → recurring (invoicing, tax, licenses) with templates
- **CommsTemplate** → channel + recipient_type + voice instructions

## Development

```bash
# Install dev dependencies
pip install -e .[dev]

# Run tests
pytest

# Lint
ruff check src/
mypy src/

# Format
ruff format src/
```

## Philosophy

1. **Start ugly and personal** — Build for one user (you) for 30 days
2. **No nagging** — Protocols run on demand, not push notifications
3. **Energy-first** — Every task has an energy cost; schedule pays it
4. **Interruption is default** — Recovery is a first-class protocol
5. **Your voice** — Comms drafter learns from your sent messages
6. **Local-first option** — Docker compose runs everything on your machine

## License

Proprietary — NextEleven LLC
