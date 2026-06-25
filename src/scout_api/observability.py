"""RED-method observability decorator for service and repository methods."""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from prometheus_client import Counter, Histogram

P = ParamSpec("P")
R = TypeVar("R")

_OPS_TOTAL = Counter("operations_total", "RED: operation invocations", ["operation", "outcome"])
_OPS_DURATION = Histogram("operation_duration_seconds", "RED: operation duration", ["operation"])


def observed(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                start = time.perf_counter()
                outcome = "success"
                try:
                    return await fn(*args, **kwargs)
                except Exception:
                    outcome = "error"
                    raise
                finally:
                    _OPS_TOTAL.labels(name, outcome).inc()
                    _OPS_DURATION.labels(name).observe(time.perf_counter() - start)

            return async_wrapper  # type: ignore[return-value]
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                start = time.perf_counter()
                outcome = "success"
                try:
                    return fn(*args, **kwargs)
                except Exception:
                    outcome = "error"
                    raise
                finally:
                    _OPS_TOTAL.labels(name, outcome).inc()
                    _OPS_DURATION.labels(name).observe(time.perf_counter() - start)

            return sync_wrapper  # type: ignore[return-value]

    return decorator
