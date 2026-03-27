from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..util.conv import coerce_bool
from .actors import INTERNAL_KIND_PET, add_actor, find_actor, find_foreman, list_visible_actors, remove_actor, update_actor
from .group import Group
from .runtime import PRIMARY_RUNTIMES, detect_runtime, get_runtime_command_with_flags, runtime_start_preflight_error

PET_ACTOR_ID = "pet-peer"
PET_ACTOR_TITLE = "Pet Peer"
LOGGER = logging.getLogger(__name__)


def is_desktop_pet_enabled(group: Group) -> bool:
    features = group.doc.get("features") if isinstance(group.doc.get("features"), dict) else {}
    return coerce_bool(features.get("desktop_pet_enabled"), default=False)


def get_pet_actor(group: Group) -> Optional[Dict[str, Any]]:
    actor = find_actor(group, PET_ACTOR_ID)
    if not isinstance(actor, dict):
        return None
    if str(actor.get("internal_kind") or "").strip() != INTERNAL_KIND_PET:
        return None
    return actor


def _pet_actor_seed(group: Group, *, fallback_actor: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = find_foreman(group)
    if not isinstance(source, dict):
        visible = list_visible_actors(group)
        source = visible[0] if visible else fallback_actor if isinstance(fallback_actor, dict) else {}
    if not isinstance(source, dict) or not source:
        LOGGER.warning(
            "pet actor seed fell back to defaults: no foreman, visible actor, or existing pet actor (group_id=%s)",
            group.group_id,
        )
        source = {}

    runtime_value = str(source.get("runtime") or "").strip()
    runner_value = str(source.get("runner") or "").strip()
    runner = runner_value if runner_value else "pty"
    command = source.get("command") if isinstance(source.get("command"), list) else []
    runtime = runtime_value if runtime_value else "codex"
    if runtime_start_preflight_error(runtime, list(command), runner=runner):
        for candidate in PRIMARY_RUNTIMES:
            if not detect_runtime(candidate).available:
                continue
            runtime = candidate
            command = get_runtime_command_with_flags(candidate)
            break
    env = source.get("env") if isinstance(source.get("env"), dict) else {}
    return {
        "title": PET_ACTOR_TITLE,
        "runtime": runtime,
        "runner": runner,
        "command": list(command or get_runtime_command_with_flags(runtime)),
        "env": dict(env or {}),
        "capability_autoload": ["pack:pet"],
        "default_scope_key": str(source.get("default_scope_key") or group.doc.get("active_scope_key") or "").strip(),
        "submit": str(source.get("submit") or "enter").strip() or "enter",
        "enabled": True,
        "internal_kind": INTERNAL_KIND_PET,
    }


def ensure_pet_actor(group: Group) -> Dict[str, Any]:
    current = get_pet_actor(group)
    seed = _pet_actor_seed(group, fallback_actor=current)
    if current is None:
        return add_actor(
            group,
            actor_id=PET_ACTOR_ID,
            title=str(seed["title"]),
            command=list(seed["command"]),
            env=dict(seed["env"]),
            capability_autoload=list(seed["capability_autoload"]),
            default_scope_key=str(seed["default_scope_key"]),
            submit=str(seed["submit"]),
            enabled=bool(seed["enabled"]),
            runner=str(seed["runner"]),  # type: ignore[arg-type]
            runtime=str(seed["runtime"]),  # type: ignore[arg-type]
            internal_kind=INTERNAL_KIND_PET,
        )
    return update_actor(
        group,
        PET_ACTOR_ID,
        {
            "title": seed["title"],
            "command": seed["command"],
            "env": seed["env"],
            "capability_autoload": seed["capability_autoload"],
            "default_scope_key": seed["default_scope_key"],
            "submit": seed["submit"],
            "enabled": seed["enabled"],
            "runner": seed["runner"],
            "runtime": seed["runtime"],
            "internal_kind": INTERNAL_KIND_PET,
        },
    )


def sync_pet_actor(group: Group) -> Optional[Dict[str, Any]]:
    if not is_desktop_pet_enabled(group):
        current = get_pet_actor(group)
        if current is not None:
            remove_actor(group, PET_ACTOR_ID)
        return None
    return ensure_pet_actor(group)
