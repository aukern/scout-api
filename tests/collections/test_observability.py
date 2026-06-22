"""Observability tests for the collections domain — Slice 17.

Validates the qa-engineer standard requirements:
  - Structured log events emitted for each operation (domain audit trail)
  - Error response shape: no internal/context/traceback fields exposed
  - OTel span attributes recorded on repository operations

Log assertions use structlog.testing.capture_logs(), which captures structlog
events as dicts regardless of the configured renderer/logger factory.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import structlog.testing
from httpx import ASGITransport, AsyncClient

from scout_api.collections.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
)
from scout_api.collections.repository import CollectionRow
from scout_api.main import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_client() -> AsyncClient:
    """Build an AsyncClient with a mock pool (no DB required)."""
    from unittest.mock import MagicMock

    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    app = create_app()
    app.state.pool = pool
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Structured log event assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_collection_emits_created_log_event() -> None:
    """POST /collections must emit a collection.created structured log event."""
    with patch("scout_api.collections.router.CollectionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.create = AsyncMock(return_value=CollectionRow(id=42, name="log-test"))

        with structlog.testing.capture_logs() as captured:
            async with _make_app_client() as client:
                resp = await client.post("/collections", json={"name": "log-test"})

    assert resp.status_code == 201
    events = [e["event"] for e in captured]
    assert "collection.created" in events, f"Expected 'collection.created' log event. Got: {events}"


@pytest.mark.asyncio
async def test_create_collection_duplicate_emits_warning_log() -> None:
    """POST /collections with duplicate name must emit collection.already_exists warning."""
    with patch("scout_api.collections.router.CollectionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.create = AsyncMock(side_effect=CollectionAlreadyExistsError("dup"))

        with structlog.testing.capture_logs() as captured:
            async with _make_app_client() as client:
                resp = await client.post("/collections", json={"name": "dup"})

    assert resp.status_code == 409
    events = [e["event"] for e in captured]
    assert "collection.already_exists" in events, (
        f"Expected 'collection.already_exists' log event. Got: {events}"
    )


@pytest.mark.asyncio
async def test_delete_collection_emits_deleted_log_event() -> None:
    """DELETE /collections/{name} must emit a collection.deleted structured log event."""
    with patch("scout_api.collections.router.CollectionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.delete = AsyncMock(return_value=None)

        with structlog.testing.capture_logs() as captured:
            async with _make_app_client() as client:
                resp = await client.delete("/collections/my-collection")

    assert resp.status_code == 204
    events = [e["event"] for e in captured]
    assert "collection.deleted" in events, f"Expected 'collection.deleted' log event. Got: {events}"


@pytest.mark.asyncio
async def test_delete_collection_not_found_does_not_emit_error_log() -> None:
    """DELETE /collections/{name} for missing collection must not emit log_level=error.

    404 is expected behavior — must be logged at warning or info, never error.
    """
    with patch("scout_api.collections.router.CollectionRepository") as MockRepo:
        instance = MockRepo.return_value
        instance.delete = AsyncMock(side_effect=CollectionNotFoundError("ghost"))

        with structlog.testing.capture_logs() as captured:
            async with _make_app_client() as client:
                resp = await client.delete("/collections/ghost")

    assert resp.status_code == 404

    error_entries = [e for e in captured if e.get("log_level") == "error"]
    assert not error_entries, (
        f"CollectionNotFoundError must not produce error-level log. Got: {error_entries}"
    )


# ---------------------------------------------------------------------------
# Error response shape: no internal fields exposed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collection_already_exists_error_response_shape() -> None:
    """409 error response must not expose internal fields (no traceback, context, internal)."""
    err = CollectionAlreadyExistsError("my-collection")
    response = err.to_response()
    body = response.body

    import json

    data = json.loads(body)

    # Must have the standard envelope
    assert "error" in data
    assert "code" in data["error"]
    assert "message" in data["error"]
    assert data["error"]["code"] == "COLLECTION_ALREADY_EXISTS"

    # Must NOT expose internal fields
    for forbidden in ("internal", "context", "traceback", "detail", "stack"):
        assert forbidden not in data, f"Error response exposes internal field: '{forbidden}'"
    for forbidden in ("internal", "context", "traceback", "stack"):
        assert forbidden not in data["error"], (
            f"Error envelope exposes internal field: '{forbidden}'"
        )

    # Message must not contain file paths or Python tracebacks
    msg = data["error"]["message"]
    assert "Traceback" not in msg
    assert ".py" not in msg


@pytest.mark.asyncio
async def test_collection_not_found_error_response_shape() -> None:
    """404 error response must not expose internal fields."""
    err = CollectionNotFoundError("ghost")
    response = err.to_response()

    import json

    data = json.loads(response.body)

    assert "error" in data
    assert data["error"]["code"] == "COLLECTION_NOT_FOUND"

    for forbidden in ("internal", "context", "traceback", "stack"):
        assert forbidden not in data
        assert forbidden not in data["error"]

    msg = data["error"]["message"]
    assert "Traceback" not in msg
    assert ".py" not in msg


# ---------------------------------------------------------------------------
# OTel span coverage on repository operations
# ---------------------------------------------------------------------------


def test_collection_repository_spans_present_in_source() -> None:
    """CollectionRepository methods must use tracer.start_as_current_span.

    This is a static verification that the span instrumentation is wired —
    avoids the overhead of running a full OTel SDK in tests.
    """
    import inspect

    from scout_api.collections import repository

    source = inspect.getsource(repository)
    assert "start_as_current_span" in source, (
        "CollectionRepository must instrument operations with OTel spans"
    )

    # Verify all four public methods are spanned
    for span_name in (
        "collection.db.create",
        "collection.db.list_all",
        "collection.db.delete",
        "collection.db.exists",
    ):
        assert span_name in source, f"Expected OTel span '{span_name}' in CollectionRepository"
