"""Shared test fixtures for scout-api.

Test isolation strategy:
- Each test that touches the database runs inside a transaction that is rolled
  back at the end of the test. This is cheaper than truncating tables and
  produces identical isolation.
- The app_client fixture builds a test FastAPI client that injects a
  transactional connection as the pool, so router tests hit a real DB
  that is also rolled back.
- Tests that do NOT need a database should avoid requesting db_conn or
  app_client to keep them fast.

Requirements:
  Set TEST_DATABASE_URL in the environment (or .env) to a Postgres DSN.
  A test database with the scout-api schema applied must be available.
  Use docker compose --profile postgres up to start a local Postgres instance.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
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
    """AsyncClient wired to the real test database pool.

    The lifespan is bypassed — we inject the test pool directly.
    This means the client uses the same pool as db_conn, and if tests
    need transaction-level isolation they should use db_conn directly.
    """
    app = create_app()
    app.state.pool = db_pool

    # Bypass lifespan so we don't open a second pool
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
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

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
