"""Global event bus for system-wide events (group changes, etc.)."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, Set

from ..util.time import utc_now_iso


class GlobalEventBus:
    """In-memory pub/sub event bus for SSE streaming."""

    def __init__(self) -> None:
        self._queues: Set[asyncio.Queue[Dict[str, Any]]] = set()

    def publish(self, kind: str, data: Dict[str, Any] | None = None) -> None:
        """Publish an event to all subscribers."""
        event = {
            "kind": kind,
            "data": data or {},
            "ts": utc_now_iso(),
        }
        for q in self._queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Drop if queue is full (slow consumer)

    async def subscribe(self) -> AsyncIterator[Dict[str, Any]]:
        """Subscribe to events. Yields events as they arrive."""
        q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._queues.add(q)
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            self._queues.discard(q)

    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._queues)


# Singleton instance
global_event_bus = GlobalEventBus()


def publish_event(kind: str, data: Dict[str, Any] | None = None) -> None:
    """Convenience function to publish to the global bus."""
    global_event_bus.publish(kind, data)
