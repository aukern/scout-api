"""Pydantic request/response models for the search HTTP layer.

These models handle validation and serialization at the API boundary.
Domain types (SearchResult, SearchQuery) are defined in contracts.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    """Request body for POST /collections/{collection_id}/search.

    Attributes:
        query: Free-text search query. Required; 1–2000 characters.
        top_k: Maximum results to return. Optional; default 10, range 1–100.
        session_id: Optional session to record this search into.
    """

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Free-text search query.",
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of results to return (1–100).",
    )
    session_id: int | None = Field(
        default=None,
        description="Optional session ID to record this search into.",
    )


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class SearchResultItem(BaseModel):
    """A single ranked result in the search response.

    Attributes:
        chunk_id: PK of the matching chunk.
        source_id: FK to the owning source.
        source_origin: URL or s3 path of the owning source.
        content: Raw text content of the chunk.
        score: Cosine similarity score in [0, 1].
    """

    chunk_id: int
    source_id: int
    source_origin: str
    content: str
    score: float


class SearchResponse(BaseModel):
    """Response body for POST /collections/{collection_id}/search.

    Attributes:
        results: Ranked list of matching chunks.
        total: Total number of results returned (len(results)).
        collection_id: Echo of the queried collection.
        query: Echo of the original query text.
        cached: True if the results were served from the Redis cache.
    """

    results: list[SearchResultItem]
    total: int
    collection_id: int
    query: str
    cached: bool
