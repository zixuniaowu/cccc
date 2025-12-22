from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ..paths import ensure_home
from ..util.fs import atomic_write_json, read_json
from ..util.time import utc_now_iso


def active_path() -> Path:
    return ensure_home() / "active.json"


def load_active() -> Dict[str, Any]:
    p = active_path()
    doc = read_json(p)
    if not doc:
        doc = {"v": 1, "active_group_id": "", "updated_at": utc_now_iso()}
        atomic_write_json(p, doc)
    return doc


def set_active_group_id(group_id: str) -> Dict[str, Any]:
    p = active_path()
    doc = {"v": 1, "active_group_id": group_id.strip(), "updated_at": utc_now_iso()}
    atomic_write_json(p, doc)
    return doc

