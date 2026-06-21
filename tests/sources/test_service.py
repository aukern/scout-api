"""Unit tests for IngestService.

Uses InMemoryStorageAdapter and InMemoryQueueAdapter (zero dependencies).
The repository is mocked at the asyncpg layer.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock

import pytest

from scout_api.sources.contracts import SourceRow, SourceStatus
from scout_api.sources.errors import CollectionNotFoundError, SourceIngestionError
from scout_api.sources.queue import InMemoryQueueAdapter
from scout_api.sources.repository import SourceRepository
from scout_api.sources.service import PROCESS_SOURCE_JOB, IngestService
from scout_api.sources.storage import InMemoryStorageAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UTC = datetime.UTC
NOW = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


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
    """Build a mock SourceRepository."""
    repo = AsyncMock(spec=SourceRepository)
    repo.collection_exists.return_value = collection_exists
    if source is None:
        source = _make_source()
    repo.upsert.return_value = (source, is_refresh)
    repo.delete_chunks.return_value = chunks_deleted
    return repo  # type: ignore[return-value]


@pytest.fixture
def storage() -> InMemoryStorageAdapter:
    return InMemoryStorageAdapter()


@pytest.fixture
def queue() -> InMemoryQueueAdapter:
    return InMemoryQueueAdapter()


# ---------------------------------------------------------------------------
# ingest_url — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_ingest_url_returns_pending(
    storage: InMemoryStorageAdapter,
    queue: InMemoryQueueAdapter,
) -> None:
    """POST a URL → Source created pending, returns id."""
    repo = _make_repo()
    service = IngestService(repo=repo, storage=storage, queue=queue)

    result = await service.ingest_url(collection_id=10, url="https://example.com/doc")

    assert result.status == SourceStatus.PENDING
    assert result.id == 1
    assert result.collection_id == 10


@pytest.mark.asyncio
async def test_source_ingest_url_enqueues_job(
    storage: InMemoryStorageAdapter,
    queue: InMemoryQueueAdapter,
) -> None:
    """Processing job enqueued after URL ingest."""
    repo = _make_repo()
    service = IngestService(repo=repo, storage=storage, queue=queue)

    await service.ingest_url(collection_id=10, url="https://example.com/doc")

    assert len(queue.jobs) == 1
    assert queue.jobs[0]["job"] == PROCESS_SOURCE_JOB
    assert queue.jobs[0]["source_id"] == 1


# ---------------------------------------------------------------------------
# ingest_file — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_ingest_file_stores_in_s3(
    storage: InMemoryStorageAdapter,
    queue: InMemoryQueueAdapter,
) -> None:
    """POST a file → file bytes stored in storage."""
    repo = _make_repo()
    service = IngestService(repo=repo, storage=storage, queue=queue)

    await service.ingest_file(
        collection_id=10,
        filename="report.pdf",
        file_bytes=b"fake-pdf-content",
        content_type="application/pdf",
    )

    assert len(storage.store) == 1
    stored_key = next(iter(storage.store))
    assert "report.pdf" in stored_key
    assert storage.store[stored_key] == b"fake-pdf-content"


@pytest.mark.asyncio
async def test_source_ingest_file_enqueues_job(
    storage: InMemoryStorageAdapter,
    queue: InMemoryQueueAdapter,
) -> None:
    """Processing job enqueued after file ingest."""
    repo = _make_repo()
    service = IngestService(repo=repo, storage=storage, queue=queue)

    await service.ingest_file(
        collection_id=10,
        filename="report.pdf",
        file_bytes=b"content",
    )

    assert len(queue.jobs) == 1
    assert queue.jobs[0]["job"] == PROCESS_SOURCE_JOB


# ---------------------------------------------------------------------------
# Re-ingest / refresh semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_reingest_same_origin_refreshes_in_place(
    storage: InMemoryStorageAdapter,
    queue: InMemoryQueueAdapter,
) -> None:
    """Re-ingesting same origin refreshes in place — no duplicate, old Chunks removed."""
    repo = _make_repo(is_refresh=True, chunks_deleted=3)
    service = IngestService(repo=repo, storage=storage, queue=queue)

    result = await service.ingest_url(collection_id=10, url="https://example.com/doc")

    repo.delete_chunks.assert_called_once_with(result.id)
    # A new job is still enqueued for reprocessing
    assert len(queue.jobs) == 1


@pytest.mark.asyncio
async def test_source_same_origin_different_collection_is_separate(
    storage: InMemoryStorageAdapter,
    queue: InMemoryQueueAdapter,
) -> None:
    """Same origin in a different Collection is a separate Source."""
    repo_a = _make_repo(source=_make_source(id=1, collection_id=10))
    repo_b = _make_repo(source=_make_source(id=2, collection_id=20))

    service_a = IngestService(repo=repo_a, storage=storage, queue=queue)
    service_b = IngestService(repo=repo_b, storage=storage, queue=queue)

    result_a = await service_a.ingest_url(collection_id=10, url="https://example.com/doc")
    result_b = await service_b.ingest_url(collection_id=20, url="https://example.com/doc")

    assert result_a.id != result_b.id
    assert result_a.collection_id == 10
    assert result_b.collection_id == 20


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_ingest_collection_not_found(
    storage: InMemoryStorageAdapter,
    queue: InMemoryQueueAdapter,
) -> None:
    """CollectionNotFoundError raised when collection does not exist."""
    repo = _make_repo(collection_exists=False)
    service = IngestService(repo=repo, storage=storage, queue=queue)

    with pytest.raises(CollectionNotFoundError) as exc_info:
        await service.ingest_url(collection_id=999, url="https://example.com/doc")

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "SRC_NF_001"


@pytest.mark.asyncio
async def test_source_ingest_s3_failure_raises_ingestion_error(
    queue: InMemoryQueueAdapter,
) -> None:
    """S3 upload failure raises SourceIngestionError."""

    class FailingStorage(InMemoryStorageAdapter):
        async def upload(
            self, key: str, data: bytes, content_type: str = "application/octet-stream"
        ) -> str:
            raise RuntimeError("S3 unavailable")

    repo = _make_repo()
    service = IngestService(repo=repo, storage=FailingStorage(), queue=queue)

    with pytest.raises(SourceIngestionError) as exc_info:
        await service.ingest_file(
            collection_id=10,
            filename="doc.txt",
            file_bytes=b"content",
        )

    assert exc_info.value.code == "SRC_ING_001"
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_source_ingest_queue_failure_raises_ingestion_error(
    storage: InMemoryStorageAdapter,
) -> None:
    """Queue enqueue failure raises SourceIngestionError."""

    class FailingQueue(InMemoryQueueAdapter):
        async def enqueue(self, job_name: str, **kwargs: object) -> None:
            raise RuntimeError("Redis unavailable")

    repo = _make_repo()
    service = IngestService(repo=repo, storage=storage, queue=FailingQueue())

    with pytest.raises(SourceIngestionError) as exc_info:
        await service.ingest_url(collection_id=10, url="https://example.com/doc")

    assert exc_info.value.code == "SRC_ING_001"


# ---------------------------------------------------------------------------
# Domain event emission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_ingest_emits_source_ingested_event(
    storage: InMemoryStorageAdapter,
    queue: InMemoryQueueAdapter,
) -> None:
    """source.ingested domain event emitted after successful ingest."""
    events: list[tuple[str, dict[object, object]]] = []

    def capture(event_name: str, payload: dict[object, object]) -> None:
        events.append((event_name, payload))

    repo = _make_repo(is_refresh=False)
    service = IngestService(repo=repo, storage=storage, queue=queue, event_emit=capture)

    await service.ingest_url(collection_id=10, url="https://example.com/doc")

    assert len(events) == 1
    name, payload = events[0]
    assert name == "source.ingested"
    assert payload["collection_id"] == 10
    assert payload["is_refresh"] is False


@pytest.mark.asyncio
async def test_source_reingest_emits_refresh_event(
    storage: InMemoryStorageAdapter,
    queue: InMemoryQueueAdapter,
) -> None:
    """source.ingested with is_refresh=True emitted on re-ingest."""
    events: list[tuple[str, dict[object, object]]] = []

    def capture(event_name: str, payload: dict[object, object]) -> None:
        events.append((event_name, payload))

    repo = _make_repo(is_refresh=True)
    service = IngestService(repo=repo, storage=storage, queue=queue, event_emit=capture)

    await service.ingest_url(collection_id=10, url="https://example.com/doc")

    _, payload = events[0]
    assert payload["is_refresh"] is True


@pytest.mark.asyncio
async def test_source_ingest_event_failure_does_not_propagate(
    storage: InMemoryStorageAdapter,
    queue: InMemoryQueueAdapter,
) -> None:
    """Event emission failures are silently swallowed — ingest still succeeds."""

    def bad_emit(event_name: str, payload: object) -> None:
        raise RuntimeError("event bus crashed")

    repo = _make_repo()
    service = IngestService(repo=repo, storage=storage, queue=queue, event_emit=bad_emit)

    # Should not raise
    result = await service.ingest_url(collection_id=10, url="https://example.com/doc")
    assert result.status == SourceStatus.PENDING
