"""Unit tests for SourceRepository.

All tests use a mock asyncpg connection — no real database required.
Mock fetchrow / fetch / execute return asyncpg-style data.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from scout_api.sources.contracts import SourceStatus
from scout_api.sources.repository import SourceRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = datetime.UTC
NOW = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_record(
    id: int = 1,
    collection_id: int = 10,
    origin: str = "https://example.com",
    status: str = "pending",
    created_at: datetime.datetime = NOW,
    updated_at: datetime.datetime = NOW,
    was_updated: bool = False,
) -> MagicMock:
    """Return a MagicMock that behaves like an asyncpg Record."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, k: {  # type: ignore[method-assign]
        "id": id,
        "collection_id": collection_id,
        "origin": origin,
        "status": status,
        "created_at": created_at,
        "updated_at": updated_at,
        "was_updated": was_updated,
    }[k]
    return rec


def _make_conn() -> AsyncMock:
    """Return a mock asyncpg connection."""
    return AsyncMock()


# ---------------------------------------------------------------------------
# collection_exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collection_exists_returns_true_when_row_found() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = MagicMock()

    repo = SourceRepository(conn)
    result = await repo.collection_exists(10)

    assert result is True
    conn.fetchrow.assert_called_once()


@pytest.mark.asyncio
async def test_collection_exists_returns_false_when_no_row() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = None

    repo = SourceRepository(conn)
    result = await repo.collection_exists(99)

    assert result is False


# ---------------------------------------------------------------------------
# get_by_origin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_origin_returns_source_row_when_found() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = _make_record()

    repo = SourceRepository(conn)
    row = await repo.get_by_origin(10, "https://example.com")

    assert row is not None
    assert row.id == 1
    assert row.collection_id == 10
    assert row.origin == "https://example.com"
    assert row.status == SourceStatus.PENDING


@pytest.mark.asyncio
async def test_get_by_origin_returns_none_when_not_found() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = None

    repo = SourceRepository(conn)
    row = await repo.get_by_origin(10, "https://missing.com")

    assert row is None


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_returns_new_source_with_is_refresh_false() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = _make_record(was_updated=False)

    repo = SourceRepository(conn)
    row, is_refresh = await repo.upsert(10, "https://example.com")

    assert row.id == 1
    assert row.status == SourceStatus.PENDING
    assert is_refresh is False


@pytest.mark.asyncio
async def test_upsert_returns_existing_source_with_is_refresh_true() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = _make_record(was_updated=True)

    repo = SourceRepository(conn)
    row, is_refresh = await repo.upsert(10, "https://example.com")

    assert row.id == 1
    assert is_refresh is True


@pytest.mark.asyncio
async def test_upsert_passes_correct_sql_params() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = _make_record()

    repo = SourceRepository(conn)
    await repo.upsert(42, "https://target.com")

    call_args = conn.fetchrow.call_args
    sql = call_args[0][0]
    params = call_args[0][1:]

    assert "ON CONFLICT" in sql
    assert 42 in params
    assert "https://target.com" in params


# ---------------------------------------------------------------------------
# delete_chunks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_chunks_returns_deleted_count() -> None:
    conn = _make_conn()
    conn.execute.return_value = "DELETE 5"

    repo = SourceRepository(conn)
    count = await repo.delete_chunks(1)

    assert count == 5


@pytest.mark.asyncio
async def test_delete_chunks_returns_zero_when_no_chunks() -> None:
    conn = _make_conn()
    conn.execute.return_value = "DELETE 0"

    repo = SourceRepository(conn)
    count = await repo.delete_chunks(99)

    assert count == 0


@pytest.mark.asyncio
async def test_delete_chunks_passes_source_id() -> None:
    conn = _make_conn()
    conn.execute.return_value = "DELETE 0"

    repo = SourceRepository(conn)
    await repo.delete_chunks(7)

    call_args = conn.execute.call_args
    assert 7 in call_args[0]
