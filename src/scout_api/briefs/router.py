"""FastAPI router for the briefs domain.

Endpoints:
  POST  /sessions/{session_id}/briefs  — Save an Answer as a Brief
  GET   /sessions/{session_id}/briefs  — List Briefs in a Session

Both endpoints are nested under /sessions/{session_id} to make the
"belongs to exactly one Session" ownership explicit in the URL structure.

Error responses follow the standard envelope:
  {"error": {"code": "BRF_NF_001", "message": "Session 42 not found."}}
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from scout_api.briefs.contracts import BriefCitation
from scout_api.briefs.errors import BriefSessionNotFoundError
from scout_api.briefs.models import (
    BriefResponse,
    CitationResponse,
    ListBriefsResponse,
    SaveBriefRequest,
)
from scout_api.briefs.repository import BriefRepository
from scout_api.db import get_pool

logger: structlog.BoundLogger = structlog.get_logger(__name__)

# Note: prefix is /sessions (shared namespace with the sessions router).
# FastAPI resolves paths without conflict because the sessions router owns
# /{session_id} (GET/DELETE) while this router owns /{session_id}/briefs (POST/GET).
router = APIRouter(prefix="/sessions", tags=["briefs"])


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/briefs
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/briefs",
    status_code=201,
    response_model=BriefResponse,
    summary="Save a Brief",
    description=(
        "Save an Answer (text + Citations) as a durable Brief within the given session. "
        "Returns 404 if the session does not exist."
    ),
)
async def save_brief(
    session_id: int,
    body: SaveBriefRequest,
    request: Request,
) -> JSONResponse:
    """Save an Answer as a Brief in the given session.

    Returns 201 with the created Brief and a Location header.
    Returns 404 if the session does not exist (BRF_NF_001).
    """
    pool = get_pool(request)
    citations = [
        BriefCitation(
            source_id=c.source_id,
            chunk_id=c.chunk_id,
            excerpt=c.excerpt,
        )
        for c in body.citations
    ]

    async with pool.acquire() as conn:
        repo = BriefRepository()
        try:
            brief = await repo.save(
                session_id=session_id,
                answer_text=body.answer_text,
                citations=citations,
                conn=conn,
            )
        except BriefSessionNotFoundError as exc:
            logger.info(
                "brief.save.session_not_found",
                session_id=session_id,
                code=exc.code,
            )
            return exc.to_response()

    return JSONResponse(
        status_code=201,
        content={
            "id": brief.id,
            "session_id": brief.session_id,
            "answer_text": brief.answer_text,
            "citations": [
                {
                    "source_id": c.source_id,
                    "chunk_id": c.chunk_id,
                    "excerpt": c.excerpt,
                }
                for c in brief.citations
            ],
            "created_at": brief.created_at.isoformat(),
        },
        headers={"Location": f"/sessions/{session_id}/briefs/{brief.id}"},
    )


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/briefs
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/briefs",
    response_model=ListBriefsResponse,
    summary="List Briefs",
    description=(
        "Return all Briefs saved in the given session, ordered oldest first. "
        "Returns 404 if the session does not exist."
    ),
)
async def list_briefs(
    session_id: int,
    request: Request,
) -> ListBriefsResponse | JSONResponse:
    """List all Briefs for a session.

    Returns 200 with the list of Briefs (may be empty if none saved yet).
    Returns 404 if the session does not exist (BRF_NF_001).
    """
    pool = get_pool(request)

    async with pool.acquire() as conn:
        repo = BriefRepository()
        try:
            rows = await repo.list_for_session(session_id=session_id, conn=conn)
        except BriefSessionNotFoundError as exc:
            logger.info(
                "brief.list.session_not_found",
                session_id=session_id,
                code=exc.code,
            )
            return exc.to_response()

    return ListBriefsResponse(
        briefs=[
            BriefResponse(
                id=r.id,
                session_id=r.session_id,
                answer_text=r.answer_text,
                citations=[
                    CitationResponse(
                        source_id=c.source_id,
                        chunk_id=c.chunk_id,
                        excerpt=c.excerpt,
                    )
                    for c in r.citations
                ],
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=len(rows),
    )
