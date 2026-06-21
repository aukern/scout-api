"""Unit tests for dependency providers in the sources domain."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scout_api.sources.dependencies import (
    get_ingest_service,
    get_queue_adapter,
    get_storage_adapter,
)
from scout_api.sources.queue import ArqQueueAdapter, InMemoryQueueAdapter
from scout_api.sources.storage import InMemoryStorageAdapter


def _make_request(state_attrs: dict[str, object] | None = None) -> MagicMock:
    """Build a mock FastAPI Request with configurable app.state."""
    request = MagicMock()
    state = MagicMock(spec=[])  # no attributes by default
    if state_attrs:
        for k, v in state_attrs.items():
            setattr(state, k, v)
    request.app.state = state
    return request


# ---------------------------------------------------------------------------
# get_storage_adapter
# ---------------------------------------------------------------------------


def test_get_storage_adapter_returns_app_state_storage_when_set() -> None:
    """If app.state.storage is set, return it directly."""
    custom_adapter = InMemoryStorageAdapter()
    request = _make_request({"storage": custom_adapter})

    result = get_storage_adapter(request)

    assert result is custom_adapter


def test_get_storage_adapter_returns_s3_when_bucket_and_region_configured() -> None:
    """If S3_BUCKET_NAME and S3_REGION are set, return S3StorageAdapter."""
    request = _make_request()

    with patch("scout_api.sources.dependencies.get_settings") as mock_settings:
        settings = MagicMock()
        settings.s3_bucket_name = "my-bucket"
        settings.s3_region = "us-east-1"
        settings.s3_endpoint_url = None
        mock_settings.return_value = settings

        # Patch S3StorageAdapter __init__ to avoid aioboto3 import
        with patch("scout_api.sources.dependencies.S3StorageAdapter") as MockS3:
            get_storage_adapter(request)
            MockS3.assert_called_once_with(
                bucket="my-bucket",
                region="us-east-1",
                endpoint_url=None,
            )


def test_get_storage_adapter_returns_in_memory_when_no_s3_config() -> None:
    """If S3 env vars are absent, fall back to InMemoryStorageAdapter."""
    request = _make_request()

    with patch("scout_api.sources.dependencies.get_settings") as mock_settings:
        settings = MagicMock()
        settings.s3_bucket_name = ""
        settings.s3_region = ""
        mock_settings.return_value = settings

        result = get_storage_adapter(request)

    assert isinstance(result, InMemoryStorageAdapter)


# ---------------------------------------------------------------------------
# get_queue_adapter
# ---------------------------------------------------------------------------


def test_get_queue_adapter_returns_app_state_queue_when_set() -> None:
    """If app.state.queue is set, return it directly."""
    custom_queue = InMemoryQueueAdapter()
    request = _make_request({"queue": custom_queue})

    result = get_queue_adapter(request)

    assert result is custom_queue


def test_get_queue_adapter_returns_arq_when_redis_url_configured() -> None:
    """If REDIS_URL is set, return ArqQueueAdapter."""
    request = _make_request()

    with patch("scout_api.sources.dependencies.get_settings") as mock_settings:
        settings = MagicMock()
        settings.redis_url = "redis://localhost:6379"
        mock_settings.return_value = settings

        result = get_queue_adapter(request)

    assert isinstance(result, ArqQueueAdapter)


def test_get_queue_adapter_returns_in_memory_when_no_redis_url() -> None:
    """If REDIS_URL is absent, fall back to InMemoryQueueAdapter."""
    request = _make_request()

    with patch("scout_api.sources.dependencies.get_settings") as mock_settings:
        settings = MagicMock()
        settings.redis_url = ""
        mock_settings.return_value = settings

        result = get_queue_adapter(request)

    assert isinstance(result, InMemoryQueueAdapter)


# ---------------------------------------------------------------------------
# get_ingest_service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ingest_service_returns_service_with_repo() -> None:
    """get_ingest_service builds an IngestService from a pool."""

    from scout_api.sources.service import IngestService

    mock_pool = MagicMock()
    storage = InMemoryStorageAdapter()
    queue = InMemoryQueueAdapter()
    request = MagicMock()

    service = await get_ingest_service(
        request=request, pool=mock_pool, storage=storage, queue=queue
    )

    assert isinstance(service, IngestService)
