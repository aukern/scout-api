"""Published contracts for the QA domain.

Public types imported by the HTTP router, MCP layer, and any future slices
that need to depend on QA types without coupling to the implementation.

Importing from contracts.py guarantees stability — the internal implementation
modules (repository.py, service.py, synthesizer.py) may change without affecting
callers.

Design decisions:
- Citation records source-level granularity (not chunk-level). The LLM prompt
  numbers Sources 1..N; inline markers like [1] map to source_id values. Chunk
  IDs are preserved in chunk_ids for debugging/traceability.
- AnswerChunk carries citations only on the final chunk (is_final=True). Mid-stream
  chunks have citations=[] to avoid partial state reaching consumers.
- QARepositoryProtocol returns SearchResult from search.contracts so that the
  same pgvector query type flows through QA without type duplication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from scout_api.search.contracts import SearchResult

# ---------------------------------------------------------------------------
# Domain value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    """A scoped question against a single Collection.

    Attributes:
        collection_id: The Collection to retrieve chunks from and scope answers to.
        text: The natural-language question text.
        top_k: Number of chunks to retrieve before synthesis (default 10).
    """

    collection_id: int
    text: str
    top_k: int = 10


@dataclass(frozen=True)
class Citation:
    """A source-level citation for a synthesized answer.

    One Citation per unique Source referenced in the answer. The inline_marker
    corresponds to the [N] reference the LLM placed in the answer text.

    Attributes:
        source_id: PK of the cited Source.
        source_origin: URL or S3 path of the cited Source — for display.
        chunk_ids: Chunk PKs that contributed to this Source's prompt entry.
        inline_marker: The literal marker used in the answer, e.g. "[1]".
    """

    source_id: int
    source_origin: str
    chunk_ids: list[int]
    inline_marker: str


@dataclass(frozen=True)
class AnswerChunk:
    """A single streamed token or token group from the LLM synthesizer.

    Mid-stream chunks carry text and is_final=False with citations=[].
    The final chunk carries is_final=True and the populated citations list.

    Attributes:
        text: Incremental token text from the LLM.
        is_final: True on the last chunk only.
        citations: Populated only when is_final=True; empty list otherwise.
    """

    text: str
    is_final: bool = False
    citations: list[Citation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Repository protocol
# ---------------------------------------------------------------------------


class QARepositoryProtocol(Protocol):
    """Interface for QA data access.

    Implemented by QARepository (production) and InMemoryQARepository (tests).
    Callers depend on this Protocol — never on the concrete class.
    """

    async def retrieve_chunks(
        self,
        collection_id: int,
        query_embedding: list[float],
        top_k: int,
    ) -> list[SearchResult]:
        """Retrieve the top_k most relevant chunks for an embedding.

        Args:
            collection_id: Scope the retrieval to this collection.
            query_embedding: Float vector from the embedding model.
            top_k: Maximum number of chunks to return.

        Returns:
            List of SearchResult ordered by descending cosine similarity.
        """
        ...

    async def collection_exists(self, collection_id: int) -> bool:
        """Return True if the collection is present in the database.

        Args:
            collection_id: PK to check.

        Returns:
            True if the collection exists, False otherwise.
        """
        ...
