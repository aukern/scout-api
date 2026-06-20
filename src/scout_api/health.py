"""Health check endpoints for scout-api.

Two endpoints following the liveness/readiness probe pattern:
- GET /health/live  — always returns 200 if the process is running (liveness)
- GET /health/ready — checks database connectivity (readiness)
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from scout_api.db import get_pool

router = APIRouter(tags=["health"])


@router.get("/health/live", include_in_schema=False)
async def liveness() -> JSONResponse:
    """Liveness probe — confirms the process is alive.

    Returns 200 always. Docker and Kubernetes use this to decide whether
    to restart the container. It does not check external dependencies.
    """
    return JSONResponse(content={"status": "ok"})


@router.get("/health/ready", include_in_schema=False)
async def readiness(request: Request) -> JSONResponse:
    """Readiness probe — confirms the service can handle traffic.

    Checks database connectivity by running a cheap query. Returns 503 if
    the database is unreachable.
    """
    try:
        pool = get_pool(request)
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return JSONResponse(content={"status": "ready", "database": "ok"})
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": str(exc)},
        )
