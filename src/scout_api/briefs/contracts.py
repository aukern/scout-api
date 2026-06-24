"""Public contracts for the briefs domain.

These are the types and protocols exported by this slice. Future slices
(e.g. Brief retrieval or deletion) import from here — never from
repository.py or router.py directly.

Design decisions:
- BriefRow and BriefCitation are frozen dataclasses, not ORM models.
  Immutability prevents accidental mutation across layers.
- BriefCitation is a value object — it carries source_id, optional
  chunk_id, and optional excerpt to link back to the originating Source
  and Chunk without embedding the full chunk text in the Brief.
- BriefRepositoryProtocol allows future slices to depend on the abstract
  interface, keeping the briefs module as the sole owner of the SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import asyncpg

# ---------------------------------------------------------------------------
# Domain value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BriefCitation:
    """A single citation inside a Brief — links back to a Source (and optionally a Chunk).

    Args:
        source_id: FK to the source that supports this claim. Always required.
        chunk_id: FK to the specific chunk, if available.
        excerpt: The relevant text fragment from the chunk, if available.
    """

    source_id: int
    chunk_id: int | None = None
    excerpt: str | None = None


@dataclass(frozen=True)
class BriefRow:
    """Represents a row from the briefs table.

    Args:
        id: Surrogate primary key.
        session_id: FK to the session this brief belongs to.
        answer_text: The Answer body saved as a durable Brief.
        citations: Ordered list of citations; empty list when none were provided.
        created_at: UTC timestamp when the Brief was saved.
    """

    id: int
    session_id: int
    answer_text: str
    citations: list[BriefCitation] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Repository protocol (for dependency injection by future slices)
# ---------------------------------------------------------------------------


class BriefRepositoryProtocol(Protocol):
    """Interface for Brief persistence operations."""

    async def save(
        self,
        session_id: int,
        answer_text: str,
        citations: list[BriefCitation],
        conn: asyncpg.Connection,
    ) -> BriefRow:
        """Save an Answer as a durable Brief in the given session.

        Args:
            session_id: The session to attach this Brief to.
            answer_text: The Answer body text.
            citations: Source references supporting the answer (may be empty).
            conn: An asyncpg connection.

        Returns:
            The newly created BriefRow.

        Raises:
            BriefSessionNotFoundError: If session_id does not exist.
        """
        ...

    async def list_for_session(
        self,
        session_id: int,
        conn: asyncpg.Connection,
    ) -> list[BriefRow]:
        """Return all Briefs for a session, oldest first.

        Args:
            session_id: The session to list Briefs for.
            conn: An asyncpg connection.

        Returns:
            List of BriefRow objects ordered by created_at ascending.

        Raises:
            BriefSessionNotFoundError: If session_id does not exist.
        """
        ...
