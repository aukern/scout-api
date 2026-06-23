"""Unit tests for process_source arq worker function.

All tests use:
  - InMemoryFetchAdapter — no network
  - Mock ProcessingRepository — no database
  - Mock Embedder (via _embed_fn injection) — no LiteLLM API calls
  - Mock Chunker — no tiktoken dependency

The arq ctx dict is constructed manually for each test.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scout_api.sources.contracts import SourceRow, SourceStatus
from scout_api.sources.embedder import Embedder
from scout_api.sources.errors import SourceNotFoundError
from scout_api.sources.fetcher import InMemoryFetchAdapter
from scout_api.worker import _fetch_content, process_source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = datetime.UTC
NOW = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

_SAMPLE_CONTENT = "This is a test document. " * 30


def _make_source_row(
    id: int = 1,
    collection_id: int = 5,
    origin: str = "https://example.com",
    status: str = "pending",
    failed_reason: str | None = None,
) -> SourceRow:
    return SourceRow(
        id=id,
        collection_id=collection_id,
        origin=origin,
        status=SourceStatus(status),
        created_at=NOW,
        updated_at=NOW,
        failed_reason=failed_reason,
    )


def _make_mock_repo(
    source: SourceRow | None = None,
    deleted_chunks: int = 0,
    chunk_id: int = 1,
) -> MagicMock:
    """Return a mock ProcessingRepository."""
    repo = MagicMock()
    repo.get_source = AsyncMock(return_value=source)
    repo.delete_chunks = AsyncMock(return_value=deleted_chunks)
    repo.set_processing = AsyncMock(return_value=_make_source_row(status="processing"))
    repo.set_ready = AsyncMock(return_value=_make_source_row(status="ready"))
    repo.set_failed = AsyncMock(return_value=_make_source_row(status="failed"))
    repo.insert_chunk = AsyncMock(return_value=chunk_id)
    repo.get_chunk_count = AsyncMock(return_value=0)
    return repo


def _make_mock_chunker(chunks: list[str] | None = None) -> MagicMock:
    """Return a mock Chunker that returns predictable chunks without tiktoken."""
    mock_chunker = MagicMock()
    default_chunks = ["chunk one", "chunk two", "chunk three"]
    mock_chunker.split = MagicMock(return_value=chunks if chunks is not None else default_chunks)
    return mock_chunker


def _make_embedder(dim: int = 768) -> Embedder:
    """Return an Embedder with an injected fake embed function (no litellm needed)."""
    vector = [0.1] * dim
    embed_fn = AsyncMock(return_value=vector)
    return Embedder(model="test-model", _embed_fn=embed_fn)


def _make_ctx(
    source: SourceRow | None = None,
    content: str = _SAMPLE_CONTENT,
    origin: str = "https://example.com",
    pool: MagicMock | None = None,
    embedder: Embedder | None = None,
    chunks: list[str] | None = None,
) -> dict:
    """Build an arq-style ctx dict for testing."""
    fetcher = InMemoryFetchAdapter({origin: content})
    emb = embedder or _make_embedder()
    chunker = _make_mock_chunker(chunks)
    return {
        "pool": pool or MagicMock(),
        "embedder": emb,
        "chunker": chunker,
        "http_fetcher": fetcher,
        "s3_fetcher": None,
        "in_memory_fetcher": None,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestProcessSourceHappyPath:
    async def test_process_source_happy_path(self) -> None:
        """pending → processing → ready on success."""
        source = _make_source_row(status="pending")
        repo = _make_mock_repo(source=source)
        ctx = _make_ctx(source=source)

        with patch("scout_api.worker.ProcessingRepository", return_value=repo):
            await process_source(ctx, source_id=1)

        repo.set_processing.assert_awaited_once_with(1)
        repo.set_ready.assert_awaited_once_with(1)
        repo.set_failed.assert_not_awaited()

    async def test_process_source_creates_chunks_with_embeddings(self) -> None:
        """Content is split into Chunks; each gets an Embedding."""
        source = _make_source_row(status="pending")
        repo = _make_mock_repo(source=source)
        ctx = _make_ctx(source=source, chunks=["chunk A", "chunk B", "chunk C"])

        with patch("scout_api.worker.ProcessingRepository", return_value=repo):
            await process_source(ctx, source_id=1)

        # Three chunks should have been inserted
        assert repo.insert_chunk.await_count == 3

        # Every insert_chunk call should have received an embedding vector
        for call in repo.insert_chunk.call_args_list:
            embedding = call.kwargs.get("embedding") or call.args[3]
            assert isinstance(embedding, list)
            assert len(embedding) > 0

    async def test_process_source_deletes_stale_chunks_before_processing(self) -> None:
        """delete_chunks is called before any chunks are inserted."""
        source = _make_source_row(status="pending")
        repo = _make_mock_repo(source=source, deleted_chunks=3)
        ctx = _make_ctx(source=source)

        with patch("scout_api.worker.ProcessingRepository", return_value=repo):
            await process_source(ctx, source_id=1)

        repo.delete_chunks.assert_awaited_once_with(1)

    async def test_process_source_chunk_positions_are_sequential(self) -> None:
        """Chunks are inserted with 0-based sequential position indices."""
        source = _make_source_row(status="pending")
        repo = _make_mock_repo(source=source)
        ctx = _make_ctx(source=source, chunks=["a", "b", "c", "d"])

        with patch("scout_api.worker.ProcessingRepository", return_value=repo):
            await process_source(ctx, source_id=1)

        positions = []
        for call in repo.insert_chunk.call_args_list:
            pos = call.kwargs.get("position") if call.kwargs else call.args[2]
            positions.append(pos)

        assert positions == [0, 1, 2, 3]

    async def test_process_source_empty_content_produces_no_chunks(self) -> None:
        """An empty document produces zero chunks but still transitions to ready."""
        source = _make_source_row(status="pending")
        repo = _make_mock_repo(source=source)
        ctx = _make_ctx(source=source, chunks=[])  # chunker returns no chunks

        with patch("scout_api.worker.ProcessingRepository", return_value=repo):
            await process_source(ctx, source_id=1)

        repo.insert_chunk.assert_not_awaited()
        repo.set_ready.assert_awaited_once_with(1)


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


class TestProcessSourceFailurePath:
    async def test_process_source_embedding_failure_marks_failed(self) -> None:
        """On failure → failed, no searchable Chunks remain."""
        source = _make_source_row(status="pending")
        repo = _make_mock_repo(source=source)

        # Embedder that raises on embed
        embed_fn = AsyncMock(side_effect=RuntimeError("LiteLLM embedding call failed: API down"))
        bad_embedder = Embedder(model="test", _embed_fn=embed_fn)

        ctx = _make_ctx(source=source, embedder=bad_embedder, chunks=["chunk A"])

        with patch("scout_api.worker.ProcessingRepository", return_value=repo):
            with pytest.raises(RuntimeError, match="API down"):
                await process_source(ctx, source_id=1)

        repo.set_failed.assert_awaited_once()
        call_kwargs = repo.set_failed.call_args
        reason = call_kwargs.kwargs.get("reason") or call_kwargs.args[1]
        assert "API down" in reason

    async def test_process_source_set_ready_not_called_on_failure(self) -> None:
        """set_ready must NOT be called when processing fails."""
        source = _make_source_row(status="pending")
        repo = _make_mock_repo(source=source)

        embed_fn = AsyncMock(side_effect=RuntimeError("LiteLLM embedding call failed: error"))
        bad_embedder = Embedder(model="test", _embed_fn=embed_fn)

        ctx = _make_ctx(source=source, embedder=bad_embedder, chunks=["chunk A"])

        with patch("scout_api.worker.ProcessingRepository", return_value=repo):
            with pytest.raises(RuntimeError):
                await process_source(ctx, source_id=1)

        repo.set_ready.assert_not_awaited()

    async def test_process_source_source_not_found_raises(self) -> None:
        """If source doesn't exist, SourceNotFoundError is raised immediately."""
        repo = _make_mock_repo(source=None)
        ctx = _make_ctx()

        with patch("scout_api.worker.ProcessingRepository", return_value=repo):
            with pytest.raises(SourceNotFoundError):
                await process_source(ctx, source_id=999)

        repo.set_processing.assert_not_awaited()
        repo.set_ready.assert_not_awaited()
        repo.set_failed.assert_not_awaited()

    async def test_process_source_fetch_failure_marks_failed(self) -> None:
        """A fetch error marks the source as failed."""
        source = _make_source_row(status="pending", origin="https://example.com")
        repo = _make_mock_repo(source=source)

        # Fetcher does NOT have the origin seeded — raises KeyError
        ctx = _make_ctx(source=source, content="content", origin="https://other.com")

        with patch("scout_api.worker.ProcessingRepository", return_value=repo):
            with pytest.raises(KeyError):
                await process_source(ctx, source_id=1)

        repo.set_failed.assert_awaited_once()


