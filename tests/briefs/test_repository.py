"""Unit tests for BriefRepository.

All tests mock asyncpg at the connection level — no real database required.
The mock_conn fixture from conftest.py provides an AsyncMock that simulates
fetchrow/fetch return values.

Integration tests (marked @pytest.mark.integration) require TEST_DATABASE_URL
and verify SQL correctness against a real Postgres instance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from scout_api.briefs.contracts import BriefCitation, BriefRow
from scout_api.briefs.errors import BriefSessionNotFoundError
from scout_api.briefs.repository import BriefRepository, _parse_citations, _serialise_citations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

CITATION_FULL = BriefCitation(source_id=3, chunk_id=41, excerpt="The key insight is here.")
CITATION_SOURCE_ONLY = BriefCitation(source_id=5)


def _make_brief_record(
    id: int = 1,
    session_id: int = 7,
    answer_text: str = "The answer is...",
    citations: list[dict] | None = None,
    created_at: datetime = NOW,
) -> MagicMock:
    """Build a mock asyncpg record resembling a briefs row."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, key: {
        "id": id,
        "session_id": session_id,
        "answer_text": answer_text,
        "citations": citations if citations is not None else [],
        "created_at": created_at,
    }[key]
    return rec


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


def test_parse_citations_none() -> None:
    """_parse_citations(None) returns an empty list."""
    assert _parse_citations(None) == []


def test_parse_citations_empty_list() -> None:
    """_parse_citations([]) returns an empty list."""
    assert _parse_citations([]) == []


def test_parse_citations_source_only() -> None:
    """_parse_citations with source_id only produces BriefCitation with None chunk/excerpt."""
    raw = [{"source_id": 5}]
    result = _parse_citations(raw)
    assert len(result) == 1
    assert result[0].source_id == 5
    assert result[0].chunk_id is None
    assert result[0].excerpt is None


def test_parse_citations_full() -> None:
    """_parse_citations with all fields produces a complete BriefCitation."""
    raw = [{"source_id": 3, "chunk_id": 41, "excerpt": "The key insight."}]
    result = _parse_citations(raw)
    assert len(result) == 1
    assert result[0] == BriefCitation(source_id=3, chunk_id=41, excerpt="The key insight.")


def test_parse_citations_json_string() -> None:
    """_parse_citations handles a JSON string (older asyncpg path)."""
    raw = '[{"source_id": 3, "chunk_id": 41, "excerpt": "text"}]'
    result = _parse_citations(raw)  # type: ignore[arg-type]
    assert len(result) == 1
    assert result[0].source_id == 3


def test_serialise_citations_empty() -> None:
    """_serialise_citations([]) produces '[]'."""
    assert _serialise_citations([]) == "[]"


def test_serialise_citations_source_only() -> None:
    """_serialise_citations with source_id only omits chunk_id and excerpt."""
    import json

    result = json.loads(_serialise_citations([BriefCitation(source_id=5)]))
    assert result == [{"source_id": 5}]


def test_serialise_citations_full() -> None:
    """_serialise_citations with all fields includes all keys."""
    import json

    result = json.loads(
        _serialise_citations([BriefCitation(source_id=3, chunk_id=41, excerpt="text")])
    )
    assert result == [{"source_id": 3, "chunk_id": 41, "excerpt": "text"}]


# ---------------------------------------------------------------------------
# BriefRepository.save()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_brief_returns_row(mock_conn: AsyncMock) -> None:
    """save() with valid session_id returns a BriefRow with correct fields."""
    citations_in_db = [{"source_id": 3, "chunk_id": 41, "excerpt": "Key text."}]
    mock_conn.fetchrow = AsyncMock(
        side_effect=[
            MagicMock(),  # SELECT 1 FROM sessions WHERE id = $1 — session exists
            _make_brief_record(  # INSERT RETURNING
                id=1,
                session_id=7,
                answer_text="The answer is...",
                citations=citations_in_db,
            ),
        ]
    )

    repo = BriefRepository()
    result = await repo.save(
        session_id=7,
        answer_text="The answer is...",
        citations=[CITATION_FULL],
        conn=mock_conn,
    )

    assert isinstance(result, BriefRow)
    assert result.id == 1
    assert result.session_id == 7
    assert result.answer_text == "The answer is..."
    assert len(result.citations) == 1
    assert result.citations[0].source_id == 3
    assert result.citations[0].chunk_id == 41


