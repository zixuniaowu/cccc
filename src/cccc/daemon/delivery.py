"""Message delivery module for CCCC daemon.

This module handles:
1. Lazy Preamble: System prompt is delivered with the first message, not at actor startup
2. Message Throttling: Batches messages within a time window to prevent message bombing
3. MCP Reminders: Periodically reminds actors to use MCP tools for messaging
4. Delivery Formatting: Renders messages in IM-style format for PTY injection
5. State-aware Delivery: Respects group state (active/idle/paused)

Key design decisions:
- delivery_min_interval_seconds: Minimum interval between deliveries (default 0s)
- Messages within the window are batched and delivered together
- Reminders are injected every N chat messages (per actor) to reduce "stdout-only" replies

Group State Behavior:
- active: All messages delivered normally
- idle: chat.message delivered (auto-transitions to active), system.notify blocked
- paused: All PTY delivery blocked (messages accumulate in inbox only)
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..kernel.actors import find_actor, list_actors
from ..kernel.group import Group, get_group_state, set_group_state
from ..kernel.inbox import is_message_for_actor
from ..kernel.system_prompt import render_system_prompt
from ..paths import ensure_home
from ..runners import pty as pty_runner
from ..runners import headless as headless_runner
from ..util.fs import atomic_write_text, atomic_write_json, read_json
from ..util.time import parse_utc_iso, utc_now_iso


# ============================================================================
# Configuration
# ============================================================================

DEFAULT_DELIVERY_MIN_INTERVAL_SECONDS = 0  # Minimum interval between deliveries
DEFAULT_DELIVERY_RETRY_INTERVAL_SECONDS = 5  # Retry interval when delivery cannot be completed


def _get_delivery_config(group: Group) -> Dict[str, Any]:
    """Get delivery configuration from group.yaml."""
    delivery = group.doc.get("delivery")
    if not isinstance(delivery, dict):
        delivery = {}
    return {
        "min_interval_seconds": int(delivery.get("min_interval_seconds", DEFAULT_DELIVERY_MIN_INTERVAL_SECONDS)),
    }


# ============================================================================
# State-aware Delivery Helpers
# ============================================================================


def should_deliver_message(group: Group, kind: str) -> bool:
    """Check if a message should be delivered based on group state.
    
    Args:
        group: The group to check
        kind: Message kind ("chat.message" or "system.notify")
    
    Returns:
        True if the message should be delivered to PTY, False if it should only go to inbox
    
    State behavior:
        - active: All messages delivered
        - idle: chat.message delivered, system.notify blocked (no auto state transition here)
        - paused: All messages blocked (inbox only)
    """
    state = get_group_state(group)
    
    if state == "paused":
        # Paused: block all PTY delivery
        return False
    
    if state == "idle":
        # Idle: only chat.message allowed (system.notify blocked).
        #
        # IMPORTANT: Do NOT auto-transition to active here. Waking an idle group is
        # handled at message-ingest time (e.g. user sends a new message), otherwise
        # agent-to-agent chatter or delayed/throttled deliveries can accidentally
        # flip idle -> active and re-enable automation.
        return kind == "chat.message"
    
    # Active: all messages delivered
    return True


# ============================================================================
# Lazy Preamble (System Prompt) 机制
# ============================================================================


def _preamble_state_path(group: Group) -> Path:
    """获取 preamble 状态文件路径"""
    return group.path / "state" / "preamble_sent.json"


def _load_preamble_sent(group: Group) -> Dict[str, bool]:
    """加载 preamble 发送状态"""
    p = _preamble_state_path(group)
    try:
        data = read_json(p)
        if isinstance(data, dict):
            return {str(k): bool(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _save_preamble_sent(group: Group, state: Dict[str, bool]) -> None:
    """保存 preamble 发送状态"""
    p = _preamble_state_path(group)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, json.dumps(state, ensure_ascii=False, indent=2))
    except Exception:
        pass


def is_preamble_sent(group: Group, actor_id: str) -> bool:
    """检查 actor 是否已经收到过 system prompt"""
    state = _load_preamble_sent(group)
    return bool(state.get(actor_id, False))


def mark_preamble_sent(group: Group, actor_id: str) -> None:
    """标记 actor 已经收到 system prompt"""
    state = _load_preamble_sent(group)
    state[actor_id] = True
    _save_preamble_sent(group, state)


def clear_preamble_sent(group: Group, actor_id: Optional[str] = None) -> None:
    """清除 preamble 发送状态（用于 actor 重启等场景）
    
    如果 actor_id 为 None，清除所有 actor 的状态
    """
    if actor_id is None:
        _save_preamble_sent(group, {})
    else:
        state = _load_preamble_sent(group)
        state.pop(actor_id, None)
        _save_preamble_sent(group, state)


# ============================================================================
# Delivery Throttle (消息打包限流)
# ============================================================================


@dataclass
class PendingMessage:
    """A message pending delivery."""
    event_id: str
    by: str
    to: List[str]
    text: str
    reply_to: Optional[str] = None
    quote_text: Optional[str] = None
    ts: str = ""
    kind: str = "chat.message"  # chat.message or system.notify
    notify_kind: str = ""  # For system.notify: nudge, keepalive, etc.
    notify_title: str = ""
    notify_message: str = ""


@dataclass
class ActorDeliveryState:
    """Delivery state for a single actor."""
    last_delivery_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None
    pending_messages: List[PendingMessage] = field(default_factory=list)
    delivered_chat_count: int = 0  # Count of delivered chat.message (per actor, in-memory)


class DeliveryThrottle:
    """Manages message delivery throttling for all groups/actors.
    
    Key behavior:
    - Messages are queued and delivered in batches
    - Minimum interval between deliveries is configurable (default 60s)
    - A periodic reminder can be injected by the delivery layer
    """
    
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # State: {group_id: {actor_id: ActorDeliveryState}}
        self._states: Dict[str, Dict[str, ActorDeliveryState]] = {}
    
    def _get_state(self, group_id: str, actor_id: str) -> ActorDeliveryState:
        """Get or create delivery state for an actor."""
        if group_id not in self._states:
            self._states[group_id] = {}
        if actor_id not in self._states[group_id]:
            self._states[group_id][actor_id] = ActorDeliveryState()
        return self._states[group_id][actor_id]
    
    def queue_message(
        self,
        group_id: str,
        actor_id: str,
        *,
        event_id: str,
        by: str,
        to: List[str],
        text: str,
        reply_to: Optional[str] = None,
        quote_text: Optional[str] = None,
        ts: str = "",
        kind: str = "chat.message",
        notify_kind: str = "",
        notify_title: str = "",
        notify_message: str = "",
    ) -> None:
        """Queue a message for delivery."""
        with self._lock:
            state = self._get_state(group_id, actor_id)
            state.pending_messages.append(PendingMessage(
                event_id=event_id,
                by=by,
                to=to,
                text=text,
                reply_to=reply_to,
                quote_text=quote_text,
                ts=ts or utc_now_iso(),
                kind=kind,
                notify_kind=notify_kind,
                notify_title=notify_title,
                notify_message=notify_message,
            ))
    
    def should_deliver(self, group_id: str, actor_id: str, min_interval_seconds: int) -> bool:
        """Check if we should deliver messages now."""
        with self._lock:
            state = self._get_state(group_id, actor_id)
            if not state.pending_messages:
                return False
            now = datetime.now(timezone.utc)
            # If we've never successfully delivered, allow attempts with a short retry backoff.
            if state.last_delivery_at is None:
                if state.last_attempt_at is None:
                    return True
                elapsed_attempt = (now - state.last_attempt_at).total_seconds()
                return elapsed_attempt >= DEFAULT_DELIVERY_RETRY_INTERVAL_SECONDS

            elapsed_delivery = (now - state.last_delivery_at).total_seconds()
            if elapsed_delivery < min_interval_seconds:
                return False

            # Past min-interval since last successful delivery; still avoid tight retry loops.
            if state.last_attempt_at is None:
                return True
            elapsed_attempt = (now - state.last_attempt_at).total_seconds()
            return elapsed_attempt >= DEFAULT_DELIVERY_RETRY_INTERVAL_SECONDS
    
    def take_pending(self, group_id: str, actor_id: str) -> List[PendingMessage]:
        """Take and clear pending messages for an actor (marks a delivery attempt)."""
        with self._lock:
            state = self._get_state(group_id, actor_id)
            messages = state.pending_messages
            state.pending_messages = []
            state.last_attempt_at = datetime.now(timezone.utc)
            return messages

    def requeue_front(self, group_id: str, actor_id: str, messages: List[PendingMessage]) -> None:
        """Requeue messages at the front to preserve ordering across retries."""
        if not messages:
            return
        with self._lock:
            state = self._get_state(group_id, actor_id)
            state.pending_messages = list(messages) + state.pending_messages

    def mark_delivered(self, group_id: str, actor_id: str) -> None:
        """Mark a successful delivery timestamp for an actor."""
        with self._lock:
            state = self._get_state(group_id, actor_id)
            state.last_delivery_at = datetime.now(timezone.utc)
            # A successful delivery should not trigger retry backoff gating.
            state.last_attempt_at = None

    def get_delivered_chat_count(self, group_id: str, actor_id: str) -> int:
        """Get delivered chat.message count for an actor (in-memory)."""
        with self._lock:
            state = self._get_state(group_id, actor_id)
            return int(state.delivered_chat_count or 0)

    def add_delivered_chat_count(self, group_id: str, actor_id: str, delta: int) -> None:
        """Add delivered chat.message count for an actor (in-memory)."""
        d = int(delta or 0)
        if d <= 0:
            return
        with self._lock:
            state = self._get_state(group_id, actor_id)
            state.delivered_chat_count = int(state.delivered_chat_count or 0) + d
    
    def has_pending(self, group_id: str, actor_id: str) -> bool:
        """Check if actor has pending messages."""
        with self._lock:
            state = self._get_state(group_id, actor_id)
            return len(state.pending_messages) > 0
    
    def clear_actor(self, group_id: str, actor_id: str) -> None:
        """Clear all state for an actor (e.g., on restart)."""
        with self._lock:
            if group_id in self._states and actor_id in self._states[group_id]:
                del self._states[group_id][actor_id]

    def clear_pending_system_notifies(self, group_id: str, *, notify_kinds: Optional[set[str]] = None) -> int:
        """Remove pending system.notify messages for a group.

        Used to prevent "catch-up" notification bursts when resuming from
        idle/paused.
        """
        gid = str(group_id or "").strip()
        if not gid:
            return 0
        with self._lock:
            actors = self._states.get(gid)
            if not isinstance(actors, dict) or not actors:
                return 0
            removed = 0
            for st in actors.values():
                before = len(st.pending_messages)
                if not before:
                    continue
                kept: list[PendingMessage] = []
                for msg in st.pending_messages:
                    if msg.kind != "system.notify":
                        kept.append(msg)
                        continue
                    if notify_kinds is not None and msg.notify_kind not in notify_kinds:
                        kept.append(msg)
                        continue
                st.pending_messages = kept
                removed += before - len(kept)
            return removed


# Global throttle instance
THROTTLE = DeliveryThrottle()


# ============================================================================
# Message Rendering
# ============================================================================

REMINDER_EVERY_N_MESSAGES = 3
MCP_REMINDER_LINE = (
    "[cccc] Reminder: communicate via MCP (cccc_message_send / cccc_message_reply); terminal output isn't delivered. "
    "See cccc_help."
)


def render_single_message(msg: PendingMessage) -> str:
    """Render a single message for PTY delivery."""
    if msg.kind == "system.notify":
        # System notification format
        return f"[cccc] SYSTEM ({msg.notify_kind}): {msg.notify_title}\n{msg.notify_message}".strip()
    
    # Chat message format
    who = str(msg.by or "user").strip() or "user"
    targets = ", ".join([str(x).strip() for x in (msg.to or []) if str(x).strip()]) or "@all"
    body = (msg.text or "").rstrip("\n")

    # Build header
    head = f"[cccc] {who} → {targets}"
    if msg.reply_to:
        head += f" (reply:{msg.reply_to[:8]})"

    # Add quote if present
    if msg.quote_text:
        quote_preview = msg.quote_text[:80].replace("\n", " ")
        if len(msg.quote_text) > 80:
            quote_preview += "..."
        head += f'\n> "{quote_preview}"'

    return f"{head}:\n{body}" if "\n" in body else f"{head}: {body}"


def render_batched_messages(messages: List[PendingMessage], *, reminder_after_index: Optional[int] = None) -> str:
    """Render multiple messages as a batch for PTY delivery."""
    if not messages:
        return ""

    blocks: List[str] = []
    if len(messages) > 1:
        blocks.append(f"[cccc] {len(messages)} new messages:")

    for i, msg in enumerate(messages, 1):
        blocks.append(render_single_message(msg))

    if reminder_after_index is not None:
        blocks.append(MCP_REMINDER_LINE)

    return "\n\n".join([b for b in blocks if b]).rstrip()


# ============================================================================
# Legacy render function (for backward compatibility)
# ============================================================================


def render_delivery_text(
    *,
    by: str,
    to: list[str],
    text: str,
    reply_to: Optional[str] = None,
    quote_text: Optional[str] = None,
) -> str:
    """Render a single message for PTY delivery (legacy interface)."""
    msg = PendingMessage(
        event_id="",
        by=by,
        to=to,
        text=text,
        reply_to=reply_to,
        quote_text=quote_text,
    )
    return render_single_message(msg)


# ============================================================================
# PTY Submission
# ============================================================================


def pty_submit_text(group: Group, *, actor_id: str, text: str, file_fallback: bool = False) -> bool:
    """向 PTY 投递消息文本。
    
    投递策略：
    1. 发送文本内容（优先用 bracketed paste wrapper，模拟“粘贴”输入）
    2. 延迟后发送回车键
    
    关键发现：
    - 原子发送（payload + submit 一起）从未成功过
    - 延迟发送有一定成功率
    - 问题可能是 CLI 应用的 readline 处理时序问题
    """
    import logging
    logger = logging.getLogger("cccc.delivery")
    
    gid = str(group.group_id or "").strip()
    aid = str(actor_id or "").strip()
    if not gid or not aid:
        logger.warning(f"[pty_submit_text] Missing gid={gid} or aid={aid}")
        return False
    if not pty_runner.SUPERVISOR.actor_running(gid, aid):
        logger.warning(f"[pty_submit_text] Actor not running: {gid}/{aid}")
        return False

    raw = (text or "").rstrip("\n")
    if not raw:
        logger.warning(f"[pty_submit_text] Empty text for {gid}/{aid}")
        return False

    multiline = ("\n" in raw) or ("\r" in raw)
    
    # 获取 actor 的 submit 模式
    submit = b"\r"
    actor = find_actor(group, aid)
    mode = str(actor.get("submit") if isinstance(actor, dict) else "") or "enter"
    if mode == "none":
        submit = b""
    elif mode == "newline":
        submit = b"\n"

    payload = raw.encode("utf-8", errors="replace")
    
    logger.info(f"[pty_submit_text] Sending to {gid}/{aid}: multiline={multiline}, mode={mode}, len={len(payload)}")
    
    # NOTE: bracketed paste 模式是“程序输出 -> 终端模拟器”的协商；这里是“向程序 stdin 写入”，
    # 不应发送 \x1b[?2004h/\x1b[?2004l 这类输出控制序列作为输入（会变成脏输入，可能导致 CLI 偶发丢输入/乱序）。
    bracketed = False
    try:
        bracketed = bool(pty_runner.SUPERVISOR.bracketed_paste_enabled(group_id=gid, actor_id=aid))
    except Exception:
        bracketed = False

    # 尽量把整段消息当作一次“粘贴”输入（允许包含换行），减少 readline/快捷键干扰。
    # 仅在目标进程已启用 bracketed paste 时使用 wrapper，否则会变成原始 ESC 序列输入。
    if bracketed:
        text_payload = b"\x1b[200~" + payload + b"\x1b[201~"
    else:
        text_payload = payload
    
    # 第一步：发送文本内容
    ok = bool(pty_runner.SUPERVISOR.write_input(group_id=gid, actor_id=aid, data=text_payload))
    if not ok:
        logger.warning(f"[pty_submit_text] Failed to write payload to {gid}/{aid}")
        return False
    logger.info(f"[pty_submit_text] Sent text payload, scheduling delayed submit")
    
    # 第二步：延迟发送回车（给 CLI 应用时间处理输入）
    if submit:
        def delayed_submit():
            time.sleep(1.5)  # 1.5秒延迟，用户反馈这个延迟有一定成功率
            if pty_runner.SUPERVISOR.actor_running(gid, aid):
                ok_submit = bool(pty_runner.SUPERVISOR.write_input(group_id=gid, actor_id=aid, data=submit))
                if ok_submit:
                    logger.info(f"[pty_submit_text] Delayed submit sent to {gid}/{aid}")
                else:
                    logger.warning(f"[pty_submit_text] Delayed submit failed to write to {gid}/{aid}")
            else:
                logger.warning(f"[pty_submit_text] Actor no longer running for delayed submit: {gid}/{aid}")
        
        submit_thread = threading.Thread(target=delayed_submit, daemon=True)
        submit_thread.start()
    
    return True


# ============================================================================
# High-level Delivery Functions
# ============================================================================


def deliver_message_with_preamble(
    group: Group,
    *,
    actor_id: str,
    message_text: str,
    by: str,
) -> bool:
    """投递消息到 PTY，如果是第一条消息则附加 system prompt。
    
    这是 lazy preamble 机制的核心：
    - 如果 actor 还没收到过 system prompt，先投递 system prompt
    - 然后投递用户消息
    
    注意：这个函数是立即投递，不经过 throttle。用于向后兼容。
    新代码应该使用 queue_and_maybe_deliver()。
    """
    aid = str(actor_id or "").strip()
    if not aid:
        return False
    
    actor = find_actor(group, aid)
    if not isinstance(actor, dict):
        return False
    
    # 检查是否需要投递 system prompt（lazy preamble）
    if not is_preamble_sent(group, aid):
        try:
            prompt = render_system_prompt(group=group, actor=actor)
            if prompt and prompt.strip():
                if pty_submit_text(group, actor_id=aid, text=prompt, file_fallback=True):
                    mark_preamble_sent(group, aid)
        except Exception:
            pass
    
    # 投递用户消息
    delivered_before = THROTTLE.get_delivered_chat_count(group.group_id, aid)
    out = (message_text or "").rstrip("\n")
    if out and (delivered_before + 1) % REMINDER_EVERY_N_MESSAGES == 0:
        out = out + "\n\n" + MCP_REMINDER_LINE
    result = pty_submit_text(group, actor_id=aid, text=out)
    if result:
        THROTTLE.add_delivered_chat_count(group.group_id, aid, 1)
    return result


def queue_chat_message(
    group: Group,
    *,
    actor_id: str,
    event_id: str,
    by: str,
    to: List[str],
    text: str,
    reply_to: Optional[str] = None,
    quote_text: Optional[str] = None,
    ts: str = "",
) -> None:
    """Queue a chat message for throttled delivery."""
    THROTTLE.queue_message(
        group.group_id,
        actor_id,
        event_id=event_id,
        by=by,
        to=to,
        text=text,
        reply_to=reply_to,
        quote_text=quote_text,
        ts=ts,
        kind="chat.message",
    )


def queue_system_notify(
    group: Group,
    *,
    actor_id: str,
    event_id: str,
    notify_kind: str,
    title: str,
    message: str,
    ts: str = "",
) -> None:
    """Queue a system notification for throttled delivery."""
    THROTTLE.queue_message(
        group.group_id,
        actor_id,
        event_id=event_id,
        by="system",
        to=[actor_id],
        text="",
        ts=ts,
        kind="system.notify",
        notify_kind=notify_kind,
        notify_title=title,
        notify_message=message,
    )


def flush_pending_messages(group: Group, *, actor_id: str) -> bool:
    """Flush pending messages for an actor if ready.
    
    Respects group state:
    - active: All messages delivered
    - idle: chat.message delivered (auto-transitions to active), system.notify blocked
    - paused: All messages blocked (stay in queue for later)
    
    Returns True if messages were delivered.
    """
    gid = group.group_id
    aid = actor_id
    
    config = _get_delivery_config(group)
    min_interval = config["min_interval_seconds"]
    
    if not THROTTLE.should_deliver(gid, aid, min_interval):
        return False
    
    messages = THROTTLE.take_pending(gid, aid)
    if not messages:
        return False
    
    # Filter messages based on group state
    deliverable: List[PendingMessage] = []
    requeue: List[PendingMessage] = []
    
    for msg in messages:
        if should_deliver_message(group, msg.kind):
            deliverable.append(msg)
        else:
            # Message blocked by state - requeue for later
            requeue.append(msg)
    
    if not deliverable:
        # Nothing is deliverable in the current group state; keep everything queued.
        THROTTLE.requeue_front(gid, aid, messages)
        return False
    
    actor = find_actor(group, aid)
    if not isinstance(actor, dict):
        THROTTLE.requeue_front(gid, aid, messages)
        return False

    chat_total = sum(1 for m in deliverable if m.kind == "chat.message")
    reminder_after_index: Optional[int] = None
    if chat_total > 0:
        delivered_before = THROTTLE.get_delivered_chat_count(gid, aid)
        delivered_after = delivered_before + chat_total
        # Remind every N delivered chat messages per actor (count is in-memory).
        if (delivered_after // REMINDER_EVERY_N_MESSAGES) > (delivered_before // REMINDER_EVERY_N_MESSAGES):
            reminder_after_index = len(deliverable)
    
    # Build the full delivery text
    parts: List[str] = []
    
    # Check if preamble needs to be sent - prepend to message
    preamble_already_sent = is_preamble_sent(group, aid)
    if not preamble_already_sent:
        try:
            prompt = render_system_prompt(group=group, actor=actor)
            if prompt and prompt.strip():
                parts.append(prompt.strip())
        except Exception:
            pass
    
    # Render batched messages
    message_text = render_batched_messages(deliverable, reminder_after_index=reminder_after_index)
    if message_text:
        parts.append(message_text)
    
    # Combine and send as single delivery
    delivered = False
    if parts:
        full_text = "\n\n".join(parts)
        delivered = bool(pty_submit_text(group, actor_id=aid, text=full_text))
        if delivered:
            if not preamble_already_sent:
                mark_preamble_sent(group, aid)
            THROTTLE.add_delivered_chat_count(gid, aid, chat_total)
            THROTTLE.mark_delivered(gid, aid)
            # Keep blocked messages queued for later.
            if requeue:
                THROTTLE.requeue_front(gid, aid, requeue)
        else:
            # Delivery failed: keep everything queued for retry.
            THROTTLE.requeue_front(gid, aid, messages)
    
    return delivered


def tick_delivery(group: Group) -> None:
    """Called periodically to flush pending messages for all actors."""
    for actor in list_actors(group):
        if not isinstance(actor, dict):
            continue
        aid = str(actor.get("id") or "").strip()
        if not aid:
            continue
        # Only for PTY runners
        runner_kind = str(actor.get("runner") or "pty").strip()
        if runner_kind != "pty":
            continue
        # Check if actor process is actually running
        if not pty_runner.SUPERVISOR.actor_running(group.group_id, aid):
            continue
        
        try:
            flush_pending_messages(group, actor_id=aid)
        except Exception:
            pass


def inject_system_prompt(group: Group, *, actor: Dict[str, Any]) -> None:
    """Inject system prompt into actor's PTY (for manual refresh)."""
    aid = str(actor.get("id") or "").strip()
    if not aid:
        return
    prompt = render_system_prompt(group=group, actor=actor)
    pty_submit_text(group, actor_id=aid, text=prompt, file_fallback=True)


def get_headless_targets_for_message(
    group: Group,
    *,
    event: Dict[str, Any],
    by: str,
) -> List[str]:
    """获取需要通知的 headless actor 列表。
    
    这个函数只做判断，不做写入操作。写入由 daemon server 负责。
    
    Returns:
        List of actor_ids that should be notified
    """
    targets: List[str] = []
    
    for actor in list_actors(group):
        if not isinstance(actor, dict):
            continue
        aid = str(actor.get("id") or "").strip()
        if not aid or aid == "user" or aid == by:
            continue
        
        # 只处理 headless runner
        runner_kind = str(actor.get("runner") or "pty").strip()
        if runner_kind != "headless":
            continue
        
        # 检查 actor 是否在运行
        if not headless_runner.SUPERVISOR.actor_running(group.group_id, aid):
            continue
        
        # 检查消息是否是发给这个 actor 的
        if not is_message_for_actor(group, actor_id=aid, event=event):
            continue
        
        targets.append(aid)
    
    return targets
