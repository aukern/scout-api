"""Domain error codes for the sources module.

Error code prefix: SRC

SRC_NF_001   — Collection not found
SRC_ING_001  — Source ingestion failed (S3 or queue error)
SRC_VAL_001  — Invalid origin (not a valid URL or empty filename)
SRC_PROC_001 — Source processing failed (embedding, fetch, or chunking error)
SRC_PROC_002 — Embedding dimension mismatch (model probe vs schema column)
SRC_PROC_003 — Source not found during processing (unexpected state)
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


class SourceProcessingError(ScoutError):
    """Raised when source processing fails (fetch, chunk, or embed step).

    The source status is set to ``failed`` when this is raised.
    """

    def __init__(self, source_id: int, detail: str) -> None:
        super().__init__(
            message=f"Processing failed for source {source_id}: {detail}",
            code="SRC_PROC_001",
            status_code=500,
        )


class EmbeddingDimensionMismatchError(ScoutError):
    """Raised when the model probe dimension does not match the schema column.

    This is a hard startup error — the worker cannot proceed without fixing
    the migration. See migrations/003_processing_columns.sql.
    """

    def __init__(self, probe_dim: int, schema_dim: int) -> None:
        super().__init__(
            message=(
                f"Embedding dimension mismatch: model produces {probe_dim}-dim vectors "
                f"but chunks.embedding column is vector({schema_dim}). "
                "Run migration 003_processing_columns.sql with the correct dimension."
            ),
            code="SRC_PROC_002",
            status_code=500,
        )


class SourceNotFoundError(ScoutError):
    """Raised when the worker cannot find the source to process."""

    def __init__(self, source_id: int) -> None:
        super().__init__(
            message=f"Source {source_id} not found — it may have been deleted.",
            code="SRC_PROC_003",
            status_code=404,
        )
