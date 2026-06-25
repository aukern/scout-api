"""SearchRepository — pgvector cosine nearest-neighbour query.

Single SQL query joins chunks and sources, filters to collection scope and
status='ready', and returns results ordered by cosine similarity descending.

The collection isolation guarantee (ADR 0001) is enforced at the SQL level:
    WHERE s.collection_id = $1 AND s.status = 'ready'

This means the guarantee holds regardless of changes to the service layer.
"""

from __future__ import annotations

import asyncpg
import structlog

from scout_api.search.contracts import SearchResult

logger = structlog.get_logger(__name__)

# SQL that executes the pgvector cosine NN search.
# <=> is the cosine distance operator from pgvector.
# score = 1 - distance converts to similarity (1.0 = identical).
_SEARCH_SQL = """
SELECT
    c.id          AS chunk_id,
    c.source_id,
    c.content,
    s.collection_id,
    s.origin      AS source_origin,
    1 - (c.embedding <=> $2::vector) AS score
FROM chunks c
JOIN sources s ON s.id = c.source_id
WHERE s.collection_id = $1
  AND s.status = 'ready'
ORDER BY c.embedding <=> $2::vector
LIMIT $3
"""

_COLLECTION_EXISTS_SQL = """
SELECT EXISTS(SELECT 1 FROM collections WHERE id = $1)
"""


class SearchRepository:
    """Executes semantic search against the database via pgvector.

    Args:
        conn: An asyncpg connection. The repository borrows the connection
              and does not manage its lifecycle.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def search(
        self,
        collection_id: int,
        query_embedding: list[float],
        top_k: int,
    ) -> list[SearchResult]:
        """Execute a cosine-similarity nearest-neighbour query.

        Args:
            collection_id: Scope the search to this collection.
            query_embedding: Float vector from the embedding model.
            top_k: Maximum number of results to return (1–100).

        Returns:
            List of SearchResult ordered by descending similarity score.
        """
        # Format the embedding as a pgvector literal: '[x,y,z]'
        vector_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"

        rows = await self._conn.fetch(
            _SEARCH_SQL,
            collection_id,
            vector_literal,
            top_k,
        )

        results = [
            SearchResult(
                chunk_id=row["chunk_id"],
                source_id=row["source_id"],
                collection_id=row["collection_id"],
                content=row["content"],
                score=float(row["score"]),
                source_origin=row["source_origin"],
            )
            for row in rows
        ]

        logger.info(
            "search.repository.search",
            collection_id=collection_id,
            top_k=top_k,
            returned=len(results),
        )
        return results

    async def collection_exists(self, collection_id: int) -> bool:
        """Return True if the collection is present in the database.

        Args:
            collection_id: PK to check.

        Returns:
            True if the collection exists, False otherwise.
        """
        row = await self._conn.fetchrow(_COLLECTION_EXISTS_SQL, collection_id)
        exists: bool = row["exists"] if row else False
        return exists
