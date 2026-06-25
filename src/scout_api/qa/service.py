"""QAService — orchestrates retrieval → synthesis → session recording.

Flow for WebSocket /collections/{collection_id}/qa:
    1. Validate question text (QAValidationError on empty or too-long)
    2. Verify collection exists via QARepository (QACollectionNotFoundError)
    3. Embed question text via Embedder (QASynthesisError on failure)
    4. Retrieve top_k chunks via QARepository (QANoContextError if empty)
    5. Build grounded prompt (synthesizer.build_prompt)
    6. Stream LiteLLM completion token-by-token (QASynthesisError on failure)
    7. After stream ends: extract citations, send done frame
    8. If session_id provided: record question + answer in session_activity

The QAService does NOT call SearchService — it has its own QARepository
with an identical SQL query. This avoids a service-layer cross-slice
dependency and keeps the module independently testable.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import structlog
from aukern_infra.metrics import observed
from opentelemetry import trace

from scout_api.qa.contracts import AnswerChunk, Citation, QARepositoryProtocol, Question
from scout_api.qa.errors import (
    QACollectionNotFoundError,
    QANoContextError,
    QASynthesisError,
    QAValidationError,
)
from scout_api.qa.synthesizer import Synthesizer, build_prompt
from scout_api.sessions.contracts import SessionActivityRepositoryProtocol
from scout_api.sources.embedder import Embedder

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

_MAX_QUESTION_LEN = 4000


class QAService:
    """Orchestrates QA: retrieval, synthesis, and optional session recording.

    Args:
        repo: Implements QARepositoryProtocol — chunk retrieval and collection check.
        synthesizer: Synthesizer instance for LLM streaming.
        embedder: LiteLLM-backed Embedder for question text.
        activity_repo: Optional SessionActivityRepositoryProtocol for recording.
    """

    def __init__(
        self,
        repo: QARepositoryProtocol,
        synthesizer: Synthesizer,
        embedder: Embedder,
        activity_repo: SessionActivityRepositoryProtocol | None = None,
    ) -> None:
        self._repo = repo
        self._synthesizer = synthesizer
        self._embedder = embedder
        self._activity_repo = activity_repo

    @observed("qa.ask")  # type: ignore[untyped-decorator]
    async def ask(
        self,
        question: Question,
        session_id: int | None = None,
        conn: object | None = None,
    ) -> AsyncIterator[AnswerChunk]:
        """Stream an answer to the given question.

        Yields AnswerChunk objects:
        - Mid-stream chunks: text=token, is_final=False, citations=[]
        - Final chunk: text="", is_final=True, citations=[...]

        Args:
            question: Validated Question domain object.
            session_id: If provided, records the activity in session_activity.
            conn: asyncpg connection for session recording. Required if
                session_id is provided.

        Yields:
            AnswerChunk objects (streaming tokens then a final citations chunk).

        Raises:
            QAValidationError: If the question text is empty or too long.
            QACollectionNotFoundError: If the collection does not exist.
            QASynthesisError: If embedding or LLM call fails.
            QANoContextError: If the collection has no ready chunks.
        """
        return self._ask_generator(question, session_id, conn)

    async def _emit_answered_event(
        self,
        collection_id: int,
        citation_count: int,
        token_count: int,
        log: object,
    ) -> None:
        """Emit question.answered notification (non-fatal)."""
        try:
            from aukern_infra.events import get_notifier  # noqa: PLC0415

            await get_notifier().emit(
                "question.answered",
                severity="info",
                payload={
                    "collection_id": collection_id,
                    "citation_count": citation_count,
                    "token_count": token_count,
                },
            )
        except Exception as emit_exc:  # noqa: BLE001
            logger.debug("qa.service.emit_error", error=str(emit_exc))

    async def _record_session_activity(
        self,
        question: Question,
        accumulated_text: str,
        citations: list[Citation],
        session_id: int,
        conn: object,
    ) -> None:
        """Record question/answer in session_activity (non-fatal on failure)."""
        try:
            output = json.dumps(
                {
                    "answer": accumulated_text,
                    "citations": [
                        {
                            "source_id": c.source_id,
                            "source_origin": c.source_origin,
                            "chunk_ids": c.chunk_ids,
                            "inline_marker": c.inline_marker,
                        }
                        for c in citations
                    ],
                }
            )
            await self._activity_repo.record(  # type: ignore[union-attr]
                session_id=session_id,
                kind="question",
                query=question.text,
                output=output,
                conn=conn,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("qa.service.session_record_error", error=str(exc))

    async def _ask_generator(
        self,
        question: Question,
        session_id: int | None,
        conn: object | None,
    ) -> AsyncIterator[AnswerChunk]:
        """Internal async generator implementing the QA pipeline.

        Separated from ask() so the @observed decorator wraps the public
        entry point without wrapping the generator itself.
        """
        log = logger.bind(
            collection_id=question.collection_id,
            top_k=question.top_k,
        )

        with tracer.start_as_current_span("qa.ask") as span:
            span.set_attribute("collection.id", question.collection_id)
            span.set_attribute("qa.top_k", question.top_k)

            # 1. Validate question text
            if not question.text or not question.text.strip():
                raise QAValidationError("Question text must not be empty")
            if len(question.text) > _MAX_QUESTION_LEN:
                raise QAValidationError(
                    f"Question text exceeds maximum length of {_MAX_QUESTION_LEN} characters"
                )

            # 2. Verify collection exists
            if not await self._repo.collection_exists(question.collection_id):
                raise QACollectionNotFoundError(question.collection_id)

            # 3. Embed question text
            try:
                embedding = await self._embedder.embed(question.text)
            except Exception as exc:  # noqa: BLE001
                log.error("qa.service.embed_error", error=str(exc))
                raise QASynthesisError(detail=f"Embedding failed: {exc}") from exc

            # 4. Retrieve chunks
            chunks = await self._repo.retrieve_chunks(
                collection_id=question.collection_id,
                query_embedding=embedding,
                top_k=question.top_k,
            )

            if not chunks:
                raise QANoContextError(question.collection_id)

            # 5. Build grounded prompt
            prompt, source_map = build_prompt(chunks, question.text)

            span.set_attribute("qa.chunks_retrieved", len(chunks))
            span.set_attribute("qa.sources_in_prompt", len(source_map))

            # 6. Stream synthesis
            accumulated_text = ""

            async for token, citations in self._synthesizer.stream(prompt, source_map):
                if citations is None:
                    # Mid-stream token
                    accumulated_text += token
                    yield AnswerChunk(text=token, is_final=False, citations=[])
                else:
                    # Stream complete — finalize and emit
                    span.set_attribute("qa.citation_count", len(citations))
                    span.set_attribute(
                        "qa.insufficient_context",
                        self._synthesizer.is_insufficient(accumulated_text),
                    )
                    log.info(
                        "qa.service.ask_complete",
                        tokens=len(accumulated_text),
                        citations=len(citations),
                    )
                    # 7. Emit event (non-fatal)
                    await self._emit_answered_event(
                        question.collection_id, len(citations), len(accumulated_text), log
                    )
                    # 8. Optional session recording
                    if (
                        session_id is not None
                        and self._activity_repo is not None
                        and conn is not None
                    ):
                        await self._record_session_activity(
                            question, accumulated_text, list(citations), session_id, conn
                        )
                    yield AnswerChunk(text="", is_final=True, citations=list(citations))
