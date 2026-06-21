"""Job queue abstractions for source processing.

The AbstractQueueAdapter Protocol defines the interface for enqueuing
background jobs. InMemoryQueueAdapter is used in tests; ArqQueueAdapter
wraps arq in production.

Production wiring:
  ArqQueueAdapter requires arq + Redis. Install with:
    pip install 'arq>=0.26' 'redis>=5.0'

Test wiring:
  InMemoryQueueAdapter captures enqueued jobs in a list — no dependencies.

Job name: ``process_source``
Payload:  ``{"source_id": <int>}``
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AbstractQueueAdapter(Protocol):
    """Protocol for background job queues."""

    async def enqueue(self, job_name: str, **kwargs: Any) -> None:
        """Enqueue a named job with the given keyword arguments.

        Args:
            job_name: Arq function name registered on the worker.
            **kwargs: Payload passed to the worker function.
        """
        ...


class InMemoryQueueAdapter:
    """In-memory queue adapter for tests and local development.

    Captures all enqueued jobs in ``self.jobs`` for assertion in tests.
    Thread-safe enough for single-threaded async test suites.
    """

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    async def enqueue(self, job_name: str, **kwargs: Any) -> None:
        """Record the job in memory without executing it."""
        self.jobs.append({"job": job_name, **kwargs})


class ArqQueueAdapter:
    """Arq + Redis queue adapter for production.

    Wraps arq's ``ArqRedis.enqueue_job``. Requires arq to be installed.

    Args:
        redis_url: Redis DSN (e.g. ``redis://localhost:6379``).
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._pool: Any = None  # arq.ArqRedis — lazy-initialised

    async def _get_pool(self) -> Any:
        """Lazily initialise the arq Redis pool."""
        if self._pool is None:
            try:
                import arq  # noqa: PLC0415
            except ImportError as exc:
                raise ImportError(
                    "arq is required for ArqQueueAdapter. Install it with: pip install 'arq>=0.26'"
                ) from exc
            redis_settings = arq.connections.RedisSettings.from_dsn(self._redis_url)
            self._pool = await arq.create_pool(redis_settings)
        return self._pool

    async def enqueue(self, job_name: str, **kwargs: Any) -> None:
        """Enqueue a job on the arq/Redis queue.

        Args:
            job_name: The arq worker function name.
            **kwargs: Payload forwarded to the worker.
        """
        pool = await self._get_pool()
        await pool.enqueue_job(job_name, **kwargs)
