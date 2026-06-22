"""Pydantic request and response schemas for the sessions HTTP layer.

All schemas use snake_case field names. FastAPI serialises them as-is.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class OpenSessionRequest(BaseModel):
    """Body for POST /sessions.

    Args:
        collection_id: The collection this session is opened against.
    """

    collection_id: int = Field(
        ..., description="ID of the collection to open this session against."
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ActivityItem(BaseModel):
    """A single entry in a session's activity trail.

    Args:
        id: Surrogate primary key.
        kind: 'search' or 'question'.
        query: The query or question text.
        output: Serialised results (search) or answer text (question); null if not recorded.
        created_at: UTC timestamp when the activity was recorded.
    """

    id: int
    kind: Literal["search", "question"]
    query: str
    output: str | None = None
    created_at: datetime


class SessionResponse(BaseModel):
    """Returned by POST /sessions and GET /sessions (list item).

    Args:
        id: Session primary key.
        collection_id: The collection this session is scoped to.
        created_at: UTC timestamp when the session was opened.
    """

    id: int
    collection_id: int
    created_at: datetime


class SessionDetailResponse(BaseModel):
    """Returned by GET /sessions/{session_id}.

    Includes the full activity trail.

    Args:
        id: Session primary key.
        collection_id: The collection this session is scoped to.
        created_at: UTC timestamp when the session was opened.
        activity: All recorded activity, oldest first.
    """

    id: int
    collection_id: int
    created_at: datetime
    activity: list[ActivityItem] = Field(default_factory=list)


class ListSessionsResponse(BaseModel):
    """Returned by GET /sessions.

    Args:
        sessions: All sessions (optionally filtered).
        total: Count of returned sessions.
    """

    sessions: list[SessionResponse]
    total: int
