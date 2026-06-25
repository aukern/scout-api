"""FastAPI application entry point for scout-api.

The app uses a lifespan context manager to:
1. Create the asyncpg connection pool on startup.
2. Close the pool gracefully on shutdown.

The pool is stored on app.state.pool and accessed by routers via db.get_pool().
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aukern_infra.metrics import metrics_asgi_app
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from scout_api.briefs.router import router as briefs_router
from scout_api.collections.router import router as collections_router
from scout_api.config import get_settings
from scout_api.db import create_pool
from scout_api.health import router as health_router
from scout_api.search.router import router as search_router
from scout_api.sessions.router import router as sessions_router
from scout_api.sources.router import router as sources_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage the asyncpg pool lifecycle.

    Creates the pool before the app accepts requests and closes it after
    shutdown is complete, ensuring all in-flight queries finish cleanly.
    """
    settings = get_settings()
    app.state.pool = await create_pool(
        database_url=settings.database_url,
        max_size=settings.max_connections,
    )
    yield
    await app.state.pool.close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Registers routers, exception handlers, and metadata.
    The lifespan manages the database pool.
    """
    app = FastAPI(
        title="Scout API",
        description=(
            "The tool layer for AI research agents. "
            "Ingests knowledge, runs semantic search, and answers questions "
            "over what it has ingested."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        field = " -> ".join(str(loc) for loc in first.get("loc", [])) if first else "request"
        msg = first.get("msg", "Invalid request") if first else "Invalid request"
        # Strip non-serializable ctx (Pydantic v2 includes the raw Exception in ctx.error)
        safe_detail = [{k: v for k, v in err.items() if k not in ("ctx", "url")} for err in errors]
        return JSONResponse(
            status_code=422,
            content={
                "error": "VALIDATION_ERROR",
                "message": f"{field}: {msg}",
                "detail": safe_detail,
            },
        )

    # Health probes (liveness + readiness)
    app.include_router(health_router)

    # Domain routers
    app.include_router(collections_router)
    app.include_router(sources_router)
    app.include_router(sessions_router)
    app.include_router(briefs_router)
    app.include_router(search_router)

    # Prometheus RED metrics scrape endpoint
    app.mount("/metrics", metrics_asgi_app())

    return app


app = create_app()
