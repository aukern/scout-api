# Development Guide

> Internal guide for developers working on this codebase. Covers architecture, local setup, testing, and contribution workflow. For deployment, see [HANDOFF.md](HANDOFF.md). For API integration, see [docs/INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md).

## Table of Contents

- [Architecture](#architecture)
- [Local Setup](#local-setup)
- [Project Structure](#project-structure)
- [Configuration System](#configuration-system)
- [Running Tests](#running-tests)
- [Adding a New Feature](#adding-a-new-feature)
- [Debugging](#debugging)
- [Architecture Decisions](#architecture-decisions)

---

## Architecture

Scout API is the tool layer for AI research agents: ingests knowledge, runs semantic search, and answers questions over what it has ingested. Decoupled from any specific agent — any agent that needs knowledge ingestion and search wires to this API.

The system is structured around three domain modules (collections, sources, sessions), a background worker (arq + Redis), vector storage (pgvector), and a FastAPI HTTP layer. Collections isolate knowledge partitions. Sources are ingested documents (URLs or files) that flow through a `pending → processing → ready` lifecycle. The arq worker handles chunking, embedding, and vector storage asynchronously so HTTP responses return immediately.

**Components:**

| Module | Capability |
|--------|-----------|
| `scout_api.collections` | Manage knowledge partitions — create, list, delete |
| `scout_api.sources` | Ingest URLs and files into a collection; enqueue processing |
| `sources` (worker) | Process pending Sources: chunk content, embed with LiteLLM, store vectors in pgvector |
| `scout_api.sessions` | Research sessions that group search queries, questions, and saved briefs |

---

## Local Setup

```bash
# Clone and install
git clone https://github.com/aukern/scout-api.git
cd scout-api
python3.12 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# Configure
cp .env.example .env
# Fill in .env — see docs/config/SECRETS.md for where to get each value

# Verify setup
make test
```

**Dependencies:**

- Python 3.12
- Docker + Docker Compose (for integration tests and local services)
- See `pyproject.toml` for the full dependency list

**Start local services:**

```bash
# Start Postgres + Redis + worker
docker compose --profile postgres --profile redis --profile worker up -d

# Verify
curl http://localhost:8000/health/ready
# → {"status": "healthy"}
```

---

## Project Structure

```
src/
  scout_api/
    collections/    — Collection domain (router, repository, models, errors)
    sources/        — Source ingestion domain (router, service, repository, chunker, embedder)
    sessions/       — Session domain (router, repository, contracts, models)
    config.py       — Pydantic-settings Settings class
    health.py       — Health check registry
    main.py         — Application entry point
    worker.py       — arq worker (process_source job function)

tests/
  collections/      — Collection tests
  sources/          — Source tests (chunker, embedder, fetcher, worker, repository)
  sessions/         — Session tests
  test_infra/       — Infrastructure module tests

config/
  app_config.yaml   — Application config (non-secret settings)
  app_config.dev.yaml
  app_config.staging.yaml
  app_config.prod.yaml

migrations/
  001_initial_schema.sql
  002_sessions_schema.sql
  003_processing_columns.sql   — failed_reason column + embedding dim guide

docs/
  api/              — API reference per module
  config/           — Configuration and secrets guide
  ops/              — Runbooks, alerts, health checks, events, metrics
  adr/              — Architecture decision records
```

---

## Configuration System

This project uses a two-layer configuration system:

**Layer 1 — Secrets via environment variables (`.env`)**

Loaded by `pydantic-settings` into the `Settings` class:

```python
from scout_api.config import get_settings
settings = get_settings()
value = settings.database_url  # type-checked, validated at startup
```

Missing required secrets raise `ValidationError` at startup — no silent failures.

Copy `.env.example` → `.env` and fill in all values. See `docs/config/SECRETS.md` for how to obtain each one.

**Layer 2 — Application config via YAML (`config/app_config.yaml`)**

Non-secret settings: timeouts, retry counts, feature flags, performance thresholds.
Override per environment with `app_config.dev.yaml`, `app_config.staging.yaml`, `app_config.prod.yaml`.

See `docs/config/CONFIGURATION.md` for all available keys.

---

## Running Tests

```bash
make test              # unit tests — fast, no I/O
make test-integration  # requires .env + Docker services running
make coverage          # HTML coverage report at htmlcov/index.html
make lint              # ruff check + ruff format --check
make typecheck         # mypy
```

**Test strategy:**

- Unit tests: pure logic, no database, no network — InMemory adapters + mock asyncpg pool
- Integration tests: real database (Docker), real HTTP calls — test the full slice
- Coverage target: 90% on `src/` — currently at 90.85%

**Key test patterns:**

```python
# Testing the arq worker function directly (no Redis needed)
ctx = {
    "pool": MagicMock(),
    "embedder": Embedder(model="test", _embed_fn=AsyncMock(return_value=[0.1] * 768)),
    "chunker": MagicMock(),
    "http_fetcher": InMemoryFetchAdapter({"https://example.com": "content"}),
}
await process_source(ctx, source_id=1)

# Testing embedder without LiteLLM
embedder = Embedder(model="any", _embed_fn=AsyncMock(return_value=[0.1] * 1536))
vector = await embedder.embed("test text")
```

---

## Adding a New Feature

1. Define contracts in `src/scout_api/{module}/contracts.py` (types, Protocols)
2. Write a failing test in `tests/{module}/`
3. Implement the logic in `service.py` and `repository.py`
4. Wire into the FastAPI router if needed
5. Add OTel spans to all public methods
6. Register health checks in `src/scout_api/health.py` for new external dependencies
7. Update `.env.example` if you add secrets
8. Update `docs/config/CONFIGURATION.md` if you add config keys
9. Run `make test` and `make coverage`
10. Update `CHANGELOG.md` under `[Unreleased]`

---

## Debugging

**Structured logs** — all logs are JSON (structlog). Filter by field:

```bash
# All errors in the last 5 minutes
docker compose logs app --since=5m | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line)
        if d.get('level') == 'error':
            print(json.dumps(d, indent=2))
    except: pass
"

# Worker events
docker compose logs worker | grep '"event":"worker.process_source'

# Source processing failures
docker compose logs worker | grep '"event":"worker.process_source.failed"'
```

**Health checks** — check which dependency is failing:

```bash
curl http://localhost:8000/health/ready | python3 -m json.tool
```

**OTel traces** — spans are exported to the configured OTLP endpoint. In local dev, use Jaeger:

```bash
docker compose --profile observability up -d
# Open http://localhost:16686
```

**Worker debugging:**

```bash
# Check processing failures by querying the DB
# Sources with status=failed have failed_reason set:
psql $DATABASE_URL -c "SELECT id, origin, failed_reason FROM sources WHERE status='failed';"
```

---

## Architecture Decisions

Significant decisions are documented as ADRs in `docs/adr/`.
Use `docs/adr/0000-template.md` as the starting point.

Current decisions: see `docs/adr/` for the full list.

**Key design decisions for slice 20 (processing worker):**

- **Zero arq retries**: failed sources require manual intervention (re-ingest). A retry policy is a future slice.
- **Dimension probing at startup**: `Embedder.probe()` is called once at worker startup to detect the model's vector dimension. A mismatch between probe dimension and DB column dimension is a hard startup error — run `migrations/003_processing_columns.sql` to fix.
- **Individual chunk embedding** (not batched): one LiteLLM call per chunk. Slower but gives better error isolation — one bad chunk does not fail the entire source.
- **Delete-before-insert**: chunk replacement on re-processing deletes old chunks before any new ones are inserted. This leaves a window of zero chunks if the worker crashes mid-processing. Wrapping in a transaction is deferred to a future slice.
