"""Pydantic models for QA HTTP/WebSocket request and response payloads.

These models handle JSON serialization/deserialization at the WebSocket
boundary. Domain types (Question, Citation, AnswerChunk) are defined in
contracts.py and used internally — these models are the wire format.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """Client-to-server message received at WebSocket connect time.

    Attributes:
        question: The natural-language question text (1–4000 characters).
        top_k: Number of chunks to retrieve before synthesis (1–100).
        session_id: Optional session to record this question into.
    """

    question: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="The question to answer from the collection's knowledge.",
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of chunks to retrieve for synthesis context.",
    )
    session_id: int | None = Field(
        default=None,
        description="Optional session ID to record this question into.",
    )


class CitationResponse(BaseModel):
    """Wire representation of a single Citation.

    Attributes:
        source_id: PK of the cited Source.
        source_origin: URL or S3 path of the cited Source.
        chunk_ids: Chunk PKs that contributed to this Source's prompt entry.
        inline_marker: The literal marker in the answer, e.g. "[1]".
    """

    source_id: int
    source_origin: str
    chunk_ids: list[int]
    inline_marker: str


class TokenFrame(BaseModel):
    """Server-to-client frame for a streaming token.

    Example JSON: {"type": "token", "text": "Scout API is "}
    """

    type: str = "token"
    text: str


class DoneFrame(BaseModel):
    """Server-to-client frame signalling the end of the answer stream.

    Example JSON:
        {"type": "done", "citations": [
            {"source_id": 3, "source_origin": "https://...",
             "chunk_ids": [12, 14], "inline_marker": "[1]"}
        ]}
    """

    type: str = "done"
    citations: list[CitationResponse]


class ErrorFrame(BaseModel):
    """Server-to-client frame for errors occurring before or during streaming.

    Example JSON: {"type": "error", "code": "QA_COL_001", "message": "..."}
    """

    type: str = "error"
    code: str
    message: str
