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
    failed_reason: str | None = None,
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
        "failed_reason": failed_reason,
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


# ---------------------------------------------------------------------------
# list_by_collection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_by_collection_returns_empty_list_when_no_sources() -> None:
    conn = _make_conn()
    conn.fetch.return_value = []

    repo = SourceRepository(conn)
    result = await repo.list_by_collection(42)

    assert result == []
    conn.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_list_by_collection_returns_all_sources() -> None:
    conn = _make_conn()
    conn.fetch.return_value = [
        _make_record(id=1, status="pending"),
        _make_record(id=2, status="ready"),
        _make_record(id=3, status="failed", failed_reason="timeout"),
    ]

    repo = SourceRepository(conn)
    result = await repo.list_by_collection(10)

    assert len(result) == 3
    assert result[0].id == 1
    assert result[1].id == 2
    assert result[2].id == 3


@pytest.mark.asyncio
async def test_list_by_collection_sources_ordered_oldest_first() -> None:
    """The SQL query orders by created_at ASC; verify the IDs come back in order."""
    conn = _make_conn()
    t1 = datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
    t2 = datetime.datetime(2024, 1, 2, tzinfo=datetime.UTC)
    conn.fetch.return_value = [
        _make_record(id=10, created_at=t1),
        _make_record(id=20, created_at=t2),
    ]

    repo = SourceRepository(conn)
    result = await repo.list_by_collection(10)

    assert result[0].id == 10
    assert result[1].id == 20


@pytest.mark.asyncio
async def test_list_by_collection_passes_collection_id_to_query() -> None:
    conn = _make_conn()
    conn.fetch.return_value = []

    repo = SourceRepository(conn)
    await repo.list_by_collection(55)

    call_args = conn.fetch.call_args
    assert 55 in call_args[0]


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_id_returns_source_row_when_found() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = _make_record(id=7, collection_id=42)

    repo = SourceRepository(conn)
    result = await repo.get_by_id(source_id=7, collection_id=42)

    assert result is not None
    assert result.id == 7
    assert result.collection_id == 42


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_source_not_found() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = None

    repo = SourceRepository(conn)
    result = await repo.get_by_id(source_id=999, collection_id=42)

    assert result is None


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_source_in_different_collection() -> None:
    """SQL enforces collection_id — fetchrow returns None for cross-collection lookup."""
    conn = _make_conn()
    conn.fetchrow.return_value = None  # DB returns nothing when collection_id mismatches

    repo = SourceRepository(conn)
    result = await repo.get_by_id(source_id=7, collection_id=999)

    assert result is None


@pytest.mark.asyncio
async def test_get_by_id_passes_both_source_and_collection_id() -> None:
    """Verify both IDs are passed to the SQL query for collection-scoping."""
    conn = _make_conn()
    conn.fetchrow.return_value = None

    repo = SourceRepository(conn)
    await repo.get_by_id(source_id=3, collection_id=10)

    call_args = conn.fetchrow.call_args
    params = call_args[0][1:]
    assert 3 in params
    assert 10 in params


@pytest.mark.asyncio
async def test_get_by_id_includes_failed_reason_in_result() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = _make_record(
        id=5,
        status="failed",
        failed_reason="HTTP 403",
    )

    repo = SourceRepository(conn)
    result = await repo.get_by_id(source_id=5, collection_id=10)

    assert result is not None
    assert result.failed_reason == "HTTP 403"


# ---------------------------------------------------------------------------
# _row_to_source — failed_reason handling (regression test)
# ---------------------------------------------------------------------------


def test_row_to_source_includes_failed_reason_when_present() -> None:
    """_row_to_source must map failed_reason — it was missing in earlier implementation."""
    rec = _make_record(status="failed", failed_reason="connection refused")
    result = SourceRepository._row_to_source(rec)
    assert result.failed_reason == "connection refused"


def test_row_to_source_failed_reason_is_none_for_ready_source() -> None:
    rec = _make_record(status="ready", failed_reason=None)
    result = SourceRepository._row_to_source(rec)
    assert result.failed_reason is None
