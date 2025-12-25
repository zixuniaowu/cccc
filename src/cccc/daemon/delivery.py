from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..kernel.actors import find_actor, list_actors
from ..kernel.group import Group
from ..kernel.inbox import is_message_for_actor
from ..kernel.system_prompt import render_system_prompt
from ..runners import pty as pty_runner
from ..runners import headless as headless_runner
from ..util.fs import atomic_write_text


def render_delivery_text(
    *,
    by: str,
    to: list[str],
    text: str,
    reply_to: Optional[str] = None,
    quote_text: Optional[str] = None,
) -> str:
    """渲染投递到 PTY 的消息格式（IM 风格）"""
    who = str(by or "user").strip() or "user"
    targets = ", ".join([str(x).strip() for x in (to or []) if str(x).strip()]) or "@all"
    body = (text or "").rstrip("\n")

    # 构建头部
    head = f"[cccc] {who} → {targets}"
    if reply_to:
        head += f" (reply:{reply_to[:8]})"

    # 如果有引用文本，添加引用块
    if quote_text:
        quote_preview = quote_text[:80].replace("\n", " ")
        if len(quote_text) > 80:
            quote_preview += "..."
        head += f'\n> "{quote_preview}"'

    return f"{head}:\n{body}" if "\n" in body else f"{head}: {body}"


def pty_submit_text(group: Group, *, actor_id: str, text: str, file_fallback: bool) -> bool:
    gid = str(group.group_id or "").strip()
    aid = str(actor_id or "").strip()
    if not gid or not aid:
        return False
    if not pty_runner.SUPERVISOR.actor_running(gid, aid):
        return False

    raw = (text or "").rstrip("\n")
    if not raw:
        return False

    multiline = ("\n" in raw) or ("\r" in raw)
    bracketed_ok = multiline and pty_runner.SUPERVISOR.bracketed_paste_enabled(group_id=gid, actor_id=aid)

    if multiline and not bracketed_ok and file_fallback:
        p = Path(group.path) / "state" / "delivery" / f"{aid}.txt"
        atomic_write_text(p, raw + "\n")
        raw = f"[cccc] Delivered as file (terminal has no bracketed-paste): {p}"
        multiline = False
        bracketed_ok = False

    if multiline and not bracketed_ok:
        raw = raw.replace("\r", "").replace("\n", "\\n")
        multiline = False

    submit = b"\r"
    actor = find_actor(group, aid)
    mode = str(actor.get("submit") if isinstance(actor, dict) else "") or "enter"
    if mode == "none":
        submit = b""
    elif mode == "newline":
        submit = b"\n"

    payload = raw.encode("utf-8", errors="replace")
    if multiline and bracketed_ok:
        payload = b"\x1b[200~" + payload + b"\x1b[201~"
    payload += submit
    pty_runner.SUPERVISOR.write_input(group_id=gid, actor_id=aid, data=payload)
    return True


def inject_system_prompt(group: Group, *, actor: Dict[str, Any]) -> None:
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
