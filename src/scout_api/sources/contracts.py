"""Published contracts for the sources domain.

These are the types and protocols imported by other slices:
  - Slice 3 (Process a source) needs SourceRow and SourceStatus
  - Slice 4 (Browse sources) needs SourceRow

Importing from contracts.py guarantees stability — the internal implementation
modules (repository.py, service.py) may change without affecting callers.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

# ---------------------------------------------------------------------------
# Domain value: SourceStatus
# ---------------------------------------------------------------------------


class SourceStatus(str, Enum):
    """Lifecycle states for a Source."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Domain value: SourceRow
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRow:
    """Immutable snapshot of a row from the sources table.

    Frozen so callers cannot mutate the fetched state and confuse it with
    a persisted update. Use the repository to mutate the database.

    Attributes:
        id: Auto-increment primary key.
        collection_id: FK to the owning collection.
        origin: URL string or ``s3://bucket/key`` for uploaded files.
        status: Current lifecycle state.
        created_at: Timestamp of initial creation (UTC).
        updated_at: Timestamp of last status change or re-ingest (UTC).
        failed_reason: Human-readable failure reason; None when status is not
            ``failed``. Stored in the DB for observability — no log grep needed.
    """

    id: int
    collection_id: int
    origin: str
    status: SourceStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime
    failed_reason: str | None = field(default=None)


# ---------------------------------------------------------------------------
# Domain value: ChunkRow
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkRow:
    """Immutable snapshot of a row from the chunks table.

    Attributes:
        id: Auto-increment primary key.
        source_id: FK to the owning source.
        content: Text content of this chunk.
        position: 0-based order index within the source.
        embedding: Float vector from the embedding model; None before embedding.
    """

    id: int
    source_id: int
    content: str
    position: int
    embedding: list[float] | None = field(default=None)


# ---------------------------------------------------------------------------
# Repository Protocol (for cross-slice type checking)
# ---------------------------------------------------------------------------


class SourceRepositoryProtocol(Protocol):
    """Protocol describing the SourceRepository interface.

    Other slices that need to interact with source persistence should depend
    on this Protocol, not on the concrete SourceRepository class. This keeps
    the import boundary clean and enables test doubles.
    """

    async def get_by_origin(
        self,
        collection_id: int,
        origin: str,
    ) -> SourceRow | None: ...

    async def upsert(
        self,
        collection_id: int,
        origin: str,
    ) -> tuple[SourceRow, bool]: ...

    async def delete_chunks(self, source_id: int) -> int: ...


# ---------------------------------------------------------------------------
# Processing Repository Protocol (worker-side operations)
# ---------------------------------------------------------------------------


class ProcessingRepositoryProtocol(Protocol):
    """Protocol for the worker-side processing repository.

    Handles status transitions and chunk persistence during source processing.
    Other slices (e.g. search) may depend on this protocol to read chunks.
    """

    async def get_source(self, source_id: int) -> SourceRow | None: ...

    async def set_processing(self, source_id: int) -> SourceRow: ...

    async def set_ready(self, source_id: int) -> SourceRow: ...

    async def set_failed(self, source_id: int, reason: str) -> SourceRow: ...

    async def delete_chunks(self, source_id: int) -> int: ...

    async def insert_chunk(
        self,
        source_id: int,
        content: str,
        position: int,
        embedding: list[float],
    ) -> int: ...
