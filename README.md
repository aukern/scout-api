# scout-api

Knowledge ingestion, semantic search, and RAG Q&A backend.

Scout API is the tool layer for AI research agents. It accepts documents and URLs,
processes them in the background, stores embeddings, and exposes search and Q&A
endpoints that any agent can call.

---

## What it does

1. **Ingest** — POST a URL or file. It uploads to object storage and queues a background job.
2. **Process** — Background worker chunks the content, generates embeddings, stores in vector DB.
3. **Search** — Semantic search across everything ingested. Results are cached.
4. **Q&A** — RAG pipeline: retrieve relevant chunks → LLM chain → streamed answer with citations.
5. **Sessions** — Research sessions group queries and saved briefs, stored in PostgreSQL.

---

## Architecture

```
POST /ingest          → S3 upload → ARQ job queue
ARQ worker            → chunk → embed (litellm) → pgvector
GET /search?q=...     → pgvector ANN → Redis cache → response
POST /qa              → pgvector retrieval → LangChain RAG → WebSocket stream
CRUD /sessions        → PostgreSQL
```

---

## Tech stack

- FastAPI + asyncpg + PostgreSQL
- pgvector for embeddings
- Redis for search result caching
- ARQ for background jobs (ingestion, embedding)
- S3-compatible object storage (MinIO in dev, R2/S3 in prod)
- LiteLLM for embedding generation
- LangChain for the RAG pipeline
- WebSocket for streaming Q&A responses
- structlog + OpenTelemetry + Prometheus

---

## Part of the Scout project

This is the **tool server**. It is intentionally decoupled from any specific agent.
Any agent that needs knowledge ingestion and search can wire to this API.

Related: [scout-agent](https://github.com/aukern/scout-agent) — the AI research agent that uses this API as its primary tool.

---

## Status

Under construction — built with the [Aukern Engineering Pipeline](https://github.com/aukern/Aukern-Skills).
