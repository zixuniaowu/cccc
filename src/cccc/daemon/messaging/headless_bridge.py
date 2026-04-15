from __future__ import annotations

from typing import Optional

from ...contracts.v1.message import ChatMessageData, ChatStreamData
from ...kernel.actors import resolve_recipient_tokens
from ...kernel.group import load_group
from ...kernel.inbox import find_event
from ...kernel.messaging import default_reply_recipients
from ...kernel.ledger import append_event


def is_user_facing_stream_phase(phase: str) -> bool:
    normalized = str(phase or "").strip().lower()
    return normalized in ("", "final_answer")


def _resolve_headless_recipients(*, group_id: str, actor_id: str, reply_to: Optional[str]) -> list[str]:
    group = load_group(group_id)
    if group is None:
        return ["user"]

    reply_target = str(reply_to or "").strip()
    if not reply_target:
        return ["user"]

    original = find_event(group, reply_target)
    if not isinstance(original, dict):
        return ["user"]

    try:
        to_tokens = default_reply_recipients(
            group,
            by=str(actor_id or "").strip(),
            original_event=original,
        )
        recipients = resolve_recipient_tokens(group, to_tokens)
    except Exception:
        recipients = []
    return recipients or ["user"]


def append_headless_chat_stream(
    *,
    group_id: str,
    actor_id: str,
    stream_id: str,
    op: str,
    text: str = "",
    seq: int = 0,
    reply_to: Optional[str] = None,
) -> Optional[dict]:
    group = load_group(group_id)
    if group is None:
        return None
    scope_key = str(group.doc.get("active_scope_key") or "").strip()
    recipients = _resolve_headless_recipients(group_id=group_id, actor_id=actor_id, reply_to=reply_to)
    payload = ChatStreamData(
        stream_id=str(stream_id or "").strip(),
        op=str(op or "").strip(),  # validated by model
        text=str(text or ""),
        seq=int(seq or 0),
        to=recipients,
        reply_to=str(reply_to or "").strip() or None,
    )
    return append_event(
        group.ledger_path,
        kind="chat.stream",
        group_id=group.group_id,
        scope_key=scope_key,
        by=str(actor_id or "").strip(),
        data=payload.model_dump(),
    )


def append_headless_chat_message(
    *,
    group_id: str,
    actor_id: str,
    text: str,
    stream_id: str,
    pending_event_id: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> Optional[dict]:
    if not str(text or "").strip():
        return None
    group = load_group(group_id)
    if group is None:
        return None
    scope_key = str(group.doc.get("active_scope_key") or "").strip()
    recipients = _resolve_headless_recipients(group_id=group_id, actor_id=actor_id, reply_to=reply_to)
    payload = ChatMessageData(
        text=str(text or ""),
        to=recipients,
        reply_to=str(reply_to or "").strip() or None,
        stream_id=str(stream_id or "").strip() or None,
        pending_event_id=str(pending_event_id or "").strip() or None,
    )
    return append_event(
        group.ledger_path,
        kind="chat.message",
        group_id=group.group_id,
        scope_key=scope_key,
        by=str(actor_id or "").strip(),
        data=payload.model_dump(),
    )
