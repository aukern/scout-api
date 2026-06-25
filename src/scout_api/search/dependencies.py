"""FastAPI dependency providers for the search domain.

Wires up SearchService with its collaborators:
  - SearchRepository (using the asyncpg pool from app.state)
  - SearchCache (using a Redis client from app.state)
  - Embedder (from settings)

The dependency chain:
  get_search_service
    └─ get_pool(request)            (from scout_api.db)
    └─ get_redis(request)           (from app.state.redis, if configured)
    └─ settings                     (for embedding model and cache TTL)

For testing, override these providers via:
  app.dependency_overrides[get_redis_client] = lambda: AsyncMock()
"""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import Depends, Request

from scout_api.config import get_settings
from scout_api.db import get_pool
from scout_api.search.cache import SearchCache
from scout_api.search.repository import SearchRepository
from scout_api.search.service import SearchService
from scout_api.sources.embedder import Embedder


def get_redis_client(request: Request) -> Any:
    """Return the async Redis client from app.state.

    In production, the lifespan should create a redis.asyncio.Redis instance
    and store it on app.state.redis. If not present, raises RuntimeError.

    For tests, override via ``app.dependency_overrides[get_redis_client]``.

    Returns:
        An async Redis client.

    Raises:
        RuntimeError: If app.state.redis is not configured or redis not installed.
    """
    if hasattr(request.app.state, "redis"):
        return request.app.state.redis
    # Build a fresh client from settings (not cached — per-request).
    # This path is for development without a pre-built client on app.state.
    try:
        import redis.asyncio as aioredis  # noqa: PLC0415

        settings = get_settings()
        if not settings.redis_url:
            raise RuntimeError(
                "REDIS_URL is not configured. Set it in .env or as an environment variable."
            )
        return aioredis.from_url(settings.redis_url, decode_responses=True)
    except ImportError as exc:
        raise RuntimeError(
            "redis package is required for search caching. Install it with: pip install redis"
        ) from exc


def get_search_service(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
) -> SearchService:
    """Build and return a SearchService for a single request.

    The connection is acquired from the pool and injected into the repository.
    The Redis client comes from app.state or is built from settings.

    Args:
        request: FastAPI request (for pool and redis client access).
        pool: asyncpg pool from app.state.pool via get_pool.

    Returns:
        A fully-wired SearchService.
    """
    settings = get_settings()
    conn: asyncpg.Pool = request.app.state.pool
    redis_client = get_redis_client(request)

    repo = SearchRepository(conn)
    cache = SearchCache(redis=redis_client, ttl=settings.search_cache_ttl_seconds)
    embedder = Embedder(
        model=settings.embedding_model,
        api_base=settings.ollama_api_base,
    )

    return SearchService(repo=repo, cache=cache, embedder=embedder)
