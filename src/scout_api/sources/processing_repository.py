"""Worker-side repository for source processing operations.

Handles the status transitions and chunk persistence that the arq worker
needs. This is separate from ``SourceRepository`` which handles ingest-side
operations (upsert, get_by_origin). Both live in the ``sources`` module.

Design decisions:
  - ``ProcessingRepository`` owns set_processing / set_ready / set_failed /
    insert_chunk. ``SourceRepository`` keeps upsert / get_by_origin / delete_chunks.
  - The split keeps each class testable in isolation and avoids a single
    repository class with too many concerns.
  - ``failed_reason`` is written to a column on ``sources``. This makes failure
    diagnosis observable without log grepping — added in migration 003.

All writes use the provided asyncpg connection or pool. The worker creates its
own pool via ``worker_startup``; the repository receives it via ``ctx``.
"""

from __future__ import annotations

import asyncpg
from opentelemetry import trace

from scout_api.sources.contracts import SourceRow, SourceStatus

tracer = trace.get_tracer(__name__)


class ProcessingRepository:
    """Executes worker-side SQL against sources and chunks.

    Args:
        conn: An asyncpg connection or pool.
    """

    def __init__(self, conn: asyncpg.Connection | asyncpg.Pool) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Source reads
    # ------------------------------------------------------------------

    async def get_source(self, source_id: int) -> SourceRow | None:
        """Fetch a Source by primary key.

        Args:
            source_id: The source primary key.

        Returns:
            SourceRow or None if not found.
        """
        with tracer.start_as_current_span("processing.db.get_source") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            span.set_attribute("source_id", source_id)
            row = await self._conn.fetchrow(
                """
                SELECT id, collection_id, origin, status, created_at, updated_at,
                       failed_reason
                FROM sources
                WHERE id = $1
                """,
                source_id,
            )
            if row is None:
                return None
            return self._row_to_source(row)

    # ------------------------------------------------------------------
    # Source status transitions
    # ------------------------------------------------------------------

    async def set_processing(self, source_id: int) -> SourceRow:
        """Transition source to ``processing``.

        Args:
            source_id: The source to update.

        Returns:
            Updated SourceRow with status=PROCESSING.

        Raises:
            RuntimeError: If the source does not exist.
        """
        with tracer.start_as_current_span("processing.db.set_processing") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "UPDATE")
            span.set_attribute("source_id", source_id)
            row = await self._conn.fetchrow(
                """
                UPDATE sources
                SET status = 'processing', updated_at = NOW(), failed_reason = NULL
                WHERE id = $1
                RETURNING id, collection_id, origin, status, created_at, updated_at,
                          failed_reason
                """,
                source_id,
            )
            if row is None:
                raise RuntimeError(f"Source {source_id} not found — cannot set processing.")
            return self._row_to_source(row)

    async def set_ready(self, source_id: int) -> SourceRow:
        """Transition source to ``ready``.

        Args:
            source_id: The source to mark ready.

        Returns:
            Updated SourceRow with status=READY.

        Raises:
            RuntimeError: If the source does not exist.
        """
        with tracer.start_as_current_span("processing.db.set_ready") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "UPDATE")
            span.set_attribute("source_id", source_id)
            row = await self._conn.fetchrow(
                """
                UPDATE sources
                SET status = 'ready', updated_at = NOW()
                WHERE id = $1
                RETURNING id, collection_id, origin, status, created_at, updated_at,
                          failed_reason
                """,
                source_id,
            )
            if row is None:
                raise RuntimeError(f"Source {source_id} not found — cannot set ready.")
            return self._row_to_source(row)

    async def set_failed(self, source_id: int, reason: str) -> SourceRow:
        """Transition source to ``failed`` and record the failure reason.

        Args:
            source_id: The source to mark failed.
            reason: Human-readable failure reason stored in ``failed_reason``.

        Returns:
            Updated SourceRow with status=FAILED and failed_reason set.

        Raises:
            RuntimeError: If the source does not exist.
        """
        with tracer.start_as_current_span("processing.db.set_failed") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "UPDATE")
            span.set_attribute("source_id", source_id)
            row = await self._conn.fetchrow(
                """
                UPDATE sources
                SET status = 'failed', updated_at = NOW(), failed_reason = $2
                WHERE id = $1
                RETURNING id, collection_id, origin, status, created_at, updated_at,
                          failed_reason
                """,
                source_id,
                reason,
            )
            if row is None:
                raise RuntimeError(f"Source {source_id} not found — cannot set failed.")
            return self._row_to_source(row)

    # ------------------------------------------------------------------
    # Chunk operations
    # ------------------------------------------------------------------

    async def delete_chunks(self, source_id: int) -> int:
        """Delete all chunks belonging to a source.

        Called at the start of (re-)processing to ensure a clean slate.

        Args:
            source_id: The source whose chunks should be removed.

        Returns:
            Number of rows deleted.
        """
        with tracer.start_as_current_span("processing.db.delete_chunks") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "DELETE")
            span.set_attribute("source_id", source_id)
            result = await self._conn.execute(
                "DELETE FROM chunks WHERE source_id = $1",
                source_id,
            )
            deleted = int(result.split()[-1])
            span.set_attribute("chunks_deleted", deleted)
            return deleted

    async def insert_chunk(
        self,
        source_id: int,
        content: str,
        position: int,
        embedding: list[float],
    ) -> int:
        """Insert a single chunk with its embedding.

        Args:
            source_id: The owning source.
            content: Text content of the chunk.
            position: 0-based position index within the source.
            embedding: Float vector from the embedding model.

        Returns:
            The new chunk primary key (id).
        """
        with tracer.start_as_current_span("processing.db.insert_chunk") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "INSERT")
            span.set_attribute("source_id", source_id)
            span.set_attribute("position", position)
            # pgvector accepts Python lists via asyncpg when the type is registered.
            # We cast to the vector type explicitly via ::vector for safety.
            row = await self._conn.fetchrow(
                """
                INSERT INTO chunks (source_id, content, position, embedding)
                VALUES ($1, $2, $3, $4::vector)
                RETURNING id
                """,
                source_id,
                content,
                position,
                str(embedding),
            )
            chunk_id: int = row["id"]
            span.set_attribute("chunk_id", chunk_id)
            return chunk_id

    async def get_chunk_count(self, source_id: int) -> int:
        """Count the number of chunks for a source.

        Used by the worker to report chunk_count in logs and events.

        Args:
            source_id: The source to count chunks for.

        Returns:
            Number of chunks.
        """
        row = await self._conn.fetchrow(
            "SELECT COUNT(*) AS n FROM chunks WHERE source_id = $1",
            source_id,
        )
        return int(row["n"])

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_source(row: asyncpg.Record) -> SourceRow:
        """Map an asyncpg Record to a SourceRow."""
        return SourceRow(
            id=row["id"],
            collection_id=row["collection_id"],
            origin=row["origin"],
            status=SourceStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            failed_reason=row["failed_reason"],
        )
