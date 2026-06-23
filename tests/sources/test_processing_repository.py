"""Unit tests for ProcessingRepository.

All tests use a mock asyncpg connection — no real database required.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from scout_api.sources.contracts import SourceStatus
from scout_api.sources.processing_repository import ProcessingRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = datetime.UTC
NOW = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_source_record(
    id: int = 1,
    collection_id: int = 10,
    origin: str = "https://example.com",
    status: str = "pending",
    failed_reason: str | None = None,
) -> MagicMock:
    """Return a MagicMock that behaves like an asyncpg Record for sources."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, k: {  # type: ignore[method-assign]
        "id": id,
        "collection_id": collection_id,
        "origin": origin,
        "status": status,
        "created_at": NOW,
        "updated_at": NOW,
        "failed_reason": failed_reason,
    }[k]
    return rec


def _make_conn() -> AsyncMock:
    return AsyncMock()


# ---------------------------------------------------------------------------
# get_source
# ---------------------------------------------------------------------------


class TestGetSource:
    async def test_returns_source_row_when_found(self) -> None:
        conn = _make_conn()
        conn.fetchrow = AsyncMock(return_value=_make_source_record(id=5, status="pending"))

        repo = ProcessingRepository(conn)
        result = await repo.get_source(5)

        assert result is not None
        assert result.id == 5
        assert result.status == SourceStatus.PENDING

    async def test_returns_none_when_not_found(self) -> None:
        conn = _make_conn()
        conn.fetchrow = AsyncMock(return_value=None)

        repo = ProcessingRepository(conn)
        result = await repo.get_source(999)
        assert result is None

    async def test_maps_failed_reason(self) -> None:
        conn = _make_conn()
        conn.fetchrow = AsyncMock(
            return_value=_make_source_record(status="failed", failed_reason="network error")
        )

        repo = ProcessingRepository(conn)
        result = await repo.get_source(1)
        assert result is not None
        assert result.failed_reason == "network error"


# ---------------------------------------------------------------------------
# set_processing
# ---------------------------------------------------------------------------


class TestSetProcessing:
    async def test_returns_source_with_processing_status(self) -> None:
        conn = _make_conn()
        conn.fetchrow = AsyncMock(
            return_value=_make_source_record(status="processing", failed_reason=None)
        )

        repo = ProcessingRepository(conn)
        result = await repo.set_processing(1)

        assert result.status == SourceStatus.PROCESSING
        assert result.failed_reason is None

    async def test_raises_when_source_not_found(self) -> None:
        conn = _make_conn()
        conn.fetchrow = AsyncMock(return_value=None)

        repo = ProcessingRepository(conn)
        with pytest.raises(RuntimeError, match="not found"):
            await repo.set_processing(999)


# ---------------------------------------------------------------------------
# set_ready
# ---------------------------------------------------------------------------


class TestSetReady:
    async def test_returns_source_with_ready_status(self) -> None:
        conn = _make_conn()
        conn.fetchrow = AsyncMock(return_value=_make_source_record(status="ready"))

        repo = ProcessingRepository(conn)
        result = await repo.set_ready(1)

        assert result.status == SourceStatus.READY

    async def test_raises_when_source_not_found(self) -> None:
        conn = _make_conn()
        conn.fetchrow = AsyncMock(return_value=None)

        repo = ProcessingRepository(conn)
        with pytest.raises(RuntimeError, match="not found"):
            await repo.set_ready(999)


# ---------------------------------------------------------------------------
# set_failed
# ---------------------------------------------------------------------------


class TestSetFailed:
    async def test_returns_source_with_failed_status(self) -> None:
        conn = _make_conn()
        conn.fetchrow = AsyncMock(
            return_value=_make_source_record(status="failed", failed_reason="boom")
        )

        repo = ProcessingRepository(conn)
        result = await repo.set_failed(1, reason="boom")

        assert result.status == SourceStatus.FAILED
        assert result.failed_reason == "boom"

    async def test_raises_when_source_not_found(self) -> None:
        conn = _make_conn()
        conn.fetchrow = AsyncMock(return_value=None)

        repo = ProcessingRepository(conn)
        with pytest.raises(RuntimeError, match="not found"):
            await repo.set_failed(999, reason="irrelevant")


# ---------------------------------------------------------------------------
# delete_chunks
# ---------------------------------------------------------------------------


class TestDeleteChunks:
    async def test_returns_deleted_count(self) -> None:
        conn = _make_conn()
        conn.execute = AsyncMock(return_value="DELETE 5")

        repo = ProcessingRepository(conn)
        count = await repo.delete_chunks(1)
        assert count == 5

    async def test_returns_zero_when_no_chunks(self) -> None:
        conn = _make_conn()
        conn.execute = AsyncMock(return_value="DELETE 0")

        repo = ProcessingRepository(conn)
        count = await repo.delete_chunks(1)
        assert count == 0


# ---------------------------------------------------------------------------
# insert_chunk
# ---------------------------------------------------------------------------


class TestInsertChunk:
    async def test_returns_chunk_id(self) -> None:
        conn = _make_conn()
        chunk_record = MagicMock()
        chunk_record.__getitem__ = lambda self, k: {"id": 42}[k]  # type: ignore[method-assign]
        conn.fetchrow = AsyncMock(return_value=chunk_record)

        repo = ProcessingRepository(conn)
        chunk_id = await repo.insert_chunk(
            source_id=1,
            content="chunk text",
            position=0,
            embedding=[0.1, 0.2, 0.3],
        )
        assert chunk_id == 42

    async def test_passes_correct_parameters(self) -> None:
        conn = _make_conn()
        chunk_record = MagicMock()
        chunk_record.__getitem__ = lambda self, k: {"id": 1}[k]  # type: ignore[method-assign]
        conn.fetchrow = AsyncMock(return_value=chunk_record)

        repo = ProcessingRepository(conn)
        await repo.insert_chunk(
            source_id=7,
            content="hello world",
            position=3,
            embedding=[0.5, 0.6],
        )

        call_args = conn.fetchrow.call_args
        positional = call_args[0]
        assert positional[1] == 7  # source_id
        assert positional[2] == "hello world"  # content
        assert positional[3] == 3  # position
