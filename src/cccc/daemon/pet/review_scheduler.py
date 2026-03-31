from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Set

from ...contracts.v1.notify import SystemNotifyData
from ...daemon.messaging.delivery import emit_system_notify
from ...kernel.context import ContextStorage
from ...kernel.group import get_group_state, load_group
from ...kernel.ledger_index import lookup_event_by_id
from ...kernel.pet_actor import PET_ACTOR_ID, get_pet_actor, is_desktop_pet_enabled
from ...kernel.pet_task_triage import enum_text, trim_task_text
from ...paths import ensure_home
from ...util.fs import atomic_write_json, read_json

PET_REVIEW_DEBOUNCE_SECONDS = 1.2
PET_REVIEW_MIN_INTERVAL_SECONDS = 3.0
PET_REVIEW_MAX_DELAY_SECONDS = 10.0
_PERSIST_SCHEMA = 1
_PERSIST_FILENAME = "pet_review_pending.json"
_REVIEW_PACKET_SCHEMA = 1
_TASK_ID_RE = re.compile(r"\bT\d+\b")
LOGGER = logging.getLogger(__name__)


@dataclass
class _PetReviewState:
    dirty_since: float = 0.0
    last_dispatched_at: float = 0.0
    due_at: float = 0.0
    reasons: Set[str] = field(default_factory=set)
    source_event_id: str = ""
    timer: Optional[threading.Timer] = None


_LOCK = threading.Lock()
_STATE_BY_GROUP: Dict[str, _PetReviewState] = {}


def _normalize_reason(value: str) -> str:
    return str(value or "").strip().lower() or "state_changed"


def _pending_review_path(group_id: str) -> Path:
    gid = str(group_id or "").strip()
    if not gid:
        return ensure_home() / "groups" / "_invalid" / "state" / _PERSIST_FILENAME
    return ensure_home() / "groups" / gid / "state" / _PERSIST_FILENAME


def _clear_pending_review_file(group_id: str) -> None:
    try:
        _pending_review_path(group_id).unlink(missing_ok=True)
    except Exception:
        pass


def _monotonic_to_wall(ts: float, *, now_wall: float, now_mono: float) -> float:
    if ts <= 0.0:
        return 0.0
    return max(0.0, now_wall + (ts - now_mono))


def _persist_pending_review(group_id: str, state: _PetReviewState) -> None:
    gid = str(group_id or "").strip()
    if not gid:
        return
    path = _pending_review_path(gid)
    path.parent.mkdir(parents=True, exist_ok=True)
    now_wall = time.time()
    now_mono = time.monotonic()
    last_dispatched_wall = _monotonic_to_wall(state.last_dispatched_at, now_wall=now_wall, now_mono=now_mono)
    dirty_since_wall = _monotonic_to_wall(state.dirty_since, now_wall=now_wall, now_mono=now_mono)
    due_at_wall = _monotonic_to_wall(state.due_at, now_wall=now_wall, now_mono=now_mono)
    atomic_write_json(
        path,
        {
            "schema": _PERSIST_SCHEMA,
            "group_id": gid,
            "dirty_since_wall": dirty_since_wall,
            "last_dispatched_wall": last_dispatched_wall,
            "due_at_wall": due_at_wall,
            "reasons": sorted({item for item in state.reasons if item}),
            "source_event_id": str(state.source_event_id or "").strip(),
        },
        indent=2,
    )


def _cancel_timer(state: _PetReviewState) -> None:
    timer = state.timer
    state.timer = None
    if timer is not None:
        timer.cancel()


def _can_review_now(group_id: str) -> bool:
    group = load_group(group_id)
    if group is None:
        return False
    if not is_desktop_pet_enabled(group):
        return False
    if get_group_state(group) not in {"active", "idle"}:
        return False
    actor = get_pet_actor(group)
    if not isinstance(actor, dict):
        return False
    if not bool(actor.get("enabled", True)):
        return False
    return True


def _truncate_text(value: Any, *, max_len: int = 160) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= max_len:
        return text
    return text[: max(1, max_len - 1)].rstrip() + "…"


def _reason_rank(reason: str) -> int:
    priority = {
        "task_waiting_user": 0,
        "chat_reply": 1,
        "system_error": 2,
        "actor_stop": 3,
        "actor_restart": 4,
        "actor_start": 5,
        "task_handoff": 6,
        "task_blocked": 7,
        "coordination_brief_changed": 8,
        "group_state_active": 9,
        "group_state_changed": 10,
        "group_start": 11,
        "pet_enabled": 12,
        "chat_message": 13,
    }
    normalized = _normalize_reason(reason)
    return priority.get(normalized, 100)


