"""QARepository — retrieves Chunks and checks collection existence.

Executes the same pgvector cosine nearest-neighbour query as SearchRepository
but scoped for the QA module. The QA slice owns its own repository to avoid
service-layer cross-slice dependencies and keep the module independently testable.

The collection isolation guarantee (ADR 0001) is enforced at the SQL level:
    WHERE s.collection_id = $1 AND s.status = 'ready'

This means the guarantee holds regardless of changes to the service layer.
"""

from __future__ import annotations

import asyncpg
import structlog

from scout_api.search.contracts import SearchResult

logger = structlog.get_logger(__name__)

# Identical SQL to SearchRepository — see search/repository.py.
# Duplicated intentionally to keep QA independently testable without
# depending on the search service layer.
_RETRIEVE_SQL = """
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


class QARepository:
    """Data access layer for the QA module.

    Retrieves Chunks from the database using pgvector cosine similarity and
    verifies Collection existence. Borrows the asyncpg connection — does not
    manage its lifecycle.

    Args:
        conn: An asyncpg connection (or pool).
    """

    def __init__(self, conn: asyncpg.Connection | asyncpg.Pool) -> None:
        self._conn = conn

    async def retrieve_chunks(
        self,
        collection_id: int,
        query_embedding: list[float],
        top_k: int,
    ) -> list[SearchResult]:
        """Retrieve top_k chunks most similar to the query embedding.

        Args:
            collection_id: Scope the retrieval to this collection.
            query_embedding: Float vector from the embedding model.
            top_k: Maximum number of chunks to return.

        Returns:
            List of SearchResult ordered by descending cosine similarity.
        """
        vector_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"

        rows = await self._conn.fetch(
            _RETRIEVE_SQL,
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
            "qa.repository.retrieve_chunks",
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
