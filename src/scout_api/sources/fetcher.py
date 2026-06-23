"""Content fetch adapters for source processing.

Each adapter satisfies the ``AbstractFetchAdapter`` Protocol and returns the
raw text content of a source origin string.

Supported origins:
  - ``http://`` / ``https://`` — HTTP GET via httpx (30 s timeout).
  - ``s3://bucket/key`` — S3 download via AbstractStorageAdapter.
  - ``file://path`` — local file read (test / dev only).
  - Any other string is treated as a file path.

The ``InMemoryFetchAdapter`` is used in tests — no network or storage required.

Production wiring:
  The arq worker injects the appropriate adapter based on the origin scheme.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)


@runtime_checkable
class AbstractFetchAdapter(Protocol):
    """Protocol for content fetch adapters."""

    async def fetch(self, origin: str) -> str:
        """Fetch raw text content for the given origin.

        Args:
            origin: URL, S3 URI, file path, or any string the adapter handles.

        Returns:
            Raw text content. Binary content is decoded as UTF-8 with
            ``errors="replace"``.

        Raises:
            SourceProcessingError: If the fetch fails.
        """
        ...


class HttpFetchAdapter:
    """Fetch content from http(s) URLs via httpx.

    Args:
        timeout: Request timeout in seconds. Default 30.
        follow_redirects: Whether to follow HTTP redirects. Default True.
    """

    def __init__(self, timeout: float = 30.0, follow_redirects: bool = True) -> None:
        self._timeout = timeout
        self._follow_redirects = follow_redirects

    async def fetch(self, origin: str) -> str:
        """GET the URL and return the response body as text.

        HTML is returned as-is; the worker may strip tags in future slices.
        Binary responses are decoded as UTF-8 with ``errors="replace"``.

        Args:
            origin: A ``http://`` or ``https://`` URL.

        Returns:
            Response body as a string.

        Raises:
            RuntimeError: On network or HTTP error.
        """
        import httpx  # noqa: PLC0415

        logger.info("fetcher.http.start", origin=origin)
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=self._follow_redirects,
        ) as client:
            response = await client.get(origin)
            response.raise_for_status()

        content = response.text
        logger.info("fetcher.http.done", origin=origin, content_length=len(content))
        return content


class S3FetchAdapter:
    """Download content from S3 (or localstack) via AbstractStorageAdapter.

    Parses ``s3://bucket/key`` URIs and delegates the download to the injected
    storage adapter. The storage adapter abstracts the actual S3 client, so
    this adapter works in tests with InMemoryStorageAdapter.

    Args:
        storage: An object satisfying the AbstractStorageAdapter Protocol.
    """

    def __init__(self, storage: Any) -> None:
        self._storage = storage

    async def fetch(self, origin: str) -> str:
        """Download an S3 object and return its content as text.

        Args:
            origin: A ``s3://bucket/key`` URI.

        Returns:
            Object content decoded as UTF-8 with ``errors="replace"``.

        Raises:
            RuntimeError: If origin is not an S3 URI or download fails.
        """
        if not origin.startswith("s3://"):
            raise RuntimeError(f"S3FetchAdapter received non-S3 origin: {origin!r}")

        # Strip s3:// prefix and split into bucket / key
        path = origin[5:]  # "bucket/key/..."
        slash = path.index("/")
        key = path[slash + 1 :]

        logger.info("fetcher.s3.start", origin=origin, key=key)
        data: bytes = await self._storage.download(key)
        content = data.decode("utf-8", errors="replace")
        logger.info("fetcher.s3.done", origin=origin, key=key, content_length=len(content))
        return content


class InMemoryFetchAdapter:
    """In-memory fetch adapter for tests — no network or storage calls.

    Pre-seed content by mapping origin strings to text:

        adapter = InMemoryFetchAdapter({"https://example.com": "hello world"})

    Fetching an origin not in the map raises ``KeyError``.

    Args:
        content_map: Mapping of origin → text content.
    """

    def __init__(self, content_map: dict[str, str] | None = None) -> None:
        self._map: dict[str, str] = content_map or {}

    async def fetch(self, origin: str) -> str:
        """Return pre-seeded content for ``origin``.

        Args:
            origin: Must be a key in the content_map provided at construction.

        Returns:
            The seeded text content.

        Raises:
            KeyError: If origin is not in the map.
        """
        return self._map[origin]
