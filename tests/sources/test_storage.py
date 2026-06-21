"""Unit tests for storage adapters."""

from __future__ import annotations

import pytest

from scout_api.sources.storage import InMemoryStorageAdapter


@pytest.mark.asyncio
async def test_in_memory_storage_upload_stores_bytes() -> None:
    """Upload stores bytes under the given key."""
    adapter = InMemoryStorageAdapter()
    key = await adapter.upload("sources/1/2/doc.pdf", b"hello", "application/pdf")

    assert key == "sources/1/2/doc.pdf"
    assert adapter.store["sources/1/2/doc.pdf"] == b"hello"


@pytest.mark.asyncio
async def test_in_memory_storage_upload_returns_key() -> None:
    """Upload returns the key used."""
    adapter = InMemoryStorageAdapter()
    returned_key = await adapter.upload("my/key", b"data")
    assert returned_key == "my/key"


@pytest.mark.asyncio
async def test_in_memory_storage_delete_removes_key() -> None:
    """Delete removes the stored object."""
    adapter = InMemoryStorageAdapter()
    await adapter.upload("key1", b"data")
    await adapter.delete("key1")
    assert "key1" not in adapter.store


@pytest.mark.asyncio
async def test_in_memory_storage_delete_missing_key_is_noop() -> None:
    """Deleting a non-existent key does not raise."""
    adapter = InMemoryStorageAdapter()
    await adapter.delete("does/not/exist")  # Should not raise


@pytest.mark.asyncio
async def test_in_memory_storage_overwrites_existing_key() -> None:
    """Uploading to an existing key overwrites the bytes."""
    adapter = InMemoryStorageAdapter()
    await adapter.upload("key", b"original")
    await adapter.upload("key", b"updated")
    assert adapter.store["key"] == b"updated"
