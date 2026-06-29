# scout-api

[![CI](https://github.com/aukern/scout-api/actions/workflows/ci.yml/badge.svg)](https://github.com/aukern/scout-api/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)](Dockerfile)

> The tool layer for AI research agents: ingests knowledge, runs semantic search, and answers questions over what it has ingested.

## The Problem

AI agents that need to reason over external knowledge have a fundamental tooling gap. Every agent ends up rolling its own retrieval stack — a bespoke mix of chunking, embedding, storage, and search — with no shared contract and no isolation between agents. A research agent ingesting academic papers cannot safely share infrastructure with a support agent ingesting product documentation, yet building separate stacks for each multiplies the maintenance burden.

The existing solutions are either too heavy (full RAG platforms that own the agent layer too) or too primitive (raw vector database clients that leave chunking, embedding lifecycle, and multi-tenant isolation as unsolved problems). What agents actually need is a narrow, composable tool server: one that handles the messy work of turning URLs and files into searchable knowledge, exposes it through a clean API and MCP interface, and gets completely out of the way of agent orchestration.

Scout API is that tool server. It is deliberately decoupled from any specific agent framework — any agent that needs knowledge ingestion and search wires to this API. One agent's collections stay invisible to another's. The API surface is small and stable enough to call from any language.

## How I Approached It

The project was decomposed into eight independent slices ordered by dependency rather than feature complexity. Collections had to ship first — they are the isolation boundary that every other slice depends on. Ingest and processing came next (a source cannot be searched until it is chunked and embedded). Search and Q&A built on top of processing. Sessions and briefs were parallel to the core ingestion path and could run alongside it.

Within each slice the architecture follows the same discipline: a repository that owns all SQL, a service that owns all orchestration logic, and a router (or worker, for the background processing slice) that handles the transport layer. Cross-slice imports are limited to contracts — frozen dataclasses and Protocol definitions exported explicitly. No slice imports from another slice's service or repository. This made it possible to build and test each slice in isolation before wiring them together.

The background processing slice (chunking and embedding) was the highest-risk component and got the most careful design work: the embedding dimension is derived from the configured model at worker startup rather than hardcoded, the dimension is validated against the actual Postgres column before any chunks are written, and failures are recorded on the source row itself so they are visible through the browse API rather than buried in logs.

## Architecture

The API is a FastAPI application backed by PostgreSQL with the pgvector extension. Ingest is split into two phases: a synchronous HTTP call that accepts a URL or file upload, stores uploaded bytes in S3, and enqueues a background job; and an arq worker that fetches content, chunks it with a token-aware sliding window, embeds each chunk via LiteLLM, and writes the vectors into Postgres. Search embeds the query inline (a single ~100ms LLM call) and executes a pgvector cosine nearest-neighbour query filtered to ready sources only. Q&A retrieves the top-k chunks, builds a numbered-source prompt, and streams the LLM completion token-by-token over WebSocket with citations extracted from inline `[N]` markers. Both search and Q&A are collection-scoped at the SQL level — no Python-layer guard can accidentally be skipped. Redis caches repeated search queries and serves as the arq job broker.

**Components:**

| Module | Role |
|--------|------|
| `scout_api.collections` | Create, list, and delete named knowledge partitions — the isolation boundary for all other resources |
| `scout_api.sources` | Accept URL and file uploads into a collection; manage the `pending → processing → ready → failed` source lifecycle |
| `scout_api.sources` (worker) | Background arq worker: fetch content, token-aware chunking with overlap, LiteLLM embedding, pgvector storage |
| `scout_api.search` | Semantic search scoped to a collection: embed query, pgvector cosine NN, Redis cache with event-driven invalidation |
| `scout_api.qa` | Grounded Q&A over a collection: retrieve chunks, build prompt, stream LiteLLM completion with source citations over WebSocket |
| `scout_api.sessions` | Research workspaces that record an activity trail of searches and questions against one collection |
| `scout_api.briefs` | Save answers as durable, named artefacts within a session |

## Key Decisions

**No ORM — raw asyncpg throughout.** pgvector queries require casting query embeddings to the `vector` type (`$1::vector`) and using the `<=>` cosine distance operator directly in SQL. ORMs that generate SQL do not support this syntax cleanly. asyncpg gives full control over the query string without introducing an abstraction that would have to be worked around on every vector query. The tradeoff is more boilerplate in repositories; the payoff is readable, debuggable SQL for every operation.

**Embedding dimension derived from the model, not hardcoded.** The worker probes the configured embedding model at startup with a single test call and reads the dimension from the response. It then validates that dimension against the actual `chunks.embedding` column in Postgres and fails fast with a clear error on mismatch. Hardcoding 1536 (OpenAI's dimension) would silently insert garbage vectors when switching to a local Ollama model at 768 dimensions. The probe-and-validate pattern catches the mismatch before any production data is written.

**Collection scope enforced in SQL, not Python.** Every query that touches chunks — search, Q&A retrieval — joins through `sources` and filters on `sources.collection_id` in the WHERE clause. The collection_id intentionally does not exist on the chunks table: chunks inherit their scope through their source. This means the isolation guarantee holds even if a future change to the service layer forgets to pass a collection filter — the SQL always enforces it.

**Cache invalidation via domain events, not TTL alone.** The search cache uses a 5-minute TTL by default, but also invalidates immediately when the `source.ready` event fires after a source finishes processing. TTL-only means a freshly indexed source is invisible for up to 5 minutes. Event-driven invalidation makes the cache eventually consistent with database state as soon as new content is available — which matters when an agent ingests a document and immediately expects to search it.

**WebSocket over SSE for Q&A streaming.** The Q&A endpoint streams LLM completions token-by-token over a WebSocket connection. Server-Sent Events would have worked for this slice's requirements, but WebSocket is bidirectional and leaves the door open for follow-up questions in the same connection without a protocol migration. FastAPI ships WebSocket natively, so there is no additional dependency. The final frame carries the extracted citations after the full answer text has accumulated.

## Features

Built to production standards: 91% test coverage, strict type checking (mypy), zero lint violations, automated security scanning.

- Collections API — create, list, and delete named knowledge partitions with cascade deletion to all owned sources and chunks
- URL ingest — POST a URL to a collection; the source is created `pending` and processing is enqueued asynchronously
- File ingest — POST a file upload; bytes stored in S3, processing job enqueued, response returns immediately
- Idempotent re-ingest — posting the same origin twice refreshes the source in place (re-chunks, re-embeds) without creating a duplicate
- Background processing — arq worker fetches content, applies token-aware sliding-window chunking (tiktoken, configurable size and overlap), embeds via LiteLLM, stores vectors in Postgres
- Source lifecycle visibility — browse sources and their status (`pending`, `processing`, `ready`, `failed`) including failure reasons on the source row itself
- Semantic search — POST free text to a collection; get back ranked chunks with relevance scores, Redis-cached per query with event-driven invalidation
- Grounded Q&A — ask a question over a collection; get a streamed, cited answer grounded only in indexed content — LLM states when context is insufficient rather than fabricating
- Research sessions — open a workspace against a collection; searches and questions are optionally recorded into a session activity trail
- Saved briefs — save any answer as a named, durable brief within a session for later reference
- MCP interface — `ingest_source`, `search_collection`, and `ask_collection` tools available to AI agents via the Model Context Protocol
- Pluggable embedding models — configure any LiteLLM-compatible model (OpenAI, Ollama, etc.); dimension is derived and validated at startup

## Tech Stack

| Technology | Role | Why |
|------------|------|-----|
| FastAPI | HTTP framework | Async-native, Pydantic v2 validation, WebSocket support, OpenAPI docs out of the box |
| asyncpg | Postgres client | Direct async Postgres access without ORM; required for clean pgvector query syntax |
| pgvector | Vector similarity search | Cosine nearest-neighbour on `chunks.embedding`; IVFFlat index for production-scale queries |
| LiteLLM | Embedding and completion | Unified interface over OpenAI, Ollama, and any other provider; swappable without code changes |
| arq + Redis | Background job queue | Async Python job queue; Redis doubles as both the arq broker and the search result cache |
| tiktoken | Token-aware chunking | Encodes text into model tokens for sliding-window chunking with overlap; falls back to `cl100k_base` for non-OpenAI models |
| S3 (aioboto3) | File storage | Stores uploaded file bytes before processing; origin recorded on the source row |
| structlog | Structured logging | JSON log lines with consistent fields (source_id, collection_id, outcome) across all modules |
| OpenTelemetry | Distributed tracing and metrics | OTel spans on all service methods; Prometheus metrics; Jaeger-compatible OTLP export |
| tenacity + circuitbreaker | Resilience | Retry with exponential backoff and circuit breaker on all external calls (S3, arq, LLM) |
| pydantic-settings | Configuration | Typed settings loaded from environment; single `Settings` object shared across all modules |

## Quick Start

**Requirements:** Docker, Docker Compose

```bash
# 1. Clone
git clone https://github.com/aukern/scout-api.git
cd scout-api

# 2. Configure
cp .env.example .env
# Edit .env — fill in required values (see table below)

# 3. Start
docker compose --profile postgres --profile redis up

# 4. Verify
curl http://localhost:8000/health/ready
# → {"status": "healthy"}
```

## What I'd Improve

**Token-aware chunking for non-text content.** The current worker extracts text from HTML by stripping tags and decodes binary formats as UTF-8 with `errors="replace"`. A production pipeline needs format-aware extractors for PDF, DOCX, and other binary formats — something like `unstructured` or `pypdf`. The chunker interface is already abstracted behind `AbstractFetchAdapter` so plugging in a real extractor is a contained change.

**Source retry policy.** Failed sources currently stay in `failed` status with zero automatic retry. The failure reason is recorded on the source row, which makes diagnosis easy, but recovery requires manually re-ingesting the source. A future slice should add a reaper job that detects sources stuck in `processing` (worker crash mid-run) and re-queues them, and optionally add configurable arq-level retries for transient embedding failures.

**Embedding model migration path.** Switching embedding models requires dropping and recreating the `chunks.embedding` column with the new dimension, then re-processing all sources. The migration file and `.env.example` document this, and the worker validates the dimension at startup. But the re-processing step is manual. An admin endpoint or CLI command that re-queues all `ready` sources for a given collection would make model upgrades operationally safe.

## License

MIT — see [LICENSE](LICENSE).
