"""Pydantic request/response schemas for the briefs HTTP layer.

These schemas are used exclusively by router.py and tests/briefs/test_router.py.
Domain logic uses BriefRow and BriefCitation from contracts.py, not these models.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CitationInput(BaseModel):
    """A single citation in a save-brief request.

    Args:
        source_id: FK to the Source that supports this claim. Required.
        chunk_id: FK to the specific Chunk, if known.
        excerpt: The relevant text fragment from the chunk, if available.
    """

    source_id: int
    chunk_id: int | None = None
    excerpt: str | None = None


class SaveBriefRequest(BaseModel):
    """Request body for POST /sessions/{session_id}/briefs.

    Args:
        answer_text: The Answer body to save as a durable Brief.
        citations: Source references supporting the answer. Optional — callers
                   that have no citations yet can omit this field.
    """

    answer_text: str = Field(..., min_length=1)
    citations: list[CitationInput] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class CitationResponse(BaseModel):
    """A single citation in a Brief response.

    Args:
        source_id: FK to the Source that supports this claim.
        chunk_id: FK to the specific Chunk, or null if not available.
        excerpt: The relevant text fragment, or null if not available.
    """

    source_id: int
    chunk_id: int | None = None
    excerpt: str | None = None


class BriefResponse(BaseModel):
    """Response schema for a single saved Brief.

    Args:
        id: Surrogate primary key.
        session_id: The session this Brief belongs to.
        answer_text: The saved Answer body.
        citations: Ordered list of citations; empty list when none were saved.
        created_at: UTC timestamp when the Brief was saved.
    """

    id: int
    session_id: int
    answer_text: str
    citations: list[CitationResponse]
    created_at: datetime


class ListBriefsResponse(BaseModel):
    """Response schema for GET /sessions/{session_id}/briefs.

    Args:
        briefs: Ordered list of Briefs (oldest first).
        total: Total number of Briefs in this session.
    """

    briefs: list[BriefResponse]
    total: int
