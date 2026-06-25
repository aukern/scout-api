"""Unit tests for SearchCache.

Uses AsyncMock for the Redis client — no real Redis required.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from scout_api.search.cache import SearchCache, make_cache_key
from scout_api.search.contracts import SearchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(chunk_id: int = 1, score: float = 0.9) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        source_id=1,
        collection_id=3,
        content="test content",
        score=score,
        source_origin="https://example.com/doc.pdf",
    )


def _make_redis() -> AsyncMock:
    """Return a minimal async Redis mock."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.scan = AsyncMock(return_value=(0, []))
    redis.delete = AsyncMock(return_value=1)
    return redis


# ---------------------------------------------------------------------------
# make_cache_key
# ---------------------------------------------------------------------------


class TestMakeCacheKey:
    def test_format(self) -> None:
        key = make_cache_key(3, "hello world")
        assert key.startswith("search:3:")
        assert len(key) > 10

    def test_normalization_case(self) -> None:
        """Upper and lower case produce the same key."""
        assert make_cache_key(3, "Hello") == make_cache_key(3, "hello")

    def test_normalization_whitespace(self) -> None:
        """Leading/trailing whitespace is stripped."""
        assert make_cache_key(3, "  hello  ") == make_cache_key(3, "hello")

    def test_collection_scope(self) -> None:
        """Different collections produce different keys for the same query."""
        assert make_cache_key(1, "hello") != make_cache_key(2, "hello")

    def test_different_queries(self) -> None:
        """Different queries produce different keys."""
        assert make_cache_key(3, "hello") != make_cache_key(3, "world")


# ---------------------------------------------------------------------------
# SearchCache.get
# ---------------------------------------------------------------------------


class TestSearchCacheGet:
    @pytest.mark.asyncio
    async def test_returns_none_on_miss(self) -> None:
        redis = _make_redis()
        redis.get.return_value = None
        cache = SearchCache(redis=redis)

        result = await cache.get("search:3:abc")

        assert result is None
        redis.get.assert_awaited_once_with("search:3:abc")

    @pytest.mark.asyncio
    async def test_returns_deserialized_results_on_hit(self) -> None:
        r = _make_result(chunk_id=42, score=0.95)
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
            ]
        )
        redis = _make_redis()
        redis.get.return_value = payload
        cache = SearchCache(redis=redis)

        results = await cache.get("search:3:abc")

        assert results is not None
        assert len(results) == 1
        assert results[0].chunk_id == 42
        assert results[0].score == 0.95

    @pytest.mark.asyncio
    async def test_returns_none_on_redis_error(self) -> None:
        """Cache errors are swallowed — returns None instead of raising."""
        redis = _make_redis()
        redis.get.side_effect = ConnectionError("Redis unavailable")
        cache = SearchCache(redis=redis)

        result = await cache.get("search:3:abc")

        assert result is None


# ---------------------------------------------------------------------------
# SearchCache.set
# ---------------------------------------------------------------------------


class TestSearchCacheSet:
    @pytest.mark.asyncio
    async def test_serializes_and_stores(self) -> None:
        r = _make_result(chunk_id=7)
        redis = _make_redis()
        cache = SearchCache(redis=redis, ttl=300)

        await cache.set("search:3:abc", [r])

        redis.set.assert_awaited_once()
        call_args = redis.set.call_args
        key_arg = call_args[0][0]
        payload_arg = call_args[0][1]
        assert key_arg == "search:3:abc"
        parsed = json.loads(payload_arg)
        assert parsed[0]["chunk_id"] == 7
        # TTL kwarg
        assert call_args[1]["ex"] == 300

    @pytest.mark.asyncio
    async def test_swallows_redis_error(self) -> None:
        """Set errors are swallowed — does not raise."""
        redis = _make_redis()
        redis.set.side_effect = ConnectionError("Redis unavailable")
        cache = SearchCache(redis=redis)

        # Should not raise
        await cache.set("search:3:abc", [_make_result()])

    @pytest.mark.asyncio
    async def test_empty_results_stored(self) -> None:
        """An empty result list is stored (valid — query with no matches)."""
        redis = _make_redis()
        cache = SearchCache(redis=redis)

        await cache.set("search:3:abc", [])

        redis.set.assert_awaited_once()
        call_args = redis.set.call_args
        payload_arg = call_args[0][1]
        assert json.loads(payload_arg) == []


# ---------------------------------------------------------------------------
# SearchCache.invalidate_collection
# ---------------------------------------------------------------------------


class TestSearchCacheInvalidateCollection:
    @pytest.mark.asyncio
    async def test_deletes_matching_keys(self) -> None:
        redis = _make_redis()
        redis.scan.return_value = (0, [b"search:3:abc", b"search:3:def"])
        cache = SearchCache(redis=redis)

        deleted = await cache.invalidate_collection(3)

        assert deleted == 2
        redis.delete.assert_awaited_once_with(b"search:3:abc", b"search:3:def")

    @pytest.mark.asyncio
    async def test_returns_zero_on_no_keys(self) -> None:
        redis = _make_redis()
        redis.scan.return_value = (0, [])
        cache = SearchCache(redis=redis)

        deleted = await cache.invalidate_collection(99)

        assert deleted == 0
        redis.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swallows_redis_error(self) -> None:
        """Invalidation errors are swallowed — returns 0."""
        redis = _make_redis()
        redis.scan.side_effect = ConnectionError("Redis down")
        cache = SearchCache(redis=redis)

        deleted = await cache.invalidate_collection(3)

        assert deleted == 0

    @pytest.mark.asyncio
    async def test_paginates_scan(self) -> None:
        """SCAN cursor loop continues until cursor == 0."""
        redis = _make_redis()
        # First call returns cursor=5 (not done), second returns cursor=0 (done)
        redis.scan.side_effect = [
            (5, [b"search:3:aaa"]),
            (0, [b"search:3:bbb"]),
        ]
        cache = SearchCache(redis=redis)

        deleted = await cache.invalidate_collection(3)

        assert deleted == 2
        assert redis.scan.await_count == 2
