from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..util.fs import atomic_write_json, read_json
from ..util.time import parse_utc_iso, utc_now_iso
from .group import Group
from .ledger import read_last_lines


@dataclass(frozen=True)
class LedgerRetentionConfig:
    max_active_bytes: int = 50_000_000
    keep_tail_lines: int = 2000
    min_interval_seconds: int = 300


def _cfg(group: Group) -> LedgerRetentionConfig:
    doc = group.doc.get("ledger")
    d = doc if isinstance(doc, dict) else {}

    def _int(key: str, default: int) -> int:
        try:
            v = int(d.get(key) if key in d else default)
        except Exception:
            v = int(default)
        return max(0, v)

    return LedgerRetentionConfig(
        max_active_bytes=_int("max_active_bytes", LedgerRetentionConfig.max_active_bytes),
        keep_tail_lines=_int("keep_tail_lines", LedgerRetentionConfig.keep_tail_lines),
        min_interval_seconds=_int("min_interval_seconds", LedgerRetentionConfig.min_interval_seconds),
    )


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _cursors_path(group: Group) -> Path:
    return group.path / "state" / "read_cursors.json"


def _ledger_lock_path(group: Group) -> Path:
    return group.path / "state" / "ledger" / "ledger.lock"


def _global_safe_cursor(group: Group) -> Tuple[str, Optional[datetime]]:
    doc = read_json(_cursors_path(group))
    if not isinstance(doc, dict) or not doc:
        return "", None
    best: Optional[datetime] = None
    best_ts = ""
    for _, cur in doc.items():
        if not isinstance(cur, dict):
            continue
        ts = str(cur.get("ts") or "").strip()
        dt = parse_utc_iso(ts) if ts else None
        if dt is None:
            continue
        if best is None or dt < best:
            best = dt
            best_ts = ts
    return best_ts, best


def snapshot(group: Group, *, reason: str = "manual") -> Dict[str, Any]:
    active = group.ledger_path
    try:
        size = int(active.stat().st_size) if active.exists() else 0
    except Exception:
        size = 0
    last_event: Dict[str, Any] = {}
    last = read_last_lines(active, 1)
    if last:
        try:
            obj = json.loads(last[0])
            if isinstance(obj, dict):
                last_event = {k: obj.get(k) for k in ("id", "ts", "kind", "by")}
        except Exception:
            pass

    snap_dir = group.path / "state" / "ledger" / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    name = f"snapshot.{_stamp()}.json"
    p = snap_dir / name
    doc = {
        "v": 1,
        "kind": "ledger.snapshot",
        "group_id": group.group_id,
        "created_at": utc_now_iso(),
        "reason": str(reason or "manual"),
        "active_ledger": {"path": "ledger.jsonl", "size_bytes": size},
        "last_event": last_event,
    }
    atomic_write_json(p, doc)
    atomic_write_json(group.path / "state" / "ledger" / "snapshot.latest.json", doc)
    return {"snapshot_path": str(p), "size_bytes": size, "last_event": last_event}


def compact(group: Group, *, reason: str = "auto", force: bool = False) -> Dict[str, Any]:
    active = group.ledger_path
    if not active.exists():
        return {"ok": False, "skipped": True, "reason": "missing_ledger"}

    cfg = _cfg(group)
    state_path = group.path / "state" / "ledger" / "compaction.json"
    state = read_json(state_path)
    if not isinstance(state, dict):
        state = {}

    if not force and cfg.min_interval_seconds > 0:
        last_ts = str(state.get("last_compacted_at") or "").strip()
        last_dt = parse_utc_iso(last_ts) if last_ts else None
        if last_dt is not None:
            age = (datetime.now(timezone.utc) - last_dt).total_seconds()
            if age < float(cfg.min_interval_seconds):
                return {"ok": True, "skipped": True, "reason": "min_interval"}

    size = int(active.stat().st_size)
    if not force and cfg.max_active_bytes > 0 and size < int(cfg.max_active_bytes):
        return {"ok": True, "skipped": True, "reason": "below_threshold", "size_bytes": size}

    safe_ts, safe_dt = _global_safe_cursor(group)
    if safe_dt is None:
        return {"ok": True, "skipped": True, "reason": "no_global_cursor"}

    lock = _ledger_lock_path(group)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a", encoding="utf-8") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)

        total_lines = 0
        with active.open("r", encoding="utf-8", errors="replace") as f:
            for _ in f:
                total_lines += 1
        keep_tail = int(cfg.keep_tail_lines)
        cutoff = max(0, total_lines - keep_tail) if keep_tail > 0 else total_lines
        if cutoff <= 0:
            return {"ok": True, "skipped": True, "reason": "nothing_to_archive", "total_lines": total_lines}

        archive_dir = group.path / "state" / "ledger" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"ledger.{_stamp()}.jsonl"

        fd, tmp = tempfile.mkstemp(prefix="ledger.keep.", dir=str(active.parent))
        archived = 0
        kept = 0
        wrote_archive = False
        arch_f = None
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as keep_f:
                with active.open("r", encoding="utf-8", errors="replace") as src:
                    for idx, line in enumerate(src, start=1):
                        if idx <= cutoff:
                            try:
                                obj = json.loads(line)
                                ts = str(obj.get("ts") or "").strip() if isinstance(obj, dict) else ""
                                dt = parse_utc_iso(ts) if ts else None
                            except Exception:
                                dt = None
                            if dt is not None and dt <= safe_dt:
                                if not wrote_archive:
                                    arch_f = archive_path.open("w", encoding="utf-8")
                                    wrote_archive = True
                                assert arch_f is not None
                                arch_f.write(line)
                                archived += 1
                                continue
                        keep_f.write(line)
                        kept += 1
            if arch_f is not None:
                try:
                    arch_f.close()
                except Exception:
                    pass
            if archived <= 0:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                try:
                    if wrote_archive:
                        archive_path.unlink()
                except Exception:
                    pass
                return {"ok": True, "skipped": True, "reason": "no_archivable_events", "safe_ts": safe_ts}

            os.replace(tmp, active)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception:
                pass

        result = {
            "v": 1,
            "last_compacted_at": utc_now_iso(),
            "reason": str(reason or "auto"),
            "safe_ts": safe_ts,
            "archived_lines": archived,
            "kept_lines": kept,
            "archive_path": str(archive_path) if wrote_archive else "",
        }
        atomic_write_json(state_path, result)
        snap = snapshot(group, reason=f"compact:{reason}")
        return {"ok": True, "skipped": False, "result": result, "snapshot": snap}