def _task_counts(tasks: list[Any]) -> Dict[str, int]:
    waiting_user_count = 0
    blocked_count = 0
    handoff_count = 0
    planned_count = 0
    for task in tasks:
        status = enum_text(getattr(task, "status", ""))
        if status in {"done", "completed", "archived"}:
            continue
        waiting_on = enum_text(getattr(task, "waiting_on", ""))
        blocked_by = list(getattr(task, "blocked_by", []) or [])
        handoff_to = str(getattr(task, "handoff_to", "") or "").strip()
        if waiting_on == "user":
            waiting_user_count += 1
        if blocked_by or waiting_on in {"actor", "external"}:
            blocked_count += 1
        if handoff_to:
            handoff_count += 1
        if status == "planned":
            planned_count += 1
    return {
        "waiting_user_count": waiting_user_count,
        "blocked_count": blocked_count,
        "handoff_count": handoff_count,
        "planned_count": planned_count,
    }


def _task_focus_payload(task: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": str(getattr(task, "id", "") or "").strip(),
        "title": trim_task_text(getattr(task, "title", ""), max_len=96),
        "status": enum_text(getattr(task, "status", "")),
    }
    assignee = str(getattr(task, "assignee", "") or "").strip()
    if assignee:
        payload["assignee"] = assignee
    waiting_on = enum_text(getattr(task, "waiting_on", ""))
    if waiting_on and waiting_on != "none":
        payload["waiting_on"] = waiting_on
    blocked_by = [str(item or "").strip() for item in list(getattr(task, "blocked_by", []) or []) if str(item or "").strip()]
    if blocked_by:
        payload["blocked_by"] = blocked_by[:3]
    handoff_to = str(getattr(task, "handoff_to", "") or "").strip()
    if handoff_to:
        payload["handoff_to"] = handoff_to
    task_type = str(getattr(task, "task_type", "") or "").strip()
    if task_type:
        payload["task_type"] = task_type
    return payload


def _task_id_from_source_event(event: Dict[str, Any]) -> str:
    top_level_task_id = str(event.get("task_id") or "").strip()
    if top_level_task_id:
        return top_level_task_id
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    direct_task_id = str(data.get("task_id") or "").strip()
    if direct_task_id:
        return direct_task_id
    changes = data.get("changes") if isinstance(data.get("changes"), list) else []
    for item in changes:
        if not isinstance(item, dict):
            continue
        structured_task_id = str(item.get("task_id") or "").strip()
        if structured_task_id:
            return structured_task_id
        detail = str(item.get("detail") or "").strip()
        match = _TASK_ID_RE.search(detail)
        if match:
            return str(match.group(0) or "").strip()
    return ""


def _pick_focus_task(tasks: list[Any], *, reasons: Set[str], source_event: Dict[str, Any] | None) -> Dict[str, Any]:
    tasks_by_id: Dict[str, Any] = {}
    waiting_user: list[Any] = []
    handoff: list[Any] = []
    blocked: list[Any] = []
    planned: list[Any] = []
    for task in tasks:
        task_id = str(getattr(task, "id", "") or "").strip()
        if task_id:
            tasks_by_id[task_id] = task
        status = enum_text(getattr(task, "status", ""))
        if status in {"done", "completed", "archived"}:
            continue
        waiting_on = enum_text(getattr(task, "waiting_on", ""))
        blocked_by = list(getattr(task, "blocked_by", []) or [])
        handoff_to = str(getattr(task, "handoff_to", "") or "").strip()
        if waiting_on == "user":
            waiting_user.append(task)
        if handoff_to:
            handoff.append(task)
        if blocked_by or waiting_on in {"actor", "external"}:
            blocked.append(task)
        if status == "planned":
            planned.append(task)

    if isinstance(source_event, dict):
        source_task_id = _task_id_from_source_event(source_event)
        if source_task_id and source_task_id in tasks_by_id:
            return _task_focus_payload(tasks_by_id[source_task_id])

    ordered_reasons = sorted({_normalize_reason(item) for item in reasons if item}, key=_reason_rank)
    for reason in ordered_reasons:
        if reason == "task_waiting_user" and waiting_user:
            return _task_focus_payload(waiting_user[0])
        if reason == "task_handoff" and handoff:
            return _task_focus_payload(handoff[0])
        if reason == "task_blocked" and blocked:
            return _task_focus_payload(blocked[0])
        if reason == "coordination_brief_changed" and waiting_user:
            return _task_focus_payload(waiting_user[0])
        if reason in {"group_start", "group_state_active", "group_state_changed", "pet_enabled"} and planned:
            return _task_focus_payload(planned[0])

    if waiting_user:
        return _task_focus_payload(waiting_user[0])
    if handoff:
        return _task_focus_payload(handoff[0])
    if blocked:
        return _task_focus_payload(blocked[0])
    return {}


