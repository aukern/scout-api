"""Tests for health check endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from scout_api.main import create_app


@pytest.fixture
def mock_pool() -> MagicMock:
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def mock_conn(mock_pool: MagicMock) -> AsyncMock:
    return mock_pool.acquire.return_value.__aenter__.return_value


async def make_client(pool: MagicMock) -> AsyncClient:
    app = create_app()
    app.state.pool = pool
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_liveness_returns_200() -> None:
    """GET /health/live always returns 200."""
    pool = MagicMock()
    async with await make_client(pool) as client:
        response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_returns_200_when_db_ok(mock_pool: MagicMock, mock_conn: AsyncMock) -> None:
    """GET /health/ready returns 200 when DB query succeeds."""
    mock_conn.fetchval = AsyncMock(return_value=1)

    async with await make_client(mock_pool) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "ok"


async def test_readiness_returns_503_when_db_fails(
    mock_pool: MagicMock, mock_conn: AsyncMock
) -> None:
    """GET /health/ready returns 503 when DB is unreachable."""
    mock_conn.fetchval = AsyncMock(side_effect=Exception("connection refused"))

    async with await make_client(mock_pool) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
