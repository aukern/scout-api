"""Integration tests for the QA WebSocket endpoint.

WebSocket /collections/{collection_id}/qa

Uses FastAPI TestClient with mock service dependency — no real database or LLM.
The app lifespan (which opens a Postgres pool) is patched so tests run without
a live database.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from scout_api.db import get_pool
from scout_api.main import create_app
from scout_api.qa.contracts import AnswerChunk, Citation
from scout_api.qa.dependencies import get_qa_service
from scout_api.qa.errors import (
    QACollectionNotFoundError,
    QANoContextError,
    QASynthesisError,
    QAValidationError,
)
from scout_api.qa.service import QAService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_citation(source_id: int = 10) -> Citation:
    return Citation(
        source_id=source_id,
        source_origin="https://example.com/doc.pdf",
        chunk_ids=[1, 2],
        inline_marker="[1]",
    )


def _make_mock_pool() -> MagicMock:
    """Return a mock asyncpg pool (no real DB needed)."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    pool.close = AsyncMock()
    return pool


def _streaming_service_from(
    tokens: list[str],
    citation: Citation | None = None,
) -> AsyncMock:
    """Return a mock QAService that streams tokens then a final done chunk."""
    _citation = citation or _make_citation()

    async def _gen(*args: Any, **kwargs: Any) -> AsyncIterator[AnswerChunk]:
        for t in tokens:
            yield AnswerChunk(text=t, is_final=False, citations=[])
        yield AnswerChunk(text="", is_final=True, citations=[_citation])

    service = AsyncMock(spec=QAService)
    service.ask = AsyncMock(return_value=_gen())
    return service


def _make_app_client(mock_service: Any) -> TestClient:
    """Return a TestClient with mocked pool and mocked QAService dependency."""
    mock_pool = _make_mock_pool()

    with patch("scout_api.main.create_pool", new=AsyncMock(return_value=mock_pool)):
        app = create_app()
        app.dependency_overrides[get_qa_service] = lambda: mock_service
        client = TestClient(app)
    return client


def _collect_ws_messages(
    mock_service: Any,
    url: str,
    payload: dict,
    max_msgs: int = 20,
) -> list[dict]:
    """Open a WS connection, send payload, collect messages until done or limit."""
    mock_pool = _make_mock_pool()
    with patch("scout_api.main.create_pool", new=AsyncMock(return_value=mock_pool)):
        app = create_app()
        app.dependency_overrides[get_qa_service] = lambda: mock_service
        app.dependency_overrides[get_pool] = lambda: mock_pool

        messages = []
        with TestClient(app) as client:
            with client.websocket_connect(url) as ws:
                ws.send_text(json.dumps(payload))
                for _ in range(max_msgs):
                    try:
                        msg = ws.receive_json()
                        messages.append(msg)
                        if msg.get("type") in ("done", "error"):
                            break
                    except Exception:
                        break
        return messages


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_ws_qa_streams_tokens_and_done_frame() -> None:
    """WebSocket endpoint sends token frames then a done frame with citations."""
    service = _streaming_service_from(tokens=["Hello ", "world [1]."])
    messages = _collect_ws_messages(
        service,
        "/collections/1/qa",
        {"question": "What is Scout?", "top_k": 5},
    )

    token_msgs = [m for m in messages if m.get("type") == "token"]
    done_msgs = [m for m in messages if m.get("type") == "done"]

    assert len(token_msgs) >= 1
    assert len(done_msgs) == 1
    assert "citations" in done_msgs[0]
    citations = done_msgs[0]["citations"]
    assert len(citations) == 1
    assert citations[0]["source_id"] == 10
    assert citations[0]["inline_marker"] == "[1]"


def test_ws_qa_token_frames_have_correct_structure() -> None:
    """Each token frame has type=token and a text field."""
    service = _streaming_service_from(tokens=["A", "B"])
    messages = _collect_ws_messages(
        service,
        "/collections/1/qa",
        {"question": "Test?"},
    )

    for msg in [m for m in messages if m.get("type") == "token"]:
        assert "text" in msg
        assert isinstance(msg["text"], str)


def test_ws_qa_uses_default_top_k_when_omitted() -> None:
    """WebSocket endpoint uses top_k=10 when the field is omitted from the request."""
    service = _streaming_service_from(tokens=["answer"])
    mock_pool = _make_mock_pool()

    with patch("scout_api.main.create_pool", new=AsyncMock(return_value=mock_pool)):
        app = create_app()
        app.dependency_overrides[get_qa_service] = lambda: service
        app.dependency_overrides[get_pool] = lambda: mock_pool

        with TestClient(app) as client:
            with client.websocket_connect("/collections/1/qa") as ws:
                ws.send_text(json.dumps({"question": "What?"}))
                for _ in range(5):
                    try:
                        msg = ws.receive_json()
                        if msg.get("type") == "done":
                            break
                    except Exception:
                        break

    service.ask.assert_called_once()
    call_kwargs = service.ask.call_args
    question = call_kwargs.kwargs.get("question") or call_kwargs.args[0]
    assert question.top_k == 10


