"""Domain event bus."""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

_logger = structlog.get_logger(__name__)
_audit_logger = structlog.get_logger("audit")


@dataclass
class DomainEvent:
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = ""
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


EventHandler = Callable[[DomainEvent], None]
SUBSCRIBE_ALL = "*"


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = threading.Lock()
        self._history: list[DomainEvent] = []

    def publish(self, event: DomainEvent) -> None:
        _audit_logger.info(
            "domain_event",
            event_id=event.event_id,
            event_type=event.event_type,
            payload=event.payload,
        )
        with self._lock:
            self._history.append(event)
            handlers = list(self._handlers.get(event.event_type, [])) + list(
                self._handlers.get(SUBSCRIBE_ALL, [])
            )
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                _logger.error("event_handler_failed", event_type=event.event_type, error=str(exc))

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def get_history(self, event_type: str | None = None) -> list[DomainEvent]:
        with self._lock:
            if event_type is None:
                return list(self._history)
            return [e for e in self._history if e.event_type == event_type]

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()

    def clear_subscribers(self) -> None:
        with self._lock:
            self._handlers.clear()


_bus_lock = threading.Lock()
_default_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _default_bus
    if _default_bus is None:
        with _bus_lock:
            if _default_bus is None:
                _default_bus = EventBus()
    return _default_bus


def reset_event_bus() -> None:
    global _default_bus
    with _bus_lock:
        _default_bus = None
