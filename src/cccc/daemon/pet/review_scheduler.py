from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from ...contracts.v1.notify import SystemNotifyData
from ...daemon.messaging.delivery import emit_system_notify
from ...kernel.group import get_group_state, load_group
from ...kernel.pet_actor import PET_ACTOR_ID, get_pet_actor, is_desktop_pet_enabled

PET_REVIEW_DEBOUNCE_SECONDS = 1.2
PET_REVIEW_MIN_INTERVAL_SECONDS = 3.0
PET_REVIEW_MAX_DELAY_SECONDS = 10.0


@dataclass
class _PetReviewState:
    dirty_since: float = 0.0
    last_dispatched_at: float = 0.0
    reasons: Set[str] = field(default_factory=set)
    source_event_id: str = ""
    timer: Optional[threading.Timer] = None


_LOCK = threading.Lock()
_STATE_BY_GROUP: Dict[str, _PetReviewState] = {}


def _normalize_reason(value: str) -> str:
    return str(value or "").strip().lower() or "state_changed"


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
    if get_group_state(group) != "active":
        return False
    actor = get_pet_actor(group)
    if not isinstance(actor, dict):
        return False
    if not bool(actor.get("enabled", True)):
        return False
    return True


def _emit_pet_review(group_id: str, reasons: Set[str], source_event_id: str) -> None:
    group = load_group(group_id)
    if group is None:
        return
    if not _can_review_now(group_id):
        return
    notify = SystemNotifyData(
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
        },
    )
    emit_system_notify(group, by="system", notify=notify)


def _flush_due_review(group_id: str) -> None:
    with _LOCK:
        state = _STATE_BY_GROUP.get(group_id)
        if state is None:
            return
        state.timer = None
        reasons = set(state.reasons)
        source_event_id = state.source_event_id
        state.reasons.clear()
        state.source_event_id = ""
        state.dirty_since = 0.0
        state.last_dispatched_at = time.monotonic()
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
    if due_at <= now:
        _cancel_timer(state)
        timer = threading.Timer(0.0, _flush_due_review, args=(group_id,))
        timer.daemon = True
        state.timer = timer
        timer.start()
        return

    delay = max(0.0, due_at - now)
    _cancel_timer(state)
    timer = threading.Timer(delay, _flush_due_review, args=(group_id,))
    timer.daemon = True
    state.timer = timer
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
    normalized_reason = _normalize_reason(reason)
    with _LOCK:
        state = _STATE_BY_GROUP.setdefault(gid, _PetReviewState())
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