def _source_event_payload(group: Any, source_event_id: str) -> Dict[str, Any]:
    event_id = str(source_event_id or "").strip()
    if not event_id:
        return {}
    try:
        event = lookup_event_by_id(group.ledger_path, event_id)
    except Exception:
        return {}
    if not isinstance(event, dict):
        return {}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    payload: Dict[str, Any] = {
        "id": event_id,
        "kind": str(event.get("kind") or "").strip(),
        "by": str(event.get("by") or "").strip(),
    }
    text = ""
    for key in ("text", "title", "message", "quote_text"):
        value = _truncate_text(data.get(key), max_len=180)
        if value:
            text = value
            break
    if text:
        payload["text"] = text
    reply_to = str(data.get("reply_to") or "").strip()
    if reply_to:
        payload["reply_to"] = reply_to
    if bool(data.get("reply_required") is True):
        payload["reply_required"] = True
    task_id = _task_id_from_source_event(event)
    if task_id:
        payload["task_id"] = task_id
    return payload


def _build_review_packet(group: Any, reasons: Set[str], source_event_id: str) -> Dict[str, Any]:
    normalized_reasons = sorted({_normalize_reason(item) for item in reasons if item}, key=_reason_rank)
    primary_reason = normalized_reasons[0] if normalized_reasons else "state_changed"
    packet: Dict[str, Any] = {
        "schema": _REVIEW_PACKET_SCHEMA,
        "primary_reason": primary_reason,
        "reasons": normalized_reasons,
        "group_state": get_group_state(group),
        "attention": {
            "waiting_user_count": 0,
            "blocked_count": 0,
            "handoff_count": 0,
            "planned_count": 0,
        },
    }
    source_event_id = str(source_event_id or "").strip()
    if source_event_id:
        packet["source_event_id"] = source_event_id
    try:
        storage = ContextStorage(group)
        context = storage.load_context()
        tasks = storage.list_tasks()
        coordination = getattr(context, "coordination", None)
        brief = getattr(coordination, "brief", None)
        brief_focus = _truncate_text(getattr(brief, "current_focus", ""), max_len=140)
        if brief_focus:
            packet["brief_focus"] = brief_focus
        packet["attention"] = _task_counts(tasks)
        source_event = _source_event_payload(group, source_event_id)
        if source_event:
            packet["source_event"] = source_event
        focus_task = _pick_focus_task(tasks, reasons=reasons, source_event=source_event if source_event else None)
        if focus_task:
            packet["focus_task"] = focus_task
    except Exception as exc:
        LOGGER.debug("pet_review_packet_build_failed group_id=%s err=%s", getattr(group, "group_id", ""), exc)
    return packet


def _pet_review_notify(group: Any, reasons: Set[str], source_event_id: str) -> SystemNotifyData:
    return SystemNotifyData(
        kind="info",
        priority="normal",
        title="Pet review requested",
        message="Re-evaluate current coordination state and refresh pet decisions.",
        target_actor_id=PET_ACTOR_ID,
        requires_ack=False,
        related_event_id=source_event_id or None,
        context={
            "kind": "pet_review",
            "reasons": sorted({item for item in reasons if item}),
            "source_event_id": source_event_id or None,
            "review_packet": _build_review_packet(group, reasons, source_event_id),
        },
    )


def _emit_pet_review(group_id: str, reasons: Set[str], source_event_id: str) -> None:
    group = load_group(group_id)
    if group is None:
        return
    if not _can_review_now(group_id):
        return
    emit_system_notify(group, by="system", notify=_pet_review_notify(group, reasons, source_event_id))


def request_manual_pet_review(
    group_id: str,
    *,
    reason: str,
    source_event_id: str = "",
) -> bool:
    gid = str(group_id or "").strip()
    if not gid:
        return False
    group = load_group(gid)
    if group is None:
        return False
    if not is_desktop_pet_enabled(group):
        return False
    if get_group_state(group) not in {"active", "idle"}:
        return False
    actor = get_pet_actor(group)
    if not isinstance(actor, dict):
        return False
    if not bool(actor.get("enabled", True)):
        return False
    emit_system_notify(
        group,
        by="system",
        notify=_pet_review_notify(group, {_normalize_reason(reason)}, source_event_id),
    )
    return True


def _flush_due_review(group_id: str) -> None:
    with _LOCK:
        state = _STATE_BY_GROUP.get(group_id)
        if state is None:
            return
        state.timer = None
        if not _can_review_now(group_id):
            state.due_at = 0.0
            _persist_pending_review(group_id, state)
            return
        reasons = set(state.reasons)
        source_event_id = state.source_event_id
        state.reasons.clear()
        state.source_event_id = ""
        state.dirty_since = 0.0
        state.due_at = 0.0
        state.last_dispatched_at = time.monotonic()
        _STATE_BY_GROUP.pop(group_id, None)
    _clear_pending_review_file(group_id)
    _emit_pet_review(group_id, reasons, source_event_id)


