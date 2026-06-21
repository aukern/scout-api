"""FastAPI dependency providers for the sources domain.

Wires up IngestService with its collaborators:
  - SourceRepository (using the asyncpg pool from app.state)
  - AbstractStorageAdapter (S3 in production, InMemory in tests)
  - AbstractQueueAdapter (Arq in production, InMemory in tests)

The dependency chain:
  get_ingest_service
    └─ get_pool(request)            (from scout_api.db)
    └─ get_storage_adapter(request) (from app.state, if configured)
    └─ get_queue_adapter(request)   (from app.state, if configured)

For testing, override these providers via:
  app.dependency_overrides[get_storage_adapter] = lambda: InMemoryStorageAdapter()
  app.dependency_overrides[get_queue_adapter] = lambda: InMemoryQueueAdapter()
"""

from __future__ import annotations

import asyncpg
from fastapi import Depends, Request

from scout_api.config import get_settings
from scout_api.db import get_pool
from scout_api.sources.queue import AbstractQueueAdapter, ArqQueueAdapter, InMemoryQueueAdapter
from scout_api.sources.repository import SourceRepository
from scout_api.sources.service import IngestService
from scout_api.sources.storage import (
    AbstractStorageAdapter,
    InMemoryStorageAdapter,
    S3StorageAdapter,
)


def get_storage_adapter(request: Request) -> AbstractStorageAdapter:
    """Return the storage adapter from app.state, or build one from settings.

    In production, the lifespan should pre-build an S3StorageAdapter and store
    it on app.state.storage. If not present, one is built from settings on
    first call (not cached — builds per-request). For tests, override via
    ``app.dependency_overrides``.

    Returns:
        An AbstractStorageAdapter instance.
    """
    if hasattr(request.app.state, "storage"):
        return request.app.state.storage  # type: ignore[no-any-return]

    settings = get_settings()
    s3_bucket = getattr(settings, "s3_bucket_name", None)
    s3_region = getattr(settings, "s3_region", None)

    if s3_bucket and s3_region:
        return S3StorageAdapter(
            bucket=s3_bucket,
            region=s3_region,
            endpoint_url=getattr(settings, "s3_endpoint_url", None),
        )

    # Fall back to in-memory (suitable for local dev without S3 credentials)
    return InMemoryStorageAdapter()


def get_queue_adapter(request: Request) -> AbstractQueueAdapter:
    """Return the queue adapter from app.state, or build one from settings.

    In production, the lifespan should pre-build an ArqQueueAdapter and store
    it on app.state.queue. If not present, falls back to InMemoryQueueAdapter
    for safety (no silent enqueue failures during development).

    Returns:
        An AbstractQueueAdapter instance.
    """
    if hasattr(request.app.state, "queue"):
        return request.app.state.queue  # type: ignore[no-any-return]

    settings = get_settings()
    redis_url = getattr(settings, "redis_url", None)

    if redis_url:
        return ArqQueueAdapter(redis_url=redis_url)

    return InMemoryQueueAdapter()


async def get_ingest_service(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
    storage: AbstractStorageAdapter = Depends(get_storage_adapter),
    queue: AbstractQueueAdapter = Depends(get_queue_adapter),
) -> IngestService:
    """Build an IngestService for this request.

    Uses a connection acquired from the pool to create the SourceRepository.
    The service is constructed fresh per request (no shared mutable state).

    Args:
        request: The current FastAPI request (unused directly, but needed for
                 the pool dependency chain).
        pool: The asyncpg pool from app.state.
        storage: The storage adapter.
        queue: The queue adapter.

    Returns:
        An IngestService ready to handle the request.
    """
    repo = SourceRepository(pool)
    return IngestService(repo=repo, storage=storage, queue=queue)
