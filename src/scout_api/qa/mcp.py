"""MCP server for the QA domain.

Exposes one tool to AI agents:
    ask_collection — ask a question against a Collection, returns the full
                     answer + citations (non-streaming — MCP is request/response).

This module wraps the same QAService used by the WebSocket layer — no
duplicate logic. The MCP tool collects all tokens into a single answer_text
string before returning, since MCP does not support streaming.

fastmcp and mcp are lazy-imported (proxy blocked at install time — they are
production-only dependencies). The module works without them installed; at
runtime the tool requires fastmcp>=2.0 and mcp>=1.0.

DB access from the MCP layer:
    The MCP layer cannot use the FastAPI request/app.state. Instead it builds
    a fresh asyncpg pool from settings on each call. In production this is cheap
    (asyncpg connection pools are lightweight). For testing, override the
    ask_collection tool by injecting mock state.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _build_mcp_server() -> Any:  # pragma: no cover
    """Build the FastMCP server instance.

    Lazy-imports fastmcp so the module can be imported without it installed.

    Returns:
        FastMCP instance with the ask_collection tool registered.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from typing import Annotated  # noqa: PLC0415

        from fastmcp import FastMCP  # noqa: PLC0415
        from fastmcp.exceptions import ToolError  # noqa: PLC0415
        from mcp.types import ToolAnnotations  # noqa: PLC0415
        from pydantic import Field  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "fastmcp and mcp are required for the MCP server. "
            "Install them with: pip install fastmcp>=2.0 mcp>=1.0"
        ) from exc

    from scout_api.qa.errors import (  # noqa: PLC0415
        QACollectionNotFoundError,
        QANoContextError,
        QASynthesisError,
        QAValidationError,
    )

    mcp: Any = FastMCP(
        "qa",
        instructions=(
            "Provides question-answering over Collections of ingested knowledge. "
            "Use ask_collection to get a grounded answer with inline citations. "
            "Only ready sources contribute to the answer context. "
            "When context is insufficient the tool returns an explicit statement "
            "rather than fabricating an answer."
        ),
    )

    @mcp.tool(  # type: ignore[untyped-decorator]
        annotations=ToolAnnotations(
            title="Ask Collection",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        )
    )
    async def ask_collection(
        collection_id: Annotated[
            int,
            Field(
                description=(
                    "The integer ID of the Collection to ask a question about. "
                    "Only chunks from ready sources within this collection are used."
                ),
                examples=[1, 42],
                gt=0,
            ),
        ],
        question: Annotated[
            str,
            Field(
                description=(
                    "The natural-language question to answer. "
                    "The answer is grounded exclusively in the collection's indexed content. "
                    "Minimum 1 character, maximum 4000 characters."
                ),
                min_length=1,
                max_length=4000,
            ),
        ],
        top_k: Annotated[
            int,
            Field(
                description=(
                    "Number of chunks to retrieve for synthesis context. "
                    "Range 1 to 100. Default 10."
                ),
                default=10,
                ge=1,
                le=100,
            ),
        ] = 10,
    ) -> dict[str, Any]:
        """Ask a question against a Collection. Returns the full answer + citations.

        Retrieves the most relevant chunks from the collection using semantic
        search, then synthesizes a grounded answer using the configured LLM.
        Citations identify which Sources the answer drew from.

        Use this after search_collection when an agent needs a synthesized
        answer rather than raw chunk text.

        Returns:
            Dict with keys:
                answer (str): The synthesized answer text with inline [N] citations.
                citations (list[dict]): List of cited sources, each with keys:
                    source_id (int), source_origin (str),
                    chunk_ids (list[int]), inline_marker (str).
            Example:
                {
                    "answer": "Scout API is a tool layer for AI agents [1].",
                    "citations": [{
                        "source_id": 3,
                        "source_origin": "https://docs.example.com",
                        "chunk_ids": [12, 14],
                        "inline_marker": "[1]"
                    }]
                }
        """
        log = logger.bind(tool="ask_collection", collection_id=collection_id)
        try:
            from scout_api.config import get_settings  # noqa: PLC0415
            from scout_api.db import create_pool  # noqa: PLC0415
            from scout_api.qa.contracts import Question  # noqa: PLC0415
            from scout_api.qa.repository import QARepository  # noqa: PLC0415
            from scout_api.qa.service import QAService  # noqa: PLC0415
            from scout_api.qa.synthesizer import Synthesizer  # noqa: PLC0415
            from scout_api.sessions.repository import SessionActivityRepository  # noqa: PLC0415
            from scout_api.sources.embedder import Embedder  # noqa: PLC0415

            settings = get_settings()

            try:
                pool = await create_pool(
                    database_url=settings.database_url,
                    max_size=1,
                )
            except Exception as pool_exc:  # noqa: BLE001
                raise ToolError(f"Database connection unavailable: {pool_exc}") from pool_exc

            async with pool.acquire() as conn:
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

                service = QAService(
                    repo=repo,
                    synthesizer=synthesizer,
                    embedder=embedder,
                    activity_repo=activity_repo,
                )

                q = Question(
                    collection_id=collection_id,
                    text=question,
                    top_k=top_k,
                )

                # Collect all tokens — MCP is request/response, not streaming
                answer_text = ""
                final_citations: list[dict[str, Any]] = []

                generator = await service.ask(question=q)
                async for chunk in generator:
                    if not chunk.is_final:
                        answer_text += chunk.text
                    else:
                        final_citations = [
                            {
                                "source_id": c.source_id,
                                "source_origin": c.source_origin,
                                "chunk_ids": c.chunk_ids,
                                "inline_marker": c.inline_marker,
                            }
                            for c in chunk.citations
                        ]

            await pool.close()

            return {
                "answer": answer_text,
                "citations": final_citations,
            }

        except (
            QACollectionNotFoundError,
            QANoContextError,
            QASynthesisError,
            QAValidationError,
        ) as exc:
            raise ToolError(exc.message) from exc
        except ToolError:
            raise
        except Exception as exc:
            log.error("mcp.ask_collection.error", error=str(exc))
            raise ToolError(f"ask_collection failed: {exc}") from exc

    return mcp


def create_mcp_app() -> Any:  # pragma: no cover
    """Return the MCP ASGI app serving at /mcp/qa.

    Lazy-builds the FastMCP server. Wire into main.py alongside the search
    MCP app.

    Returns:
        Starlette ASGI application for the QA MCP server.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    mcp = _build_mcp_server()
    return mcp.http_app(path="/mcp/qa")
