"""Unit tests for queue adapters."""

from __future__ import annotations

import pytest

from scout_api.sources.queue import InMemoryQueueAdapter


@pytest.mark.asyncio
async def test_in_memory_queue_records_job() -> None:
    """Enqueue captures the job name and payload."""
    q = InMemoryQueueAdapter()
    await q.enqueue("process_source", source_id=42)

    assert len(q.jobs) == 1
    assert q.jobs[0]["job"] == "process_source"
    assert q.jobs[0]["source_id"] == 42


@pytest.mark.asyncio
async def test_in_memory_queue_records_multiple_jobs() -> None:
    """Multiple enqueues accumulate in order."""
    q = InMemoryQueueAdapter()
    await q.enqueue("process_source", source_id=1)
    await q.enqueue("process_source", source_id=2)

    assert len(q.jobs) == 2
    assert q.jobs[0]["source_id"] == 1
    assert q.jobs[1]["source_id"] == 2


@pytest.mark.asyncio
async def test_in_memory_queue_starts_empty() -> None:
    """New adapter has no queued jobs."""
    q = InMemoryQueueAdapter()
    assert q.jobs == []
