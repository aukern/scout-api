"""SQL repository for the sources domain.

All database interactions for sources live here. No business logic —
just parameterised queries and result mapping.

The repository is a plain class that receives an asyncpg connection or pool.
It follows the same pattern as CollectionRepository in the collections module.

Upsert semantics:
  - INSERT ... ON CONFLICT (collection_id, origin) DO UPDATE resets status to
    'pending' and bumps updated_at on refresh.
  - Returns (SourceRow, is_refresh: bool). is_refresh=True means the Source
    already existed; the caller should delete old chunks and re-enqueue.

Chunk deletion:
  - DELETE FROM chunks WHERE source_id = $1 to clean up before re-processing.
  - Returns the number of rows deleted.
"""

from __future__ import annotations

import asyncpg
from opentelemetry import trace

from scout_api.sources.contracts import SourceRow, SourceStatus

tracer = trace.get_tracer(__name__)


class SourceRepository:
    """Executes SQL queries against the sources (and chunks) tables.

    Args:
        conn: An asyncpg connection or pool to execute queries against.
    """

    def __init__(self, conn: asyncpg.Connection | asyncpg.Pool) -> None:
        self._conn = conn

    async def collection_exists(self, collection_id: int) -> bool:
        """Return True if a collection with this id exists.

        Args:
            collection_id: The collection primary key to check.

        Returns:
            True if the collection exists, False otherwise.
        """
        with tracer.start_as_current_span("source.db.collection_exists") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            span.set_attribute("db.table", "collections")
            span.set_attribute("collection_id", collection_id)
            row = await self._conn.fetchrow(
                "SELECT 1 FROM collections WHERE id = $1 LIMIT 1",
                collection_id,
            )
            return row is not None

    async def get_by_origin(
        self,
        collection_id: int,
        origin: str,
    ) -> SourceRow | None:
        """Look up a Source by its unique (collection_id, origin) key.

        Args:
            collection_id: The owning collection.
            origin: The URL or S3 key that identifies the source.

        Returns:
            SourceRow if found, None otherwise.
        """
        with tracer.start_as_current_span("source.db.get_by_origin") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            span.set_attribute("db.table", "sources")
            span.set_attribute("collection_id", collection_id)
            row = await self._conn.fetchrow(
                """
                SELECT id, collection_id, origin, status, created_at, updated_at
                FROM sources
                WHERE collection_id = $1 AND origin = $2
                """,
                collection_id,
                origin,
            )
            if row is None:
                return None
            return self._row_to_source(row)

    async def upsert(
        self,
        collection_id: int,
        origin: str,
    ) -> tuple[SourceRow, bool]:
        """Insert a new Source or refresh an existing one in-place.

        On conflict (collection_id, origin already exists):
          - status is reset to 'pending'
          - updated_at is set to NOW()

        Args:
            collection_id: The owning collection.
            origin: URL or S3 key that uniquely identifies this source.

        Returns:
            A tuple of (SourceRow, is_refresh) where is_refresh=True means
            the source already existed and was refreshed.
        """
        with tracer.start_as_current_span("source.db.upsert") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "INSERT ... ON CONFLICT DO UPDATE")
            span.set_attribute("db.table", "sources")
            span.set_attribute("collection_id", collection_id)
            # We detect refresh via xmax: xmax=0 means a fresh INSERT, non-zero means UPDATE.
            row = await self._conn.fetchrow(
                """
                INSERT INTO sources (collection_id, origin, status, created_at, updated_at)
                VALUES ($1, $2, 'pending', NOW(), NOW())
                ON CONFLICT (collection_id, origin) DO UPDATE
                    SET status     = 'pending',
                        updated_at = NOW()
                RETURNING id, collection_id, origin, status, created_at, updated_at,
                          (xmax <> 0) AS was_updated
                """,
                collection_id,
                origin,
            )
            is_refresh = bool(row["was_updated"])
            span.set_attribute("is_refresh", is_refresh)
            return self._row_to_source(row), is_refresh

    async def delete_chunks(self, source_id: int) -> int:
        """Delete all chunks belonging to a source.

        Called during re-ingest to remove stale chunks before re-processing.

        Args:
            source_id: The source whose chunks should be removed.

        Returns:
            The number of chunk rows deleted.
        """
        with tracer.start_as_current_span("source.db.delete_chunks") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "DELETE")
            span.set_attribute("db.table", "chunks")
            span.set_attribute("source_id", source_id)
            result = await self._conn.execute(
                "DELETE FROM chunks WHERE source_id = $1",
                source_id,
            )
            # asyncpg returns "DELETE N" — extract the count
            deleted = int(result.split()[-1])
            span.set_attribute("chunks_deleted", deleted)
            return deleted

    async def list_by_collection(self, collection_id: int) -> list[SourceRow]:
        """Return all sources in a collection, ordered oldest-first.

        Args:
            collection_id: The owning collection primary key.

        Returns:
            A list of SourceRow snapshots, ordered by created_at ASC.
            Returns an empty list if the collection has no sources.
        """
        with tracer.start_as_current_span("source.db.list_by_collection") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            span.set_attribute("db.table", "sources")
            span.set_attribute("collection_id", collection_id)
            rows = await self._conn.fetch(
                """
                SELECT id, collection_id, origin, status,
                       created_at, updated_at, failed_reason
                FROM sources
                WHERE collection_id = $1
                ORDER BY created_at ASC
                """,
                collection_id,
            )
            span.set_attribute("count", len(rows))
            return [self._row_to_source(row) for row in rows]

    async def get_by_id(
        self,
        source_id: int,
        collection_id: int,
    ) -> SourceRow | None:
        """Fetch a single source by primary key, scoped to a collection.

        The ``collection_id`` filter is enforced at the SQL level so that a
        source belonging to collection 5 returns ``None`` when fetched via
        collection 7 — preventing cross-collection data leakage.

        Args:
            source_id: The source primary key.
            collection_id: The collection this source must belong to.

        Returns:
            SourceRow if found in the given collection, None otherwise.
        """
        with tracer.start_as_current_span("source.db.get_by_id") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            span.set_attribute("db.table", "sources")
            span.set_attribute("source_id", source_id)
            span.set_attribute("collection_id", collection_id)
            row = await self._conn.fetchrow(
                """
                SELECT id, collection_id, origin, status,
                       created_at, updated_at, failed_reason
                FROM sources
                WHERE id = $1 AND collection_id = $2
                """,
                source_id,
                collection_id,
            )
            if row is None:
                return None
            return self._row_to_source(row)

    @staticmethod
    def _row_to_source(row: asyncpg.Record) -> SourceRow:
        """Map an asyncpg Record to a SourceRow dataclass.

        Handles both legacy rows (no failed_reason column) and current rows.
        """
        # Use .get() style access — some callers pass rows without failed_reason
        # (e.g. upsert RETURNING clause which does not select it).
        try:
            failed_reason: str | None = row["failed_reason"]
        except (KeyError, IndexError):
            failed_reason = None

        return SourceRow(
            id=row["id"],
            collection_id=row["collection_id"],
            origin=row["origin"],
            status=SourceStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            failed_reason=failed_reason,
        )
