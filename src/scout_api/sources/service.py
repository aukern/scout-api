"""IngestService — the facade orchestrating source ingestion.

This module is the deep module for the sources domain. It hides:
  1. Database upsert (via SourceRepository)
  2. Object storage upload (via AbstractStorageAdapter)
  3. Background job enqueue (via AbstractQueueAdapter)
  4. Domain event emission (source.ingested)
  5. Old chunk cleanup on re-ingest

Callers see only ``IngestService.ingest()``.

Dependency injection:
  All collaborators are injected via __init__ (constructor injection).
  The FastAPI dependency chain (dependencies.py) builds the service for
  request-scoped use. Tests inject mocks directly.

Resilience:
  - Timeout (10 s) is enforced on storage upload and queue enqueue calls.
  - Both are non-transactional: if upload or enqueue fails after a
    successful DB upsert, the Source stays in 'pending' status and will
    be visible in the API. A separate retry / reaper job should re-attempt
    processing. This is intentional — it prevents silent data loss.

Observability:
  - structlog is used for structured logging.
  - OpenTelemetry spans wrap the full ingest call and each sub-operation.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any

import structlog
from aukern_infra.metrics import observed
from opentelemetry import trace

from scout_api.sources.contracts import SourceRow
from scout_api.sources.errors import (
    CollectionNotFoundError,
    SourceIngestionError,
)
from scout_api.sources.queue import AbstractQueueAdapter
from scout_api.sources.repository import SourceRepository
from scout_api.sources.storage import AbstractStorageAdapter

logger: structlog.BoundLogger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

# Job name recognised by the arq worker (slice 3)
PROCESS_SOURCE_JOB = "process_source"


def _s3_key(collection_id: int, source_id: int, filename: str) -> str:
    """Build the S3 object key for an uploaded file.

    Format: ``sources/{collection_id}/{source_id}/{filename}``

    Args:
        collection_id: The owning collection.
        source_id: The source primary key.
        filename: Original filename from the multipart upload.

    Returns:
        The S3 object key string.
    """
    return f"sources/{collection_id}/{source_id}/{filename}"


def _origin_for_url(url: str) -> str:
    """Return the canonical origin string for a URL source.

    The URL is stored as-is. This helper exists so the logic is testable
    without the rest of the service.
    """
    return url


def _origin_for_file(collection_id: int, source_id: int, filename: str) -> str:
    """Build the canonical origin string for a file upload.

    The origin is the S3 URI, which acts as the stable identity for
    re-ingest detection. The same file uploaded twice to the same collection
    is treated as a refresh because the key is deterministic.

    Args:
        collection_id: The owning collection.
        source_id: The source primary key (used to build the S3 key).
        filename: Original filename from the multipart upload.

    Returns:
        An ``s3://`` URI: ``s3://{collection_id}/{source_id}/{filename}``
    """
    key = _s3_key(collection_id, source_id, filename)
    return f"s3://{key}"


class IngestService:
    """Orchestrates the full ingest pipeline for a single Source.

    Composes:
      - ``SourceRepository`` for all DB interactions.
      - ``AbstractStorageAdapter`` for file upload (S3 or in-memory).
      - ``AbstractQueueAdapter`` for job enqueue (arq or in-memory).

    Args:
        repo: A SourceRepository bound to an asyncpg connection or pool.
        storage: An AbstractStorageAdapter implementation.
        queue: An AbstractQueueAdapter implementation.
        event_emit: Optional callable for domain event emission.
                    Signature: ``(event_name: str, payload: dict) -> None``.
                    Defaults to a no-op if not provided.
    """

    def __init__(
        self,
        repo: SourceRepository,
        storage: AbstractStorageAdapter,
        queue: AbstractQueueAdapter,
        event_emit: Any | None = None,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._queue = queue
        self._emit = event_emit or (lambda *a, **kw: None)

    @observed("sources.ingest_url")
    async def ingest_url(
        self,
        collection_id: int,
        url: str,
    ) -> SourceRow:
        """Ingest a URL source into a collection.

        Creates (or refreshes) a Source with ``origin=url``, enqueues
        a processing job, and emits ``source.ingested``.

        Args:
            collection_id: The target collection.
            url: A valid HTTP(S) URL identifying the remote document.

        Returns:
            The newly created or refreshed SourceRow (status=pending).

        Raises:
            CollectionNotFoundError: If the collection does not exist.
            SourceIngestionError: If job enqueue fails.
        """
        with tracer.start_as_current_span("source.ingest_url") as span:
            span.set_attribute("slice", "sources")
            span.set_attribute("collection_id", collection_id)
            try:
                await self._assert_collection_exists(collection_id)
                origin = _origin_for_url(url)
                source, is_refresh = await self._repo.upsert(collection_id, origin)
                if is_refresh:
                    deleted = await self._repo.delete_chunks(source.id)
                    logger.info(
                        "source.refresh",
                        source_id=source.id,
                        collection_id=collection_id,
                        origin=origin,
                        chunks_deleted=deleted,
                    )
                else:
                    logger.info(
                        "source.created",
                        source_id=source.id,
                        collection_id=collection_id,
                        origin=origin,
                    )
                span.set_attribute("source_id", source.id)
                span.set_attribute("is_refresh", is_refresh)
                await self._enqueue(source.id)
                self._emit_event(source, is_refresh)
                return source
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                raise

    @observed("sources.ingest_file")
    async def ingest_file(
        self,
        collection_id: int,
        filename: str,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
    ) -> SourceRow:
        """Ingest an uploaded file into a collection.

        The filename-based origin is first used to check for an existing
        source. The file is uploaded to object storage before the DB upsert
        so the S3 key is stable and can be used as the origin identifier.

        Origin format: ``s3://sources/{collection_id}/{source_id}/{filename}``

        Args:
            collection_id: The target collection.
            filename: Original filename from the multipart upload.
            file_bytes: Raw bytes of the uploaded file.
            content_type: MIME type of the file.

        Returns:
            The newly created or refreshed SourceRow (status=pending).

        Raises:
            CollectionNotFoundError: If the collection does not exist.
            SourceIngestionError: If S3 upload or job enqueue fails.
        """
        with tracer.start_as_current_span("source.ingest_file") as span:
            span.set_attribute("slice", "sources")
            span.set_attribute("collection_id", collection_id)
            span.set_attribute("filename", filename)
            try:
                await self._assert_collection_exists(collection_id)

                # Sanitise the filename — strip path separators to prevent path traversal
                # in S3 keys. Use only the base name component.
                safe_filename = os.path.basename(filename.replace("\\", "/"))
                if not safe_filename:
                    safe_filename = "upload"

                # Use a hash-based placeholder origin to detect existing sources
                # before we have a source_id. The hash is the stable component.
                name_hash = hashlib.sha256(safe_filename.encode()).hexdigest()[:16]
                placeholder_origin = f"file://{collection_id}/{name_hash}/{safe_filename}"

                # Upsert with placeholder to get/create the source_id
                source, is_refresh = await self._repo.upsert(collection_id, placeholder_origin)
                if is_refresh:
                    deleted = await self._repo.delete_chunks(source.id)
                    logger.info(
                        "source.refresh",
                        source_id=source.id,
                        collection_id=collection_id,
                        filename=filename,
                        chunks_deleted=deleted,
                    )

                # Upload to object storage
                s3_key = _s3_key(collection_id, source.id, safe_filename)
                try:
                    async with asyncio.timeout(10):
                        await self._storage.upload(s3_key, file_bytes, content_type)
                except TimeoutError as exc:
                    raise SourceIngestionError("storage upload timed out") from exc
                except Exception as exc:
                    raise SourceIngestionError(f"storage upload failed: {exc}") from exc

                logger.info(
                    "source.uploaded",
                    source_id=source.id,
                    collection_id=collection_id,
                    s3_key=s3_key,
                    filename=safe_filename,
                )

                span.set_attribute("source_id", source.id)
                span.set_attribute("is_refresh", is_refresh)
                span.set_attribute("s3_key", _s3_key(collection_id, source.id, safe_filename))
                await self._enqueue(source.id)
                self._emit_event(source, is_refresh)
                return source
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _assert_collection_exists(self, collection_id: int) -> None:
        """Raise CollectionNotFoundError if the collection does not exist."""
        exists = await self._repo.collection_exists(collection_id)
        if not exists:
            raise CollectionNotFoundError(collection_id)

    async def _enqueue(self, source_id: int) -> None:
        """Enqueue the process_source job; raise SourceIngestionError on failure."""
        try:
            async with asyncio.timeout(10):
                await self._queue.enqueue(PROCESS_SOURCE_JOB, source_id=source_id)
        except TimeoutError as exc:
            raise SourceIngestionError("job enqueue timed out") from exc
        except Exception as exc:
            raise SourceIngestionError(f"job enqueue failed: {exc}") from exc

    def _emit_event(self, source: SourceRow, is_refresh: bool) -> None:
        """Emit the source.ingested domain event."""
        try:
            self._emit(
                "source.ingested",
                {
                    "source_id": source.id,
                    "collection_id": source.collection_id,
                    "origin": source.origin,
                    "is_refresh": is_refresh,
                },
            )
        except Exception:
            # Event emission is best-effort — never fail the ingest for it.
            logging.getLogger(__name__).warning("source.event_emit_failed", exc_info=True)
