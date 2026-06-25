"""SQL repository for the briefs domain.

BriefRepository — save and list Briefs in a Session.

Accepts an asyncpg.Connection. The caller (router or test) owns the
connection lifecycle. This avoids nested acquire() calls and makes
transaction control explicit.

Resilience: all DB calls are wrapped in asyncio.timeout(5) to prevent
unbounded waits on a slow or saturated Postgres instance.

Citations are serialised to/from JSONB. asyncpg passes Python strings to
JSONB columns without a special codec — json.dumps() is sufficient on write;
json.loads() on read (asyncpg returns JSONB as a Python dict/list directly
for asyncpg >= 0.28).
"""

from __future__ import annotations

import asyncio
import json

import asyncpg
import structlog
from scout_api.observability import observed
from opentelemetry import trace

from scout_api.briefs.contracts import BriefCitation, BriefRow
from scout_api.briefs.errors import BriefSessionNotFoundError

logger: structlog.BoundLogger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


def _parse_citations(raw: list[dict[str, object]] | str | None) -> list[BriefCitation]:
    """Deserialise the JSONB citations column to a list of BriefCitation.

    asyncpg returns JSONB columns as Python objects (list/dict). When the
    column is NULL, raw is None; when it is an empty array, raw is [].

    Args:
        raw: The value returned by asyncpg for the citations column.

    Returns:
        List of BriefCitation; empty list when raw is None or [].
    """
    if not raw:
        return []
    # asyncpg >= 0.28 deserialises JSONB to Python automatically.
    # If it comes back as a string (older asyncpg or explicit TEXT cast), parse it.
    items: list[dict[str, object]]
    if isinstance(raw, str):
        items = json.loads(raw)
    else:
        items = raw
    result: list[BriefCitation] = []
    for item in items:
        source_id_raw = item["source_id"]
        chunk_id_raw = item.get("chunk_id")
        excerpt_raw = item.get("excerpt")
        result.append(
            BriefCitation(
                source_id=source_id_raw
                if isinstance(source_id_raw, int)
                else int(str(source_id_raw)),
                chunk_id=chunk_id_raw
                if isinstance(chunk_id_raw, int)
                else (int(str(chunk_id_raw)) if chunk_id_raw is not None else None),
                excerpt=excerpt_raw
                if isinstance(excerpt_raw, str)
                else (str(excerpt_raw) if excerpt_raw is not None else None),
            )
        )
    return result


def _serialise_citations(citations: list[BriefCitation]) -> str:
    """Serialise a list of BriefCitation to a JSON string for JSONB storage.

    Args:
        citations: The citations to serialise.

    Returns:
        JSON string suitable for a JSONB parameter placeholder.
    """
    return json.dumps(
        [
            {
                "source_id": c.source_id,
                **({"chunk_id": c.chunk_id} if c.chunk_id is not None else {}),
                **({"excerpt": c.excerpt} if c.excerpt is not None else {}),
            }
            for c in citations
        ]
    )


class BriefRepository:
    """Executes SQL queries against the briefs table.

    Designed to be instantiated per-request (no shared state beyond the class
    definition). The caller passes an asyncpg.Connection to each method.
    """

    @observed("briefs.save")  # type: ignore[untyped-decorator]
    async def save(
        self,
        session_id: int,
        answer_text: str,
        citations: list[BriefCitation],
        conn: asyncpg.Connection,
    ) -> BriefRow:
        """Save an Answer as a durable Brief in the given session.

        Verifies that the session exists before inserting. If not, raises
        BriefSessionNotFoundError (BRF_NF_001).

        Args:
            session_id: The session to attach this Brief to.
            answer_text: The Answer body text.
            citations: Source references (may be empty).
            conn: The asyncpg connection to use.

        Returns:
            The newly created BriefRow.

        Raises:
            BriefSessionNotFoundError: If session_id does not exist.
        """
        with tracer.start_as_current_span("brief.db.save") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "INSERT")
            span.set_attribute("session.id", session_id)

            # Verify session exists — gives precise BRF_NF_001 instead of FK violation.
            async with asyncio.timeout(5):
                exists = await conn.fetchrow(
                    "SELECT 1 FROM sessions WHERE id = $1",
                    session_id,
                )

            if exists is None:
                err = BriefSessionNotFoundError(session_id)
                span.record_exception(err)
                span.set_status(trace.StatusCode.ERROR, str(err))
                raise err

            citations_json = _serialise_citations(citations)

            async with asyncio.timeout(5):
                row = await conn.fetchrow(
                    "INSERT INTO briefs (session_id, answer_text, citations) "
                    "VALUES ($1, $2, $3::jsonb) "
                    "RETURNING id, session_id, answer_text, citations, created_at",
                    session_id,
                    answer_text,
                    citations_json,
                )

            result = BriefRow(
                id=row["id"],
                session_id=row["session_id"],
                answer_text=row["answer_text"],
                citations=_parse_citations(row["citations"]),
                created_at=row["created_at"],
            )
            span.set_attribute("brief.id", result.id)
            span.set_attribute("brief.citations_count", len(citations))
            logger.info(
                "brief.saved",
                session_id=session_id,
                brief_id=result.id,
                citations_count=len(citations),
            )
            return result

    @observed("briefs.list_for_session")  # type: ignore[untyped-decorator]
    async def list_for_session(
        self,
        session_id: int,
        conn: asyncpg.Connection,
    ) -> list[BriefRow]:
        """Return all Briefs for a session, oldest first.

        Verifies that the session exists before querying. If not, raises
        BriefSessionNotFoundError (BRF_NF_001).

        Args:
            session_id: The session whose Briefs to list.
            conn: The asyncpg connection to use.

        Returns:
            List of BriefRow ordered by created_at ascending.

        Raises:
            BriefSessionNotFoundError: If session_id does not exist.
        """
        with tracer.start_as_current_span("brief.db.list_for_session") as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.operation", "SELECT")
            span.set_attribute("session.id", session_id)

            # Verify session exists — surface BRF_NF_001 instead of an empty list.
            async with asyncio.timeout(5):
                exists = await conn.fetchrow(
                    "SELECT 1 FROM sessions WHERE id = $1",
                    session_id,
                )

            if exists is None:
                err = BriefSessionNotFoundError(session_id)
                span.record_exception(err)
                span.set_status(trace.StatusCode.ERROR, str(err))
                raise err

            async with asyncio.timeout(5):
                rows = await conn.fetch(
                    "SELECT id, session_id, answer_text, citations, created_at "
                    "FROM briefs "
                    "WHERE session_id = $1 "
                    "ORDER BY created_at ASC",
                    session_id,
                )

            result = [
                BriefRow(
                    id=r["id"],
                    session_id=r["session_id"],
                    answer_text=r["answer_text"],
                    citations=_parse_citations(r["citations"]),
                    created_at=r["created_at"],
                )
                for r in rows
            ]
            span.set_attribute("brief.count", len(result))
            logger.info(
                "brief.listed",
                session_id=session_id,
                count=len(result),
            )
            return result
