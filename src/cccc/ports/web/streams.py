"""SSE streaming utilities for the web port."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import AsyncIterator, TextIO

from starlette.responses import StreamingResponse


async def sse_jsonl_tail(path: Path, *, event_name: str, heartbeat_s: float = 30.0) -> AsyncIterator[bytes]:
    """Tail a JSONL file and yield SSE events for each appended line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    inode = -1
    f: TextIO | None = None
    last_send = time.monotonic()

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

    yield b": connected\n\n"

    while True:
        line = f.readline()
        if line:
            raw = line.rstrip("\n")
            if raw:
                yield f"event: {event_name}\n".encode("utf-8")
                yield b"data: " + raw.encode("utf-8", errors="replace") + b"\n\n"
                last_send = time.monotonic()
            continue

        now = time.monotonic()
        if heartbeat_s > 0 and now - last_send >= heartbeat_s:
            yield b": heartbeat\n\n"
            last_send = now

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


async def sse_ledger_tail(path: Path) -> AsyncIterator[bytes]:
    async for item in sse_jsonl_tail(path, event_name="ledger", heartbeat_s=30.0):
        yield item


async def sse_global_events_tail(home: Path | None = None) -> AsyncIterator[bytes]:
    from ...kernel.events import global_events_path

    path = global_events_path(home)
    async for item in sse_jsonl_tail(path, event_name="event", heartbeat_s=30.0):
        yield item


def create_sse_response(generator: AsyncIterator[bytes]) -> StreamingResponse:
    """Create a properly configured SSE StreamingResponse."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
