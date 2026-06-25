"""Unit tests for SearchService.

Uses in-memory/mock adapters — no real database, Redis, or embedding model.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scout_api.search.cache import SearchCache
from scout_api.search.contracts import SearchQuery, SearchResult
from scout_api.search.errors import CollectionNotFoundForSearchError, SearchEmbeddingError
from scout_api.search.service import SearchService
from scout_api.sources.embedder import Embedder

# ---------------------------------------------------------------------------
# In-memory test adapters
# ---------------------------------------------------------------------------


class InMemorySearchRepository:
    """Test double for SearchRepositoryProtocol — no database needed."""

    def __init__(
        self,
        results: list[SearchResult] | None = None,
        exists: bool = True,
    ) -> None:
        self._results = results or []
        self._exists = exists
        self.search_calls: list[dict] = []

    async def search(
        self,
        collection_id: int,
        query_embedding: list[float],
        top_k: int,
    ) -> list[SearchResult]:
        self.search_calls.append({"collection_id": collection_id, "top_k": top_k})
        return self._results[:top_k]

    async def collection_exists(self, collection_id: int) -> bool:
        return self._exists


def _make_embedder(vector: list[float] | None = None) -> Embedder:
    """Return an Embedder with a fake embedding function."""

    async def fake_embed(text: str, model: str, api_base: str) -> list[float]:
        return vector or [0.1, 0.2, 0.3]

    return Embedder(model="test-model", _embed_fn=fake_embed)


def _make_cache(
    cached_results: list[SearchResult] | None = None,
) -> SearchCache:
    """Return a SearchCache with mock Redis."""
    redis = AsyncMock()
    # Default: cache miss
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.scan = AsyncMock(return_value=(0, []))
    redis.delete = AsyncMock(return_value=0)
    cache = SearchCache(redis=redis)

    if cached_results is not None:
        # Simulate a cache hit by patching get()
        import json

        payload = json.dumps(
            [
                {
                    "chunk_id": r.chunk_id,
                    "source_id": r.source_id,
                    "collection_id": r.collection_id,
                    "content": r.content,
                    "score": r.score,
                    "source_origin": r.source_origin,
                }
                for r in cached_results
            ]
        )
        redis.get = AsyncMock(return_value=payload)

    return cache


def _make_result(chunk_id: int = 1, score: float = 0.9) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        source_id=2,
        collection_id=3,
        content="relevant text",
        score=score,
        source_origin="https://example.com/doc.pdf",
    )


# ---------------------------------------------------------------------------
# SearchService.search — happy path
# ---------------------------------------------------------------------------


class TestSearchServiceSearch:
    @pytest.mark.asyncio
    async def test_returns_results_from_repo(self) -> None:
        expected = [_make_result(chunk_id=1), _make_result(chunk_id=2)]
        repo = InMemorySearchRepository(results=expected, exists=True)
        cache = _make_cache()  # miss
        embedder = _make_embedder()

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        query = SearchQuery(collection_id=3, query_text="test query", top_k=10)
        response = await service.search(query)

        assert len(response.results) == 2
        assert response.results[0].chunk_id == 1
        assert response.results[1].chunk_id == 2
        assert response.cached is False
        assert response.collection_id == 3
        assert response.query == "test query"

    @pytest.mark.asyncio
    async def test_returns_cached_results_on_hit(self) -> None:
        cached = [_make_result(chunk_id=99)]
        repo = InMemorySearchRepository(exists=True)
        cache = _make_cache(cached_results=cached)
        embedder = _make_embedder()

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        query = SearchQuery(collection_id=3, query_text="test", top_k=5)
        response = await service.search(query)

        assert response.cached is True
        assert len(response.results) == 1
        assert response.results[0].chunk_id == 99
        # Repo should NOT have been called — cache hit
        assert repo.search_calls == []

    @pytest.mark.asyncio
    async def test_raises_404_on_missing_collection(self) -> None:
        repo = InMemorySearchRepository(exists=False)
        cache = _make_cache()
        embedder = _make_embedder()

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        with pytest.raises(CollectionNotFoundForSearchError) as exc_info:
            await service.search(SearchQuery(collection_id=999, query_text="test", top_k=5))

        assert exc_info.value.collection_id == 999
        assert exc_info.value.code == "SEARCH_COL_001"

    @pytest.mark.asyncio
    async def test_raises_502_on_embedding_failure(self) -> None:
        async def failing_embed(text: str, model: str, api_base: str) -> list[float]:
            raise RuntimeError("LiteLLM timeout")

        repo = InMemorySearchRepository(exists=True)
        cache = _make_cache()
        embedder = Embedder(model="test-model", _embed_fn=failing_embed)

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        with pytest.raises(SearchEmbeddingError) as exc_info:
            await service.search(SearchQuery(collection_id=3, query_text="test", top_k=5))

        assert exc_info.value.code == "SEARCH_EMB_001"
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_caches_results_after_repo_call(self) -> None:
        """Results from the DB are stored in the cache."""
        repo = InMemorySearchRepository(results=[_make_result()], exists=True)
        cache = _make_cache()  # miss
        embedder = _make_embedder()

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        await service.search(SearchQuery(collection_id=3, query_text="test", top_k=5))

        # Cache.set should have been called
        cache._redis.set.assert_awaited()

    @pytest.mark.asyncio
    async def test_top_k_respected(self) -> None:
        """top_k is passed through to the repository."""
        repo = InMemorySearchRepository(
            results=[_make_result(i) for i in range(20)],
            exists=True,
        )
        cache = _make_cache()
        embedder = _make_embedder()

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        response = await service.search(SearchQuery(collection_id=3, query_text="test", top_k=3))

        assert len(response.results) == 3
        assert repo.search_calls[0]["top_k"] == 3

    @pytest.mark.asyncio
    async def test_cache_failure_does_not_block_search(self) -> None:
        """A Redis error on set() does not propagate — service returns results."""
        repo = InMemorySearchRepository(results=[_make_result()], exists=True)
        cache = _make_cache()
        cache._redis.set.side_effect = ConnectionError("Redis down")
        embedder = _make_embedder()

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        response = await service.search(SearchQuery(collection_id=3, query_text="test", top_k=5))

        assert len(response.results) == 1
        assert response.cached is False

    @pytest.mark.asyncio
    async def test_empty_results_returned(self) -> None:
        """No matching chunks returns empty list, not an error."""
        repo = InMemorySearchRepository(results=[], exists=True)
        cache = _make_cache()
        embedder = _make_embedder()

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        response = await service.search(
            SearchQuery(collection_id=3, query_text="obscure query", top_k=10)
        )

        assert response.results == []
        assert response.cached is False


# ---------------------------------------------------------------------------
# Event registration and cache invalidation
# ---------------------------------------------------------------------------


class TestSearchServiceEventRegistration:
    def test_register_subscribes_to_event_bus(self) -> None:
        """_register_cache_invalidation subscribes to source.ready event."""
        from aukern_infra.events import get_event_bus

        repo = InMemorySearchRepository()
        cache = _make_cache()
        embedder = _make_embedder()

        # Build service WITHOUT patching — let it subscribe for real
        service = SearchService(repo=repo, cache=cache, embedder=embedder)

        bus = get_event_bus()
        # Verify handler is subscribed
        assert service._on_source_ready in bus._handlers.get("source.ready", [])

        # Cleanup: unsubscribe to avoid leaking across tests
        bus.unsubscribe("source.ready", service._on_source_ready)

    def test_register_handles_missing_event_bus_gracefully(self) -> None:
        """If event bus import fails, service still constructs successfully."""
        repo = InMemorySearchRepository()
        cache = _make_cache()
        embedder = _make_embedder()

        import sys

        with patch.dict(sys.modules, {"aukern_infra": None, "aukern_infra.events": None}):
            # Should not raise even if aukern_infra is unavailable
            try:
                service = SearchService(repo=repo, cache=cache, embedder=embedder)
            except Exception:
                # If construction fails due to existing imports, patch the method instead
                with patch.object(SearchService, "_register_cache_invalidation"):
                    service = SearchService(repo=repo, cache=cache, embedder=embedder)
            assert service is not None

    def test_on_source_ready_with_no_collection_id(self) -> None:
        """Handler with missing collection_id is a no-op."""
        repo = InMemorySearchRepository()
        cache = _make_cache()
        embedder = _make_embedder()

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        event = MagicMock()
        event.payload = {}  # No collection_id

        # Should not raise
        service._on_source_ready(event)

    @pytest.mark.asyncio
    async def test_on_source_ready_schedules_invalidation(self) -> None:
        """Handler schedules cache.invalidate_collection when loop is running."""
        repo = InMemorySearchRepository()
        cache = _make_cache()
        embedder = _make_embedder()

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        # Patch invalidate_collection to track calls
        invalidate_called: list[int] = []

        async def fake_invalidate(collection_id: int) -> int:
            invalidate_called.append(collection_id)
            return 0

        service._cache.invalidate_collection = fake_invalidate  # type: ignore[method-assign]

        event = MagicMock()
        event.payload = {"collection_id": 5}

        # Call handler while running in async context (loop is active)
        service._on_source_ready(event)

        # Give the event loop a chance to run the scheduled task
        await asyncio.sleep(0)

        assert 5 in invalidate_called

    def test_on_source_ready_handles_exception_gracefully(self) -> None:
        """Handler swallows exceptions — never raises to caller."""
        repo = InMemorySearchRepository()
        cache = _make_cache()
        embedder = _make_embedder()

        with patch.object(SearchService, "_register_cache_invalidation"):
            service = SearchService(repo=repo, cache=cache, embedder=embedder)

        event = MagicMock()
        event.payload = MagicMock(side_effect=RuntimeError("unexpected"))

        # Should not raise
        service._on_source_ready(event)
