"""Domain errors for the QA module.

All errors extend ScoutError so callers get a consistent (message, code,
status_code) contract. The WebSocket router converts these to JSON error
frames rather than HTTP 4xx/5xx responses.

Error codes:
    QA_COL_001 — Collection not found (or no longer present).
    QA_CTX_001 — Retrieval returned no ready chunks; context insufficient.
    QA_SYN_001 — LLM synthesis failed (network, auth, timeout).
    QA_VAL_001 — Question text empty or exceeds 4000 characters.
"""

from __future__ import annotations

from scout_api.errors import ScoutError


class QACollectionNotFoundError(ScoutError):
    """Raised when the target Collection does not exist.

    WebSocket callers receive:
        {"type": "error", "code": "QA_COL_001", "message": "..."}
    """

    def __init__(self, collection_id: int) -> None:
        super().__init__(
            message=f"Collection {collection_id} not found",
            code="QA_COL_001",
            status_code=404,
        )
        self.collection_id = collection_id


class QANoContextError(ScoutError):
    """Raised when retrieval returns no ready chunks for the question.

    This happens when the collection has no ready sources, or when all
    chunks score below the cosine-similarity threshold (no results returned).

    WebSocket callers receive:
        {"type": "error", "code": "QA_CTX_001", "message": "..."}
    """

    def __init__(self, collection_id: int) -> None:
        super().__init__(
            message=(
                f"No ready context available in collection {collection_id}. "
                "Ensure sources have finished processing before asking a question."
            ),
            code="QA_CTX_001",
            status_code=422,
        )
        self.collection_id = collection_id


class QASynthesisError(ScoutError):
    """Raised when the LLM completion call fails.

    Wraps network errors, authentication failures, and model timeouts from
    the LiteLLM layer.

    WebSocket callers receive:
        {"type": "error", "code": "QA_SYN_001", "message": "..."}
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            message=f"LLM synthesis failed: {detail}",
            code="QA_SYN_001",
            status_code=502,
        )
        self.detail = detail


class QAValidationError(ScoutError):
    """Raised when the question text fails basic validation.

    Causes:
        - Empty question text.
        - Question text exceeds 4000 characters.

    WebSocket callers receive a close(4000) instead of an error frame.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            message=f"Invalid question: {detail}",
            code="QA_VAL_001",
            status_code=400,
        )
        self.detail = detail
