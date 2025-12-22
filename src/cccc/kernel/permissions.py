from __future__ import annotations

from typing import Literal, Optional

from .actors import find_actor
from .group import Group


ActorAction = Literal[
    "actor.list",
    "actor.add",
    "actor.remove",
    "actor.update",
    "actor.start",
    "actor.stop",
    "actor.restart",
]


def actor_role(group: Group, actor_id: str) -> Optional[str]:
    item = find_actor(group, actor_id)
    if item is None:
        return None
    role = item.get("role")
    return role if isinstance(role, str) else None


def require_actor_permission(
    group: Group,
    *,
    by: str,
    action: ActorAction,
    target_actor_id: str = "",
) -> None:
    who = (by or "").strip()
    target = (target_actor_id or "").strip()

    if not who or who == "user":
        return

    role = actor_role(group, who)
    if role == "foreman":
        return

    if role == "peer":
        if action in ("actor.stop", "actor.restart", "actor.start"):
            if target and target == who:
                return
        raise ValueError(f"permission denied: {who} cannot {action} {target or ''}".strip())

    raise ValueError(f"unknown actor: {who}")


def require_inbox_permission(group: Group, *, by: str, target_actor_id: str) -> None:
    who = (by or "").strip()
    target = (target_actor_id or "").strip()

    if not who or who == "user":
        return

    role = actor_role(group, who)
    if role == "foreman":
        return

    if role == "peer":
        if target and target == who:
            return
        raise ValueError(f"permission denied: {who} cannot access inbox of {target or ''}".strip())

    raise ValueError(f"unknown actor: {who}")
