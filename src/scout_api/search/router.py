"""FastAPI router for the search domain.

Endpoint:
  POST /collections/{collection_id}/search — semantic search within a collection

Returns ranked chunks by cosine similarity. Only chunks from ready sources
are included. Results are cached in Redis for repeated identical queries.

Response 200: SearchResponse
Response 404: collection not found
Response 422: validation error (empty query, top_k out of range)
Response 502: embedding model unreachable
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from opentelemetry import trace

from scout_api.search.contracts import SearchQuery
from scout_api.search.dependencies import get_search_service
from scout_api.search.errors import CollectionNotFoundForSearchError, SearchEmbeddingError
from scout_api.search.models import SearchRequest, SearchResponse, SearchResultItem
from scout_api.search.service import SearchService

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

router = APIRouter(
    prefix="/collections/{collection_id}",
    tags=["search"],
)


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Semantic search within a collection",
    description=(
        "Embed the query and execute a pgvector cosine nearest-neighbour search "
        "scoped to the given collection. Only chunks from ready sources appear. "
        "Repeated identical searches are served from the Redis cache."
    ),
)
async def search_collection(
    collection_id: int,
    body: SearchRequest,
    service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    """POST /collections/{collection_id}/search.

    Args:
        collection_id: PK of the collection to search.
        body: Validated request body (query, top_k, optional session_id).
        service: Wired SearchService from DI.

    Returns:
        SearchResponse with ranked results and cache metadata.

    Raises:
        HTTPException 404: Collection not found.
        HTTPException 502: Embedding model unreachable.
    """
    with tracer.start_as_current_span("search.collection") as span:
        span.set_attribute("collection.id", collection_id)
        span.set_attribute("search.top_k", body.top_k)

        query = SearchQuery(
            collection_id=collection_id,
            query_text=body.query,
            top_k=body.top_k,
        )

        try:
            response = await service.search(query)
        except CollectionNotFoundForSearchError as exc:
            span.set_attribute("outcome", "collection_not_found")
            return exc.to_response()  # type: ignore[return-value]
        except SearchEmbeddingError as exc:
            span.set_attribute("outcome", "embedding_error")
            logger.error(
                "search.router.embedding_error",
                collection_id=collection_id,
                detail=exc.detail,
            )
            return exc.to_response()  # type: ignore[return-value]

        span.set_attribute("outcome", "success")
        span.set_attribute("search.returned", len(response.results))
        span.set_attribute("search.cached", response.cached)

        return SearchResponse(
            results=[
                SearchResultItem(
                    chunk_id=r.chunk_id,
                    source_id=r.source_id,
                    source_origin=r.source_origin,
                    content=r.content,
                    score=r.score,
                )
                for r in response.results
            ],
            total=len(response.results),
            collection_id=response.collection_id,
            query=response.query,
            cached=response.cached,
        )
