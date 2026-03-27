from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from ..util.fs import atomic_write_json, read_json

if TYPE_CHECKING:
    from .group import Group

_SCHEMA = 1


def pet_decisions_path(group: Group) -> Path:
    return group.path / "state" / "pet_decisions.json"


def _normalize_action(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    action_type = str(raw.get("type") or "").strip()
    out: Dict[str, Any] = {"type": action_type}
    if action_type == "restart_actor":
        out["group_id"] = str(raw.get("group_id") or "").strip()
        out["actor_id"] = str(raw.get("actor_id") or "").strip()
        return out
    if action_type == "send_suggestion":
        out["group_id"] = str(raw.get("group_id") or "").strip()
        out["text"] = str(raw.get("text") or "").strip()
        if isinstance(raw.get("to"), list):
            out["to"] = [str(item or "").strip() for item in raw.get("to") if str(item or "").strip()]
        reply_to = str(raw.get("reply_to") or "").strip()
        if reply_to:
            out["reply_to"] = reply_to
        return out
    if action_type == "task_proposal":
        out["group_id"] = str(raw.get("group_id") or "").strip()
        out["operation"] = str(raw.get("operation") or "").strip().lower()
        task_id = str(raw.get("task_id") or "").strip()
        if task_id:
            out["task_id"] = task_id
        title = str(raw.get("title") or "").strip()
        if title:
            out["title"] = title
        status = str(raw.get("status") or "").strip().lower()
        if status:
            out["status"] = status
        assignee = str(raw.get("assignee") or "").strip()
        if assignee:
            out["assignee"] = assignee
        text = str(raw.get("text") or "").strip()
        if text:
            out["text"] = text
        return out
    if action_type == "automation_proposal":
        out["group_id"] = str(raw.get("group_id") or "").strip()
        title = str(raw.get("title") or "").strip()
        if title:
            out["title"] = title
        summary = str(raw.get("summary") or "").strip()
        if summary:
            out["summary"] = summary
        actions_raw = raw.get("actions")
        if isinstance(actions_raw, list):
            normalized_actions: list[dict[str, Any]] = []
            for item in actions_raw:
                if not isinstance(item, dict):
                    continue
                action_item: Dict[str, Any] = {}
                action_kind = str(item.get("type") or "").strip()
                if not action_kind:
                    continue
                action_item["type"] = action_kind
                for key in ("rule_id", "enabled"):
                    if key in item:
                        action_item[key] = item.get(key)
                if isinstance(item.get("rule"), dict):
                    action_item["rule"] = dict(item.get("rule") or {})
                if isinstance(item.get("ruleset"), dict):
                    action_item["ruleset"] = dict(item.get("ruleset") or {})
                normalized_actions.append(action_item)
            if normalized_actions:
                out["actions"] = normalized_actions
        return out
    return {}


def _normalize_source(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in ("event_id", "task_id", "actor_id", "actor_role", "error_reason", "suggestion_kind"):
        value = str(raw.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def _normalize_decision(raw: Any) -> Dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    decision_id = str(raw.get("id") or "").strip()
    fingerprint = str(raw.get("fingerprint") or "").strip()
    kind = str(raw.get("kind") or "").strip()
    summary = str(raw.get("summary") or "").strip()
    action = _normalize_action(raw.get("action"))
    action_type = str(action.get("type") or "").strip()
    if not decision_id or not fingerprint or not kind or not summary or not action_type:
        return None
    out: Dict[str, Any] = {
        "id": decision_id,
        "kind": kind,
        "priority": int(raw.get("priority") or 0),
        "summary": summary,
        "agent": str(raw.get("agent") or "").strip(),
        "fingerprint": fingerprint,
        "action": action,
        "source": _normalize_source(raw.get("source")),
        "updated_at": str(raw.get("updated_at") or "").strip(),
    }
    suggestion = str(raw.get("suggestion") or "").strip()
    if suggestion:
        out["suggestion"] = suggestion
    suggestion_preview = str(raw.get("suggestion_preview") or "").strip()
    if suggestion_preview:
        out["suggestion_preview"] = suggestion_preview
    if bool(raw.get("ephemeral")):
        out["ephemeral"] = True
    return out


def load_pet_decisions(group: Group) -> List[Dict[str, Any]]:
    raw = read_json(pet_decisions_path(group))
    if not isinstance(raw, dict):
        return []
    items = raw.get("decisions")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in items:
        normalized = _normalize_decision(item)
        if normalized is not None:
            out.append(normalized)
    return out


def replace_pet_decisions(group: Group, *, decisions: List[Dict[str, Any]], actor_id: str) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in decisions:
        normalized_item = _normalize_decision(item)
        if normalized_item is not None:
            normalized.append(normalized_item)
    atomic_write_json(
        pet_decisions_path(group),
        {
            "schema": _SCHEMA,
            "by": str(actor_id or "").strip(),
            "decisions": normalized,
        },
        indent=2,
    )
    return normalized


def clear_pet_decisions(group: Group, *, actor_id: str) -> None:
    atomic_write_json(
        pet_decisions_path(group),
        {
            "schema": _SCHEMA,
            "by": str(actor_id or "").strip(),
            "decisions": [],
        },
        indent=2,
    )
