"""Domain error codes for the collections module.

Error codes follow the pattern: COLLECTION_{CONDITION}.
All errors use the standard scout-api envelope via ScoutError.
"""

from __future__ import annotations

from scout_api.errors import ScoutError


class CollectionAlreadyExistsError(ScoutError):
    """Raised when attempting to create a collection with a name already in use."""

    def __init__(self, name: str) -> None:
        super().__init__(
            message=f"A collection named '{name}' already exists.",
            code="COLLECTION_ALREADY_EXISTS",
            status_code=409,
        )


class CollectionNotFoundError(ScoutError):
    """Raised when a collection with the requested name does not exist."""

    def __init__(self, name: str) -> None:
        super().__init__(
            message=f"Collection '{name}' not found.",
            code="COLLECTION_NOT_FOUND",
            status_code=404,
        )
