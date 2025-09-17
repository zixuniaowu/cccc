# -*- coding: utf-8 -*-
"""Utilities for Plan-of-Record (POR) storage and summaries."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
import hashlib
import json
import time

POR_NOTES_LIMIT = 12


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _coerce_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.splitlines() if part.strip()]
    return []


def normalize_por(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a POR dict with all expected fields."""
    now = _iso_now()
    return {
        "version": str(raw.get("version") or "0.1"),
        "updated_at": str(raw.get("updated_at") or now),
        "goal": str(raw.get("goal") or "").strip(),
        "constraints": _coerce_list(raw.get("constraints")),
        "acceptance": _coerce_list(raw.get("acceptance")),
        "subtask": str(raw.get("subtask") or "").strip(),
        "next_step": str(raw.get("next_step") or "").strip(),
        "risks": _coerce_list(raw.get("risks")),
        "notes": _coerce_list(raw.get("notes")),
    }


def _por_path(home: Path) -> Path:
    return home/"state"/"por.json"


def read_por(home: Path) -> Dict[str, Any]:
    path = _por_path(home)
    if not path.exists():
        return normalize_por({})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return normalize_por(data)


def save_por(home: Path, por: Dict[str, Any], log_fn: Callable[[Dict[str, Any]], None], *, reason: Optional[str] = None) -> Dict[str, Any]:
    por["updated_at"] = _iso_now()
    payload = json.dumps(por, ensure_ascii=False, indent=2)
    path = _por_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    entry = {"from": "system", "kind": "por_update", "updated_at": por["updated_at"], "hash": digest}
    if reason:
        entry["reason"] = reason
    log_fn(entry)
    return por


def ensure_por(home: Path, log_fn: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    path = _por_path(home)
    if path.exists():
        return read_por(home)
    por = normalize_por({})
    save_por(home, por, log_fn, reason="init")
    return por


def append_note(por: Dict[str, Any], note: str, *, limit: int = POR_NOTES_LIMIT) -> Dict[str, Any]:
    if not note:
        return por
    notes = por.get("notes")
    if isinstance(notes, list):
        notes.append(note)
    elif isinstance(notes, str) and notes.strip():
        notes = [notes.strip(), note]
    else:
        notes = [note]
    while len(notes) > limit:
        notes.pop(0)
    por["notes"] = notes
    return por


def compact_summary(home: Path, por: Dict[str, Any], index: int) -> str:
    timestamp = time.strftime("%Y-%m-%d %H:%M")

    def first_line(peer: str, filename: str, limit: int = 160) -> str:
        try:
            text = (home/"mailbox"/peer/filename).read_text(encoding="utf-8").strip()
            if not text:
                return ""
            return text.splitlines()[0][:limit]
        except Exception:
            return ""

    parts: List[str] = []
    a_to_b = first_line("peerA", "to_peer.md")
    if a_to_b:
        parts.append(f"A->B {a_to_b}")
    b_to_a = first_line("peerB", "to_peer.md")
    if b_to_a:
        parts.append(f"B->A {b_to_a}")
    to_user = first_line("peerA", "to_user.md")
    if to_user:
        parts.append(f"ToUser {to_user}")
    next_step = str(por.get("next_step") or "").strip()
    if next_step:
        parts.append(f"Next {next_step[:80]}")
    detail = " | ".join(parts) if parts else "-"
    return f"[compact#{index}] {timestamp} {detail}"


def por_status_snapshot(por: Dict[str, Any]) -> Dict[str, Any]:
    notes = por.get("notes")
    if isinstance(notes, list):
        last = notes[-1] if notes else ""
        count = len(notes)
    elif isinstance(notes, str) and notes.strip():
        last = notes.strip()
        count = 1
    else:
        last = ""
        count = 0
    return {
        "goal": por.get("goal", ""),
        "next_step": por.get("next_step", ""),
        "subtask": por.get("subtask", ""),
        "last_note": last,
        "notes_count": count,
    }
