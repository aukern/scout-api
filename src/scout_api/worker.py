"""arq worker entry point for scout-api.

This module defines the arq ``WorkerSettings`` and the ``process_source``
worker function that turns a ``pending`` Source into searchable knowledge.

State machine:
    pending → processing → ready     (success)
    pending → processing → failed    (any exception)

No HTTP endpoints. The worker is invoked by arq when the ``process_source``
job fires (enqueued by ``IngestService`` in slice 18 via ``ArqQueueAdapter``).

Running the worker::

    arq scout_api.worker.WorkerSettings

Environment variables (all via ``Settings``):
    DATABASE_URL       — asyncpg connection string
    REDIS_URL          — arq broker (e.g. redis://localhost:6379)
    EMBEDDING_MODEL    — LiteLLM model string (default: text-embedding-ada-002)
    OLLAMA_API_BASE    — Base URL for Ollama (e.g. http://localhost:11434)
    CHUNK_TOKEN_SIZE   — Tokens per chunk (default: 512)
    CHUNK_OVERLAP_TOKENS — Overlap tokens (default: 64)
    ARQ_CONCURRENCY    — Max concurrent jobs (default: 10)
"""

from __future__ import annotations

from typing import Any

import structlog

from scout_api.config import get_settings
from scout_api.sources.chunker import Chunker
from scout_api.sources.embedder import Embedder
from scout_api.sources.errors import SourceNotFoundError
from scout_api.sources.fetcher import HttpFetchAdapter, S3FetchAdapter
from scout_api.sources.processing_repository import ProcessingRepository

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Worker startup / shutdown (arq lifecycle hooks)
# ---------------------------------------------------------------------------


async def worker_startup(ctx: dict[str, Any]) -> None:
    """Create shared resources and store them in the arq context.

    Called once when the arq worker process starts. Resources stored in
    ``ctx`` are available to every job function via the ``ctx`` dict.

    Sets up:
      - asyncpg pool (``ctx["pool"]``)
      - Embedder with probed dimension (``ctx["embedder"]``)
      - Chunker (``ctx["chunker"]``)
      - Fetch adapters (``ctx["http_fetcher"]``, ``ctx["s3_fetcher"]``)
    """
    import asyncpg  # noqa: PLC0415

    settings = get_settings()
    log = logger.bind(env=settings.app_env)

    log.info("worker.startup.begin")

    # Database pool
    pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    ctx["pool"] = pool
    log.info("worker.startup.pool_created")

    # Embedder — probe dimension at startup
    embedder = Embedder(
        model=settings.embedding_model,
        api_base=settings.ollama_api_base,
    )
    try:
        dim = await embedder.probe()
        ctx["embedder"] = embedder
        ctx["embedding_dim"] = dim
        log.info("worker.startup.embedder_ready", model=settings.embedding_model, dim=dim)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "worker.startup.embedder_probe_failed",
            error=str(exc),
            note="Worker will attempt embedding per-job; startup probe is advisory",
        )
        ctx["embedder"] = embedder
        ctx["embedding_dim"] = None

    # Chunker
    ctx["chunker"] = Chunker(
        chunk_token_size=settings.chunk_token_size,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
        model=settings.embedding_model,
    )

    # Fetch adapters
    ctx["http_fetcher"] = HttpFetchAdapter()
    # S3 fetcher requires storage adapter — lazy-init per job if needed
    # (storage adapter construction requires aioboto3 which may not be installed)
    ctx["s3_fetcher"] = None  # populated on first S3 job

    log.info("worker.startup.done")


async def worker_shutdown(ctx: dict[str, Any]) -> None:
    """Close shared resources on worker shutdown.

    Called when the arq worker process stops (SIGTERM or graceful shutdown).
    """
    pool = ctx.get("pool")
    if pool is not None:
        await pool.close()
        logger.info("worker.shutdown.pool_closed")


# ---------------------------------------------------------------------------
# process_source — the main worker function
# ---------------------------------------------------------------------------


