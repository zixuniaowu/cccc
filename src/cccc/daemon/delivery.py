from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ..kernel.actors import find_actor
from ..kernel.group import Group
from ..kernel.system_prompt import render_system_prompt
from ..runners import pty as pty_runner
from ..util.fs import atomic_write_text


def render_delivery_text(*, by: str, to: list[str], text: str) -> str:
    who = str(by or "user").strip() or "user"
    targets = ", ".join([str(x).strip() for x in (to or []) if str(x).strip()]) or "@all"
    body = (text or "").rstrip("\n")
    head = f"[cccc] {who} → {targets}"
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
