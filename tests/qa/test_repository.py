"""Unit tests for QARepository.

All tests use mock asyncpg connections — no real database required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from scout_api.qa.repository import QARepository
from scout_api.search.contracts import SearchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_conn(rows: list[dict] | None = None, exists: bool = True) -> AsyncMock:
    """Return a mock asyncpg connection."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    exists_row = MagicMock()
    exists_row.__getitem__ = MagicMock(return_value=exists)
    conn.fetchrow = AsyncMock(return_value=exists_row)
    return conn


def _make_db_row(
    chunk_id: int = 1,
    source_id: int = 10,
    collection_id: int = 1,
    content: str = "chunk text",
    source_origin: str = "https://example.com/doc.pdf",
    score: float = 0.92,
) -> MagicMock:
    """Return a mock asyncpg Record-like object."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {  # type: ignore[misc]
        "chunk_id": chunk_id,
        "source_id": source_id,
        "collection_id": collection_id,
        "content": content,
        "source_origin": source_origin,
        "score": score,
    }[key]
    return row


# ---------------------------------------------------------------------------
# retrieve_chunks tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_chunks_returns_search_results() -> None:
    """QARepository.retrieve_chunks maps DB rows to SearchResult objects."""
    row = _make_db_row(chunk_id=1, source_id=10, score=0.92)
    conn = _make_mock_conn(rows=[row])

    repo = QARepository(conn)
    results = await repo.retrieve_chunks(
        collection_id=1,
        query_embedding=[0.1, 0.2, 0.3],
        top_k=5,
    )

    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    assert results[0].chunk_id == 1
    assert results[0].source_id == 10
    assert abs(results[0].score - 0.92) < 0.001


@pytest.mark.asyncio
async def test_retrieve_chunks_passes_correct_args() -> None:
    """QARepository.retrieve_chunks calls fetch with collection_id, vector, top_k."""
    conn = _make_mock_conn(rows=[])
    repo = QARepository(conn)

    embedding = [0.1, 0.2, 0.3]
    await repo.retrieve_chunks(collection_id=5, query_embedding=embedding, top_k=10)

    conn.fetch.assert_called_once()
    args = conn.fetch.call_args.args
    # First positional arg after SQL is collection_id
    assert args[1] == 5
    # Third positional arg is top_k
    assert args[3] == 10
    # Second positional arg is the vector literal string
    assert "[0.1,0.2,0.3]" in args[2]


@pytest.mark.asyncio
async def test_retrieve_chunks_empty_result() -> None:
    """QARepository.retrieve_chunks returns empty list when no rows matched."""
    conn = _make_mock_conn(rows=[])
    repo = QARepository(conn)

    results = await repo.retrieve_chunks(collection_id=1, query_embedding=[0.1], top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_retrieve_chunks_multiple_rows() -> None:
    """QARepository.retrieve_chunks maps multiple rows correctly."""
    rows = [
        _make_db_row(chunk_id=1, source_id=10, score=0.95),
        _make_db_row(chunk_id=2, source_id=20, score=0.80),
    ]
    conn = _make_mock_conn(rows=rows)
    repo = QARepository(conn)

    results = await repo.retrieve_chunks(collection_id=1, query_embedding=[0.1], top_k=5)

    assert len(results) == 2
    assert results[0].chunk_id == 1
    assert results[1].chunk_id == 2


# ---------------------------------------------------------------------------
# collection_exists tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collection_exists_returns_true_when_present() -> None:
    """QARepository.collection_exists returns True when collection row exists."""
    conn = _make_mock_conn(exists=True)
    repo = QARepository(conn)

    result = await repo.collection_exists(collection_id=1)

    assert result is True


@pytest.mark.asyncio
async def test_collection_exists_returns_false_when_absent() -> None:
    """QARepository.collection_exists returns False when no row found."""
    conn = _make_mock_conn(exists=False)
    repo = QARepository(conn)

    result = await repo.collection_exists(collection_id=999)

    assert result is False


@pytest.mark.asyncio
async def test_collection_exists_queries_correct_id() -> None:
    """QARepository.collection_exists passes collection_id to fetchrow."""
    conn = _make_mock_conn(exists=True)
    repo = QARepository(conn)

    await repo.collection_exists(collection_id=42)

    conn.fetchrow.assert_called_once()
    args = conn.fetchrow.call_args.args
    assert 42 in args
