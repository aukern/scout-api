"""Unit tests for SessionRepository and SessionActivityRepository.

All tests mock asyncpg at the connection level — no real database required.
The mock_conn fixture from conftest.py provides an AsyncMock that simulates
fetchrow/fetch/execute return values.

Integration tests (marked @pytest.mark.integration) require TEST_DATABASE_URL
and verify SQL correctness against a real Postgres instance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from scout_api.sessions.contracts import SessionActivityRow, SessionRow
from scout_api.sessions.errors import SessionCollectionNotFoundError
from scout_api.sessions.repository import SessionActivityRepository, SessionRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)


def _make_session_record(
    id: int = 1,
    collection_id: int = 5,
    created_at: datetime = NOW,
) -> MagicMock:
    """Build a mock asyncpg record resembling a sessions row."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, key: {
        "id": id,
        "collection_id": collection_id,
        "created_at": created_at,
    }[key]
    return rec


def _make_activity_record(
    id: int = 10,
    session_id: int = 1,
    kind: str = "search",
    query: str = "test query",
    output: str | None = None,
    created_at: datetime = NOW,
) -> MagicMock:
    """Build a mock asyncpg record resembling a session_activity row."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, key: {
        "id": id,
        "session_id": session_id,
        "kind": kind,
        "query": query,
        "output": output,
        "created_at": created_at,
    }[key]
    return rec


# ---------------------------------------------------------------------------
# SessionRepository.open()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_session_inserts_row(mock_conn: AsyncMock) -> None:
    """open() with a valid collection_id returns a SessionRow."""
    # Collection exists
    mock_conn.fetchrow = AsyncMock(
        side_effect=[
            MagicMock(),  # SELECT 1 FROM collections WHERE id = $1
            _make_session_record(id=1, collection_id=5),  # INSERT RETURNING
        ]
    )

    repo = SessionRepository(mock_conn)
    result = await repo.open(collection_id=5, conn=mock_conn)

    assert isinstance(result, SessionRow)
    assert result.id == 1
    assert result.collection_id == 5


@pytest.mark.asyncio
async def test_open_session_collection_not_found(mock_conn: AsyncMock) -> None:
    """open() raises SessionCollectionNotFoundError when collection_id does not exist."""
    mock_conn.fetchrow = AsyncMock(return_value=None)

    repo = SessionRepository(mock_conn)
    with pytest.raises(SessionCollectionNotFoundError) as exc_info:
        await repo.open(collection_id=999, conn=mock_conn)

    assert exc_info.value.code == "SES_NF_002"
    assert exc_info.value.collection_id == 999


# ---------------------------------------------------------------------------
# SessionRepository.list_all()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_all(mock_conn: AsyncMock) -> None:
    """list_all(None) returns all sessions without a collection filter."""
    mock_conn.fetch = AsyncMock(
        return_value=[
            _make_session_record(id=1, collection_id=5),
            _make_session_record(id=2, collection_id=5),
        ]
    )

    repo = SessionRepository(mock_conn)
    result = await repo.list_all(collection_id=None, conn=mock_conn)

    assert len(result) == 2
    assert result[0].id == 1
    assert result[1].id == 2
    # Verify no filter was applied (fetch called without collection_id param)
    call_args = mock_conn.fetch.call_args
    assert "$1" not in call_args[0][0]


@pytest.mark.asyncio
async def test_list_sessions_by_collection(mock_conn: AsyncMock) -> None:
    """list_all(collection_id=5) passes the collection filter to the query."""
    mock_conn.fetch = AsyncMock(return_value=[_make_session_record(id=3, collection_id=5)])

    repo = SessionRepository(mock_conn)
    result = await repo.list_all(collection_id=5, conn=mock_conn)

    assert len(result) == 1
    assert result[0].collection_id == 5
    # Verify the filter argument was passed
    call_args = mock_conn.fetch.call_args
    assert call_args[0][1] == 5


@pytest.mark.asyncio
async def test_list_sessions_empty(mock_conn: AsyncMock) -> None:
    """list_all() on an empty system returns an empty list."""
    mock_conn.fetch = AsyncMock(return_value=[])

    repo = SessionRepository(mock_conn)
    result = await repo.list_all(collection_id=None, conn=mock_conn)

    assert result == []


# ---------------------------------------------------------------------------
# SessionRepository.get()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_found(mock_conn: AsyncMock) -> None:
    """get() returns a SessionRow when the session exists."""
    mock_conn.fetchrow = AsyncMock(return_value=_make_session_record(id=7, collection_id=3))

    repo = SessionRepository(mock_conn)
    result = await repo.get(session_id=7, conn=mock_conn)

    assert result is not None
    assert result.id == 7
    assert result.collection_id == 3


@pytest.mark.asyncio
async def test_get_session_not_found(mock_conn: AsyncMock) -> None:
    """get() returns None when the session does not exist."""
    mock_conn.fetchrow = AsyncMock(return_value=None)

    repo = SessionRepository(mock_conn)
    result = await repo.get(session_id=999, conn=mock_conn)

    assert result is None


# ---------------------------------------------------------------------------
# SessionRepository.delete()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session_existing(mock_conn: AsyncMock) -> None:
    """delete() returns True when the session existed and was deleted."""
    mock_conn.execute = AsyncMock(return_value="DELETE 1")

    repo = SessionRepository(mock_conn)
    result = await repo.delete(session_id=1, conn=mock_conn)

    assert result is True


@pytest.mark.asyncio
async def test_delete_session_not_found(mock_conn: AsyncMock) -> None:
    """delete() returns False when the session did not exist."""
    mock_conn.execute = AsyncMock(return_value="DELETE 0")

    repo = SessionRepository(mock_conn)
    result = await repo.delete(session_id=999, conn=mock_conn)

    assert result is False


# ---------------------------------------------------------------------------
# SessionActivityRepository.record()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_activity_search(mock_conn: AsyncMock) -> None:
    """record() with kind='search' inserts and returns a SessionActivityRow."""
    mock_conn.fetchrow = AsyncMock(
        return_value=_make_activity_record(
            id=10,
            session_id=1,
            kind="search",
            query="machine learning",
            output=None,
        )
    )

    repo = SessionActivityRepository()
    result = await repo.record(
        session_id=1,
        kind="search",
        query="machine learning",
        output=None,
        conn=mock_conn,
    )

    assert isinstance(result, SessionActivityRow)
    assert result.kind == "search"
    assert result.query == "machine learning"
    assert result.output is None


@pytest.mark.asyncio
async def test_record_activity_question_with_output(mock_conn: AsyncMock) -> None:
    """record() with kind='question' and a non-null output stores the answer."""
    mock_conn.fetchrow = AsyncMock(
        return_value=_make_activity_record(
            id=11,
            session_id=1,
            kind="question",
            query="What is the capital of France?",
            output="Paris is the capital of France.",
        )
    )

    repo = SessionActivityRepository()
    result = await repo.record(
        session_id=1,
        kind="question",
        query="What is the capital of France?",
        output="Paris is the capital of France.",
        conn=mock_conn,
    )

    assert result.kind == "question"
    assert result.output == "Paris is the capital of France."
    # Verify output was passed to the INSERT
    call_args = mock_conn.fetchrow.call_args[0]
    assert "Paris is the capital of France." in call_args


# ---------------------------------------------------------------------------
# SessionActivityRepository.list_for_session()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_activity_for_session(mock_conn: AsyncMock) -> None:
    """list_for_session() returns activity rows ordered oldest first."""
    earlier = datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC)
    later = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

    mock_conn.fetch = AsyncMock(
        return_value=[
            _make_activity_record(id=1, session_id=5, kind="search", created_at=earlier),
            _make_activity_record(id=2, session_id=5, kind="question", created_at=later),
        ]
    )

    repo = SessionActivityRepository()
    result = await repo.list_for_session(session_id=5, conn=mock_conn)

    assert len(result) == 2
    assert result[0].id == 1
    assert result[1].id == 2
    assert result[0].created_at < result[1].created_at


@pytest.mark.asyncio
async def test_list_activity_empty_session(mock_conn: AsyncMock) -> None:
    """list_for_session() returns an empty list for a session with no activity."""
    mock_conn.fetch = AsyncMock(return_value=[])

    repo = SessionActivityRepository()
    result = await repo.list_for_session(session_id=5, conn=mock_conn)

    assert result == []
