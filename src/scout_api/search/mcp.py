"""MCP server for the search domain.

Exposes one tool to AI agents:
    search_collection — semantic search within a collection

This module wraps the same SearchService used by the HTTP layer — no
duplicate logic. The MCP server is integrated into the FastAPI app via
create_mcp_app() in main.py.

fastmcp and mcp are lazy-imported (proxy blocked at install time — they are
production-only dependencies). The module works without them installed; at
runtime the search tool requires fastmcp>=2.0 and mcp>=1.0.

Integration in main.py:
    from scout_api.search.mcp import create_mcp_app
    _mcp_app = create_mcp_app()
    app = FastAPI(
        ...,
        routes=list(_mcp_app.routes),
        lifespan=combine_lifespans(lifespan, _mcp_app.lifespan),
    )

DB access from the MCP layer:
    The MCP layer cannot use the FastAPI request/app.state. Instead it builds
    a fresh asyncpg pool from settings on each call. In production this is cheap
    (asyncpg connection pools are lightweight). For testing, override the
    search_collection tool by injecting mock state.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _build_mcp_server() -> Any:  # pragma: no cover
    """Build the FastMCP server instance.

    Lazy-imports fastmcp so the module can be imported without it installed.

    Returns:
        FastMCP instance with the search_collection tool registered.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from typing import Annotated  # noqa: PLC0415

        from fastmcp import FastMCP  # noqa: PLC0415
        from fastmcp.exceptions import ToolError  # noqa: PLC0415
        from mcp.types import ToolAnnotations  # noqa: PLC0415
        from pydantic import Field  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "fastmcp and mcp are required for the MCP server. "
            "Install them with: pip install fastmcp>=2.0 mcp>=1.0"
        ) from exc

    from scout_api.search.contracts import SearchQuery  # noqa: PLC0415
    from scout_api.search.errors import (  # noqa: PLC0415
        CollectionNotFoundForSearchError,
        SearchEmbeddingError,
    )

    mcp: Any = FastMCP(
        "search",
        instructions=(
            "Provides semantic search over collections of ingested knowledge. "
            "Use search_collection to find relevant chunks by free-text query. "
            "Only chunks from ready sources are returned — sources still processing "
            "or failed do not appear in results."
        ),
    )

    @mcp.tool(  # type: ignore[untyped-decorator]
        annotations=ToolAnnotations(
            title="Search Collection",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        )
    )
    async def search_collection(
        collection_id: Annotated[
            int,
            Field(
                description=(
                    "The integer ID of the collection to search. "
                    "Only chunks from sources within this collection are returned."
                ),
                examples=[1, 42],
                gt=0,
            ),
        ],
        query: Annotated[
            str,
            Field(
                description=(
                    "Free-text search query embedded and compared against chunk embeddings "
                    "using cosine similarity. Minimum 1 character, maximum 2000 characters."
                ),
                min_length=1,
                max_length=2000,
            ),
        ],
        top_k: Annotated[
            int,
            Field(
                description="Maximum number of results to return. Range 1 to 100. Default 10.",
                default=10,
                ge=1,
                le=100,
            ),
        ] = 10,
    ) -> list[dict[str, Any]]:
        """Search a Collection semantically. Returns chunks ranked by relevance.

        Embeds the query text using the configured embedding model, executes a
        pgvector cosine nearest-neighbour query scoped to the collection, and
        returns the top_k most relevant chunks.

        Use this when: an agent needs to find relevant knowledge within a
        specific collection before answering a question or generating a brief.
        Call this before any synthesis step — retrieve first, synthesize second.

        Returns:
            List of result dicts, each with keys:
                chunk_id (int), source_id (int), source_origin (str),
                content (str), score (float 0-1), collection_id (int).
            Ordered by descending cosine similarity score.
            Example: [{"chunk_id": 42, "source_id": 7,
                       "source_origin": "https://example.com/paper.pdf",
                       "content": "chunk text...", "score": 0.923, "collection_id": 3}]
        """
        log = logger.bind(tool="search_collection", collection_id=collection_id)
        try:
            from scout_api.config import get_settings  # noqa: PLC0415
            from scout_api.db import create_pool  # noqa: PLC0415
            from scout_api.search.cache import SearchCache  # noqa: PLC0415
            from scout_api.search.repository import SearchRepository  # noqa: PLC0415
            from scout_api.search.service import SearchService  # noqa: PLC0415
            from scout_api.sources.embedder import Embedder  # noqa: PLC0415

            settings = get_settings()

            # Redis — optional; on ImportError fall through (no caching in MCP)
            redis_client: Any = None
            try:
                import redis.asyncio as aioredis  # noqa: PLC0415

                redis_client = aioredis.from_url(
                    settings.redis_url or "redis://localhost:6379",
                    decode_responses=True,
                )
            except ImportError:
                pass  # cache unavailable — service will fall through to DB

            # Build a fresh pool from settings for the MCP request path
            try:
                pool = await create_pool(
                    database_url=settings.database_url,
                    max_size=1,
                )
                conn: Any = pool
            except Exception as pool_exc:  # noqa: BLE001
                raise ToolError(f"Database connection unavailable: {pool_exc}") from pool_exc

            repo = SearchRepository(conn)
            cache = SearchCache(redis=redis_client, ttl=settings.search_cache_ttl_seconds)
            embedder = Embedder(
                model=settings.embedding_model,
                api_base=settings.ollama_api_base,
            )

            service = SearchService(repo=repo, cache=cache, embedder=embedder)
            search_query = SearchQuery(
                collection_id=collection_id,
                query_text=query,
                top_k=top_k,
            )
            response = await service.search(search_query)

            return [
                {
                    "chunk_id": r.chunk_id,
                    "source_id": r.source_id,
                    "source_origin": r.source_origin,
                    "content": r.content,
                    "score": r.score,
                    "collection_id": r.collection_id,
                }
                for r in response.results
            ]

        except (CollectionNotFoundForSearchError, SearchEmbeddingError) as exc:
            raise ToolError(exc.message) from exc
        except ToolError:
            raise
        except Exception as exc:
            log.error("mcp.search_collection.error", error=str(exc))
            raise ToolError(f"search_collection failed: {exc}") from exc

    return mcp


def create_mcp_app() -> Any:  # pragma: no cover
    """Return the MCP ASGI app serving at /mcp.

    Lazy-builds the FastMCP server. Wire into main.py using
    routes=list(create_mcp_app().routes) — do NOT use app.mount().

    Returns:
        Starlette ASGI application for the MCP server.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    mcp = _build_mcp_server()
    return mcp.http_app(path="/mcp")
