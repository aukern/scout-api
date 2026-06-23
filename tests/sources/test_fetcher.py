"""Unit tests for content fetch adapters.

HttpFetchAdapter and S3FetchAdapter tests use mocks — no real network or S3.
InMemoryFetchAdapter tests are pure in-memory.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scout_api.sources.fetcher import (
    AbstractFetchAdapter,
    HttpFetchAdapter,
    InMemoryFetchAdapter,
    S3FetchAdapter,
)

# ---------------------------------------------------------------------------
# InMemoryFetchAdapter
# ---------------------------------------------------------------------------


class TestInMemoryFetchAdapter:
    async def test_returns_seeded_content(self) -> None:
        adapter = InMemoryFetchAdapter({"https://example.com": "hello world"})
        result = await adapter.fetch("https://example.com")
        assert result == "hello world"

    async def test_missing_origin_raises_key_error(self) -> None:
        adapter = InMemoryFetchAdapter({})
        with pytest.raises(KeyError):
            await adapter.fetch("https://not-seeded.com")

    async def test_empty_map_raises_for_any_origin(self) -> None:
        adapter = InMemoryFetchAdapter()
        with pytest.raises(KeyError):
            await adapter.fetch("anything")

    async def test_multiple_origins(self) -> None:
        adapter = InMemoryFetchAdapter(
            {
                "https://a.com": "content A",
                "https://b.com": "content B",
            }
        )
        assert await adapter.fetch("https://a.com") == "content A"
        assert await adapter.fetch("https://b.com") == "content B"

    def test_satisfies_protocol(self) -> None:
        adapter = InMemoryFetchAdapter()
        assert isinstance(adapter, AbstractFetchAdapter)


# ---------------------------------------------------------------------------
# HttpFetchAdapter
# ---------------------------------------------------------------------------


class TestHttpFetchAdapter:
    async def test_fetch_returns_response_text(self) -> None:
        """Fetch returns response.text on success."""
        mock_response = MagicMock()
        mock_response.text = "page content"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            adapter = HttpFetchAdapter(timeout=5.0)
            result = await adapter.fetch("https://example.com")

        assert result == "page content"
        mock_response.raise_for_status.assert_called_once()

    async def test_fetch_raises_on_http_error(self) -> None:
        """raise_for_status propagates HTTP errors."""
        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            adapter = HttpFetchAdapter()
            with pytest.raises(httpx.HTTPStatusError):
                await adapter.fetch("https://example.com/404")

    def test_satisfies_protocol(self) -> None:
        adapter = HttpFetchAdapter()
        assert isinstance(adapter, AbstractFetchAdapter)

    def test_default_timeout_is_30(self) -> None:
        adapter = HttpFetchAdapter()
        assert adapter._timeout == 30.0  # noqa: SLF001

    def test_follow_redirects_default_true(self) -> None:
        adapter = HttpFetchAdapter()
        assert adapter._follow_redirects is True  # noqa: SLF001


# ---------------------------------------------------------------------------
# S3FetchAdapter
# ---------------------------------------------------------------------------


class TestS3FetchAdapter:
    async def test_fetch_downloads_and_decodes(self) -> None:
        mock_storage = AsyncMock()
        mock_storage.download = AsyncMock(return_value=b"s3 content bytes")

        adapter = S3FetchAdapter(mock_storage)
        result = await adapter.fetch("s3://my-bucket/path/to/file.txt")

        mock_storage.download.assert_awaited_once_with("path/to/file.txt")
        assert result == "s3 content bytes"

    async def test_fetch_decodes_binary_with_replacement(self) -> None:
        mock_storage = AsyncMock()
        mock_storage.download = AsyncMock(return_value=b"valid\xff\xfeinvalid")

        adapter = S3FetchAdapter(mock_storage)
        result = await adapter.fetch("s3://bucket/key")
        assert "valid" in result
        assert result  # non-empty

    async def test_fetch_raises_for_non_s3_origin(self) -> None:
        mock_storage = AsyncMock()
        adapter = S3FetchAdapter(mock_storage)

        with pytest.raises(RuntimeError, match="non-S3 origin"):
            await adapter.fetch("https://example.com")

    async def test_key_extraction_strips_bucket_prefix(self) -> None:
        mock_storage = AsyncMock()
        mock_storage.download = AsyncMock(return_value=b"content")

        adapter = S3FetchAdapter(mock_storage)
        await adapter.fetch("s3://my-bucket/folder/sub/file.pdf")

        mock_storage.download.assert_awaited_once_with("folder/sub/file.pdf")

    async def test_key_extraction_single_level(self) -> None:
        mock_storage = AsyncMock()
        mock_storage.download = AsyncMock(return_value=b"content")

        adapter = S3FetchAdapter(mock_storage)
        await adapter.fetch("s3://bucket/file.txt")
        mock_storage.download.assert_awaited_once_with("file.txt")

    def test_satisfies_protocol(self) -> None:
        mock_storage = MagicMock()
        adapter = S3FetchAdapter(mock_storage)
        assert isinstance(adapter, AbstractFetchAdapter)
