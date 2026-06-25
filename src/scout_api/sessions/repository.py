"""SQL repositories for the sessions domain.

SessionRepository   — CRUD for the sessions table.
SessionActivityRepository — records and reads session_activity rows.

Both repositories accept an asyncpg.Connection. The caller (router or
test) owns the connection lifecycle. This avoids nested acquire() calls
and makes transaction control explicit.

Resilience: all DB calls are wrapped in asyncio.timeout(5) to prevent
unbounded waits on a slow or saturated Postgres instance.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import asyncpg
import structlog
from opentelemetry import trace

from scout_api.observability import observed
from scout_api.sessions.contracts import SessionActivityRow, SessionRow
from scout_api.sessions.errors import SessionCollectionNotFoundError

logger: structlog.BoundLogger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


# ---------------------------------------------------------------------------
# SessionRepository
# ---------------------------------------------------------------------------


class SessionRepository:
    """Executes SQL queries against the sessions table.

    Args:
        conn: An asyncpg connection (or pool — pool.fetchrow() works too).
    """

    def __init__(self, conn: asyncpg.Connection | asyncpg.Pool) -> None:
        self._conn = conn

    @observed("sessions.open")  # type: ignore[untyped-decorator]
    async def open(self, collection_id: int, conn: asyncpg.Connection) -> SessionRow:
        """Open (INSERT) a new session scoped to the given collection.

        Verifies that the collection exists before inserting. If not, raises
        SessionCollectionNotFoundError (SES_NF_002).

        Args:
            collection_id: The collection to scope the session to.
            conn: The asyncpg connection to use (overrides self._conn).

        Returns:
            The newly created SessionRow.

        Raises:
            SessionCollectionNotFoundError: If collection_id does not exist.
        """
        with tracer.start_as_current_span("session.open") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "INSERT")
            span.set_attribute("collection.id", collection_id)

            # Verify the collection exists — FK would also catch it, but we
            # want the specific SES_NF_002 error code rather than a generic
            # FK violation.
            async with asyncio.timeout(5):
                exists = await conn.fetchrow(
                    "SELECT 1 FROM collections WHERE id = $1",
                    collection_id,
                )

            if exists is None:
                err = SessionCollectionNotFoundError(collection_id)
                span.record_exception(err)
                span.set_status(trace.StatusCode.ERROR, str(err))
                raise err

            async with asyncio.timeout(5):
                row = await conn.fetchrow(
                    "INSERT INTO sessions (collection_id) VALUES ($1) "
                    "RETURNING id, collection_id, created_at",
                    collection_id,
                )

            result = SessionRow(
                id=row["id"],
                collection_id=row["collection_id"],
                created_at=row["created_at"],
            )
            span.set_attribute("session.id", result.id)
            logger.info(
                "session.opened",
                session_id=result.id,
                collection_id=collection_id,
            )
            return result

    @observed("sessions.get")  # type: ignore[untyped-decorator]
    async def get(self, session_id: int, conn: asyncpg.Connection) -> SessionRow | None:
        """Fetch a session row by id.

        Args:
            session_id: The id to look up.
            conn: The asyncpg connection to use.

        Returns:
            SessionRow if found, None otherwise.
        """
        with tracer.start_as_current_span("session.get") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            span.set_attribute("session.id", session_id)

            async with asyncio.timeout(5):
                row = await conn.fetchrow(
                    "SELECT id, collection_id, created_at FROM sessions WHERE id = $1",
                    session_id,
                )

            if row is None:
                span.set_attribute("session.found", False)
                return None

            span.set_attribute("session.found", True)
            return SessionRow(
                id=row["id"],
                collection_id=row["collection_id"],
                created_at=row["created_at"],
            )

    @observed("sessions.list_all")  # type: ignore[untyped-decorator]
    async def list_all(
        self,
        collection_id: int | None,
        conn: asyncpg.Connection,
    ) -> list[SessionRow]:
        """Return all sessions, optionally filtered by collection_id.

        Args:
            collection_id: If provided, only sessions for this collection.
            conn: The asyncpg connection to use.

        Returns:
            List of SessionRow objects ordered by created_at ascending.
        """
        with tracer.start_as_current_span("session.list") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            if collection_id is not None:
                span.set_attribute("collection.id", collection_id)
                async with asyncio.timeout(5):
                    rows = await conn.fetch(
                        "SELECT id, collection_id, created_at FROM sessions "
                        "WHERE collection_id = $1 ORDER BY created_at ASC",
                        collection_id,
                    )
            else:
                async with asyncio.timeout(5):
                    rows = await conn.fetch(
                        "SELECT id, collection_id, created_at FROM sessions ORDER BY created_at ASC"
                    )

            result = [
                SessionRow(
                    id=r["id"],
                    collection_id=r["collection_id"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
            span.set_attribute("sessions.count", len(result))
            return result

    @observed("sessions.delete")  # type: ignore[untyped-decorator]
    async def delete(self, session_id: int, conn: asyncpg.Connection) -> bool:
        """Delete a session row by id.

        ON DELETE CASCADE on session_activity removes activity rows automatically.

        Args:
            session_id: The session to delete.
            conn: The asyncpg connection to use.

        Returns:
            True if deleted; False if the session was not found.
        """
        with tracer.start_as_current_span("session.delete") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "DELETE")
            span.set_attribute("session.id", session_id)

            async with asyncio.timeout(5):
                result = await conn.execute(
                    "DELETE FROM sessions WHERE id = $1",
                    session_id,
                )

            deleted = int(result.split()[-1]) > 0
            span.set_attribute("session.deleted", deleted)
            if deleted:
                logger.info("session.closed", session_id=session_id)
            return deleted


# ---------------------------------------------------------------------------
# SessionActivityRepository
# ---------------------------------------------------------------------------


class SessionActivityRepository:
    """Records and reads session_activity rows.

    Called by slices 5 and 6 when a session_id is present in their request.
    The caller passes the connection — this repository does not acquire its own.
    """

    @observed("sessions.activity.record")  # type: ignore[untyped-decorator]
    async def record(
        self,
        session_id: int,
        kind: Literal["search", "question"],
        query: str,
        output: str | None,
        conn: asyncpg.Connection,
    ) -> SessionActivityRow:
        """Insert a new activity row into session_activity.

        Args:
            session_id: The session to record into.
            kind: 'search' or 'question'.
            query: The search query or question text.
            output: Serialised results / answer text; None if unavailable.
            conn: asyncpg connection (caller owns the transaction).

        Returns:
            The newly created SessionActivityRow.
        """
        with tracer.start_as_current_span("session.record") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "INSERT")
            span.set_attribute("session.id", session_id)
            span.set_attribute("activity.kind", kind)

            async with asyncio.timeout(5):
                row = await conn.fetchrow(
                    "INSERT INTO session_activity "
                    "  (session_id, kind, query, output) "
                    "VALUES ($1, $2, $3, $4) "
                    "RETURNING id, session_id, kind, query, output, created_at",
                    session_id,
                    kind,
                    query,
                    output,
                )

            result = SessionActivityRow(
                id=row["id"],
                session_id=row["session_id"],
                kind=row["kind"],
                query=row["query"],
                output=row["output"],
                created_at=row["created_at"],
            )
            span.set_attribute("activity.id", result.id)
            return result

    @observed("sessions.activity.list_for_session")  # type: ignore[untyped-decorator]
    async def list_for_session(
        self,
        session_id: int,
        conn: asyncpg.Connection,
    ) -> list[SessionActivityRow]:
        """Return all activity rows for a session, oldest first.

        Args:
            session_id: The session to query.
            conn: asyncpg connection.

        Returns:
            List of SessionActivityRow ordered by created_at ascending.
        """
        with tracer.start_as_current_span("session.list_activity") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            span.set_attribute("session.id", session_id)

            async with asyncio.timeout(5):
                rows = await conn.fetch(
                    "SELECT id, session_id, kind, query, output, created_at "
                    "FROM session_activity "
                    "WHERE session_id = $1 "
                    "ORDER BY created_at ASC",
                    session_id,
                )

            result = [
                SessionActivityRow(
                    id=r["id"],
                    session_id=r["session_id"],
                    kind=r["kind"],
                    query=r["query"],
                    output=r["output"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
            span.set_attribute("activity.count", len(result))
            return result
