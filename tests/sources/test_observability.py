"""Observability tests for the sources domain — Slice 18.

Validates the qa-engineer standard requirements:
  - Structured log events emitted for ingest operations (domain audit trail)
  - Error response shape: no internal/context/traceback fields exposed
  - OTel span attributes recorded on service operations
  - 404 (CollectionNotFoundError) must not produce ERROR-level log

Log assertions use structlog.testing.capture_logs(), which captures structlog
events as dicts regardless of the configured renderer/logger factory.
Tests use InMemoryStorageAdapter / InMemoryQueueAdapter (zero external deps).
"""

from __future__ import annotations

import datetime
import inspect
import json
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from scout_api.sources.contracts import SourceRow, SourceStatus
from scout_api.sources.errors import (
    CollectionNotFoundError,
    SourceIngestionError,
)
from scout_api.sources.queue import InMemoryQueueAdapter
from scout_api.sources.repository import SourceRepository
from scout_api.sources.service import IngestService
from scout_api.sources.storage import InMemoryStorageAdapter

UTC = datetime.UTC
NOW = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(
    id: int = 1,
    collection_id: int = 10,
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


def _make_repo(
    collection_exists: bool = True,
    source: SourceRow | None = None,
    is_refresh: bool = False,
    chunks_deleted: int = 0,
) -> SourceRepository:
    repo = AsyncMock(spec=SourceRepository)
    repo.collection_exists.return_value = collection_exists
    if source is None:
        source = _make_source()
    repo.upsert.return_value = (source, is_refresh)
    repo.delete_chunks.return_value = chunks_deleted
    return repo  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Structured log event assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_url_emits_source_created_log_event() -> None:
    """ingest_url must emit a source.created structured log event on first ingest."""
    storage = InMemoryStorageAdapter()
    queue = InMemoryQueueAdapter()
    repo = _make_repo(is_refresh=False)
    service = IngestService(repo=repo, storage=storage, queue=queue)

    with structlog.testing.capture_logs() as captured:
        await service.ingest_url(collection_id=10, url="https://example.com/doc")

    events = [e["event"] for e in captured]
    assert "source.created" in events, (
        f"Expected 'source.created' log event after first ingest. Got: {events}"
    )


@pytest.mark.asyncio
async def test_ingest_url_refresh_emits_source_refresh_log_event() -> None:
    """ingest_url on re-ingest must emit a source.refresh structured log event."""
    storage = InMemoryStorageAdapter()
    queue = InMemoryQueueAdapter()
    repo = _make_repo(is_refresh=True, chunks_deleted=2)
    service = IngestService(repo=repo, storage=storage, queue=queue)

    with structlog.testing.capture_logs() as captured:
        await service.ingest_url(collection_id=10, url="https://example.com/doc")

    events = [e["event"] for e in captured]
    assert "source.refresh" in events, (
        f"Expected 'source.refresh' log event on re-ingest. Got: {events}"
    )


@pytest.mark.asyncio
async def test_ingest_file_emits_source_uploaded_log_event() -> None:
    """ingest_file must emit a source.uploaded structured log event after S3 upload."""
    storage = InMemoryStorageAdapter()
    queue = InMemoryQueueAdapter()
    repo = _make_repo(is_refresh=False)
    service = IngestService(repo=repo, storage=storage, queue=queue)

    with structlog.testing.capture_logs() as captured:
        await service.ingest_file(
            collection_id=10,
            filename="report.pdf",
            file_bytes=b"pdf content",
            content_type="application/pdf",
        )

    events = [e["event"] for e in captured]
    assert "source.uploaded" in events, (
        f"Expected 'source.uploaded' log event after file ingest. Got: {events}"
    )


@pytest.mark.asyncio
async def test_collection_not_found_does_not_produce_error_log() -> None:
    """CollectionNotFoundError (404) must not produce an error-level structlog event.

    NotFoundError is expected behavior — it must not be logged at error level,
    which would trigger false alerts in production.
    """
    storage = InMemoryStorageAdapter()
    queue = InMemoryQueueAdapter()
    repo = _make_repo(collection_exists=False)
    service = IngestService(repo=repo, storage=storage, queue=queue)

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(CollectionNotFoundError):
            await service.ingest_url(collection_id=999, url="https://example.com/doc")

    error_entries = [e for e in captured if e.get("log_level") == "error"]
    assert not error_entries, (
        f"CollectionNotFoundError must not produce error-level log. Got: {error_entries}"
    )


# ---------------------------------------------------------------------------
# Error response shape: no internal fields exposed
# ---------------------------------------------------------------------------


def test_collection_not_found_error_response_shape() -> None:
    """SRC_NF_001 response must not expose internal fields."""
    err = CollectionNotFoundError(collection_id=42)
    response = err.to_response()
    data = json.loads(response.body)

    assert "error" in data
    assert data["error"]["code"] == "SRC_NF_001"
    assert "message" in data["error"]

    for forbidden in ("internal", "context", "traceback", "stack"):
        assert forbidden not in data, f"Error response exposes '{forbidden}'"
        assert forbidden not in data["error"], f"Error envelope exposes '{forbidden}'"

    msg = data["error"]["message"]
    assert "Traceback" not in msg
    assert ".py" not in msg


def test_source_ingestion_error_response_shape() -> None:
    """SRC_ING_001 response must not expose internal fields."""
    err = SourceIngestionError("Redis connection refused")
    response = err.to_response()
    data = json.loads(response.body)

    assert "error" in data
    assert data["error"]["code"] == "SRC_ING_001"

    for forbidden in ("internal", "context", "traceback", "stack"):
        assert forbidden not in data
        assert forbidden not in data["error"]

    msg = data["error"]["message"]
    assert "Traceback" not in msg
    assert ".py" not in msg

    # Internal detail ("Redis connection refused") is allowed in message —
    # but no Python file paths or exception class names with module path
    assert "scout_api" not in msg


def test_source_ingestion_error_message_does_not_expose_stack_path() -> None:
    """SourceIngestionError message must not contain Python file paths."""
    err = SourceIngestionError("timeout after 10s")
    data = json.loads(err.to_response().body)
    msg = data["error"]["message"]
    assert "/" not in msg or not any(part.endswith(".py") for part in msg.split())


# ---------------------------------------------------------------------------
# OTel span coverage on service operations
# ---------------------------------------------------------------------------


def test_ingest_service_spans_present_in_source() -> None:
    """IngestService.ingest_url and ingest_file must use OTel spans.

    Static verification that span instrumentation is wired in the service.
    """
    from scout_api.sources import service as svc_module

    source = inspect.getsource(svc_module)

    assert "start_as_current_span" in source, (
        "IngestService must instrument operations with OTel spans"
    )
    assert "source.ingest_url" in source, "Expected 'source.ingest_url' span name in IngestService"
    assert "source.ingest_file" in source, (
        "Expected 'source.ingest_file' span name in IngestService"
    )


def test_ingest_service_spans_record_exception_on_error() -> None:
    """IngestService must call span.record_exception on failures."""
    from scout_api.sources import service as svc_module

    source = inspect.getsource(svc_module)
    assert "record_exception" in source, (
        "IngestService must call span.record_exception in error paths"
    )
    assert "set_status" in source, "IngestService must set span status on error"


# ---------------------------------------------------------------------------
# Prometheus RED metrics — @observed counter increments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observed_ingest_url_increments_prometheus_counter() -> None:
    """@observed("sources.ingest_url") must increment operations_total in the Prometheus registry.

    Verifies that the decorator is wired correctly — not just present in source code.
    Uses prometheus_client.generate_latest() to read the in-process registry after
    calling ingest_url, matching the pattern in test_infra_modules::test_observed_emits_prometheus_red_metrics.
    """
    pytest.importorskip("prometheus_client")
    from prometheus_client import generate_latest

    storage = InMemoryStorageAdapter()
    queue = InMemoryQueueAdapter()
    repo = _make_repo(is_refresh=False)
    service = IngestService(repo=repo, storage=storage, queue=queue)

    await service.ingest_url(collection_id=10, url="https://example.com/red-metrics-test")

    exposition = generate_latest().decode()
    assert "operations_total" in exposition, (
        "Prometheus registry must contain operations_total counter after ingest_url call"
    )
    assert 'operation="sources.ingest_url"' in exposition, (
        "operations_total must carry operation=\"sources.ingest_url\" label — "
        "@observed decorator not correctly wired on ingest_url"
    )


@pytest.mark.asyncio
async def test_observed_ingest_file_increments_prometheus_counter() -> None:
    """@observed("sources.ingest_file") must increment operations_total in the Prometheus registry."""
    pytest.importorskip("prometheus_client")
    from prometheus_client import generate_latest

    storage = InMemoryStorageAdapter()
    queue = InMemoryQueueAdapter()
    repo = _make_repo(is_refresh=False)
    service = IngestService(repo=repo, storage=storage, queue=queue)

    await service.ingest_file(
        collection_id=10,
        filename="red_metrics.pdf",
        file_bytes=b"pdf",
        content_type="application/pdf",
    )

    exposition = generate_latest().decode()
    assert "operations_total" in exposition, (
        "Prometheus registry must contain operations_total counter after ingest_file call"
    )
    assert 'operation="sources.ingest_file"' in exposition, (
        "operations_total must carry operation=\"sources.ingest_file\" label — "
        "@observed decorator not correctly wired on ingest_file"
    )
