"""asyncpg connection pool management for scout-api.

The pool is created once during application lifespan and stored on app.state.pool.
Routers and repositories access it via the typed get_pool() accessor.
"""

from __future__ import annotations

import asyncpg
from fastapi import Request


async def create_pool(database_url: str, max_size: int = 10) -> asyncpg.Pool:
    """Create and return an asyncpg connection pool.

    Args:
        database_url: PostgreSQL DSN (postgresql://user:pass@host:port/db).
        max_size: Maximum number of connections in the pool.

    Returns:
        An initialized asyncpg connection pool.
    """
    return await asyncpg.create_pool(
        dsn=database_url,
        min_size=1,
        max_size=max_size,
        command_timeout=30,
    )


def get_pool(request: Request) -> asyncpg.Pool:
    """Return the connection pool from app state.

    This is the standard accessor for routers to retrieve the pool that was
    created during the lifespan startup.

    Args:
        request: The current FastAPI request.

    Returns:
        The asyncpg pool stored on app.state.pool.

    Raises:
        RuntimeError: If the pool was not initialized (lifespan not configured).
    """
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "Database pool not initialized. "
            "Ensure the app lifespan is configured correctly."
        )
    return pool
