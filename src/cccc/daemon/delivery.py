"""Message delivery module for CCCC daemon.

This module handles:
1. Lazy Preamble: System prompt is delivered with the first message, not at actor startup
2. Message Throttling: Batches messages within a time window to prevent message bombing
3. MCP Hints: Adds MCP usage hints on first delivery and nudge
4. Delivery Formatting: Renders messages in IM-style format for PTY injection

Key design decisions:
- delivery_min_interval_seconds: Minimum interval between deliveries (default 60s)
- Messages within the window are batched and delivered together
- MCP hints are added on first delivery and nudge only
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
from ..kernel.group import Group
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

DEFAULT_DELIVERY_MIN_INTERVAL_SECONDS = 60  # Minimum interval between deliveries


def _get_delivery_config(group: Group) -> Dict[str, Any]:
    """Get delivery configuration from group.yaml."""
    delivery = group.doc.get("delivery")
    if not isinstance(delivery, dict):
        delivery = {}
    return {
        "min_interval_seconds": int(delivery.get("min_interval_seconds", DEFAULT_DELIVERY_MIN_INTERVAL_SECONDS)),
    }


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
    pending_messages: List[PendingMessage] = field(default_factory=list)
    hint_sent: bool = False  # Whether MCP hint has been sent


class DeliveryThrottle:
    """Manages message delivery throttling for all groups/actors.
    
    Key behavior:
    - Messages are queued and delivered in batches
    - Minimum interval between deliveries is configurable (default 60s)
    - MCP hints are added on first delivery and nudge
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
            if state.last_delivery_at is None:
                return True
            now = datetime.now(timezone.utc)
            elapsed = (now - state.last_delivery_at).total_seconds()
            return elapsed >= min_interval_seconds
    
    def flush(self, group_id: str, actor_id: str) -> List[PendingMessage]:
        """Get and clear pending messages for an actor."""
        with self._lock:
            state = self._get_state(group_id, actor_id)
            messages = state.pending_messages
            state.pending_messages = []
            state.last_delivery_at = datetime.now(timezone.utc)
            return messages
    
    def is_first_delivery(self, group_id: str, actor_id: str) -> bool:
        """Check if this is the first delivery for an actor."""
        with self._lock:
            state = self._get_state(group_id, actor_id)
            return not state.hint_sent
    
    def mark_hint_sent(self, group_id: str, actor_id: str) -> None:
        """Mark that MCP hint has been sent."""
        with self._lock:
            state = self._get_state(group_id, actor_id)
            state.hint_sent = True
    
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


# Global throttle instance
THROTTLE = DeliveryThrottle()


# ============================================================================
# Message Rendering
# ============================================================================


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


def render_batched_messages(messages: List[PendingMessage], *, add_hint: bool = False) -> str:
    """Render multiple messages as a batch for PTY delivery."""
    if not messages:
        return ""
    
    if len(messages) == 1:
        # Single message
        text = render_single_message(messages[0])
    else:
        # Multiple messages - batch format
        lines = [f"[cccc] {len(messages)} new messages:"]
        lines.append("")
        for i, msg in enumerate(messages, 1):
            ts_short = msg.ts[11:19] if len(msg.ts) >= 19 else msg.ts  # Extract HH:MM:SS
            if msg.kind == "system.notify":
                lines.append(f"[{i}] {ts_short} SYSTEM ({msg.notify_kind}):")
                lines.append(f"    {msg.notify_title}")
                if msg.notify_message:
                    # Indent message lines
                    for line in msg.notify_message.split("\n")[:2]:  # Max 2 lines
                        lines.append(f"    {line}")
            else:
                who = str(msg.by or "user").strip() or "user"
                targets = ", ".join([str(x).strip() for x in (msg.to or []) if str(x).strip()]) or "@all"
                lines.append(f"[{i}] {ts_short} {who} → {targets}:")
                # Show first 2 lines of message body
                body_lines = (msg.text or "").strip().split("\n")[:2]
                for line in body_lines:
                    preview = line[:80] + "..." if len(line) > 80 else line
                    lines.append(f"    {preview}")
            lines.append("")
        text = "\n".join(lines).rstrip()
    
    # Add MCP hint if requested
    if add_hint:
        text += "\n\n---\n💡 Use cccc_inbox_list() to see full inbox. Mark read with cccc_inbox_mark_read()."
    
    return text


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
    1. 先发送禁用 bracketed paste 模式的转义序列
    2. 发送文本内容
    3. 延迟后发送回车键
    
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
    
    # 策略：先禁用 bracketed paste 模式，然后发送文本，最后延迟发送回车
    # 禁用 bracketed paste 模式的转义序列: \x1b[?2004l
    disable_bracketed_paste = b"\x1b[?2004l"
    
    if multiline:
        # 多行消息：使用 bracketed paste 包裹
        text_payload = b"\x1b[200~" + payload + b"\x1b[201~"
    else:
        # 单行消息：直接发送
        text_payload = payload
    
    # 第一步：发送禁用 bracketed paste + 文本内容
    pty_runner.SUPERVISOR.write_input(group_id=gid, actor_id=aid, data=disable_bracketed_paste + text_payload)
    logger.info(f"[pty_submit_text] Sent text payload, scheduling delayed submit")
    
    # 第二步：延迟发送回车（给 CLI 应用时间处理输入）
    if submit:
        def delayed_submit():
            time.sleep(1.5)  # 1.5秒延迟，用户反馈这个延迟有一定成功率
            if pty_runner.SUPERVISOR.actor_running(gid, aid):
                pty_runner.SUPERVISOR.write_input(group_id=gid, actor_id=aid, data=submit)
                logger.info(f"[pty_submit_text] Delayed submit sent to {gid}/{aid}")
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
                pty_submit_text(group, actor_id=aid, text=prompt, file_fallback=True)
                mark_preamble_sent(group, aid)
        except Exception:
            pass
    
    # 投递用户消息
    return pty_submit_text(group, actor_id=aid, text=message_text)


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
    
    Returns True if messages were delivered.
    """
    gid = group.group_id
    aid = actor_id
    
    config = _get_delivery_config(group)
    min_interval = config["min_interval_seconds"]
    
    if not THROTTLE.should_deliver(gid, aid, min_interval):
        return False
    
    messages = THROTTLE.flush(gid, aid)
    if not messages:
        return False
    
    actor = find_actor(group, aid)
    if not isinstance(actor, dict):
        return False
    
    # Check if we should add MCP hint
    # Add hint on first delivery or if any message is a nudge
    is_first = THROTTLE.is_first_delivery(gid, aid)
    has_nudge = any(m.kind == "system.notify" and m.notify_kind == "nudge" for m in messages)
    add_hint = is_first or has_nudge
    
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
    message_text = render_batched_messages(messages, add_hint=add_hint)
    if message_text:
        parts.append(message_text)
    
    # Combine and send as single delivery
    if parts:
        full_text = "\n\n".join(parts)
        result = pty_submit_text(group, actor_id=aid, text=full_text)
        if result and not preamble_already_sent:
            mark_preamble_sent(group, aid)
    
    if is_first:
        THROTTLE.mark_hint_sent(gid, aid)
    
    return True


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
