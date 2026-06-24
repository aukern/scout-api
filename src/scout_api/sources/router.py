"""FastAPI router for the sources domain.

Ingest endpoints (POST):
  POST /collections/{collection_id}/sources/url   — ingest a URL
  POST /collections/{collection_id}/sources/file  — ingest an uploaded file

Browse endpoints (GET):
  GET /collections/{collection_id}/sources            — list sources with status
  GET /collections/{collection_id}/sources/{source_id} — single source detail

Ingest endpoints return 201 with SourceResponse.
Browse endpoints return 200 with SourceDetailResponse / ListSourcesResponse.
Both return 404 when the collection or source does not exist.

File uploads use ``multipart/form-data`` with a single ``file`` field.
The ``python-multipart`` package must be installed for FastAPI to accept
UploadFile. If not installed, FastAPI raises an import error at startup.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from opentelemetry import trace

from scout_api.sources.dependencies import get_ingest_service
from scout_api.sources.errors import (
    CollectionNotFoundError,
    InvalidOriginError,
    SourceIngestionError,
    SourceNotFoundForBrowseError,
)
from scout_api.sources.models import (
    IngestUrlRequest,
    ListSourcesResponse,
    SourceDetailResponse,
    SourceResponse,
)
from scout_api.sources.repository import SourceRepository
from scout_api.sources.service import IngestService

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

router = APIRouter(
    prefix="/collections/{collection_id}/sources",
    tags=["sources"],
)


@router.post(
    "/url",
    status_code=201,
    response_model=SourceResponse,
    summary="Ingest a URL into a collection",
    description=(
        "Accept a URL and create a Source in the given collection. "
        "The source is created with status=pending and a processing job is enqueued immediately. "
        "Re-ingesting the same URL in the same collection refreshes the existing source in place, "
        "removing old chunks. The same URL in a different collection creates a separate source."
    ),
)
async def ingest_url(
    collection_id: int,
    body: IngestUrlRequest,
    service: IngestService = Depends(get_ingest_service),
) -> JSONResponse:
    """Ingest a URL source into a collection.

    Returns 201 with the created/refreshed source on success.
    Returns 404 if the collection does not exist.
    Returns 500 if the processing job could not be enqueued.
    """
    try:
        source = await service.ingest_url(
            collection_id=collection_id,
            url=str(body.url),
        )
    except CollectionNotFoundError as exc:
        return exc.to_response()
    except InvalidOriginError as exc:
        return exc.to_response()
    except SourceIngestionError as exc:
        return exc.to_response()

    return JSONResponse(
        status_code=201,
        content={
            "id": source.id,
            "collection_id": source.collection_id,
            "origin": source.origin,
            "status": source.status.value,
        },
        headers={"Location": f"/collections/{collection_id}/sources/{source.id}"},
    )


@router.post(
    "/file",
    status_code=201,
    response_model=SourceResponse,
    summary="Ingest an uploaded file into a collection",
    description=(
        "Accept a multipart file upload and create a Source in the given collection. "
        "The file bytes are stored in object storage; a processing job is enqueued immediately. "
        "Re-uploading the same filename to the same collection refreshes the existing source, "
        "removing old chunks. The response does not wait for processing to complete."
    ),
)
async def ingest_file(
    collection_id: int,
    file: UploadFile = File(..., description="The file to ingest."),
    service: IngestService = Depends(get_ingest_service),
) -> JSONResponse:
    """Ingest an uploaded file into a collection.

    Returns 201 with the created/refreshed source on success.
    Returns 404 if the collection does not exist.
    Returns 422 if the file is empty or has no filename.
    Returns 500 if storage upload or job enqueue fails.
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="File must have a filename.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="File must not be empty.")

    content_type = file.content_type or "application/octet-stream"

    try:
        source = await service.ingest_file(
            collection_id=collection_id,
            filename=file.filename,
            file_bytes=file_bytes,
            content_type=content_type,
        )
    except CollectionNotFoundError as exc:
        return exc.to_response()
    except InvalidOriginError as exc:
        return exc.to_response()
    except SourceIngestionError as exc:
        return exc.to_response()

    return JSONResponse(
        status_code=201,
        content={
            "id": source.id,
            "collection_id": source.collection_id,
            "origin": source.origin,
            "status": source.status.value,
        },
        headers={"Location": f"/collections/{collection_id}/sources/{source.id}"},
    )


@router.get(
    "",
    status_code=200,
    response_model=ListSourcesResponse,
    summary="List sources in a collection",
    description=(
        "Return all sources in the given collection with their current lifecycle status. "
        "Results are ordered by creation time (oldest first). "
        "Returns an empty list when the collection exists but has no sources. "
        "Returns 404 when the collection does not exist."
    ),
)
async def list_sources(
    collection_id: int,
    request: Request,
) -> JSONResponse:
    """List all sources in a collection with their lifecycle status.

    Returns 200 with a list of sources (may be empty).
    Returns 404 if the collection does not exist.
    """
    with tracer.start_as_current_span("source.http.list_sources") as span:
        span.set_attribute("collection_id", collection_id)

        async with request.app.state.pool.acquire() as conn:
            repo = SourceRepository(conn)

            # Verify the collection exists before listing
            collection_exists = await repo.collection_exists(collection_id)
            if not collection_exists:
                exc = CollectionNotFoundError(collection_id)
                return exc.to_response()

            sources = await repo.list_by_collection(collection_id)

        span.set_attribute("count", len(sources))
        logger.info("source.listed", collection_id=collection_id, count=len(sources))

        return JSONResponse(
            status_code=200,
            content={
                "sources": [
                    {
                        "id": s.id,
                        "collection_id": s.collection_id,
                        "origin": s.origin,
                        "status": s.status.value,
                        "created_at": s.created_at.isoformat(),
                        "updated_at": s.updated_at.isoformat(),
                        "failed_reason": s.failed_reason,
                    }
                    for s in sources
                ],
                "total": len(sources),
            },
        )


@router.get(
    "/{source_id}",
    status_code=200,
    response_model=SourceDetailResponse,
    summary="Fetch a single source's status and metadata",
    description=(
        "Return the full status and metadata for a single source. "
        "The source must belong to the given collection — cross-collection lookups return 404. "
        "Returns 404 when the source does not exist or belongs to a different collection."
    ),
)
async def get_source(
    collection_id: int,
    source_id: int,
    request: Request,
) -> JSONResponse:
    """Fetch a single source by ID, scoped to a collection.

    Returns 200 with the source detail on success.
    Returns 404 if the source does not exist or belongs to a different collection.
    """
    with tracer.start_as_current_span("source.http.get_source") as span:
        span.set_attribute("collection_id", collection_id)
        span.set_attribute("source_id", source_id)

        async with request.app.state.pool.acquire() as conn:
            repo = SourceRepository(conn)
            source = await repo.get_by_id(
                source_id=source_id,
                collection_id=collection_id,
            )

        if source is None:
            exc = SourceNotFoundForBrowseError(
                source_id=source_id,
                collection_id=collection_id,
            )
            return exc.to_response()

        span.set_attribute("status", source.status.value)
        logger.info(
            "source.fetched",
            source_id=source_id,
            collection_id=collection_id,
            status=source.status.value,
        )

        return JSONResponse(
            status_code=200,
            content={
                "id": source.id,
                "collection_id": source.collection_id,
                "origin": source.origin,
                "status": source.status.value,
                "created_at": source.created_at.isoformat(),
                "updated_at": source.updated_at.isoformat(),
                "failed_reason": source.failed_reason,
            },
        )