async def process_source(ctx: dict[str, Any], *, source_id: int) -> None:
    """Process a pending Source into searchable chunks.

    Registered as an arq job under the name ``"process_source"``.
    Enqueued by ``IngestService`` after a successful upsert.

    Args:
        ctx: arq context dict (carries pool, embedder, chunker, fetchers).
        source_id: Primary key of the Source to process.

    State transitions:
        pending → processing → ready     (success path)
        * → processing → failed          (any exception)

    The ``failed_reason`` column on ``sources`` is set on failure so that
    failure diagnosis does not require log grepping.
    """
    settings = get_settings()
    pool = ctx["pool"]
    embedder: Embedder = ctx["embedder"]
    chunker: Chunker = ctx["chunker"]

    repo = ProcessingRepository(pool)
    log = logger.bind(source_id=source_id)

    log.info("worker.process_source.start")

    # ── 1. Fetch the source record ──────────────────────────────────────────
    source = await repo.get_source(source_id)
    if source is None:
        log.error("worker.process_source.not_found")
        raise SourceNotFoundError(source_id)

    log = log.bind(collection_id=source.collection_id, origin=source.origin)

    # ── 2. Delete any stale chunks (idempotent — safe on first run) ─────────
    deleted = await repo.delete_chunks(source_id)
    if deleted:
        log.info("worker.process_source.stale_chunks_deleted", count=deleted)

    # ── 3. Transition to processing ─────────────────────────────────────────
    await repo.set_processing(source_id)
    log.info("worker.process_source.processing")

    # Emit domain event
    try:
        from scout_api.events import get_event_bus  # noqa: PLC0415

        get_event_bus().emit("source.processing_started", {"source_id": source_id})
    except Exception:  # noqa: BLE001, S110
        pass  # event bus is advisory; never block the processing path

    try:
        # ── 4. Fetch content ────────────────────────────────────────────────
        content = await _fetch_content(ctx, source.origin, log)
        log.info("worker.process_source.fetched", content_length=len(content))

        # ── 5. Chunk content ────────────────────────────────────────────────
        raw_chunks = chunker.split(content)
        log.info("worker.process_source.chunked", chunk_count=len(raw_chunks))

        if not raw_chunks:
            log.warning("worker.process_source.no_chunks", origin=source.origin)

        # ── 6. Embed and persist each chunk ─────────────────────────────────
        for position, chunk_text in enumerate(raw_chunks):
            vector = await embedder.embed(chunk_text)
            await repo.insert_chunk(
                source_id=source_id,
                content=chunk_text,
                position=position,
                embedding=vector,
            )

        chunk_count = len(raw_chunks)
        log.info(
            "worker.process_source.chunks_stored",
            chunk_count=chunk_count,
            embedding_model=settings.embedding_model,
        )

        # ── 7. Transition to ready ───────────────────────────────────────────
        await repo.set_ready(source_id)
        log.info(
            "worker.process_source.ready",
            chunk_count=chunk_count,
            outcome="success",
        )

        # Emit domain event
        try:
            from scout_api.events import get_event_bus  # noqa: PLC0415

            get_event_bus().emit(
                "source.ready",
                {
                    "source_id": source_id,
                    "collection_id": source.collection_id,
                    "chunk_count": chunk_count,
                    "embedding_model": settings.embedding_model,
                },
            )
        except Exception:  # noqa: BLE001, S110
            pass  # event bus is advisory; never block the processing path

    except Exception as exc:  # noqa: BLE001
        # ── 8. On any failure: mark failed, do NOT re-raise ─────────────────
        # arq default: zero retries (see decision in design doc).
        reason = str(exc) or type(exc).__name__
        log.error(
            "worker.process_source.failed",
            error=reason,
            outcome="failed",
        )
        try:
            await repo.set_failed(source_id, reason=reason)
        except Exception as inner:  # noqa: BLE001
            log.error("worker.process_source.set_failed_error", error=str(inner))

        # Emit domain event
        try:
            from scout_api.events import get_event_bus  # noqa: PLC0415

            get_event_bus().emit(
                "source.failed",
                {
                    "source_id": source_id,
                    "collection_id": source.collection_id,
                    "reason": reason,
                },
            )
        except Exception:  # noqa: BLE001, S110
            pass  # event bus is advisory; never block the failure path

        raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_content(
    ctx: dict[str, Any],
    origin: str,
    log: Any,
) -> str:
    """Route the fetch to the appropriate adapter based on origin scheme.

    Args:
        ctx: arq context (carries fetchers).
        origin: The source origin URI.
        log: Bound structlog logger.

    Returns:
        Raw text content.

    Raises:
        RuntimeError: If no adapter supports the origin scheme.
    """
    if origin.startswith(("http://", "https://")):
        fetcher = ctx["http_fetcher"]
        result: str = await fetcher.fetch(origin)
        return result

    if origin.startswith("s3://"):
        s3_fetcher = ctx.get("s3_fetcher")
        if s3_fetcher is None:
            # Lazy-initialise S3 fetcher — aioboto3 may not be installed in all envs
            from scout_api.sources.storage import S3StorageAdapter  # noqa: PLC0415

            settings = get_settings()
            storage = S3StorageAdapter(
                bucket=settings.s3_bucket_name,
                region=settings.s3_region,
                endpoint_url=settings.s3_endpoint_url or None,
            )
            s3_fetcher = S3FetchAdapter(storage)
            ctx["s3_fetcher"] = s3_fetcher
        return await s3_fetcher.fetch(origin)

    if origin.startswith("file://"):
        path = origin[7:]
        import pathlib  # noqa: PLC0415

        return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")

    # Unknown scheme — attempt as InMemory (for test injection via ctx)
    in_memory = ctx.get("in_memory_fetcher")
    if in_memory is not None:
        in_memory_result: str = await in_memory.fetch(origin)
        return in_memory_result

    raise RuntimeError(
        f"No fetch adapter for origin scheme: {origin!r}. "
        "Supported: http://, https://, s3://, file://"
    )


# ---------------------------------------------------------------------------
# arq WorkerSettings
# ---------------------------------------------------------------------------


def _get_redis_settings() -> Any:
    """Build arq RedisSettings from environment — lazy import."""
    try:
        from arq.connections import RedisSettings  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "arq is required to run the worker. Install it with: pip install 'arq>=0.26'"
        ) from exc
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379")


class WorkerSettings:
    """arq worker configuration.

    Run with: ``arq scout_api.worker.WorkerSettings``
    """

    functions = [process_source]
    on_startup = worker_startup
    on_shutdown = worker_shutdown

    @property
    def redis_settings(self) -> Any:
        return _get_redis_settings()

    max_jobs = 10
    job_timeout = 300  # 5 minutes per job