def test_ws_qa_collection_id_from_path() -> None:
    """WebSocket endpoint passes collection_id from the URL path to the question."""
    service = _streaming_service_from(tokens=["answer"])
    mock_pool = _make_mock_pool()

    with patch("scout_api.main.create_pool", new=AsyncMock(return_value=mock_pool)):
        app = create_app()
        app.dependency_overrides[get_qa_service] = lambda: service
        app.dependency_overrides[get_pool] = lambda: mock_pool

        with TestClient(app) as client:
            with client.websocket_connect("/collections/42/qa") as ws:
                ws.send_text(json.dumps({"question": "What?"}))
                for _ in range(5):
                    try:
                        msg = ws.receive_json()
                        if msg.get("type") == "done":
                            break
                    except Exception:
                        break

    service.ask.assert_called_once()
    call_kwargs = service.ask.call_args
    question = call_kwargs.kwargs.get("question") or call_kwargs.args[0]
    assert question.collection_id == 42


# ---------------------------------------------------------------------------
# Error path tests
# ---------------------------------------------------------------------------


def _error_service(exc: Exception) -> AsyncMock:
    """Return a mock QAService whose ask() raises the given exception."""

    async def _raise(*a: Any, **kw: Any) -> AsyncIterator[AnswerChunk]:
        raise exc
        yield  # pragma: no cover

    service = AsyncMock(spec=QAService)
    service.ask = AsyncMock(return_value=_raise())
    return service


def test_ws_qa_sends_error_frame_on_collection_not_found() -> None:
    """WebSocket endpoint sends error frame when collection is not found."""
    service = _error_service(QACollectionNotFoundError(99))
    messages = _collect_ws_messages(service, "/collections/99/qa", {"question": "What?"})

    assert len(messages) >= 1
    assert messages[0]["type"] == "error"
    assert messages[0]["code"] == "QA_COL_001"


def test_ws_qa_sends_error_frame_on_no_context() -> None:
    """WebSocket endpoint sends error frame when no context is available."""
    service = _error_service(QANoContextError(1))
    messages = _collect_ws_messages(service, "/collections/1/qa", {"question": "What?"})

    assert len(messages) >= 1
    assert messages[0]["type"] == "error"
    assert messages[0]["code"] == "QA_CTX_001"


def test_ws_qa_sends_error_frame_on_synthesis_error() -> None:
    """WebSocket endpoint sends error frame when LLM synthesis fails."""
    service = _error_service(QASynthesisError("LLM unreachable"))
    messages = _collect_ws_messages(service, "/collections/1/qa", {"question": "What?"})

    assert len(messages) >= 1
    assert messages[0]["type"] == "error"
    assert messages[0]["code"] == "QA_SYN_001"


def test_ws_qa_sends_error_on_validation_error() -> None:
    """WebSocket endpoint sends error frame on validation error."""
    service = _error_service(QAValidationError("too long"))
    messages = _collect_ws_messages(service, "/collections/1/qa", {"question": "x" * 4001})

    assert len(messages) >= 1
    assert messages[0]["type"] == "error"
    assert messages[0]["code"] == "QA_VAL_001"


def test_ws_qa_rejects_invalid_json() -> None:
    """WebSocket endpoint sends error frame when the client sends invalid JSON."""
    service = _streaming_service_from(tokens=["answer"])
    mock_pool = _make_mock_pool()

    with patch("scout_api.main.create_pool", new=AsyncMock(return_value=mock_pool)):
        app = create_app()
        app.dependency_overrides[get_qa_service] = lambda: service
        app.dependency_overrides[get_pool] = lambda: mock_pool

        with TestClient(app) as client:
            with client.websocket_connect("/collections/1/qa") as ws:
                ws.send_text("not valid json at all")
                msg = ws.receive_json()

    assert msg["type"] == "error"
    assert msg["code"] == "QA_VAL_001"


def test_ws_qa_rejects_missing_question_field() -> None:
    """WebSocket endpoint sends error frame when question field is missing."""
    service = _streaming_service_from(tokens=["answer"])
    mock_pool = _make_mock_pool()

    with patch("scout_api.main.create_pool", new=AsyncMock(return_value=mock_pool)):
        app = create_app()
        app.dependency_overrides[get_qa_service] = lambda: service
        app.dependency_overrides[get_pool] = lambda: mock_pool

        with TestClient(app) as client:
            with client.websocket_connect("/collections/1/qa") as ws:
                ws.send_text(json.dumps({"top_k": 5}))
                msg = ws.receive_json()

    assert msg["type"] == "error"
