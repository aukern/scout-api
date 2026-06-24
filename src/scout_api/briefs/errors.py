"""Domain errors for the briefs module.

Error code registry:
  BRF_NF_001  Session not found (when saving or listing Briefs)   HTTP 404
  BRF_NF_002  Brief not found (reserved for future GET by id)     HTTP 404
"""

from __future__ import annotations

from scout_api.errors import ScoutError


class BriefSessionNotFoundError(ScoutError):
    """Raised when a session_id does not exist when saving or listing Briefs.

    Args:
        session_id: The id that was not found.
    """

    def __init__(self, session_id: int) -> None:
        super().__init__(
            message=f"Session {session_id} not found.",
            code="BRF_NF_001",
            status_code=404,
        )
        self.session_id = session_id


class BriefNotFoundError(ScoutError):
    """Reserved for future GET /sessions/{session_id}/briefs/{brief_id}.

    Not raised in this slice — defined to keep the error code registry
    consistent so a future endpoint can use BRF_NF_002 without a new entry.

    Args:
        brief_id: The id that was not found.
    """

    def __init__(self, brief_id: int) -> None:
        super().__init__(
            message=f"Brief {brief_id} not found.",
            code="BRF_NF_002",
            status_code=404,
        )
        self.brief_id = brief_id
