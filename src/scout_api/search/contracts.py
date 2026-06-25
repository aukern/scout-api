"""Published contracts for the search domain.

Public types imported by the HTTP router, MCP layer, and any future slices
that need to depend on search results without coupling to the implementation.

Importing from contracts.py guarantees stability — the internal implementation
modules (repository.py, service.py, cache.py) may change without affecting callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# ---------------------------------------------------------------------------
# Domain value: SearchResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchResult:
    """A single ranked result from a semantic search query.

    Attributes:
        chunk_id: PK of the matching chunk row.
        source_id: FK to the owning source.
        collection_id: Collection scope — always equal to the queried collection.
        content: Raw text content of the chunk.
        score: Cosine similarity in [0, 1]; higher means more relevant.
        source_origin: URL or s3 path of the owning source.
    """

    chunk_id: int
    source_id: int
    collection_id: int
    content: str
    score: float
    source_origin: str


# ---------------------------------------------------------------------------
# Domain value: SearchQuery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchQuery:
    """Validated parameters for a semantic search request.

    Attributes:
        collection_id: Collection to search within.
        query_text: Free-text query from the caller.
        top_k: Maximum number of results to return (1–100, default 10).
    """

    collection_id: int
    query_text: str
    top_k: int = field(default=10)


# ---------------------------------------------------------------------------
# Repository Protocol
# ---------------------------------------------------------------------------


class SearchRepositoryProtocol(Protocol):
    """Protocol describing the SearchRepository interface.

    Other slices that depend on search persistence should depend on this
    Protocol rather than the concrete SearchRepository, enabling test doubles
    and keeping the import boundary clean.
    """

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
            top_k: Maximum number of results to return.

        Returns:
            List of SearchResult ordered by descending similarity score.
        """
        ...

    async def collection_exists(self, collection_id: int) -> bool:
        """Return True if the collection is present in the database.

        Args:
            collection_id: PK to check.

        Returns:
            True if the collection exists, False otherwise.
        """
        ...
