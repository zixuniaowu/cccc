"""SSE streaming utilities for the web port."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, AsyncIterator, Dict, TextIO

from starlette.responses import StreamingResponse


async def sse_ledger_tail(path: Path) -> AsyncIterator[bytes]:
    """Tail a ledger file and yield SSE events for new lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    inode = -1
    f: TextIO | None = None

    def _open() -> None:
        nonlocal f, inode
        if f is not None:
            try:
                f.close()
            except Exception:
                pass
        f = path.open("r", encoding="utf-8", errors="replace")
        try:
            st = os.fstat(f.fileno())
            inode = int(getattr(st, "st_ino", -1) or -1)
        except Exception:
            inode = -1
        f.seek(0, 2)

    _open()
    assert f is not None

    while True:
        line = f.readline()
        if line:
            raw = line.rstrip("\n")
            if raw:
                yield b"event: ledger\n"
                yield b"data: " + raw.encode("utf-8", errors="replace") + b"\n\n"
            continue

        await asyncio.sleep(0.2)
        try:
            st = path.stat()
            cur_inode = int(getattr(st, "st_ino", -1) or -1)
            if inode != -1 and cur_inode != -1 and cur_inode != inode:
                _open()
                continue
            if st.st_size < f.tell():
                _open()
                continue
        except Exception:
            try:
                path.touch(exist_ok=True)
            except Exception:
                pass
            _open()


async def sse_global_events_generator() -> AsyncIterator[bytes]:
    """Generate SSE events from the global event bus with heartbeat."""
    from ...kernel.events import global_event_bus

    # Send initial comment to establish connection
    yield b": connected\n\n"

    # Create a queue and subscribe to the event bus
    q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=100)
    global_event_bus._queues.add(q)
    try:
        while True:
            try:
                # Wait for event with 30s timeout
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                data = json.dumps(event, ensure_ascii=False)
                yield f"event: event\ndata: {data}\n\n".encode("utf-8")
            except asyncio.TimeoutError:
                # Send heartbeat comment to keep connection alive
                yield b": heartbeat\n\n"
    finally:
        global_event_bus._queues.discard(q)


def create_sse_response(generator: AsyncIterator[bytes]) -> StreamingResponse:
    """Create a properly configured SSE StreamingResponse."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
