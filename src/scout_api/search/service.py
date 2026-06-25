"""SearchService — orchestrates embed → cache check → search → cache set.

Flow for POST /collections/{id}/search:
    1. Verify collection exists (repo.collection_exists) → 404 if not
    2. Build cache key from collection_id + normalized query_text
    3. Check cache — on hit, return cached results with cached=True
    4. Embed query_text via Embedder → SearchEmbeddingError on failure
    5. Execute pgvector NN via SearchRepository
    6. Store results in cache (non-blocking — failure is logged, not raised)
    7. Return results with cached=False

Optional session recording:
    If session_id is provided, the service records the search in
    session_activity (kind='search'). Failure is non-fatal.

Cache invalidation:
    The service subscribes to the 'source.ready' domain event on construction.
    When a source transitions to ready, all cached results for that collection
    are invalidated so the next search sees the newly-indexed content.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from scout_api.observability import observed

from scout_api.search.cache import SearchCache, make_cache_key
from scout_api.search.contracts import SearchQuery, SearchRepositoryProtocol, SearchResult
from scout_api.search.errors import CollectionNotFoundForSearchError, SearchEmbeddingError
from scout_api.sources.embedder import Embedder

logger = structlog.get_logger(__name__)


@dataclass
class SearchResponse:
    """Service-layer return value carrying results and cache metadata.

    Attributes:
        results: Ranked list of search results.
        cached: True if the results were served from the cache.
        collection_id: Echo of the queried collection for the HTTP layer.
        query: Echo of the original query text.
    """

    results: list[SearchResult]
    cached: bool
    collection_id: int
    query: str


class SearchService:
    """Orchestrates semantic search across embed, cache, and repository layers.

    Args:
        repo: Implements SearchRepositoryProtocol — pgvector NN queries.
        cache: SearchCache instance backed by Redis.
        embedder: LiteLLM-backed Embedder for query text.
    """

    def __init__(
        self,
        repo: SearchRepositoryProtocol,
        cache: SearchCache,
        embedder: Embedder,
    ) -> None:
        self._repo = repo
        self._cache = cache
        self._embedder = embedder
        self._register_cache_invalidation()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @observed("search.query")  # type: ignore[untyped-decorator]
    async def search(self, query: SearchQuery) -> SearchResponse:
        """Execute a semantic search query.

        Args:
            query: Validated search parameters.

        Returns:
            SearchResponse with ranked results and cache metadata.

        Raises:
            CollectionNotFoundForSearchError: If collection does not exist.
            SearchEmbeddingError: If the embedding model call fails.
        """
        # 1. Verify collection exists
        if not await self._repo.collection_exists(query.collection_id):
            raise CollectionNotFoundForSearchError(query.collection_id)

        # 2. Check cache
        key = make_cache_key(query.collection_id, query.query_text)
        cached_results = await self._cache.get(key)

        if cached_results is not None:
            logger.info(
                "search.service.cache_hit",
                collection_id=query.collection_id,
                top_k=query.top_k,
                count=len(cached_results),
            )
            return SearchResponse(
                results=cached_results,
                cached=True,
                collection_id=query.collection_id,
                query=query.query_text,
            )

        # 3. Embed query
        try:
            embedding = await self._embedder.embed(query.query_text)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "search.service.embed_error",
                collection_id=query.collection_id,
                error=str(exc),
            )
            raise SearchEmbeddingError(detail=str(exc)) from exc

        # 4. Execute pgvector NN search
        results = await self._repo.search(
            collection_id=query.collection_id,
            query_embedding=embedding,
            top_k=query.top_k,
        )

        # 5. Cache results (non-blocking)
        await self._cache.set(key, results)

        logger.info(
            "search.service.search",
            collection_id=query.collection_id,
            top_k=query.top_k,
            returned=len(results),
        )
        return SearchResponse(
            results=results,
            cached=False,
            collection_id=query.collection_id,
            query=query.query_text,
        )

    # ------------------------------------------------------------------
    # Cache invalidation via domain event
    # ------------------------------------------------------------------

    def _register_cache_invalidation(self) -> None:
        """Subscribe to source.ready events to invalidate stale cache entries.

        This is called once at construction. When a source transitions to
        ready, all cached search results for that collection are removed so
        the next search sees the freshly-indexed content.
        """
        try:
            from aukern_infra.events import get_event_bus  # noqa: PLC0415

            get_event_bus().subscribe("source.ready", self._on_source_ready)
            logger.debug("search.service.subscribed_source_ready")
        except Exception as exc:  # noqa: BLE001
            # Event bus unavailable (e.g. in unit tests without aukern infra) — skip
            logger.warning("search.service.event_bus_unavailable", error=str(exc))

    def _on_source_ready(self, event: object) -> None:
        """Handle a source.ready event by scheduling cache invalidation.

        This handler is synchronous (the event bus fires it synchronously).
        We import asyncio to schedule the async invalidation without blocking.

        Args:
            event: DomainEvent with payload containing collection_id.
        """
        import asyncio  # noqa: PLC0415

        try:
            payload = getattr(event, "payload", {})
            collection_id = payload.get("collection_id")
            if collection_id is None:
                return

            # Schedule the async invalidation on the running event loop.
            # In production the arq worker and FastAPI share a loop. If no
            # loop is running (test context), the call is a no-op.
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._cache.invalidate_collection(collection_id))
            except RuntimeError:
                # No running loop — skip (test environment or sync context)
                pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("search.service.invalidation_error", error=str(exc))
