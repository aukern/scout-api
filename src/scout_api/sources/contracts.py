"""Published contracts for the sources domain.

These are the types and protocols imported by other slices:
  - Slice 3 (Process a source) needs SourceRow and SourceStatus
  - Slice 4 (Browse sources) needs SourceRow

Importing from contracts.py guarantees stability — the internal implementation
modules (repository.py, service.py) may change without affecting callers.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
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
    """

    id: int
    collection_id: int
    origin: str
    status: SourceStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime


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
