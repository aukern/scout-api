"""HTTP-level tests for the sources router.

Uses mock_client from conftest.py (no real DB) with dependency_overrides
to inject InMemoryStorageAdapter, InMemoryQueueAdapter, and a mock service.

Tests cover the full HTTP path: request parsing → service dispatch → response.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from scout_api.main import create_app
from scout_api.sources.contracts import SourceRow, SourceStatus
from scout_api.sources.dependencies import get_ingest_service
from scout_api.sources.errors import CollectionNotFoundError, SourceIngestionError
from scout_api.sources.service import IngestService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = datetime.UTC
NOW = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_source(
    id: int = 1,
    collection_id: int = 1,
    origin: str = "https://example.com",
    status: SourceStatus = SourceStatus.PENDING,
) -> SourceRow:
    return SourceRow(
        id=id,
        collection_id=collection_id,
        origin=origin,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def _mock_service(
    source: SourceRow | None = None,
    raise_exc: Exception | None = None,
) -> IngestService:
    """Build an IngestService mock with controllable behavior."""
    svc = AsyncMock(spec=IngestService)
    if source is None:
        source = _make_source()
    if raise_exc is not None:
        svc.ingest_url.side_effect = raise_exc
        svc.ingest_file.side_effect = raise_exc
    else:
        svc.ingest_url.return_value = source
        svc.ingest_file.return_value = source
    return svc  # type: ignore[return-value]


@pytest.fixture
def sources_client(mock_pool: Any) -> Any:
    """AsyncClient with dependency overrides for sources tests."""
    # This fixture builder is used inside the individual tests via
    # create_app() + override pattern. Returning the factory here is cleaner.
    return mock_pool  # unused directly — each test builds its own app


# ---------------------------------------------------------------------------
# URL ingest endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_ingest_url_returns_pending() -> None:
    """POST /collections/1/sources/url → 201 with pending status."""
    svc = _mock_service(
        source=_make_source(id=5, collection_id=1, origin="https://example.com/doc")
    )
    app = create_app()
    app.dependency_overrides[get_ingest_service] = lambda: svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/collections/1/sources/url",
            json={"url": "https://example.com/doc"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == 5
    assert data["status"] == "pending"
    assert data["collection_id"] == 1
    assert "location" in resp.headers


@pytest.mark.asyncio
async def test_source_ingest_url_collection_not_found_returns_404() -> None:
    """POST to non-existent collection → 404."""
    exc = CollectionNotFoundError(collection_id=999)
    svc = _mock_service(raise_exc=exc)
    app = create_app()
    app.dependency_overrides[get_ingest_service] = lambda: svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/collections/999/sources/url",
            json={"url": "https://example.com"},
        )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SRC_NF_001"


@pytest.mark.asyncio
async def test_source_ingest_invalid_url_returns_422() -> None:
    """POST with invalid URL → 422 from Pydantic validation."""
    app = create_app()
    svc = _mock_service()
    app.dependency_overrides[get_ingest_service] = lambda: svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/collections/1/sources/url",
            json={"url": "not-a-valid-url"},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_source_ingest_url_enqueue_failure_returns_500() -> None:
    """Queue failure → 500 with SRC_ING_001."""
    exc = SourceIngestionError("Redis down")
    svc = _mock_service(raise_exc=exc)
    app = create_app()
    app.dependency_overrides[get_ingest_service] = lambda: svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/collections/1/sources/url",
            json={"url": "https://example.com"},
        )

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "SRC_ING_001"


# ---------------------------------------------------------------------------
# File ingest endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_ingest_file_returns_201() -> None:
    """POST file → 201 with source response."""
    svc = _mock_service(source=_make_source(id=7, origin="file://10/abc123/report.pdf"))
    app = create_app()
    app.dependency_overrides[get_ingest_service] = lambda: svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/collections/1/sources/file",
            files={"file": ("report.pdf", b"pdf-content", "application/pdf")},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == 7
    assert data["status"] == "pending"
    assert "location" in resp.headers


@pytest.mark.asyncio
async def test_source_ingest_file_empty_returns_422() -> None:
    """POST empty file → 422."""
    app = create_app()
    svc = _mock_service()
    app.dependency_overrides[get_ingest_service] = lambda: svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/collections/1/sources/file",
            files={"file": ("empty.txt", b"", "text/plain")},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_source_ingest_file_collection_not_found_returns_404() -> None:
    """File ingest to missing collection → 404."""
    exc = CollectionNotFoundError(collection_id=99)
    svc = _mock_service(raise_exc=exc)
    app = create_app()
    app.dependency_overrides[get_ingest_service] = lambda: svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/collections/99/sources/file",
            files={"file": ("doc.pdf", b"content", "application/pdf")},
        )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SRC_NF_001"


@pytest.mark.asyncio
async def test_source_ingest_file_s3_failure_returns_500() -> None:
    """S3 failure → 500 with SRC_ING_001."""
    exc = SourceIngestionError("S3 down")
    svc = _mock_service(raise_exc=exc)
    app = create_app()
    app.dependency_overrides[get_ingest_service] = lambda: svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/collections/1/sources/file",
            files={"file": ("doc.pdf", b"content", "application/pdf")},
        )

    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Re-ingest (refresh) via HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_reingest_same_origin_returns_201_with_same_id() -> None:
    """Re-ingesting same URL returns 201 with the same source id (refresh)."""
    existing_source = _make_source(id=3, origin="https://example.com/doc")
    svc = _mock_service(source=existing_source)
    app = create_app()
    app.dependency_overrides[get_ingest_service] = lambda: svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/collections/1/sources/url",
            json={"url": "https://example.com/doc"},
        )

    assert resp.status_code == 201
    assert resp.json()["id"] == 3


# ---------------------------------------------------------------------------
# Browse endpoint helpers
# ---------------------------------------------------------------------------


def _make_source_full(
    id: int = 1,
    collection_id: int = 42,
    origin: str = "https://example.com/doc.pdf",
    status: SourceStatus = SourceStatus.READY,
    failed_reason: str | None = None,
) -> SourceRow:
    """SourceRow with timestamps for browse endpoint tests."""
    return SourceRow(
        id=id,
        collection_id=collection_id,
        origin=origin,
        status=status,
        created_at=NOW,
        updated_at=NOW,
        failed_reason=failed_reason,
    )


def _make_mock_pool(
    collection_exists: bool = True,
    sources: list[SourceRow] | None = None,
    single_source: SourceRow | None = None,
) -> MagicMock:
    """Build a mock asyncpg pool that controls SourceRepository behaviour.

    The browse endpoints call pool.acquire() as a context manager, then
    instantiate SourceRepository(conn). We patch SourceRepository at the
    router module level so the mock controls what the repository returns.
    """
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool


# ---------------------------------------------------------------------------
# GET /collections/{id}/sources — list sources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sources_returns_200_with_sources() -> None:
    """GET list → 200 with correct shape including timestamps."""
    source = _make_source_full(id=1, status=SourceStatus.READY)
    app = create_app()
    pool = _make_mock_pool()
    app.state.pool = pool

    with patch("scout_api.sources.router.SourceRepository") as MockRepo:
        repo_instance = AsyncMock()
        repo_instance.collection_exists.return_value = True
        repo_instance.list_by_collection.return_value = [source]
        MockRepo.return_value = repo_instance

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/collections/42/sources")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["sources"]) == 1
    s = data["sources"][0]
    assert s["id"] == 1
    assert s["status"] == "ready"
    assert "created_at" in s
    assert "updated_at" in s
    assert s["failed_reason"] is None


@pytest.mark.asyncio
async def test_list_sources_returns_404_when_collection_not_found() -> None:
    """GET list for non-existent collection → 404 with SRC_NF_001."""
    app = create_app()
    pool = _make_mock_pool()
    app.state.pool = pool

    with patch("scout_api.sources.router.SourceRepository") as MockRepo:
        repo_instance = AsyncMock()
        repo_instance.collection_exists.return_value = False
        MockRepo.return_value = repo_instance

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/collections/999/sources")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SRC_NF_001"


@pytest.mark.asyncio
async def test_list_sources_returns_empty_list_for_valid_collection_with_no_sources() -> None:
    """GET list for valid collection with no sources → 200 empty list."""
    app = create_app()
    pool = _make_mock_pool()
    app.state.pool = pool

    with patch("scout_api.sources.router.SourceRepository") as MockRepo:
        repo_instance = AsyncMock()
        repo_instance.collection_exists.return_value = True
        repo_instance.list_by_collection.return_value = []
        MockRepo.return_value = repo_instance

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/collections/42/sources")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["sources"] == []


# ---------------------------------------------------------------------------
# GET /collections/{id}/sources/{source_id} — single source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_source_returns_200_with_full_detail() -> None:
    """GET single source → 200 with all fields including failed_reason=null."""
    source = _make_source_full(id=5, status=SourceStatus.READY)
    app = create_app()
    pool = _make_mock_pool()
    app.state.pool = pool

    with patch("scout_api.sources.router.SourceRepository") as MockRepo:
        repo_instance = AsyncMock()
        repo_instance.get_by_id.return_value = source
        MockRepo.return_value = repo_instance

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/collections/42/sources/5")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 5
    assert data["collection_id"] == 42
    assert data["status"] == "ready"
    assert data["failed_reason"] is None
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_get_source_returns_200_with_failed_reason_when_failed() -> None:
    """GET single failed source → 200 with failed_reason populated."""
    source = _make_source_full(
        id=7,
        status=SourceStatus.FAILED,
        failed_reason="HTTP 403 fetching origin URL",
    )
    app = create_app()
    pool = _make_mock_pool()
    app.state.pool = pool

    with patch("scout_api.sources.router.SourceRepository") as MockRepo:
        repo_instance = AsyncMock()
        repo_instance.get_by_id.return_value = source
        MockRepo.return_value = repo_instance

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/collections/42/sources/7")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert data["failed_reason"] == "HTTP 403 fetching origin URL"


@pytest.mark.asyncio
async def test_get_source_returns_404_when_source_not_found() -> None:
    """GET non-existent source → 404 with SRC_NF_002."""
    app = create_app()
    pool = _make_mock_pool()
    app.state.pool = pool

    with patch("scout_api.sources.router.SourceRepository") as MockRepo:
        repo_instance = AsyncMock()
        repo_instance.get_by_id.return_value = None
        MockRepo.return_value = repo_instance

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/collections/42/sources/999")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SRC_NF_002"


@pytest.mark.asyncio
async def test_get_source_returns_404_when_source_belongs_to_different_collection() -> None:
    """GET source from wrong collection → 404 (SQL returns None for cross-collection)."""
    app = create_app()
    pool = _make_mock_pool()
    app.state.pool = pool

    with patch("scout_api.sources.router.SourceRepository") as MockRepo:
        repo_instance = AsyncMock()
        # Repository returns None when collection_id doesn't match (SQL-enforced)
        repo_instance.get_by_id.return_value = None
        MockRepo.return_value = repo_instance

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/collections/99/sources/5")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SRC_NF_002"
