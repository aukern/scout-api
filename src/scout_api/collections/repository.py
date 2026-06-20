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

from scout_api.collections.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
)


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

    async def create(self, name: str) -> CollectionRow:
        """Insert a new collection and return the created row.

        Args:
            name: Unique name for the collection.

        Returns:
            The newly created CollectionRow with id and name.

        Raises:
            CollectionAlreadyExistsError: If a collection with this name exists.
        """
        try:
            row = await self._conn.fetchrow(
                "INSERT INTO collections (name) VALUES ($1) RETURNING id, name",
                name,
            )
        except asyncpg.UniqueViolationError as exc:
            raise CollectionAlreadyExistsError(name) from exc

        return CollectionRow(id=row["id"], name=row["name"])

    async def list_all(self) -> list[CollectionRow]:
        """Return all collections ordered by creation time (id ascending).

        Returns:
            List of CollectionRow objects, oldest first.
        """
        rows = await self._conn.fetch("SELECT id, name FROM collections ORDER BY id ASC")
        return [CollectionRow(id=r["id"], name=r["name"]) for r in rows]

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
        result = await self._conn.execute(
            "DELETE FROM collections WHERE name = $1",
            name,
        )
        # asyncpg returns "DELETE N" — N is the row count
        deleted_count = int(result.split()[-1])
        if deleted_count == 0:
            raise CollectionNotFoundError(name)

    async def exists(self, name: str) -> bool:
        """Check whether a collection with the given name exists.

        Args:
            name: Collection name to look up.

        Returns:
            True if the collection exists, False otherwise.
        """
        row = await self._conn.fetchrow(
            "SELECT 1 FROM collections WHERE name = $1 LIMIT 1",
            name,
        )
        return row is not None
