from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from ..util.fs import atomic_write_json, read_json
from ..util.time import parse_utc_iso, utc_now_iso
from .actors import find_actor, get_effective_role
from .group import Group


# 消息类型过滤
MessageKindFilter = Literal["all", "chat", "notify"]


def iter_events(ledger_path: Path) -> Iterable[Dict[str, Any]]:
    """遍历 ledger 中的所有事件"""
    if not ledger_path.exists():
        return
    with ledger_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _cursor_path(group: Group) -> Path:
    return group.path / "state" / "read_cursors.json"


def load_cursors(group: Group) -> Dict[str, Any]:
    """加载所有 actor 的已读游标"""
    p = _cursor_path(group)
    doc = read_json(p)
    return doc if isinstance(doc, dict) else {}


def _save_cursors(group: Group, doc: Dict[str, Any]) -> None:
    p = _cursor_path(group)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, doc)


def get_cursor(group: Group, actor_id: str) -> Tuple[str, str]:
    """获取 actor 的已读游标 (event_id, ts)"""
    cursors = load_cursors(group)
    cur = cursors.get(actor_id)
    if isinstance(cur, dict):
        event_id = str(cur.get("event_id") or "")
        ts = str(cur.get("ts") or "")
        return event_id, ts
    return "", ""


def set_cursor(group: Group, actor_id: str, *, event_id: str, ts: str) -> Dict[str, Any]:
    """设置 actor 的已读游标（只能往前推进）"""
    cursors = load_cursors(group)
    cur = cursors.get(actor_id)

    # 检查是否往前推进
    if isinstance(cur, dict):
        cur_ts = str(cur.get("ts") or "")
        if cur_ts:
            cur_dt = parse_utc_iso(cur_ts)
            new_dt = parse_utc_iso(ts)
            if cur_dt is not None and new_dt is not None and new_dt < cur_dt:
                # 不允许往回退
                return dict(cur)

    cursors[str(actor_id)] = {
        "event_id": str(event_id),
        "ts": str(ts),
        "updated_at": utc_now_iso(),
    }
    _save_cursors(group, cursors)
    return dict(cursors[str(actor_id)])


def _message_targets(event: Dict[str, Any]) -> List[str]:
    """获取消息的目标收件人列表"""
    data = event.get("data")
    if not isinstance(data, dict):
        return []
    to = data.get("to")
    if isinstance(to, list):
        return [str(x) for x in to if isinstance(x, str) and x.strip()]
    return []


def _actor_role(group: Group, actor_id: str) -> str:
    """获取 actor 的有效角色（基于位置自动判断）"""
    return get_effective_role(group, actor_id)


def is_message_for_actor(group: Group, *, actor_id: str, event: Dict[str, Any]) -> bool:
    """判断消息是否应该投递给指定 actor"""
    kind = str(event.get("kind") or "")
    
    # system.notify 事件检查 target_actor_id
    if kind == "system.notify":
        data = event.get("data")
        if not isinstance(data, dict):
            return False
        target = str(data.get("target_actor_id") or "").strip()
        # 空 target = 广播给所有人
        if not target:
            return True
        return target == actor_id
    
    # chat.message 事件检查 to 字段
    targets = _message_targets(event)

    # 空 targets = 广播，所有人可见
    if not targets:
        return True

    # @all = 所有 actors
    if "@all" in targets:
        return True

    # 直接指定 actor_id
    if actor_id in targets:
        return True

    # 按角色匹配
    role = _actor_role(group, actor_id)
    if role == "peer" and "@peers" in targets:
        return True
    if role == "foreman" and "@foreman" in targets:
        return True

    return False


def unread_messages(group: Group, *, actor_id: str, limit: int = 50, kind_filter: MessageKindFilter = "all") -> List[Dict[str, Any]]:
    """获取 actor 的未读消息列表
    
    Args:
        group: 工作组
        actor_id: actor ID
        limit: 最大返回数量
        kind_filter: 消息类型过滤
            - "all": 所有消息（chat.message + system.notify）
            - "chat": 仅 chat.message
            - "notify": 仅 system.notify
    """
    _, cursor_ts = get_cursor(group, actor_id)
    cursor_dt = parse_utc_iso(cursor_ts) if cursor_ts else None

    # 确定要匹配的 event kinds
    if kind_filter == "chat":
        allowed_kinds = {"chat.message"}
    elif kind_filter == "notify":
        allowed_kinds = {"system.notify"}
    else:
        allowed_kinds = {"chat.message", "system.notify"}

    out: List[Dict[str, Any]] = []
    for ev in iter_events(group.ledger_path):
        ev_kind = str(ev.get("kind") or "")
        if ev_kind not in allowed_kinds:
            continue
        # 排除自己发的消息（chat.message）
        if ev_kind == "chat.message" and str(ev.get("by") or "") == actor_id:
            continue
        # 检查是否是发给自己的
        if not is_message_for_actor(group, actor_id=actor_id, event=ev):
            continue
        # 检查是否已读
        if cursor_dt is not None:
            ev_dt = parse_utc_iso(str(ev.get("ts") or ""))
            if ev_dt is not None and ev_dt <= cursor_dt:
                continue
        out.append(ev)
        if limit > 0 and len(out) >= limit:
            break
    return out


