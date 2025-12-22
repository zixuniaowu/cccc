from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..util.fs import atomic_write_json, read_json
from ..util.time import parse_utc_iso, utc_now_iso
from .actors import find_actor
from .group import Group


def iter_events(ledger_path: Path) -> Iterable[Dict[str, Any]]:
    if not ledger_path.exists():
        return
    with ledger_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _cursor_path(group: Group) -> Path:
    return group.path / "state" / "read_cursors.json"


def load_cursors(group: Group) -> Dict[str, Any]:
    p = _cursor_path(group)
    doc = read_json(p)
    return doc if isinstance(doc, dict) else {}


def _save_cursors(group: Group, doc: Dict[str, Any]) -> None:
    p = _cursor_path(group)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, doc)


def get_cursor(group: Group, actor_id: str) -> Tuple[str, str]:
    cursors = load_cursors(group)
    cur = cursors.get(actor_id)
    if isinstance(cur, dict):
        event_id = str(cur.get("event_id") or "")
        ts = str(cur.get("ts") or "")
        return event_id, ts
    return "", ""


def set_cursor(group: Group, actor_id: str, *, event_id: str, ts: str) -> Dict[str, Any]:
    cursors = load_cursors(group)
    cursors[str(actor_id)] = {"event_id": str(event_id), "ts": str(ts), "updated_at": utc_now_iso()}
    _save_cursors(group, cursors)
    return dict(cursors[str(actor_id)])


def _message_targets(event: Dict[str, Any]) -> List[str]:
    data = event.get("data")
    if not isinstance(data, dict):
        return []
    to = data.get("to")
    if isinstance(to, list):
        return [str(x) for x in to if isinstance(x, str) and x.strip()]
    return []


def _actor_role(group: Group, actor_id: str) -> str:
    item = find_actor(group, actor_id)
    role = item.get("role") if isinstance(item, dict) else ""
    return role if isinstance(role, str) else ""


def is_message_for_actor(group: Group, *, actor_id: str, event: Dict[str, Any]) -> bool:
    targets = _message_targets(event)
    if not targets:
        return True
    if "@all" in targets:
        return True
    if actor_id in targets:
        return True
    role = _actor_role(group, actor_id)
    if role == "peer" and "@peers" in targets:
        return True
    if role == "foreman" and "@foreman" in targets:
        return True
    return False


def unread_messages(group: Group, *, actor_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    _, cursor_ts = get_cursor(group, actor_id)
    cursor_dt = parse_utc_iso(cursor_ts) if cursor_ts else None

    out: List[Dict[str, Any]] = []
    for ev in iter_events(group.ledger_path):
        if ev.get("kind") != "chat.message":
            continue
        if str(ev.get("by") or "") == actor_id:
            continue
        if not is_message_for_actor(group, actor_id=actor_id, event=ev):
            continue
        if cursor_dt is not None:
            ev_dt = parse_utc_iso(str(ev.get("ts") or ""))
            if ev_dt is not None and ev_dt <= cursor_dt:
                continue
        out.append(ev)
        if limit > 0 and len(out) >= limit:
            break
    return out


def find_event(group: Group, event_id: str) -> Optional[Dict[str, Any]]:
    wanted = event_id.strip()
    if not wanted:
        return None
    for ev in iter_events(group.ledger_path):
        if str(ev.get("id") or "") == wanted:
            return ev
    return None

