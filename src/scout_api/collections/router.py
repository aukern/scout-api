"""FastAPI router for the collections domain.

Three endpoints:
  POST   /collections          — create a new collection
  GET    /collections          — list all collections
  DELETE /collections/{name}   — delete a collection (cascades to sources/chunks)

All responses use the glossary vocabulary: 'collection' / 'collections'.
Error responses use the standard envelope: {"error": {"code": "...", "message": "..."}}.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from scout_api.collections.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
)
from scout_api.collections.models import (
    CollectionResponse,
    CreateCollectionRequest,
    ListCollectionsResponse,
)
from scout_api.collections.repository import CollectionRepository
from scout_api.db import get_pool

router = APIRouter(prefix="/collections", tags=["collections"])


@router.post(
    "",
    status_code=201,
    response_model=CollectionResponse,
    summary="Create a collection",
    description=(
        "Create a named partition of knowledge. "
        "The name must be unique across all collections. "
        "Returns 409 if a collection with this name already exists."
    ),
)
async def create_collection(
    body: CreateCollectionRequest,
    request: Request,
    response: Response,
) -> JSONResponse:
    """Create a new collection.

    Returns 201 with the created collection and a Location header.
    Returns 409 if the name is already in use.
    """
    pool = get_pool(request)
    async with pool.acquire() as conn:
        repo = CollectionRepository(conn)
        try:
            collection = await repo.create(body.name)
        except CollectionAlreadyExistsError as exc:
            return exc.to_response()

    response.headers["Location"] = f"/collections/{collection.name}"
    return JSONResponse(
        status_code=201,
        content={"id": collection.id, "name": collection.name},
        headers={"Location": f"/collections/{collection.name}"},
    )


@router.get(
    "",
    response_model=ListCollectionsResponse,
    summary="List all collections",
    description="Return all collections ordered by creation time (oldest first).",
)
async def list_collections(request: Request) -> ListCollectionsResponse:
    """List all collections."""
    pool = get_pool(request)
    async with pool.acquire() as conn:
        repo = CollectionRepository(conn)
        rows = await repo.list_all()

    return ListCollectionsResponse(
        collections=[CollectionResponse(id=r.id, name=r.name) for r in rows],
        total=len(rows),
    )


@router.delete(
    "/{name}",
    status_code=204,
    summary="Delete a collection",
    description=(
        "Delete a collection by name. "
        "All Sources and Chunks belonging to this collection are also deleted. "
        "Returns 404 if the collection does not exist."
    ),
)
async def delete_collection(name: str, request: Request) -> Response:
    """Delete a collection and all its Sources and Chunks.

    Returns 204 on success (no body).
    Returns 404 if the collection does not exist.
    """
    pool = get_pool(request)
    async with pool.acquire() as conn:
        repo = CollectionRepository(conn)
        try:
            await repo.delete(name)
        except CollectionNotFoundError as exc:
            return exc.to_response()

    return Response(status_code=204)
