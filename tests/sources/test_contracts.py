"""Unit tests for SourceRow and SourceStatus contracts."""

from __future__ import annotations

import datetime

import pytest

from scout_api.sources.contracts import SourceRow, SourceStatus


def test_source_status_values() -> None:
    """SourceStatus enum has all four lifecycle states."""
    assert SourceStatus.PENDING == "pending"
    assert SourceStatus.PROCESSING == "processing"
    assert SourceStatus.READY == "ready"
    assert SourceStatus.FAILED == "failed"


def test_source_row_is_frozen() -> None:
    """SourceRow is immutable — attribute assignment raises."""
    row = SourceRow(
        id=1,
        collection_id=10,
        origin="https://example.com",
        status=SourceStatus.PENDING,
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        updated_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
    )
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        row.id = 2  # type: ignore[misc]


def test_source_row_equality() -> None:
    """Two SourceRows with the same fields are equal."""
    ts = datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
    r1 = SourceRow(
        id=1,
        collection_id=10,
        origin="u",
        status=SourceStatus.PENDING,
        created_at=ts,
        updated_at=ts,
    )
    r2 = SourceRow(
        id=1,
        collection_id=10,
        origin="u",
        status=SourceStatus.PENDING,
        created_at=ts,
        updated_at=ts,
    )
    assert r1 == r2
