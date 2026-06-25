"""FastAPI dependency providers for the QA domain.

Wires up QAService with its collaborators:
  - QARepository (using the asyncpg pool from app.state)
  - Synthesizer (from settings: llm_model, litellm_api_base)
  - Embedder (from settings: embedding_model, ollama_api_base)
  - SessionActivityRepository (optional, for session recording)

The WebSocket router acquires an asyncpg connection from the pool
before streaming begins and passes it to QAService.ask() along with
the optional session_id from the client message.

For testing, override via:
  app.dependency_overrides[get_qa_service] = lambda: mock_service
"""

from __future__ import annotations

import asyncpg
from fastapi import Depends, Request

from scout_api.config import get_settings
from scout_api.db import get_pool
from scout_api.qa.repository import QARepository
from scout_api.qa.service import QAService
from scout_api.qa.synthesizer import Synthesizer
from scout_api.sessions.repository import SessionActivityRepository
from scout_api.sources.embedder import Embedder


def get_qa_service(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
) -> QAService:
    """Build and return a QAService for a single WebSocket connection.

    Args:
        request: FastAPI request (for pool access).
        pool: asyncpg pool from app.state.pool via get_pool.

    Returns:
        A fully-wired QAService.
    """
    settings = get_settings()
    conn: asyncpg.Pool = request.app.state.pool

    repo = QARepository(conn)
    synthesizer = Synthesizer(
        model=settings.llm_model,
        api_base=settings.litellm_api_base,
    )
    embedder = Embedder(
        model=settings.embedding_model,
        api_base=settings.ollama_api_base,
    )
    activity_repo = SessionActivityRepository()

    return QAService(
        repo=repo,
        synthesizer=synthesizer,
        embedder=embedder,
        activity_repo=activity_repo,
    )
