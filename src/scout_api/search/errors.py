"""Domain-specific errors for the search module.

Error codes:
    SEARCH_COL_001 — Collection not found (HTTP 404)
    SEARCH_EMB_001 — Embedding model call failed (HTTP 502)
    SEARCH_VAL_001 — Query text empty or too long (HTTP 422)
    SEARCH_VAL_002 — top_k out of range 1–100 (HTTP 422)
"""

from __future__ import annotations

from scout_api.errors import ScoutError


class CollectionNotFoundForSearchError(ScoutError):
    """The requested collection does not exist.

    Args:
        collection_id: The collection PK that was not found.
    """

    def __init__(self, collection_id: int) -> None:
        super().__init__(
            message=f"Collection {collection_id} not found.",
            code="SEARCH_COL_001",
            status_code=404,
        )
        self.collection_id = collection_id


class SearchEmbeddingError(ScoutError):
    """The embedding model call failed.

    Args:
        detail: Low-level error detail for logging (not surfaced to callers).
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            message="Failed to embed the search query. The embedding model may be unavailable.",
            code="SEARCH_EMB_001",
            status_code=502,
        )
        self.detail = detail
