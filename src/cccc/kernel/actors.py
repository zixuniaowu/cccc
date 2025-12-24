from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..contracts.v1 import Actor, ActorRole, ActorSubmit
from ..util.time import utc_now_iso
from .group import Group

_ACTOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _ensure_actor_list(group: Group) -> List[Dict[str, Any]]:
    actors = group.doc.get("actors")
    if not isinstance(actors, list):
        actors = []
        group.doc["actors"] = actors
    return actors


def list_actors(group: Group) -> List[Dict[str, Any]]:
    actors = _ensure_actor_list(group)
    out: List[Dict[str, Any]] = []
    for item in actors:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id"):
            out.append(item)
    return out


def find_actor(group: Group, actor_id: str) -> Optional[Dict[str, Any]]:
    wanted = actor_id.strip()
    if not wanted:
        return None
    for item in list_actors(group):
        if item.get("id") == wanted:
            return item
    return None


def find_foreman(group: Group) -> Optional[Dict[str, Any]]:
    for item in list_actors(group):
        if item.get("role") == "foreman":
            return item
    return None


def add_actor(
    group: Group,
    *,
    actor_id: str,
    role: ActorRole,
    title: str = "",
    command: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    default_scope_key: str = "",
    submit: ActorSubmit = "enter",
    enabled: bool = True,
) -> Dict[str, Any]:
    aid = actor_id.strip()
    if not aid:
        raise ValueError("missing actor id")
    if not _ACTOR_ID_RE.match(aid):
        raise ValueError("invalid actor id (use a simple token; use title for display)")
    if aid.casefold() == "user":
        raise ValueError("reserved actor id: user")

    existing = find_actor(group, aid)
    if existing is not None:
        raise ValueError(f"actor already exists: {aid}")

    if role == "foreman":
        foreman = find_foreman(group)
        if foreman is not None:
            raise ValueError(f"foreman already exists: {foreman.get('id')}")

    now = utc_now_iso()
    actor = Actor(
        id=aid,
        role=role,
        title=title.strip(),
        command=list(command or []),
        env=dict(env or {}),
        default_scope_key=default_scope_key.strip(),
        submit=submit,
        enabled=bool(enabled),
        created_at=now,
        updated_at=now,
    )
    _ensure_actor_list(group).append(actor.model_dump())
    group.save()
    return actor.model_dump()


def remove_actor(group: Group, actor_id: str) -> None:
    aid = actor_id.strip()
    if not aid:
        raise ValueError("missing actor id")

    actors = _ensure_actor_list(group)
    before = len(actors)
    actors[:] = [a for a in actors if not (isinstance(a, dict) and a.get("id") == aid)]
    if len(actors) == before:
        raise ValueError(f"actor not found: {aid}")
    group.save()


def set_actor_role(group: Group, actor_id: str, role: ActorRole) -> Dict[str, Any]:
    item = find_actor(group, actor_id)
    if item is None:
        raise ValueError(f"actor not found: {actor_id.strip()}")

    if role == "foreman":
        foreman = find_foreman(group)
        if foreman is not None and foreman.get("id") != item.get("id"):
            raise ValueError(f"foreman already exists: {foreman.get('id')}")

    item["role"] = role
    item["updated_at"] = utc_now_iso()
    group.save()
    return dict(item)


def update_actor(group: Group, actor_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    item = find_actor(group, actor_id)
    if item is None:
        raise ValueError(f"actor not found: {actor_id.strip()}")

    if "role" in patch:
        role = patch.get("role")
        if role not in ("foreman", "peer"):
            raise ValueError("invalid role")
        set_actor_role(group, actor_id, role)  # validates foreman uniqueness

    if "title" in patch:
        title = str(patch.get("title") or "").strip()
        item["title"] = title

    if "command" in patch:
        cmd = patch.get("command")
        if cmd is None:
            item["command"] = []
        elif isinstance(cmd, list) and all(isinstance(x, str) for x in cmd):
            item["command"] = cmd
        else:
            raise ValueError("invalid command")

    if "env" in patch:
        env = patch.get("env")
        if env is None:
            item["env"] = {}
        elif isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            item["env"] = env
        else:
            raise ValueError("invalid env")

    if "default_scope_key" in patch:
        item["default_scope_key"] = str(patch.get("default_scope_key") or "").strip()

    if "submit" in patch:
        submit = patch.get("submit")
        if submit is None:
            item["submit"] = "enter"
        elif submit in ("enter", "newline", "none"):
            item["submit"] = submit
        else:
            raise ValueError("invalid submit")

    if "enabled" in patch:
        item["enabled"] = bool(patch.get("enabled"))

    item["updated_at"] = utc_now_iso()
    group.save()
    return dict(item)


def resolve_recipient_tokens(group: Group, tokens: List[str]) -> List[str]:
    raw: List[str] = []
    for t in tokens:
        if not isinstance(t, str):
            continue
        s = t.strip()
        if not s:
            continue
        raw.append(s)

    if not raw:
        return []

    actors = list_actors(group)
    id_set = {str(a.get("id")) for a in actors if isinstance(a, dict) and isinstance(a.get("id"), str)}
    title_map: Dict[str, List[str]] = {}
    for a in actors:
        if not isinstance(a, dict):
            continue
        aid = a.get("id")
        title = a.get("title")
        if not isinstance(aid, str) or not aid.strip():
            continue
        if not isinstance(title, str) or not title.strip():
            continue
        key = title.strip().casefold()
        title_map.setdefault(key, []).append(aid.strip())

    def _canonical_one(token: str) -> str:
        t = token.strip()
        if not t:
            return ""

        # IM-like mentions: allow "@peer-a" / "@Claude" by stripping leading "@"
        if t.startswith("@") and t not in ("@all", "@peers", "@foreman", "@user"):
            t = t[1:].strip()

        if t in ("@all", "@peers", "@foreman"):
            return t
        if t in ("user", "@user"):
            return "user"

        if t in id_set:
            return t

        key = t.casefold()
        ids = title_map.get(key) or []
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            raise ValueError(f"ambiguous recipient title: {t}")

        raise ValueError(f"unknown recipient: {t}")

    out: List[str] = []
    seen = set()
    for t in raw:
        c = _canonical_one(t)
        if not c:
            continue
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out
