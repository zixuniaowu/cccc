from __future__ import annotations

from typing import Any, Dict, Optional

from ...contracts.v1 import DaemonError, DaemonResponse
from ...kernel.group import load_group
from ...kernel.ledger import append_event
from ...kernel.pet_actor import PET_ACTOR_ID, get_pet_actor
from ...kernel.pet_decisions import clear_pet_decisions, load_pet_decisions, replace_pet_decisions


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def _require_pet_actor(group: Any, actor_id: str) -> None:
    pet_actor = get_pet_actor(group)
    if not isinstance(pet_actor, dict) or str(actor_id or "").strip() != PET_ACTOR_ID:
        raise ValueError("pet decisions are restricted to pet actor")


def handle_pet_decisions_get(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    return DaemonResponse(ok=True, result={"decisions": load_pet_decisions(group)})


def handle_pet_decisions_replace(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    actor_id = str(args.get("actor_id") or "").strip()
    decisions = args.get("decisions") if isinstance(args.get("decisions"), list) else []
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    try:
        _require_pet_actor(group, actor_id)
        normalized = replace_pet_decisions(group, decisions=list(decisions), actor_id=actor_id)
    except Exception as e:
        return _error("pet_decisions_replace_failed", str(e))
    event = append_event(
        group.ledger_path,
        kind="pet.decisions.replace",
        group_id=group.group_id,
        scope_key="",
        by=actor_id or PET_ACTOR_ID,
        data={"count": len(normalized)},
    )
    return DaemonResponse(ok=True, result={"decisions": normalized, "event": event})


def handle_pet_decisions_clear(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    actor_id = str(args.get("actor_id") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    try:
        _require_pet_actor(group, actor_id)
        clear_pet_decisions(group, actor_id=actor_id)
    except Exception as e:
        return _error("pet_decisions_clear_failed", str(e))
    event = append_event(
        group.ledger_path,
        kind="pet.decisions.clear",
        group_id=group.group_id,
        scope_key="",
        by=actor_id or PET_ACTOR_ID,
        data={},
    )
    return DaemonResponse(ok=True, result={"cleared": True, "event": event})


def try_handle_pet_decision_op(op: str, args: Dict[str, Any]) -> Optional[DaemonResponse]:
    if op == "pet_decisions_get":
        return handle_pet_decisions_get(args)
    if op == "pet_decisions_replace":
        return handle_pet_decisions_replace(args)
    if op == "pet_decisions_clear":
        return handle_pet_decisions_clear(args)
    return None
