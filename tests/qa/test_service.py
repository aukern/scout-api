"""Unit tests for QAService.

Uses in-memory test adapters — no database, LiteLLM, or embedding model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from scout_api.qa.contracts import AnswerChunk, Question
from scout_api.qa.errors import (
    QACollectionNotFoundError,
    QANoContextError,
    QASynthesisError,
    QAValidationError,
)
from scout_api.qa.service import QAService
from scout_api.qa.synthesizer import Synthesizer
from scout_api.search.contracts import SearchResult
from scout_api.sources.embedder import Embedder

# ---------------------------------------------------------------------------
# Test adapters
# ---------------------------------------------------------------------------


class InMemoryQARepository:
    """Test double for QARepositoryProtocol."""

    def __init__(
        self,
        chunks: list[SearchResult] | None = None,
        exists: bool = True,
    ) -> None:
        self._chunks = chunks or []
        self._exists = exists
        self.retrieve_calls: list[dict] = []

    async def retrieve_chunks(
        self,
        collection_id: int,
        query_embedding: list[float],
        top_k: int,
    ) -> list[SearchResult]:
        self.retrieve_calls.append({"collection_id": collection_id, "top_k": top_k})
        return self._chunks[:top_k]

    async def collection_exists(self, collection_id: int) -> bool:
        return self._exists


def _make_embedder(vector: list[float] | None = None) -> Embedder:
    """Return an Embedder with a fake embedding function."""

    async def fake_embed(text: str, model: str, api_base: str) -> list[float]:
        return vector or [0.1, 0.2, 0.3]

    return Embedder(model="test-model", _embed_fn=fake_embed)


def _make_chunk(chunk_id: int = 1, source_id: int = 10, score: float = 0.9) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        source_id=source_id,
        collection_id=1,
        content=f"Chunk content {chunk_id}",
        score=score,
        source_origin="https://example.com/doc.pdf",
    )


def _make_synthesizer(tokens: list[str] | None = None) -> Synthesizer:
    """Return a Synthesizer with a fake LiteLLM completion function."""
    _tokens = tokens or ["Scout ", "API ", "is great [1]."]

    async def fake_completion(
        model: str,
        messages: list[dict[str, str]],
        api_base: str,
        stream: bool,
    ) -> Any:
        """Return an async iterable simulating litellm chunks."""

        class FakeChunk:
            def __init__(self, text: str) -> None:
                self.choices = [type("c", (), {"delta": type("d", (), {"content": text})()})]

        async def _gen() -> AsyncIterator[FakeChunk]:
            for t in _tokens:
                yield FakeChunk(t)

        return _gen()

    return Synthesizer(model="test-model", _completion_fn=fake_completion)


def _make_service(
    chunks: list[SearchResult] | None = None,
    exists: bool = True,
    tokens: list[str] | None = None,
    embedding: list[float] | None = None,
) -> tuple[QAService, InMemoryQARepository]:
    repo = InMemoryQARepository(chunks=chunks, exists=exists)
    synthesizer = _make_synthesizer(tokens=tokens)
    embedder = _make_embedder(vector=embedding)
    service = QAService(repo=repo, synthesizer=synthesizer, embedder=embedder)
    return service, repo


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_streams_tokens_and_done_chunk() -> None:
    """QAService yields mid-stream token chunks then a final done chunk."""
    chunks = [_make_chunk(1, 10), _make_chunk(2, 10)]
    service, _ = _make_service(chunks=chunks, tokens=["Hello ", "world [1]."])

    question = Question(collection_id=1, text="What is this?")
    generator = await service.ask(question)

    results: list[AnswerChunk] = []
    async for chunk in generator:
        results.append(chunk)

    assert len(results) >= 2
    token_chunks = [r for r in results if not r.is_final]
    done_chunks = [r for r in results if r.is_final]

    assert len(done_chunks) == 1
    assert len(token_chunks) >= 1

    # All mid-stream chunks have empty citations
    for tc in token_chunks:
        assert tc.citations == []

    # Done chunk carries citations
    done = done_chunks[0]
    assert done.text == ""
    assert len(done.citations) == 1
    assert done.citations[0].source_id == 10
    assert done.citations[0].inline_marker == "[1]"


@pytest.mark.asyncio
async def test_ask_respects_top_k() -> None:
    """QAService passes top_k to the repository."""
    chunks = [_make_chunk(i) for i in range(20)]
    service, repo = _make_service(chunks=chunks, tokens=["answer [1]."])

    question = Question(collection_id=1, text="Question?", top_k=5)
    generator = await service.ask(question)
    async for _ in generator:
        pass

    assert repo.retrieve_calls[0]["top_k"] == 5


@pytest.mark.asyncio
async def test_ask_no_citations_when_none_referenced() -> None:
    """When the LLM doesn't use [N] markers, citations list is empty."""
    chunks = [_make_chunk(1, 10)]
    service, _ = _make_service(chunks=chunks, tokens=["I don't know."])

    question = Question(collection_id=1, text="Something?")
    generator = await service.ask(question)

    done_chunk: AnswerChunk | None = None
    async for chunk in generator:
        if chunk.is_final:
            done_chunk = chunk

    assert done_chunk is not None
    assert done_chunk.citations == []


