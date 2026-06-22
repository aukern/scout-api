"""Public contracts for the sessions domain.

These are the types and protocols exported by this slice. Slices 5, 6, and 8
import from here — never from repository.py or router.py directly.

Design decisions:
- SessionRow and SessionActivityRow are frozen dataclasses, not ORM models.
  They are returned from the repository and passed up to the HTTP layer.
  Immutability prevents accidental mutation across layers.
- SessionRepositoryProtocol and SessionActivityRepositoryProtocol allow
  slices 5 and 6 to depend on the abstract interface, not the implementation.
  This keeps the session module as the sole owner of the SQL.
- 'output' is nullable: a Search records results (JSON string) or None if
  serialisation failed; a Question records the answer text or None if no
  answer was generated yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

import asyncpg

# ---------------------------------------------------------------------------
# Domain value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRow:
    """Represents a row from the sessions table.

    Args:
        id: Surrogate primary key.
        collection_id: FK to the collection this session is scoped to.
        created_at: UTC timestamp when the session was opened.
    """

    id: int
    collection_id: int
    created_at: datetime


@dataclass(frozen=True)
class SessionActivityRow:
    """Represents a row from the session_activity table.

    Args:
        id: Surrogate primary key.
        session_id: FK to the parent session.
        kind: 'search' or 'question'.
        query: The search query string or question text.
        output: Serialised search results (JSON) or answer text; nullable.
        created_at: UTC timestamp when the activity was recorded.
    """

    id: int
    session_id: int
    kind: Literal["search", "question"]
    query: str
    output: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Repository protocols (for dependency injection by slices 5, 6, 8)
# ---------------------------------------------------------------------------


class SessionRepositoryProtocol(Protocol):
    """Interface for session CRUD operations."""

    async def open(
        self,
        collection_id: int,
        conn: asyncpg.Connection,
    ) -> SessionRow:
        """Open (create) a new session against the given collection.

        Args:
            collection_id: The collection this session is scoped to.
            conn: An asyncpg connection.

        Returns:
            The newly created SessionRow.

        Raises:
            SessionCollectionNotFoundError: If collection_id does not exist.
        """
        ...

    async def get(
        self,
        session_id: int,
        conn: asyncpg.Connection,
    ) -> SessionRow | None:
        """Fetch a session by id.

        Args:
            session_id: The session to look up.
            conn: An asyncpg connection.

        Returns:
            The SessionRow, or None if not found.
        """
        ...

    async def list_all(
        self,
        collection_id: int | None,
        conn: asyncpg.Connection,
    ) -> list[SessionRow]:
        """Return all sessions, optionally filtered by collection.

        Args:
            collection_id: If provided, only sessions for this collection.
            conn: An asyncpg connection.

        Returns:
            List of SessionRow objects, ordered by creation time ascending.
        """
        ...

    async def delete(
        self,
        session_id: int,
        conn: asyncpg.Connection,
    ) -> bool:
        """Delete a session by id.

        ON DELETE CASCADE removes all activity rows automatically.

        Args:
            session_id: The session to delete.
            conn: An asyncpg connection.

        Returns:
            True if the session existed and was deleted; False if not found.
        """
        ...


class SessionActivityRepositoryProtocol(Protocol):
    """Interface for recording and reading session activity.

    Called by slices 5 and 6 when a session_id is present in the request.
    Called by slice 8 (briefs) for activity retrieval.
    """

    async def record(
        self,
        session_id: int,
        kind: Literal["search", "question"],
        query: str,
        output: str | None,
        conn: asyncpg.Connection,
    ) -> SessionActivityRow:
        """Record a search or question in the session's activity trail.

        Args:
            session_id: The session to record into.
            kind: 'search' or 'question'.
            query: The search query or question text.
            output: Serialised results / answer; None if not yet available.
            conn: An asyncpg connection (caller owns the transaction).

        Returns:
            The newly created SessionActivityRow.
        """
        ...

    async def list_for_session(
        self,
        session_id: int,
        conn: asyncpg.Connection,
    ) -> list[SessionActivityRow]:
        """Return all activity rows for a session, oldest first.

        Args:
            session_id: The session to query.
            conn: An asyncpg connection.

        Returns:
            List of SessionActivityRow, ordered by created_at ascending.
        """
        ...
