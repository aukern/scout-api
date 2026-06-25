"""SQL repository for the collections domain.

All database interactions for collections live here. No business logic —
just parameterized queries and result mapping.

Duplicate-name detection uses the unique constraint on collections.name
(UNIQUE NOT NULL) and maps asyncpg.UniqueViolationError to
CollectionAlreadyExistsError. This avoids a separate existence check +
insert race condition.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
from opentelemetry import trace

from scout_api.collections.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
)
from scout_api.observability import observed

tracer = trace.get_tracer(__name__)


@dataclass
class CollectionRow:
    """Lightweight dataclass mapping a row from the collections table."""

    id: int
    name: str


class CollectionRepository:
    """Executes SQL queries against the collections table.

    Args:
        conn: An asyncpg connection or pool to execute queries against.
    """

    def __init__(self, conn: asyncpg.Connection | asyncpg.Pool) -> None:
        self._conn = conn

    @observed("collections.create")  # type: ignore[untyped-decorator]
    async def create(self, name: str) -> CollectionRow:
        """Insert a new collection and return the created row.

        Args:
            name: Unique name for the collection.

        Returns:
            The newly created CollectionRow with id and name.

        Raises:
            CollectionAlreadyExistsError: If a collection with this name exists.
        """
        with tracer.start_as_current_span("collection.db.create") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "INSERT")
            span.set_attribute("db.table", "collections")
            span.set_attribute("collection.name", name)
            try:
                row = await self._conn.fetchrow(
                    "INSERT INTO collections (name) VALUES ($1) RETURNING id, name",
                    name,
                )
            except asyncpg.UniqueViolationError as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                raise CollectionAlreadyExistsError(name) from exc
            span.set_attribute("collection.id", row["id"])
            return CollectionRow(id=row["id"], name=row["name"])

    @observed("collections.list_all")  # type: ignore[untyped-decorator]
    async def list_all(self) -> list[CollectionRow]:
        """Return all collections ordered by creation time (id ascending).

        Returns:
            List of CollectionRow objects, oldest first.
        """
        with tracer.start_as_current_span("collection.db.list_all") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            span.set_attribute("db.table", "collections")
            rows = await self._conn.fetch("SELECT id, name FROM collections ORDER BY id ASC")
            result = [CollectionRow(id=r["id"], name=r["name"]) for r in rows]
            span.set_attribute("collections.count", len(result))
            return result

    @observed("collections.delete")  # type: ignore[untyped-decorator]
    async def delete(self, name: str) -> None:
        """Delete a collection by name, cascading to its Sources and Chunks.

        The CASCADE is enforced by foreign key constraints in the schema:
        sources.collection_id references collections.id ON DELETE CASCADE.
        Chunks cascade from sources.

        Args:
            name: Name of the collection to delete.

        Raises:
            CollectionNotFoundError: If no collection with this name exists.
        """
        with tracer.start_as_current_span("collection.db.delete") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "DELETE")
            span.set_attribute("db.table", "collections")
            span.set_attribute("collection.name", name)
            result = await self._conn.execute(
                "DELETE FROM collections WHERE name = $1",
                name,
            )
            # asyncpg returns "DELETE N" — N is the row count
            deleted_count = int(result.split()[-1])
            if deleted_count == 0:
                err = CollectionNotFoundError(name)
                span.record_exception(err)
                span.set_status(trace.StatusCode.ERROR, str(err))
                raise err

    @observed("collections.exists")  # type: ignore[untyped-decorator]
    async def exists(self, name: str) -> bool:
        """Check whether a collection with the given name exists.

        Args:
            name: Collection name to look up.

        Returns:
            True if the collection exists, False otherwise.
        """
        with tracer.start_as_current_span("collection.db.exists") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            span.set_attribute("db.table", "collections")
            span.set_attribute("collection.name", name)
            row = await self._conn.fetchrow(
                "SELECT 1 FROM collections WHERE name = $1 LIMIT 1",
                name,
            )
            found = row is not None
            span.set_attribute("collection.exists", found)
            return found