@pytest.mark.asyncio
async def test_save_brief_session_not_found(mock_conn: AsyncMock) -> None:
    """save() raises BriefSessionNotFoundError (BRF_NF_001) when session missing."""
    mock_conn.fetchrow = AsyncMock(return_value=None)  # session does not exist

    repo = BriefRepository()
    with pytest.raises(BriefSessionNotFoundError) as exc_info:
        await repo.save(
            session_id=999,
            answer_text="text",
            citations=[],
            conn=mock_conn,
        )

    err = exc_info.value
    assert err.code == "BRF_NF_001"
    assert err.status_code == 404
    assert err.session_id == 999


@pytest.mark.asyncio
async def test_save_brief_empty_citations(mock_conn: AsyncMock) -> None:
    """save() with empty citations stores '[]' and round-trips to empty list."""
    mock_conn.fetchrow = AsyncMock(
        side_effect=[
            MagicMock(),  # session exists
            _make_brief_record(citations=[]),  # INSERT returns empty citations
        ]
    )

    repo = BriefRepository()
    result = await repo.save(
        session_id=7,
        answer_text="No citations needed.",
        citations=[],
        conn=mock_conn,
    )

    assert result.citations == []


@pytest.mark.asyncio
async def test_save_brief_with_source_only_citation(mock_conn: AsyncMock) -> None:
    """save() with source_id-only citation round-trips correctly."""
    mock_conn.fetchrow = AsyncMock(
        side_effect=[
            MagicMock(),  # session exists
            _make_brief_record(citations=[{"source_id": 5}]),
        ]
    )

    repo = BriefRepository()
    result = await repo.save(
        session_id=7,
        answer_text="Answer with minimal citation.",
        citations=[CITATION_SOURCE_ONLY],
        conn=mock_conn,
    )

    assert len(result.citations) == 1
    assert result.citations[0].source_id == 5
    assert result.citations[0].chunk_id is None
    assert result.citations[0].excerpt is None


# ---------------------------------------------------------------------------
# BriefRepository.list_for_session()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_for_session_empty(mock_conn: AsyncMock) -> None:
    """list_for_session() returns [] when session has no Briefs."""
    mock_conn.fetchrow = AsyncMock(return_value=MagicMock())  # session exists
    mock_conn.fetch = AsyncMock(return_value=[])

    repo = BriefRepository()
    result = await repo.list_for_session(session_id=7, conn=mock_conn)

    assert result == []


@pytest.mark.asyncio
async def test_list_for_session_returns_ordered(mock_conn: AsyncMock) -> None:
    """list_for_session() returns rows in the order returned by the DB (oldest first)."""
    from datetime import timedelta

    t1 = NOW
    t2 = NOW + timedelta(minutes=5)

    mock_conn.fetchrow = AsyncMock(return_value=MagicMock())  # session exists
    mock_conn.fetch = AsyncMock(
        return_value=[
            _make_brief_record(id=1, answer_text="First answer", created_at=t1),
            _make_brief_record(id=2, answer_text="Second answer", created_at=t2),
        ]
    )

    repo = BriefRepository()
    result = await repo.list_for_session(session_id=7, conn=mock_conn)

    assert len(result) == 2
    assert result[0].id == 1
    assert result[0].answer_text == "First answer"
    assert result[1].id == 2
    assert result[1].answer_text == "Second answer"


@pytest.mark.asyncio
async def test_list_for_session_null_citations_column(mock_conn: AsyncMock) -> None:
    """list_for_session() coerces NULL citations column to empty list."""
    mock_conn.fetchrow = AsyncMock(return_value=MagicMock())  # session exists
    mock_conn.fetch = AsyncMock(
        return_value=[
            _make_brief_record(citations=None),  # NULL in DB
        ]
    )

    repo = BriefRepository()
    result = await repo.list_for_session(session_id=7, conn=mock_conn)

    assert len(result) == 1
    assert result[0].citations == []


@pytest.mark.asyncio
async def test_list_for_session_session_not_found(mock_conn: AsyncMock) -> None:
    """list_for_session() raises BriefSessionNotFoundError when session missing."""
    mock_conn.fetchrow = AsyncMock(return_value=None)  # session does not exist

    repo = BriefRepository()
    with pytest.raises(BriefSessionNotFoundError) as exc_info:
        await repo.list_for_session(session_id=999, conn=mock_conn)

    err = exc_info.value
    assert err.code == "BRF_NF_001"
    assert err.session_id == 999