def _schedule_locked(group_id: str, state: _PetReviewState, *, immediate: bool) -> None:
    now = time.monotonic()
    if state.dirty_since <= 0.0:
        state.dirty_since = now

    due_at = max(
        state.last_dispatched_at + PET_REVIEW_MIN_INTERVAL_SECONDS,
        now if immediate else state.dirty_since + PET_REVIEW_DEBOUNCE_SECONDS,
    )
    max_due_at = state.dirty_since + PET_REVIEW_MAX_DELAY_SECONDS
    if due_at > max_due_at:
        due_at = max_due_at
    state.due_at = due_at
    if due_at <= now:
        _cancel_timer(state)
        timer = threading.Timer(0.0, _flush_due_review, args=(group_id,))
        timer.daemon = True
        state.timer = timer
        _persist_pending_review(group_id, state)
        timer.start()
        return

    delay = max(0.0, due_at - now)
    _cancel_timer(state)
    timer = threading.Timer(delay, _flush_due_review, args=(group_id,))
    timer.daemon = True
    state.timer = timer
    _persist_pending_review(group_id, state)
    timer.start()


def request_pet_review(
    group_id: str,
    *,
    reason: str,
    source_event_id: str = "",
    immediate: bool = False,
) -> None:
    gid = str(group_id or "").strip()
    if not gid:
        return
    if not _can_review_now(gid):
        return
    persisted_reasons: Set[str] = set()
    persisted_source_event_id = ""
    pending_path = _pending_review_path(gid)
    payload = read_json(pending_path)
    if isinstance(payload, dict) and not _STATE_BY_GROUP.get(gid):
        persisted_reasons = {
            _normalize_reason(item)
            for item in (payload.get("reasons") if isinstance(payload.get("reasons"), list) else [])
            if str(item or "").strip()
        }
        persisted_source_event_id = str(payload.get("source_event_id") or "").strip()
    normalized_reason = _normalize_reason(reason)
    with _LOCK:
        state = _STATE_BY_GROUP.setdefault(gid, _PetReviewState())
        state.reasons.update(persisted_reasons)
        if persisted_source_event_id and not state.source_event_id:
            state.source_event_id = persisted_source_event_id
        state.reasons.add(normalized_reason)
        if source_event_id:
            state.source_event_id = str(source_event_id).strip()
        _schedule_locked(gid, state, immediate=immediate)


def cancel_pet_review(group_id: str) -> None:
    gid = str(group_id or "").strip()
    if not gid:
        return
    with _LOCK:
        state = _STATE_BY_GROUP.pop(gid, None)
    if state is not None:
        _cancel_timer(state)
    _clear_pending_review_file(gid)


def recover_pending_pet_reviews() -> None:
    groups_root = ensure_home() / "groups"
    if not groups_root.exists():
        return
    now_wall = time.time()
    now_mono = time.monotonic()
    for path in groups_root.glob(f"*/state/{_PERSIST_FILENAME}"):
        try:
            payload = read_json(path)
            if not isinstance(payload, dict):
                path.unlink(missing_ok=True)
                continue
            group_id = str(payload.get("group_id") or path.parent.parent.name or "").strip()
            if not group_id:
                path.unlink(missing_ok=True)
                continue
            if not _can_review_now(group_id):
                continue
            reasons_raw = payload.get("reasons")
            reasons = {
                _normalize_reason(item)
                for item in (reasons_raw if isinstance(reasons_raw, list) else [])
                if str(item or "").strip()
            }
            if not reasons:
                path.unlink(missing_ok=True)
                continue
            due_at_wall = float(payload.get("due_at_wall") or 0.0)
            dirty_since_wall = float(payload.get("dirty_since_wall") or 0.0)
            last_dispatched_wall = float(payload.get("last_dispatched_wall") or 0.0)
            with _LOCK:
                state = _STATE_BY_GROUP.setdefault(group_id, _PetReviewState())
                state.reasons = set(reasons)
                state.source_event_id = str(payload.get("source_event_id") or "").strip()
                state.dirty_since = now_mono - max(0.0, now_wall - dirty_since_wall) if dirty_since_wall > 0.0 else 0.0
                state.last_dispatched_at = (
                    now_mono - max(0.0, now_wall - last_dispatched_wall) if last_dispatched_wall > 0.0 else 0.0
                )
                if due_at_wall > 0.0:
                    state.due_at = now_mono + max(0.0, due_at_wall - now_wall)
                else:
                    state.due_at = now_mono
                _schedule_locked(group_id, state, immediate=False)
        except Exception as exc:
            LOGGER.warning("recover_pending_pet_reviews_failed path=%s err=%s", path, exc)
