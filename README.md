# NeuroOS

**External executive function for neurodivergent builders and shop owners.**

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
git clone <repo> neuro-os && cd neuro-os

# 2. Configure
cp .env.example .env
# Edit .env with your API keys (OpenAI/Anthropic/Google)

# 3. Start infrastructure
docker compose up -d postgres redis

# 4. Run migrations
alembic upgrade head

# 5. Register user
neuro-os register --email you@example.com

# 6. Morning protocol (your 6am command)
neuro-os morning --email you@example.com

# 7. During the day: interruption recovery
neuro-os recover --email you@example.com

# 8. End of day
neuro-os shutdown --email you@example.com
```

## API Server

```bash
neuro-os serve  # Runs on http://127.0.0.1:8000
# Docs at http://127.0.0.1:8000/docs
```

## Energy Model

Three levels drive everything:

- **Deep** (morning 6-10am): Complex, creative, high-focus work
- **Shallow** (midday/evening): Routine, admin, communications
- **Recovery** (lunch, late afternoon): Breaks, low-cognitive tasks

The scheduler learns your actual patterns and adjusts.

## Data Model

- **User** → EnergyProfile (weekly pattern + overrides)
- **Task** → energy_level, sequence, depends_on, context_snapshot (for recovery)
- **Protocol** → definition (steps + AI prompts), runs tracked
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
