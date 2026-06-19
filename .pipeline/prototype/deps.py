# .pipeline/prototype/deps.py
#
# External dependencies for the proof of concept.
# Separated from poc.py so auk-3 can detect infrastructure requirements by reading this file.
# auk-3 reads this file directly — keep all external system connections here, no business logic.
#
# Run (see docker-compose.yml for the externals):
#   docker compose -f .pipeline/prototype/docker-compose.yml up -d
#   docker compose -f .pipeline/prototype/docker-compose.yml exec ollama ollama pull nomic-embed-text
#   docker compose -f .pipeline/prototype/docker-compose.yml exec ollama ollama pull llama3.2
#   python -m venv .venv && . .venv/bin/activate
#   pip install -r .pipeline/prototype/requirements.txt
#   python .pipeline/prototype/poc.py
# Commit when FINDINGS.md is complete.

import os

# ── Database (Postgres + pgvector) ────────────────────────────────────────────
# Concept-critical: Sources, Chunks, Embeddings, Sessions, Briefs persist here, and
# pgvector's `<=>` cosine operator powers Collection-scoped semantic Search.
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://scout:scout@localhost:5432/scout_api_poc")


async def get_db():
    """Return a single asyncpg connection for POC testing.

    Requires the pgvector extension (the docker-compose image ships it):
    CREATE EXTENSION IF NOT EXISTS vector;
    """
    return await asyncpg.connect(DATABASE_URL)


def to_pgvector(embedding: list[float]) -> str:
    """Encode a float list into pgvector's text input format: '[0.1,0.2,...]'."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


# ── LLM / Embeddings (LiteLLM, provider-agnostic) ─────────────────────────────
# Concept-critical: embed() turns Chunks into Embeddings for semantic Search;
# complete() synthesizes an Answer from retrieved Chunks.
#
# Default is KEYLESS/LOCAL via Ollama (the docker-compose runs it) so the POC works
# out of the box. Swap to a hosted provider with ENV ONLY — never by editing logic:
#   EMBEDDING_MODEL=text-embedding-3-small COMPLETION_MODEL=gpt-4o-mini OPENAI_API_KEY=sk-...
#
# Do NOT hardcode the embedding dimension here or in the schema — it is coupled to the
# model (nomic-embed-text=768, text-embedding-3-small=1536). poc.py derives it at setup.
import litellm

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "ollama/nomic-embed-text")
COMPLETION_MODEL = os.environ.get("COMPLETION_MODEL", "ollama/llama3.2")
# litellm reads OLLAMA_API_BASE for the local server (compose maps it to localhost):
os.environ.setdefault("OLLAMA_API_BASE", os.environ.get("OLLAMA_API_BASE", "http://localhost:11434"))


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed one or more texts. Returns one vector per input text."""
    resp = await litellm.aembedding(model=EMBEDDING_MODEL, input=texts)
    return [item["embedding"] for item in resp.data]


async def complete(messages: list[dict]) -> str:
    """Single non-streamed completion. (Production streams the Answer — see WebSocket below.)"""
    resp = await litellm.acompletion(model=COMPLETION_MODEL, messages=messages)
    return resp.choices[0].message.content


# ── URL fetch ─────────────────────────────────────────────────────────────────
# A Source can originate from a URL. Fetching the page is mechanical, not the concept
# under test — the POC ingests text inline instead. Named for auk-3.
# import httpx
# async def fetch_url(url: str) -> str:
#     async with httpx.AsyncClient() as c:
#         return (await c.get(url, timeout=30)).text


# ── File storage ──────────────────────── PLUMBING — named for auk-3, not exercised
# Uploaded files (the other Source origin) land in S3-compatible storage (MinIO dev,
# R2/S3 prod) before processing. The POC skips upload and ingests content directly.
# import boto3
# S3_BUCKET = os.environ.get("S3_BUCKET", "scout-api-poc")
# s3 = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"))


# ── Cache / Queue ───────────────────────── PLUMBING — named for auk-3, not exercised
# Redis caches Search Results; ARQ runs ingestion/embedding as background jobs
# (Source status pending → processing → ready). The POC runs that work synchronously.
# import redis.asyncio as redis
# REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
# async def get_redis(): return await redis.from_url(REDIS_URL)


# ── Streaming ───────────────────────────── PLUMBING — named for auk-3, not exercised
# Answers stream to the caller over a WebSocket in production. The POC returns the whole
# Answer at once from complete() above.
