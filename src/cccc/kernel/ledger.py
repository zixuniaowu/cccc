from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ..contracts.v1 import Event


def append_event(
    ledger_path: Path,
    *,
    kind: str,
    group_id: str,
    scope_key: str,
    by: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    event = Event(kind=kind, group_id=group_id, scope_key=scope_key, by=by, data=(data or {}))
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = event.model_dump()
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def read_last_lines(path: Path, n: int) -> list[str]:
    if n <= 0:
        return []
    if not path.exists():
        return []
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                step = min(block, size)
                f.seek(size - step)
                data = f.read(step) + data
                size -= step
        lines = data.splitlines()[-n:]
        return [ln.decode("utf-8", errors="replace") for ln in lines]
    except Exception:
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        except Exception:
            return []


def follow(path: Path, *, sleep_seconds: float = 0.2) -> Iterable[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                yield line.rstrip("\n")
                continue
            time.sleep(sleep_seconds)
