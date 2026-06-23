"""Unit tests for worker startup / shutdown lifecycle hooks.

These tests mock asyncpg and the Embedder probe to cover the worker
startup/shutdown paths without requiring a real Postgres or Redis connection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from scout_api.worker import worker_shutdown, worker_startup

# ---------------------------------------------------------------------------
# worker_startup
# ---------------------------------------------------------------------------


class TestWorkerStartup:
    def _patch_startup(
        self,
        mock_pool: AsyncMock,
        mock_embedder: MagicMock,
        mock_chunker_instance: MagicMock | None = None,
    ) -> tuple:
        """Build the context manager stack for patching worker_startup dependencies."""
        import types

        # asyncpg is lazy-imported inside worker_startup — patch sys.modules
        mock_asyncpg = types.ModuleType("asyncpg")
        mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)  # type: ignore[attr-defined]

        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch.dict("sys.modules", {"asyncpg": mock_asyncpg}))
        stack.enter_context(patch("scout_api.worker.Embedder", return_value=mock_embedder))
        if mock_chunker_instance is not None:
            stack.enter_context(
                patch("scout_api.worker.Chunker", return_value=mock_chunker_instance)
            )
        else:
            stack.enter_context(patch("scout_api.worker.Chunker"))
        stack.enter_context(patch("scout_api.worker.HttpFetchAdapter"))
        return stack, mock_asyncpg

    async def test_startup_creates_pool_in_ctx(self) -> None:
        """worker_startup stores the asyncpg pool in ctx['pool']."""
        ctx: dict = {}
        mock_pool = AsyncMock()
        mock_embedder = MagicMock()
        mock_embedder.probe = AsyncMock(return_value=768)

        stack, _ = self._patch_startup(mock_pool, mock_embedder)
        with stack:
            await worker_startup(ctx)

        assert ctx["pool"] is mock_pool

    async def test_startup_stores_embedder_in_ctx(self) -> None:
        """worker_startup stores the Embedder instance in ctx['embedder']."""
        ctx: dict = {}
        mock_pool = AsyncMock()
        mock_embedder = MagicMock()
        mock_embedder.probe = AsyncMock(return_value=1536)

        stack, _ = self._patch_startup(mock_pool, mock_embedder)
        with stack:
            await worker_startup(ctx)

        assert ctx["embedder"] is mock_embedder
        assert ctx["embedding_dim"] == 1536

    async def test_startup_stores_chunker_in_ctx(self) -> None:
        """worker_startup stores the Chunker instance in ctx['chunker']."""
        ctx: dict = {}
        mock_pool = AsyncMock()
        mock_embedder = MagicMock()
        mock_embedder.probe = AsyncMock(return_value=768)
        mock_chunker_instance = MagicMock()

        stack, _ = self._patch_startup(mock_pool, mock_embedder, mock_chunker_instance)
        with stack:
            await worker_startup(ctx)

        assert ctx["chunker"] is mock_chunker_instance

    async def test_startup_continues_when_probe_fails(self) -> None:
        """worker_startup logs a warning but does not raise if probe fails."""
        ctx: dict = {}
        mock_pool = AsyncMock()
        mock_embedder = MagicMock()
        mock_embedder.probe = AsyncMock(side_effect=Exception("model not reachable"))

        stack, _ = self._patch_startup(mock_pool, mock_embedder)
        with stack:
            await worker_startup(ctx)

        assert "pool" in ctx
        assert "embedder" in ctx
        assert ctx["embedding_dim"] is None


# ---------------------------------------------------------------------------
# worker_shutdown
# ---------------------------------------------------------------------------


class TestWorkerShutdown:
    async def test_shutdown_closes_pool(self) -> None:
        """worker_shutdown closes the pool if it exists."""
        mock_pool = AsyncMock()
        ctx = {"pool": mock_pool}

        await worker_shutdown(ctx)

        mock_pool.close.assert_awaited_once()

    async def test_shutdown_is_safe_when_no_pool(self) -> None:
        """worker_shutdown does not raise if ctx has no pool."""
        ctx: dict = {}
        await worker_shutdown(ctx)  # should not raise