def unread_count(group: Group, *, actor_id: str, kind_filter: MessageKindFilter = "all") -> int:
    """获取 actor 的未读消息数量
    
    Args:
        group: 工作组
        actor_id: actor ID
        kind_filter: 消息类型过滤（同 unread_messages）
    """
    _, cursor_ts = get_cursor(group, actor_id)
    cursor_dt = parse_utc_iso(cursor_ts) if cursor_ts else None

    # 确定要匹配的 event kinds
    if kind_filter == "chat":
        allowed_kinds = {"chat.message"}
    elif kind_filter == "notify":
        allowed_kinds = {"system.notify"}
    else:
        allowed_kinds = {"chat.message", "system.notify"}

    count = 0
    for ev in iter_events(group.ledger_path):
        ev_kind = str(ev.get("kind") or "")
        if ev_kind not in allowed_kinds:
            continue
        if ev_kind == "chat.message" and str(ev.get("by") or "") == actor_id:
            continue
        if not is_message_for_actor(group, actor_id=actor_id, event=ev):
            continue
        if cursor_dt is not None:
            ev_dt = parse_utc_iso(str(ev.get("ts") or ""))
            if ev_dt is not None and ev_dt <= cursor_dt:
                continue
        count += 1
    return count


def find_event(group: Group, event_id: str) -> Optional[Dict[str, Any]]:
    """根据 event_id 查找事件"""
    wanted = event_id.strip()
    if not wanted:
        return None
    for ev in iter_events(group.ledger_path):
        if str(ev.get("id") or "") == wanted:
            return ev
    return None


def get_quote_text(group: Group, event_id: str, max_len: int = 100) -> Optional[str]:
    """获取被引用消息的文本片段（用于 reply_to）"""
    ev = find_event(group, event_id)
    if ev is None:
        return None
    data = ev.get("data")
    if not isinstance(data, dict):
        return None
    text = data.get("text")
    if not isinstance(text, str):
        return None
    text = text.strip()
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def get_read_status(group: Group, event_id: str) -> Dict[str, bool]:
    """获取消息的已读状态（哪些 actor 已读）"""
    ev = find_event(group, event_id)
    if ev is None:
        return {}

    ev_ts = str(ev.get("ts") or "")
    ev_dt = parse_utc_iso(ev_ts) if ev_ts else None
    if ev_dt is None:
        return {}

    cursors = load_cursors(group)
    result: Dict[str, bool] = {}

    for actor_id, cur in cursors.items():
        if not isinstance(cur, dict):
            continue
        cur_ts = str(cur.get("ts") or "")
        cur_dt = parse_utc_iso(cur_ts) if cur_ts else None
        if cur_dt is not None and cur_dt >= ev_dt:
            result[actor_id] = True
        else:
            result[actor_id] = False

    return result


def search_messages(
    group: Group,
    *,
    query: str = "",
    kind_filter: MessageKindFilter = "all",
    by_filter: str = "",
    before_id: str = "",
    after_id: str = "",
    limit: int = 50,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Search and paginate messages in the ledger.
    
    Args:
        group: Working group
        query: Text search query (case-insensitive substring match)
        kind_filter: Filter by message type (all/chat/notify)
        by_filter: Filter by sender (actor_id or "user")
        before_id: Return messages before this event_id (for backward pagination)
        after_id: Return messages after this event_id (for forward pagination)
        limit: Maximum number of messages to return
    
    Returns:
        Tuple of (messages, has_more)
    """
    # Determine allowed kinds
    if kind_filter == "chat":
        allowed_kinds = {"chat.message"}
    elif kind_filter == "notify":
        allowed_kinds = {"system.notify"}
    else:
        allowed_kinds = {"chat.message", "system.notify"}
    
    query_lower = query.lower().strip() if query else ""
    by_filter = by_filter.strip()
    
    # Collect all matching events
    all_events: List[Dict[str, Any]] = []
    for ev in iter_events(group.ledger_path):
        ev_kind = str(ev.get("kind") or "")
        if ev_kind not in allowed_kinds:
            continue
        
        # Filter by sender
        if by_filter:
            ev_by = str(ev.get("by") or "")
            if ev_by != by_filter:
                continue
        
        # Text search
        if query_lower:
            data = ev.get("data")
            if isinstance(data, dict):
                text = str(data.get("text") or "").lower()
                title = str(data.get("title") or "").lower()
                message = str(data.get("message") or "").lower()
                if query_lower not in text and query_lower not in title and query_lower not in message:
                    continue
            else:
                continue
        
        all_events.append(ev)
    
    # Handle pagination
    if before_id:
        # Find the index of before_id and return events before it
        idx = -1
        for i, ev in enumerate(all_events):
            if str(ev.get("id") or "") == before_id:
                idx = i
                break
        if idx > 0:
            start = max(0, idx - limit)
            result = all_events[start:idx]
            has_more = start > 0
            return result, has_more
        return [], False
    
    if after_id:
        # Find the index of after_id and return events after it
        idx = -1
        for i, ev in enumerate(all_events):
            if str(ev.get("id") or "") == after_id:
                idx = i
                break
        if idx >= 0 and idx < len(all_events) - 1:
            start = idx + 1
            end = min(len(all_events), start + limit)
            result = all_events[start:end]
            has_more = end < len(all_events)
            return result, has_more
        return [], False
    
    # Default: return last N messages
    if len(all_events) > limit:
        result = all_events[-limit:]
        has_more = True
    else:
        result = all_events
        has_more = False
    
    return result, has_more

