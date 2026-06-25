"""SearchCache — Redis-backed cache for semantic search results.

Cache key design:
    search:{collection_id}:{sha256(query_text.strip().lower())}

Properties:
- Collection-scoped: collection_id is part of the key, so a cache hit for
  collection A can never be returned for collection B.
- Normalized: query is trimmed and lowercased before hashing, so "  Hello  "
  and "hello" hit the same cache entry.
- Collision-free: SHA-256 hash of the normalized query text.

Invalidation:
    invalidate_collection(collection_id) uses Redis SCAN + DEL to remove all
    keys matching search:{collection_id}:*. Called when a source transitions
    to 'ready' so stale results are not served after new content is indexed.

Failure policy:
    Cache failure is never fatal. get() returns None on error; set() and
    invalidate_collection() log and swallow exceptions. The service falls
    through to the database on any cache error.

Serialization:
    Results are stored as JSON. Each SearchResult is converted to a dict and
    back. The list is serialized with json.dumps / json.loads.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import structlog

from scout_api.search.contracts import SearchResult

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "search"
_DEFAULT_TTL = 300  # 5 minutes


def make_cache_key(collection_id: int, query_text: str) -> str:
    """Build the Redis cache key for a search query.

    Args:
        collection_id: Collection scope.
        query_text: Raw query string from the caller.

    Returns:
        Key string: ``search:{collection_id}:{sha256_hex}``.
    """
    normalized = query_text.strip().lower()
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return f"{_KEY_PREFIX}:{collection_id}:{digest}"


def _serialize(results: list[SearchResult]) -> str:
    """Serialize a list of SearchResult to JSON string."""
    return json.dumps(
        [
            {
                "chunk_id": r.chunk_id,
                "source_id": r.source_id,
                "collection_id": r.collection_id,
                "content": r.content,
                "score": r.score,
                "source_origin": r.source_origin,
            }
            for r in results
        ]
    )


def _deserialize(raw: str) -> list[SearchResult]:
    """Deserialize a JSON string back to a list of SearchResult."""
    items = json.loads(raw)
    return [
        SearchResult(
            chunk_id=item["chunk_id"],
            source_id=item["source_id"],
            collection_id=item["collection_id"],
            content=item["content"],
            score=item["score"],
            source_origin=item["source_origin"],
        )
        for item in items
    ]


class SearchCache:
    """Redis cache adapter for search results.

    Args:
        redis: An async Redis client (redis.asyncio.Redis).
        ttl: Cache TTL in seconds (default 300 / 5 minutes).
    """

    def __init__(self, redis: Redis, ttl: int = _DEFAULT_TTL) -> None:
        self._redis = redis
        self._ttl = ttl

    async def get(self, key: str) -> list[SearchResult] | None:
        """Return cached results for key, or None on miss/error.

        Args:
            key: Cache key from make_cache_key().

        Returns:
            Deserialized list of SearchResult, or None on miss or Redis error.
        """
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            results = _deserialize(raw)
            logger.debug("search.cache.hit", key=key, count=len(results))
            return results
        except Exception as exc:  # noqa: BLE001
            logger.warning("search.cache.get_error", key=key, error=str(exc))
            return None

    async def set(self, key: str, results: list[SearchResult]) -> None:
        """Store results under key with TTL.

        Args:
            key: Cache key from make_cache_key().
            results: Results to cache.
        """
        try:
            payload = _serialize(results)
            await self._redis.set(key, payload, ex=self._ttl)
            logger.debug("search.cache.set", key=key, count=len(results), ttl=self._ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("search.cache.set_error", key=key, error=str(exc))

    async def invalidate_collection(self, collection_id: int) -> int:
        """Remove all cached search results for a collection.

        Uses SCAN + DEL to avoid blocking Redis with a large KEYS call.

        Args:
            collection_id: The collection whose cache entries should be removed.

        Returns:
            Number of keys deleted (0 if Redis error or no keys found).
        """
        pattern = f"{_KEY_PREFIX}:{collection_id}:*"
        deleted = 0
        try:
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
                if keys:
                    await self._redis.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
            logger.info(
                "search.cache.invalidated",
                collection_id=collection_id,
                deleted=deleted,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "search.cache.invalidate_error",
                collection_id=collection_id,
                error=str(exc),
            )
        return deleted
