"""Domain errors for the sessions module.

Error code registry:
  SES_NF_001  Session not found                           HTTP 404
  SES_NF_002  Collection not found when opening session   HTTP 404
"""

from __future__ import annotations

from scout_api.errors import ScoutError


class SessionNotFoundError(ScoutError):
    """Raised when a session_id does not match any row in the sessions table.

    Args:
        session_id: The id that was not found.
    """

    def __init__(self, session_id: int) -> None:
        super().__init__(
            message=f"Session {session_id} not found.",
            code="SES_NF_001",
            status_code=404,
        )
        self.session_id = session_id


class SessionCollectionNotFoundError(ScoutError):
    """Raised when opening a session against a collection_id that does not exist.

    Args:
        collection_id: The id that was not found.
    """

    def __init__(self, collection_id: int) -> None:
        super().__init__(
            message=f"Collection {collection_id} not found.",
            code="SES_NF_002",
            status_code=404,
        )
        self.collection_id = collection_id