# ---------------------------------------------------------------------------
# Refresh / re-processing
# ---------------------------------------------------------------------------


class TestProcessSourceRefresh:
    async def test_process_source_refresh_replaces_chunks(self) -> None:
        """Re-processing a source: old chunks deleted, new chunks inserted, status=ready."""
        source = _make_source_row(status="pending")
        repo = _make_mock_repo(source=source, deleted_chunks=5)
        ctx = _make_ctx(source=source, chunks=["new chunk A", "new chunk B"])

        with patch("scout_api.worker.ProcessingRepository", return_value=repo):
            await process_source(ctx, source_id=1)

        repo.delete_chunks.assert_awaited_once_with(1)
        assert repo.insert_chunk.await_count == 2
        repo.set_ready.assert_awaited_once_with(1)


# ---------------------------------------------------------------------------
# _fetch_content routing
# ---------------------------------------------------------------------------


class TestFetchContentRouting:
    async def test_http_origin_uses_http_fetcher(self) -> None:
        fetcher = InMemoryFetchAdapter({"https://example.com": "content"})
        ctx = {"http_fetcher": fetcher, "s3_fetcher": None, "in_memory_fetcher": None}
        result = await _fetch_content(ctx, "https://example.com", MagicMock())
        assert result == "content"

    async def test_https_origin_uses_http_fetcher(self) -> None:
        fetcher = InMemoryFetchAdapter({"https://secure.example.com": "secure content"})
        ctx = {"http_fetcher": fetcher, "s3_fetcher": None, "in_memory_fetcher": None}
        result = await _fetch_content(ctx, "https://secure.example.com", MagicMock())
        assert result == "secure content"

    async def test_s3_origin_uses_s3_fetcher(self) -> None:
        s3_fetcher = MagicMock()
        s3_fetcher.fetch = AsyncMock(return_value="s3 content")
        ctx = {"http_fetcher": None, "s3_fetcher": s3_fetcher, "in_memory_fetcher": None}
        result = await _fetch_content(ctx, "s3://bucket/key", MagicMock())
        assert result == "s3 content"

    async def test_unknown_scheme_with_in_memory_fallback(self) -> None:
        in_memory = InMemoryFetchAdapter({"custom://origin": "custom content"})
        ctx = {
            "http_fetcher": None,
            "s3_fetcher": None,
            "in_memory_fetcher": in_memory,
        }
        result = await _fetch_content(ctx, "custom://origin", MagicMock())
        assert result == "custom content"

    async def test_unknown_scheme_without_fallback_raises(self) -> None:
        ctx = {"http_fetcher": None, "s3_fetcher": None, "in_memory_fetcher": None}
        with pytest.raises(RuntimeError, match="No fetch adapter"):
            await _fetch_content(ctx, "ftp://example.com", MagicMock())
