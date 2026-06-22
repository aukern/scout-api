"""FastAPI router for the sessions domain.

Endpoints:
  POST   /sessions               — Open a session against a collection
  GET    /sessions               — List sessions (optional ?collection_id filter)
  GET    /sessions/{session_id}  — Fetch a session with its activity trail
  DELETE /sessions/{session_id}  — Close (delete) a session

Error responses follow the standard envelope:
  {"error": {"code": "SES_NF_001", "message": "Session 42 not found."}}
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from scout_api.db import get_pool
from scout_api.sessions.errors import (
    SessionCollectionNotFoundError,
    SessionNotFoundError,
)
from scout_api.sessions.models import (
    ActivityItem,
    ListSessionsResponse,
    OpenSessionRequest,
    SessionDetailResponse,
    SessionResponse,
)
from scout_api.sessions.repository import SessionActivityRepository, SessionRepository

logger: structlog.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ---------------------------------------------------------------------------
# POST /sessions
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=201,
    response_model=SessionResponse,
    summary="Open a session",
    description=(
        "Open a new research session against the specified collection. "
        "Returns 404 if the collection does not exist."
    ),
)
async def open_session(
    body: OpenSessionRequest,
    request: Request,
) -> JSONResponse:
    """Open a new session.

    Returns 201 with the created session and a Location header.
    Returns 404 if the collection does not exist.
    """
    pool = get_pool(request)
    async with pool.acquire() as conn:
        repo = SessionRepository(conn)
        try:
            session = await repo.open(body.collection_id, conn)
        except SessionCollectionNotFoundError as exc:
            logger.info(
                "session.open.collection_not_found",
                collection_id=body.collection_id,
                code=exc.code,
            )
            return exc.to_response()

    logger.info(
        "session.opened",
        session_id=session.id,
        collection_id=session.collection_id,
    )
    return JSONResponse(
        status_code=201,
        content={
            "id": session.id,
            "collection_id": session.collection_id,
            "created_at": session.created_at.isoformat(),
        },
        headers={"Location": f"/sessions/{session.id}"},
    )


# ---------------------------------------------------------------------------
# GET /sessions
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ListSessionsResponse,
    summary="List sessions",
    description="Return all sessions. Use ?collection_id=N to filter by collection.",
)
async def list_sessions(
    request: Request,
    collection_id: Annotated[int | None, Query(description="Filter by collection id.")] = None,
) -> ListSessionsResponse:
    """List all sessions, optionally filtered by collection_id."""
    pool = get_pool(request)
    async with pool.acquire() as conn:
        repo = SessionRepository(conn)
        rows = await repo.list_all(collection_id, conn)

    return ListSessionsResponse(
        sessions=[
            SessionResponse(
                id=r.id,
                collection_id=r.collection_id,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=len(rows),
    )


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}",
    response_model=SessionDetailResponse,
    summary="Fetch a session",
    description="Return a session and its full activity trail. Returns 404 if not found.",
)
async def get_session(session_id: int, request: Request) -> SessionDetailResponse | JSONResponse:
    """Fetch a session with its activity trail.

    Returns 200 with session + activity.
    Returns 404 if the session does not exist.
    """
    pool = get_pool(request)
    async with pool.acquire() as conn:
        repo = SessionRepository(conn)
        activity_repo = SessionActivityRepository()

        session = await repo.get(session_id, conn)
        if session is None:
            err = SessionNotFoundError(session_id)
            logger.info(
                "session.get.not_found",
                session_id=session_id,
                code=err.code,
            )
            return err.to_response()

        activity_rows = await activity_repo.list_for_session(session_id, conn)

    return SessionDetailResponse(
        id=session.id,
        collection_id=session.collection_id,
        created_at=session.created_at,
        activity=[
            ActivityItem(
                id=a.id,
                kind=a.kind,
                query=a.query,
                output=a.output,
                created_at=a.created_at,
            )
            for a in activity_rows
        ],
    )


# ---------------------------------------------------------------------------
# DELETE /sessions/{session_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{session_id}",
    status_code=204,
    summary="Close a session",
    description=(
        "Close (delete) a session. All activity is also removed. "
        "Returns 404 if the session does not exist."
    ),
)
async def close_session(session_id: int, request: Request) -> Response:
    """Close a session.

    Returns 204 on success (no body).
    Returns 404 if the session does not exist.
    """
    pool = get_pool(request)
    async with pool.acquire() as conn:
        repo = SessionRepository(conn)
        deleted = await repo.delete(session_id, conn)

    if not deleted:
        err = SessionNotFoundError(session_id)
        logger.info(
            "session.close.not_found",
            session_id=session_id,
            code=err.code,
        )
        return err.to_response()

    logger.info("session.closed", session_id=session_id)
    return Response(status_code=204)
