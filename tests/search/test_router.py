"""Integration tests for POST /collections/{collection_id}/search.

Uses FastAPI AsyncClient with mock dependencies — no real database or Redis.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from scout_api.main import create_app
from scout_api.search.contracts import SearchResult
from scout_api.search.dependencies import get_search_service
from scout_api.search.errors import CollectionNotFoundForSearchError, SearchEmbeddingError
from scout_api.search.service import SearchResponse, SearchService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(chunk_id: int = 1, score: float = 0.9) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        source_id=2,
        collection_id=3,
        content="relevant content",
        score=score,
        source_origin="https://example.com/doc.pdf",
    )


def _make_service(
    results: list[SearchResult] | None = None,
    cached: bool = False,
    raises: Exception | None = None,
) -> AsyncMock:
    """Return a mock SearchService."""
    service = AsyncMock(spec=SearchService)
    if raises is not None:
        service.search.side_effect = raises
    else:
        effective_results = [_make_result()] if results is None else results
        service.search.return_value = SearchResponse(
            results=effective_results,
            cached=cached,
            collection_id=3,
            query="test query",
        )
    return service


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestSearchEndpoint:
    @pytest.mark.asyncio
    async def test_returns_200_with_results(self, app) -> None:
        mock_service = _make_service(results=[_make_result(chunk_id=42, score=0.95)])
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/collections/3/search",
                json={"query": "test query"},
            )

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["collection_id"] == 3
        assert data["cached"] is False
        assert data["results"][0]["chunk_id"] == 42
        assert data["results"][0]["score"] == 0.95

    @pytest.mark.asyncio
    async def test_returns_cached_flag_on_cache_hit(self, app) -> None:
        mock_service = _make_service(cached=True)
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/collections/3/search", json={"query": "hello"})

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["cached"] is True

    @pytest.mark.asyncio
    async def test_top_k_passed_to_service(self, app) -> None:
        mock_service = _make_service()
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/collections/3/search", json={"query": "test", "top_k": 25})

        app.dependency_overrides.clear()
        call_args = mock_service.search.call_args[0][0]
        assert call_args.top_k == 25

    @pytest.mark.asyncio
    async def test_default_top_k_is_10(self, app) -> None:
        mock_service = _make_service()
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/collections/3/search", json={"query": "test"})

        app.dependency_overrides.clear()
        call_args = mock_service.search.call_args[0][0]
        assert call_args.top_k == 10

    @pytest.mark.asyncio
    async def test_empty_results_returns_200(self, app) -> None:
        mock_service = _make_service(results=[])
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/collections/3/search", json={"query": "obscure"})

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["results"] == []


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestSearchEndpointErrors:
    @pytest.mark.asyncio
    async def test_returns_404_on_missing_collection(self, app) -> None:
        mock_service = _make_service(raises=CollectionNotFoundForSearchError(99))
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/collections/99/search", json={"query": "test"})

        app.dependency_overrides.clear()
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SEARCH_COL_001"

    @pytest.mark.asyncio
    async def test_returns_502_on_embedding_failure(self, app) -> None:
        mock_service = _make_service(raises=SearchEmbeddingError("model timeout"))
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/collections/3/search", json={"query": "test"})

        app.dependency_overrides.clear()
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "SEARCH_EMB_001"

    @pytest.mark.asyncio
    async def test_returns_422_on_empty_query(self, app) -> None:
        mock_service = _make_service()
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/collections/3/search", json={"query": ""})

        app.dependency_overrides.clear()
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_422_on_missing_query(self, app) -> None:
        mock_service = _make_service()
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/collections/3/search", json={})

        app.dependency_overrides.clear()
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_422_on_top_k_out_of_range(self, app) -> None:
        mock_service = _make_service()
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/collections/3/search", json={"query": "test", "top_k": 0})

        app.dependency_overrides.clear()
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_422_on_top_k_too_large(self, app) -> None:
        mock_service = _make_service()
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/collections/3/search", json={"query": "test", "top_k": 101})

        app.dependency_overrides.clear()
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_query_echoed_in_response(self, app) -> None:
        mock_service = _make_service()
        app.dependency_overrides[get_search_service] = lambda: mock_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/collections/3/search", json={"query": "my specific question"})

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["query"] == "test query"  # echoed from service response
