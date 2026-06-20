"""Unit tests for CollectionRepository.

These tests use a real PostgreSQL database to verify SQL correctness.
They are skipped if TEST_DATABASE_URL is not set.

Test isolation: each test runs inside a transaction rolled back at teardown,
so no cleanup code is needed.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import asyncpg

from scout_api.collections.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
)
from scout_api.collections.repository import CollectionRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def repo(db_conn: asyncpg.Connection) -> CollectionRepository:
    """Return a CollectionRepository using the transactional test connection."""
    return CollectionRepository(db_conn)


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_returns_row_with_id_and_name(repo: CollectionRepository) -> None:
    row = await repo.create("my-research")
    assert row.id > 0
    assert row.name == "my-research"


@pytest.mark.integration
async def test_create_duplicate_name_raises_already_exists(
    repo: CollectionRepository,
) -> None:
    await repo.create("duplicate-name")
    with pytest.raises(CollectionAlreadyExistsError) as exc_info:
        await repo.create("duplicate-name")
    assert exc_info.value.code == "COLLECTION_ALREADY_EXISTS"


@pytest.mark.integration
async def test_create_different_names_succeed(repo: CollectionRepository) -> None:
    r1 = await repo.create("alpha")
    r2 = await repo.create("beta")
    assert r1.name == "alpha"
    assert r2.name == "beta"
    assert r1.id != r2.id


# ---------------------------------------------------------------------------
# list_all()
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_all_empty(repo: CollectionRepository) -> None:
    rows = await repo.list_all()
    assert rows == []


@pytest.mark.integration
async def test_list_all_returns_in_creation_order(repo: CollectionRepository) -> None:
    await repo.create("first")
    await repo.create("second")
    await repo.create("third")
    rows = await repo.list_all()
    # IDs are ascending (ORDER BY id ASC)
    assert [r.name for r in rows] == ["first", "second", "third"]
    assert rows[0].id < rows[1].id < rows[2].id


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_delete_existing_collection(repo: CollectionRepository) -> None:
    await repo.create("to-delete")
    await repo.delete("to-delete")  # should not raise
    rows = await repo.list_all()
    assert all(r.name != "to-delete" for r in rows)


@pytest.mark.integration
async def test_delete_nonexistent_raises_not_found(repo: CollectionRepository) -> None:
    with pytest.raises(CollectionNotFoundError) as exc_info:
        await repo.delete("no-such-collection")
    assert exc_info.value.code == "COLLECTION_NOT_FOUND"


# ---------------------------------------------------------------------------
# exists()
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_exists_returns_true_when_present(repo: CollectionRepository) -> None:
    await repo.create("present")
    assert await repo.exists("present") is True


@pytest.mark.integration
async def test_exists_returns_false_when_absent(repo: CollectionRepository) -> None:
    assert await repo.exists("ghost") is False
