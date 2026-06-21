"""Domain error codes for the sources module.

Error code prefix: SRC

SRC_NF_001  — Collection not found
SRC_ING_001 — Source ingestion failed (S3 or queue error)
SRC_VAL_001 — Invalid origin (not a valid URL or empty filename)
"""

from __future__ import annotations

from scout_api.errors import ScoutError


class CollectionNotFoundError(ScoutError):
    """Raised when the target collection does not exist."""

    def __init__(self, collection_id: int) -> None:
        super().__init__(
            message=f"Collection {collection_id} not found.",
            code="SRC_NF_001",
            status_code=404,
        )


class SourceIngestionError(ScoutError):
    """Raised when storage upload or job enqueue fails."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            message=f"Source ingestion failed: {detail}",
            code="SRC_ING_001",
            status_code=500,
        )


class InvalidOriginError(ScoutError):
    """Raised when the provided URL or filename is invalid or empty."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            message=f"Invalid origin: {detail}",
            code="SRC_VAL_001",
            status_code=422,
        )