# ---------------------------------------------------------------------------
# Error path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_raises_collection_not_found() -> None:
    """QAService raises QACollectionNotFoundError when collection is absent."""
    service, _ = _make_service(exists=False)
    question = Question(collection_id=99, text="What?")

    with pytest.raises(QACollectionNotFoundError) as exc_info:
        generator = await service.ask(question)
        async for _ in generator:
            pass

    assert exc_info.value.code == "QA_COL_001"
    assert exc_info.value.collection_id == 99


@pytest.mark.asyncio
async def test_ask_raises_no_context_when_empty_chunks() -> None:
    """QAService raises QANoContextError when retrieval returns no chunks."""
    service, _ = _make_service(chunks=[])
    question = Question(collection_id=1, text="What?")

    with pytest.raises(QANoContextError) as exc_info:
        generator = await service.ask(question)
        async for _ in generator:
            pass

    assert exc_info.value.code == "QA_CTX_001"


@pytest.mark.asyncio
async def test_ask_raises_validation_error_on_empty_text() -> None:
    """QAService raises QAValidationError when question text is empty."""
    service, _ = _make_service()
    question = Question(collection_id=1, text="")

    with pytest.raises(QAValidationError) as exc_info:
        generator = await service.ask(question)
        async for _ in generator:
            pass

    assert exc_info.value.code == "QA_VAL_001"


@pytest.mark.asyncio
async def test_ask_raises_validation_error_on_whitespace_text() -> None:
    """QAService raises QAValidationError when question text is only whitespace."""
    service, _ = _make_service()
    question = Question(collection_id=1, text="   ")

    with pytest.raises(QAValidationError):
        generator = await service.ask(question)
        async for _ in generator:
            pass


@pytest.mark.asyncio
async def test_ask_raises_validation_error_on_too_long_text() -> None:
    """QAService raises QAValidationError when question text exceeds 4000 chars."""
    service, _ = _make_service()
    question = Question(collection_id=1, text="x" * 4001)

    with pytest.raises(QAValidationError):
        generator = await service.ask(question)
        async for _ in generator:
            pass


@pytest.mark.asyncio
async def test_ask_raises_synthesis_error_when_embed_fails() -> None:
    """QAService raises QASynthesisError when embedding the question fails."""
    repo = InMemoryQARepository(exists=True)
    synthesizer = _make_synthesizer()

    async def failing_embed(text: str, model: str, api_base: str) -> list[float]:
        raise RuntimeError("embedding model unavailable")

    embedder = Embedder(model="test-model", _embed_fn=failing_embed)
    service = QAService(repo=repo, synthesizer=synthesizer, embedder=embedder)

    question = Question(collection_id=1, text="What?")
    with pytest.raises(QASynthesisError) as exc_info:
        generator = await service.ask(question)
        async for _ in generator:
            pass

    assert exc_info.value.code == "QA_SYN_001"
    assert "embedding model unavailable" in exc_info.value.message


@pytest.mark.asyncio
async def test_ask_records_session_activity_when_session_id_provided() -> None:
    """QAService records question activity when session_id is provided."""
    chunks = [_make_chunk(1, 10)]
    service, _ = _make_service(chunks=chunks, tokens=["Answer [1]."])

    activity_repo = AsyncMock()
    activity_repo.record = AsyncMock(return_value=None)
    service._activity_repo = activity_repo

    mock_conn = AsyncMock()
    question = Question(collection_id=1, text="What?")
    generator = await service.ask(question, session_id=42, conn=mock_conn)
    async for _ in generator:
        pass

    activity_repo.record.assert_called_once()
    call_kwargs = activity_repo.record.call_args.kwargs
    assert call_kwargs["session_id"] == 42
    assert call_kwargs["kind"] == "question"
    assert call_kwargs["query"] == "What?"
    assert call_kwargs["conn"] is mock_conn


@pytest.mark.asyncio
async def test_ask_skips_session_recording_when_no_session_id() -> None:
    """QAService does not record activity when session_id is None."""
    chunks = [_make_chunk(1, 10)]
    service, _ = _make_service(chunks=chunks, tokens=["Answer."])

    activity_repo = AsyncMock()
    activity_repo.record = AsyncMock()
    service._activity_repo = activity_repo

    question = Question(collection_id=1, text="What?")
    generator = await service.ask(question, session_id=None)
    async for _ in generator:
        pass

    activity_repo.record.assert_not_called()


@pytest.mark.asyncio
async def test_ask_handles_multiple_sources_with_citations() -> None:
    """QAService correctly handles answers citing multiple sources."""
    chunks = [
        _make_chunk(1, source_id=10),
        _make_chunk(2, source_id=20),
    ]
    service, _ = _make_service(chunks=chunks, tokens=["From [1] and also [2]."])

    question = Question(collection_id=1, text="Tell me everything.")
    generator = await service.ask(question)

    done: AnswerChunk | None = None
    async for chunk in generator:
        if chunk.is_final:
            done = chunk

    assert done is not None
    assert len(done.citations) == 2
    source_ids = {c.source_id for c in done.citations}
    assert 10 in source_ids
    assert 20 in source_ids
