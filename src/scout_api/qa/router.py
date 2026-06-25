"""WebSocket router for the QA domain.

Exposes one endpoint:

    WebSocket /collections/{collection_id}/qa

Protocol (JSON frames over WebSocket):

Client → Server (single message after connect):
    {"question": "What is the Scout API?", "top_k": 10, "session_id": null}

Server → Client (multiple frames):
    {"type": "token", "text": "Scout API is "}
    {"type": "token", "text": "a tool layer..."}
    {"type": "done", "citations": [
        {"source_id": 3, "source_origin": "https://...",
         "chunk_ids": [12, 14], "inline_marker": "[1]"}
    ]}

On error (before or during streaming):
    {"type": "error", "code": "QA_COL_001", "message": "Collection not found"}

Validation errors close the WebSocket with code 4000.

Design:
- The router accepts the WebSocket, receives a single JSON message,
  then streams AnswerChunk events from QAService.ask().
- The pool connection is acquired per-connection and released after streaming.
- Session recording is handled inside QAService — the router passes
  session_id and the connection.
"""

from __future__ import annotations

import json

import asyncpg
import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from scout_api.db import get_pool
from scout_api.qa.dependencies import get_qa_service
from scout_api.qa.errors import (
    QACollectionNotFoundError,
    QANoContextError,
    QASynthesisError,
    QAValidationError,
)
from scout_api.qa.models import AskRequest, CitationResponse, DoneFrame, ErrorFrame, TokenFrame
from scout_api.qa.service import QAService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/collections/{collection_id}", tags=["qa"])


@router.websocket("/qa")
async def ask_question(
    websocket: WebSocket,
    collection_id: int,
    pool: asyncpg.Pool = Depends(get_pool),
    service: QAService = Depends(get_qa_service),
) -> None:
    """Stream a grounded answer to a question over a collection.

    The client connects, sends one JSON message (AskRequest), and receives
    token frames followed by a done frame (or an error frame).

    Args:
        websocket: The active WebSocket connection.
        collection_id: The collection to scope the question to.
        pool: asyncpg pool for acquiring a connection.
        service: Fully-wired QAService (injected by get_qa_service).
    """
    await websocket.accept()
    log = logger.bind(collection_id=collection_id)

    try:
        # Receive the question message
        try:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            request = AskRequest(**data)
        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
            await websocket.send_text(
                ErrorFrame(code="QA_VAL_001", message=f"Invalid request: {exc}").model_dump_json()
            )
            await websocket.close(code=4000)
            return

        from scout_api.qa.contracts import Question  # noqa: PLC0415

        question = Question(
            collection_id=collection_id,
            text=request.question,
            top_k=request.top_k,
        )

        # Acquire a connection for session recording (released after streaming)
        async with pool.acquire() as conn:
            try:
                generator = await service.ask(
                    question=question,
                    session_id=request.session_id,
                    conn=conn,
                )

                async for chunk in generator:
                    if not chunk.is_final:
                        frame = TokenFrame(text=chunk.text).model_dump_json()
                        await websocket.send_text(frame)
                    else:
                        done = DoneFrame(
                            citations=[
                                CitationResponse(
                                    source_id=c.source_id,
                                    source_origin=c.source_origin,
                                    chunk_ids=c.chunk_ids,
                                    inline_marker=c.inline_marker,
                                )
                                for c in chunk.citations
                            ]
                        )
                        await websocket.send_text(done.model_dump_json())

            except QAValidationError as exc:
                await websocket.send_text(
                    ErrorFrame(code=exc.code, message=exc.message).model_dump_json()
                )
                await websocket.close(code=4000)
                return

            except (QACollectionNotFoundError, QANoContextError, QASynthesisError) as exc:
                log.warning("qa.router.error", code=exc.code, message=exc.message)
                await websocket.send_text(
                    ErrorFrame(code=exc.code, message=exc.message).model_dump_json()
                )

        await websocket.close()

    except WebSocketDisconnect:
        log.info("qa.router.client_disconnected")
    except Exception as exc:  # noqa: BLE001
        log.error("qa.router.unexpected_error", error=str(exc))
        try:
            await websocket.send_text(
                ErrorFrame(code="QA_SYN_001", message=f"Unexpected error: {exc}").model_dump_json()
            )
            await websocket.close()
        except Exception as close_exc:  # noqa: BLE001
            log.debug("qa.router.close_error", error=str(close_exc))
