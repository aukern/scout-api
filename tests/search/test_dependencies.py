"""Unit tests for search dependency providers.

Tests the get_redis_client and get_search_service DI functions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scout_api.search.dependencies import get_redis_client, get_search_service
from scout_api.search.service import SearchService

# ---------------------------------------------------------------------------
# get_redis_client
# ---------------------------------------------------------------------------


class TestGetRedisClient:
    def test_returns_redis_from_app_state(self) -> None:
        """Returns app.state.redis when it is set."""
        mock_redis = MagicMock()
        request = MagicMock()
        request.app.state.redis = mock_redis

        result = get_redis_client(request)

        assert result is mock_redis

    def test_builds_client_from_redis_url_when_not_on_state(self) -> None:
        """Builds a Redis client from settings when app.state.redis is absent."""
        import sys

        request = MagicMock()
        # Remove 'redis' attribute from state so hasattr() returns False
        del request.app.state.redis

        mock_redis_client = MagicMock()
        mock_aioredis = MagicMock()
        mock_aioredis.from_url = MagicMock(return_value=mock_redis_client)
        mock_redis = MagicMock()
        mock_redis.asyncio = mock_aioredis

        with (
            patch.dict(sys.modules, {"redis": mock_redis, "redis.asyncio": mock_aioredis}),
            patch(
                "scout_api.search.dependencies.get_settings",
                return_value=MagicMock(
                    redis_url="redis://localhost:6379",
                    embedding_model="test-model",
                    ollama_api_base="",
                ),
            ),
        ):
            result = get_redis_client(request)

        assert result is mock_redis_client

    def test_raises_when_redis_url_not_configured(self) -> None:
        """Raises RuntimeError when redis_url is empty and redis is installed."""
        import sys

        request = MagicMock()
        del request.app.state.redis

        mock_aioredis = MagicMock()
        mock_redis = MagicMock()
        mock_redis.asyncio = mock_aioredis

        with (
            patch.dict(sys.modules, {"redis": mock_redis, "redis.asyncio": mock_aioredis}),
            patch(
                "scout_api.search.dependencies.get_settings",
                return_value=MagicMock(redis_url=""),
            ),
        ):
            with pytest.raises(RuntimeError, match="REDIS_URL is not configured"):
                get_redis_client(request)

    def test_raises_when_redis_package_not_installed(self) -> None:
        """Raises RuntimeError with helpful message when redis package is missing."""
        import sys

        request = MagicMock()
        del request.app.state.redis

        with patch.dict(sys.modules, {"redis": None, "redis.asyncio": None}):
            with pytest.raises((RuntimeError, ImportError)):
                get_redis_client(request)


# ---------------------------------------------------------------------------
# get_search_service
# ---------------------------------------------------------------------------


class TestGetSearchService:
    def test_returns_search_service_instance(self) -> None:
        """get_search_service returns a SearchService."""
        mock_redis = AsyncMock()
        request = MagicMock()
        request.app.state.redis = mock_redis
        mock_pool = MagicMock()
        request.app.state.pool = mock_pool

        with (
            patch(
                "scout_api.search.dependencies.get_settings",
                return_value=MagicMock(
                    embedding_model="text-embedding-ada-002",
                    ollama_api_base="",
                    search_cache_ttl_seconds=300,
                    redis_url="redis://localhost",
                ),
            ),
            patch.object(SearchService, "_register_cache_invalidation"),
        ):
            service = get_search_service(request=request, pool=mock_pool)

        assert isinstance(service, SearchService)
