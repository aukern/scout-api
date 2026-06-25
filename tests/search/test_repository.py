"""Unit tests for SearchRepository.

Uses an AsyncMock asyncpg connection — no real database required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from scout_api.search.contracts import SearchResult
from scout_api.search.repository import SearchRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn() -> AsyncMock:
    """Return a minimal mock asyncpg connection."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _make_row(
    chunk_id: int = 1,
    source_id: int = 2,
    collection_id: int = 3,
    content: str = "hello world",
    score: float = 0.9,
    source_origin: str = "https://example.com/doc.pdf",
) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: {  # type: ignore[misc]
        "chunk_id": chunk_id,
        "source_id": source_id,
        "collection_id": collection_id,
        "content": content,
        "score": score,
        "source_origin": source_origin,
    }[key]
    return row


# ---------------------------------------------------------------------------
# SearchRepository.search
# ---------------------------------------------------------------------------


class TestSearchRepositorySearch:
    @pytest.mark.asyncio
    async def test_returns_empty_on_no_results(self) -> None:
        conn = _make_conn()
        conn.fetch.return_value = []
        repo = SearchRepository(conn)

        results = await repo.search(
            collection_id=3,
            query_embedding=[0.1, 0.2, 0.3],
            top_k=10,
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_maps_row_to_search_result(self) -> None:
        row = _make_row(chunk_id=42, source_id=7, collection_id=3, score=0.95)
        conn = _make_conn()
        conn.fetch.return_value = [row]
        repo = SearchRepository(conn)

        results = await repo.search(
            collection_id=3,
            query_embedding=[0.1, 0.2],
            top_k=5,
        )

        assert len(results) == 1
        r = results[0]
        assert isinstance(r, SearchResult)
        assert r.chunk_id == 42
        assert r.source_id == 7
        assert r.collection_id == 3
        assert r.score == 0.95

    @pytest.mark.asyncio
    async def test_passes_collection_id_and_top_k(self) -> None:
        conn = _make_conn()
        conn.fetch.return_value = []
        repo = SearchRepository(conn)

        await repo.search(
            collection_id=5,
            query_embedding=[1.0, 0.0],
            top_k=20,
        )

        call_args = conn.fetch.call_args
        # Positional args: SQL, collection_id, vector_literal, top_k
        assert call_args[0][1] == 5
        assert call_args[0][3] == 20

    @pytest.mark.asyncio
    async def test_vector_formatted_as_pg_literal(self) -> None:
        """Embedding is formatted as [x,y,z] for pgvector."""
        conn = _make_conn()
        conn.fetch.return_value = []
        repo = SearchRepository(conn)

        await repo.search(
            collection_id=1,
            query_embedding=[0.1, 0.2, 0.3],
            top_k=10,
        )

        call_args = conn.fetch.call_args
        vector_arg = call_args[0][2]
        assert vector_arg == "[0.1,0.2,0.3]"

    @pytest.mark.asyncio
    async def test_score_converted_to_float(self) -> None:
        """Score from DB (may be Decimal) is coerced to float."""
        from decimal import Decimal

        row = _make_row(score=Decimal("0.876"))
        conn = _make_conn()
        conn.fetch.return_value = [row]
        repo = SearchRepository(conn)

        results = await repo.search(
            collection_id=1,
            query_embedding=[0.1],
            top_k=5,
        )

        assert isinstance(results[0].score, float)
        assert abs(results[0].score - 0.876) < 0.001

    @pytest.mark.asyncio
    async def test_returns_multiple_results_in_order(self) -> None:
        """Multiple rows returned in the order provided by the DB."""
        rows = [
            _make_row(chunk_id=10, score=0.99),
            _make_row(chunk_id=20, score=0.88),
            _make_row(chunk_id=30, score=0.77),
        ]
        conn = _make_conn()
        conn.fetch.return_value = rows
        repo = SearchRepository(conn)

        results = await repo.search(
            collection_id=1,
            query_embedding=[0.5],
            top_k=10,
        )

        assert [r.chunk_id for r in results] == [10, 20, 30]


# ---------------------------------------------------------------------------
# SearchRepository.collection_exists
# ---------------------------------------------------------------------------


class TestSearchRepositoryCollectionExists:
    @pytest.mark.asyncio
    async def test_returns_true_when_exists(self) -> None:
        row = MagicMock()
        row.__getitem__ = lambda self, key: True  # type: ignore[misc]
        conn = _make_conn()
        conn.fetchrow.return_value = row
        repo = SearchRepository(conn)

        result = await repo.collection_exists(3)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_exists(self) -> None:
        row = MagicMock()
        row.__getitem__ = lambda self, key: False  # type: ignore[misc]
        conn = _make_conn()
        conn.fetchrow.return_value = row
        repo = SearchRepository(conn)

        result = await repo.collection_exists(999)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_none_row(self) -> None:
        """fetchrow returning None means no row matched."""
        conn = _make_conn()
        conn.fetchrow.return_value = None
        repo = SearchRepository(conn)

        result = await repo.collection_exists(999)

        assert result is False

    @pytest.mark.asyncio
    async def test_passes_collection_id(self) -> None:
        row = MagicMock()
        row.__getitem__ = lambda self, key: True  # type: ignore[misc]
        conn = _make_conn()
        conn.fetchrow.return_value = row
        repo = SearchRepository(conn)

        await repo.collection_exists(42)

        call_args = conn.fetchrow.call_args
        assert call_args[0][1] == 42
