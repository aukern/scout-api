"""Project-wide pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from scout_api.main import create_app

# ---------------------------------------------------------------------------
# Database URL — tests that need a real DB use this
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    os.environ.get("DATABASE_URL", ""),
)


def has_test_db() -> bool:
    """Return True if a test database URL is configured."""
    return bool(TEST_DATABASE_URL)


# ---------------------------------------------------------------------------
# Infrastructure singleton reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_infra_singletons() -> Generator[None, None, None]:
    """Reset project infrastructure singletons between tests."""
    import scout_api.events as events_mod

    orig_bus = events_mod._default_bus
    events_mod._default_bus = None

    yield

    events_mod._default_bus = orig_bus


# ---------------------------------------------------------------------------
# Real database fixtures (skip if no DB configured)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_pool() -> AsyncGenerator[asyncpg.Pool, None]:
    """Create a real asyncpg pool for tests that need a database.

    Skips if TEST_DATABASE_URL is not set.
    """
    if not has_test_db():
        pytest.skip("TEST_DATABASE_URL not set — skipping DB test")

    pool = await asyncpg.create_pool(
        dsn=TEST_DATABASE_URL,
        min_size=1,
        max_size=3,
        command_timeout=10,
    )
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def db_conn(db_pool: asyncpg.Pool) -> AsyncGenerator[asyncpg.Connection, None]:
    """Provide a single connection inside a rolled-back transaction.

    Each test gets a clean state — no truncation needed.
    """
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


# ---------------------------------------------------------------------------
# App client with real DB
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_client(db_pool: asyncpg.Pool) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient wired to the real test database pool."""
    app = create_app()
    app.state.pool = db_pool

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# Mock pool fixtures (no DB required)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """A mock asyncpg pool for unit tests that do not need a real database."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def mock_conn(mock_pool: MagicMock) -> AsyncMock:
    """The mock asyncpg connection from mock_pool."""
    return mock_pool.acquire.return_value.__aenter__.return_value


@pytest_asyncio.fixture
async def mock_client(mock_pool: MagicMock) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient wired to a mock pool (no real DB needed)."""
    app = create_app()
    app.state.pool = mock_pool

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
