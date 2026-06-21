"""FastAPI router for the sources domain.

Two ingest endpoints:
  POST /collections/{collection_id}/sources/url   — ingest a URL
  POST /collections/{collection_id}/sources/file  — ingest an uploaded file

Both endpoints:
  - Return 201 with SourceResponse (id, collection_id, origin, status)
  - Return 404 if the collection does not exist
  - Return 422 on validation errors (invalid URL, empty file, etc.)
  - Return 500 on S3 or queue failures

File uploads use ``multipart/form-data`` with a single ``file`` field.
The ``python-multipart`` package must be installed for FastAPI to accept
UploadFile. If not installed, FastAPI raises an import error at startup.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from scout_api.sources.dependencies import get_ingest_service
from scout_api.sources.errors import (
    CollectionNotFoundError,
    InvalidOriginError,
    SourceIngestionError,
)
from scout_api.sources.models import IngestUrlRequest, SourceResponse
from scout_api.sources.service import IngestService

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
