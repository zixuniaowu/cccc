from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .contracts.v1 import ChatMessageData
from .daemon.server import call_daemon
from .kernel.active import load_active, set_active_group_id
from .kernel.actors import add_actor, list_actors, remove_actor, set_actor_role, update_actor
from .kernel.group import attach_scope_to_group, create_group, ensure_group_for_scope, load_group, set_active_scope
from .kernel.inbox import find_event, get_cursor, set_cursor, unread_messages
from .kernel.ledger import append_event, follow, read_last_lines
from .kernel.permissions import require_actor_permission, require_inbox_permission
from .kernel.registry import load_registry
from .kernel.scope import detect_scope


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _ensure_daemon_running() -> bool:
    resp = call_daemon({"op": "ping"})
    if resp.get("ok"):
        return True

    try:
        subprocess.run(
            [sys.executable, "-m", "cccc.daemon_main", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return False

    for _ in range(30):
        time.sleep(0.05)
        resp = call_daemon({"op": "ping"})
        if resp.get("ok"):
            return True
    return False


def _resolve_group_id(explicit: str) -> str:
    gid = (explicit or "").strip()
    if gid:
        return gid
    active = load_active()
    return str(active.get("active_group_id") or "").strip()


def cmd_attach(args: argparse.Namespace) -> int:
    if _ensure_daemon_running():
        resp = call_daemon(
            {"op": "attach", "args": {"path": args.path, "by": "cli", "group_id": str(args.group_id or "")}}
        )
        if resp.get("ok"):
            try:
                gid = str((resp.get("result") or {}).get("group_id") or "").strip()
                if gid:
                    set_active_group_id(gid)
            except Exception:
                pass
            _print_json(resp)
            return 0

    # Fallback: local execution (dev convenience)
    scope = detect_scope(Path(args.path))
    reg = load_registry()
    if args.group_id:
        group = load_group(str(args.group_id))
        if group is None:
            _print_json({"ok": False, "error": f"group not found: {args.group_id}"})
            return 2
        group = attach_scope_to_group(reg, group, scope, set_active=True)
    else:
        group = ensure_group_for_scope(reg, scope)
    append_event(
        group.ledger_path,
        kind="group.attach",
        group_id=group.group_id,
        scope_key=scope.scope_key,
        by="cli",
        data={"url": scope.url, "label": scope.label, "git_remote": scope.git_remote},
    )
    set_active_group_id(group.group_id)
    _print_json(
        {
            "ok": True,
            "result": {"group_id": group.group_id, "scope_key": scope.scope_key, "title": group.doc.get("title")},
        }
    )
    return 0


def cmd_group_create(args: argparse.Namespace) -> int:
    if _ensure_daemon_running():
        resp = call_daemon({"op": "group_create", "args": {"title": args.title, "by": "cli"}})
        if resp.get("ok"):
            try:
                gid = str((resp.get("result") or {}).get("group_id") or "").strip()
                if gid:
                    set_active_group_id(gid)
            except Exception:
                pass
            _print_json(resp)
            return 0

    reg = load_registry()
    group = create_group(reg, title=str(args.title or "working-group"))
    ev = append_event(
        group.ledger_path,
        kind="group.create",
        group_id=group.group_id,
        scope_key="",
        by="cli",
        data={"title": group.doc.get("title", "")},
    )
    set_active_group_id(group.group_id)
    _print_json({"ok": True, "result": {"group_id": group.group_id, "title": group.doc.get("title"), "event": ev}})
    return 0


def cmd_group_show(args: argparse.Namespace) -> int:
    group = load_group(args.group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {args.group_id}"}})
        return 2
    _print_json({"ok": True, "result": {"group": group.doc}})
    return 0


def cmd_group_use(args: argparse.Namespace) -> int:
    if _ensure_daemon_running():
        resp = call_daemon({"op": "group_use", "args": {"group_id": args.group_id, "path": args.path, "by": "cli"}})
        if resp.get("ok"):
            _print_json(resp)
            return 0

    group = load_group(args.group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {args.group_id}"}})
        return 2
    scope = detect_scope(Path(args.path))
    reg = load_registry()
    try:
        group = set_active_scope(reg, group, scope_key=scope.scope_key)
    except ValueError as e:
        _print_json(
            {
                "ok": False,
                "error": {"code": "scope_not_attached", "message": str(e), "details": {"hint": "cccc attach <path> --group <id>"}},
            }
        )
        return 2
    ev = append_event(
        group.ledger_path,
        kind="group.set_active_scope",
        group_id=group.group_id,
        scope_key=scope.scope_key,
        by="cli",
        data={"path": scope.url},
    )
    _print_json({"ok": True, "result": {"group_id": group.group_id, "active_scope_key": scope.scope_key, "event": ev}})
    return 0


def cmd_groups(args: argparse.Namespace) -> int:
    resp = call_daemon({"op": "groups"})
    if resp.get("ok"):
        _print_json(resp)
        return 0
    reg = load_registry()
    groups = list(reg.groups.values())
    groups.sort(key=lambda g: (g.get("updated_at") or "", g.get("created_at") or ""), reverse=True)
    _print_json({"ok": True, "result": {"groups": groups}})
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2

    to: list[str] = []
    to_raw = getattr(args, "to", None)
    if isinstance(to_raw, list):
        for item in to_raw:
            if not isinstance(item, str):
                continue
            parts = [p.strip() for p in item.split(",") if p.strip()]
            to.extend(parts)
    if _ensure_daemon_running():
        resp = call_daemon(
            {
                "op": "send",
                "args": {
                    "group_id": group_id,
                    "text": args.text,
                    "by": str(args.by or "user"),
                    "path": str(args.path or ""),
                    "to": to,
                },
            }
        )
        if resp.get("ok"):
            _print_json(resp)
            return 0

    # Fallback: local execution (dev convenience)
    scope_key = str(group.doc.get("active_scope_key") or "")
    if args.path:
        scope = detect_scope(Path(args.path))
        scope_key = scope.scope_key
        scopes = group.doc.get("scopes")
        attached = False
        if isinstance(scopes, list):
            attached = any(isinstance(item, dict) and item.get("scope_key") == scope_key for item in scopes)
        if not attached:
            _print_json(
                {
                    "ok": False,
                    "error": {
                        "code": "scope_not_attached",
                        "message": f"scope not attached: {scope_key}",
                        "details": {"hint": "cccc attach <path> --group <id>"},
                    },
                }
            )
            return 2
    if not scope_key:
        scope_key = ""
    event = append_event(
        group.ledger_path,
        kind="chat.message",
        group_id=group.group_id,
        scope_key=scope_key,
        by=str(args.by or "user"),
        data=ChatMessageData(text=args.text, format="plain", to=to).model_dump(),
    )
    _print_json({"ok": True, "result": {"event": event}})
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    if args.follow:
        for line in follow(group.ledger_path):
            print(line)
        return 0
    for line in read_last_lines(group.ledger_path, args.lines):
        print(line)
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    print(__version__)
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    group_id = str(args.group_id or "").strip()
    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    doc = set_active_group_id(group_id)
    _print_json({"ok": True, "result": doc})
    return 0


def cmd_active(_: argparse.Namespace) -> int:
    doc = load_active()
    _print_json({"ok": True, "result": doc})
    return 0


def cmd_actor_list(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2

    if _ensure_daemon_running():
        resp = call_daemon({"op": "actor_list", "args": {"group_id": group_id}})
        if resp.get("ok"):
            _print_json(resp)
            return 0

    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    _print_json({"ok": True, "result": {"actors": list_actors(group)}})
    return 0


def cmd_actor_add(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    actor_id = str(args.actor_id or "").strip()
    role = str(args.role or "peer").strip()
    title = str(args.title or "").strip()
    by = str(args.by or "user").strip()

    if _ensure_daemon_running():
        resp = call_daemon(
            {"op": "actor_add", "args": {"group_id": group_id, "actor_id": actor_id, "role": role, "title": title, "by": by}}
        )
        if resp.get("ok"):
            _print_json(resp)
            return 0

    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    try:
        require_actor_permission(group, by=by, action="actor.add")
        if role not in ("foreman", "peer"):
            raise ValueError("invalid role")
        actor = add_actor(group, actor_id=actor_id, role=role, title=title)
    except Exception as e:
        _print_json({"ok": False, "error": {"code": "actor_add_failed", "message": str(e)}})
        return 2
    ev = append_event(group.ledger_path, kind="actor.add", group_id=group.group_id, scope_key="", by=by, data={"actor": actor})
    _print_json({"ok": True, "result": {"actor": actor, "event": ev}})
    return 0


def cmd_actor_remove(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    actor_id = str(args.actor_id or "").strip()
    by = str(args.by or "user").strip()

    if _ensure_daemon_running():
        resp = call_daemon({"op": "actor_remove", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})
        if resp.get("ok"):
            _print_json(resp)
            return 0

    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    try:
        require_actor_permission(group, by=by, action="actor.remove", target_actor_id=actor_id)
        remove_actor(group, actor_id)
    except Exception as e:
        _print_json({"ok": False, "error": {"code": "actor_remove_failed", "message": str(e)}})
        return 2
    ev = append_event(group.ledger_path, kind="actor.remove", group_id=group.group_id, scope_key="", by=by, data={"actor_id": actor_id})
    _print_json({"ok": True, "result": {"actor_id": actor_id, "event": ev}})
    return 0


def cmd_actor_set_role(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    actor_id = str(args.actor_id or "").strip()
    role = str(args.role or "").strip()
    by = str(args.by or "user").strip()

    if _ensure_daemon_running():
        resp = call_daemon({"op": "actor_set_role", "args": {"group_id": group_id, "actor_id": actor_id, "role": role, "by": by}})
        if resp.get("ok"):
            _print_json(resp)
            return 0

    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    try:
        require_actor_permission(group, by=by, action="actor.update", target_actor_id=actor_id)
        if role not in ("foreman", "peer"):
            raise ValueError("invalid role")
        actor = set_actor_role(group, actor_id, role)
    except Exception as e:
        _print_json({"ok": False, "error": {"code": "actor_set_role_failed", "message": str(e)}})
        return 2
    ev = append_event(group.ledger_path, kind="actor.set_role", group_id=group.group_id, scope_key="", by=by, data={"actor_id": actor_id, "role": role})
    _print_json({"ok": True, "result": {"actor": actor, "event": ev}})
    return 0


def cmd_actor_start(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    actor_id = str(args.actor_id or "").strip()
    by = str(args.by or "user").strip()

    if _ensure_daemon_running():
        resp = call_daemon({"op": "actor_start", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})
        if resp.get("ok"):
            _print_json(resp)
            return 0

    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    try:
        require_actor_permission(group, by=by, action="actor.start", target_actor_id=actor_id)
        actor = update_actor(group, actor_id, {"enabled": True})
    except Exception as e:
        _print_json({"ok": False, "error": {"code": "actor_start_failed", "message": str(e)}})
        return 2
    ev = append_event(group.ledger_path, kind="actor.start", group_id=group.group_id, scope_key="", by=by, data={"actor_id": actor_id})
    _print_json({"ok": True, "result": {"actor": actor, "event": ev}})
    return 0


def cmd_actor_stop(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    actor_id = str(args.actor_id or "").strip()
    by = str(args.by or "user").strip()

    if _ensure_daemon_running():
        resp = call_daemon({"op": "actor_stop", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})
        if resp.get("ok"):
            _print_json(resp)
            return 0

    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    try:
        require_actor_permission(group, by=by, action="actor.stop", target_actor_id=actor_id)
        actor = update_actor(group, actor_id, {"enabled": False})
    except Exception as e:
        _print_json({"ok": False, "error": {"code": "actor_stop_failed", "message": str(e)}})
        return 2
    ev = append_event(group.ledger_path, kind="actor.stop", group_id=group.group_id, scope_key="", by=by, data={"actor_id": actor_id})
    _print_json({"ok": True, "result": {"actor": actor, "event": ev}})
    return 0


def cmd_actor_restart(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    actor_id = str(args.actor_id or "").strip()
    by = str(args.by or "user").strip()

    if _ensure_daemon_running():
        resp = call_daemon({"op": "actor_restart", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})
        if resp.get("ok"):
            _print_json(resp)
            return 0

    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    try:
        require_actor_permission(group, by=by, action="actor.restart", target_actor_id=actor_id)
        actor = update_actor(group, actor_id, {"enabled": True})
    except Exception as e:
        _print_json({"ok": False, "error": {"code": "actor_restart_failed", "message": str(e)}})
        return 2
    ev = append_event(group.ledger_path, kind="actor.restart", group_id=group.group_id, scope_key="", by=by, data={"actor_id": actor_id})
    _print_json({"ok": True, "result": {"actor": actor, "event": ev}})
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    actor_id = str(args.actor_id or "").strip()
    by = str(args.by or "user").strip()
    limit = int(args.limit) if isinstance(args.limit, int) else 50

    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    if not actor_id:
        _print_json({"ok": False, "error": {"code": "missing_actor_id", "message": "missing actor_id"}})
        return 2

    if _ensure_daemon_running():
        resp = call_daemon({"op": "inbox_list", "args": {"group_id": group_id, "actor_id": actor_id, "by": by, "limit": limit}})
        if resp.get("ok") and not args.mark_read:
            _print_json(resp)
            return 0
        if resp.get("ok") and args.mark_read:
            result = resp.get("result") if isinstance(resp.get("result"), dict) else {}
            messages = result.get("messages") if isinstance(result.get("messages"), list) else []
            if messages:
                last_id = str((messages[-1] or {}).get("id") or "").strip()
                if last_id:
                    mark = call_daemon({"op": "inbox_mark_read", "args": {"group_id": group_id, "actor_id": actor_id, "event_id": last_id, "by": by}})
                    if mark.get("ok"):
                        _print_json({"ok": True, "result": {"messages": messages, "marked": mark.get("result", {})}})
                        return 0
            _print_json(resp)
            return 0

    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    try:
        require_inbox_permission(group, by=by, target_actor_id=actor_id)
    except Exception as e:
        _print_json({"ok": False, "error": {"code": "permission_denied", "message": str(e)}})
        return 2

    messages = unread_messages(group, actor_id=actor_id, limit=limit)
    cur_event_id, cur_ts = get_cursor(group, actor_id)
    if args.mark_read and messages:
        last = messages[-1]
        last_id = str(last.get("id") or "").strip()
        last_ts = str(last.get("ts") or "")
        if last_id:
            cursor = set_cursor(group, actor_id, event_id=last_id, ts=last_ts)
            read_ev = append_event(
                group.ledger_path,
                kind="chat.read",
                group_id=group.group_id,
                scope_key="",
                by=by,
                data={"actor_id": actor_id, "event_id": last_id},
            )
            _print_json({"ok": True, "result": {"messages": messages, "cursor": cursor, "event": read_ev}})
            return 0

    _print_json({"ok": True, "result": {"messages": messages, "cursor": {"event_id": cur_event_id, "ts": cur_ts}}})
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    actor_id = str(args.actor_id or "").strip()
    by = str(args.by or "user").strip()
    event_id = str(args.event_id or "").strip()

    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    if not actor_id:
        _print_json({"ok": False, "error": {"code": "missing_actor_id", "message": "missing actor_id"}})
        return 2
    if not event_id:
        _print_json({"ok": False, "error": {"code": "missing_event_id", "message": "missing event_id"}})
        return 2

    if _ensure_daemon_running():
        resp = call_daemon({"op": "inbox_mark_read", "args": {"group_id": group_id, "actor_id": actor_id, "event_id": event_id, "by": by}})
        _print_json(resp)
        return 0 if resp.get("ok") else 2

    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2
    try:
        require_inbox_permission(group, by=by, target_actor_id=actor_id)
    except Exception as e:
        _print_json({"ok": False, "error": {"code": "permission_denied", "message": str(e)}})
        return 2
    ev = find_event(group, event_id)
    if ev is None:
        _print_json({"ok": False, "error": {"code": "event_not_found", "message": f"event not found: {event_id}"}})
        return 2
    ts = str(ev.get("ts") or "")
    cursor = set_cursor(group, actor_id, event_id=event_id, ts=ts)
    read_ev = append_event(
        group.ledger_path,
        kind="chat.read",
        group_id=group.group_id,
        scope_key="",
        by=by,
        data={"actor_id": actor_id, "event_id": event_id},
    )
    _print_json({"ok": True, "result": {"cursor": cursor, "event": read_ev}})
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    group_id = _resolve_group_id(getattr(args, "group", ""))
    actor_id = str(args.actor_id or "").strip()
    if not group_id:
        _print_json({"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id (no active group?)"}})
        return 2
    if not actor_id:
        _print_json({"ok": False, "error": {"code": "missing_actor_id", "message": "missing actor id"}})
        return 2

    group = load_group(group_id)
    if group is None:
        _print_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
        return 2

    actor = None
    for item in list_actors(group):
        if item.get("id") == actor_id:
            actor = item
            break
    if actor is None:
        _print_json({"ok": False, "error": {"code": "actor_not_found", "message": f"actor not found: {actor_id}"}})
        return 2

    title = str(group.doc.get("title") or group_id)
    role = str(actor.get("role") or "")
    scopes = group.doc.get("scopes") if isinstance(group.doc.get("scopes"), list) else []
    active_scope_key = str(group.doc.get("active_scope_key") or "")
    scope_lines: list[str] = []
    for sc in scopes:
        if not isinstance(sc, dict):
            continue
        sk = str(sc.get("scope_key") or "")
        url = str(sc.get("url") or "")
        label = str(sc.get("label") or sk)
        mark = " (active)" if sk and sk == active_scope_key else ""
        if url:
            scope_lines.append(f"- {label}: {url}{mark}")

    perms = ""
    if role == "foreman":
        perms = "You can manage other peers in this group (create/update/stop/restart)."
    elif role == "peer":
        perms = "You can only stop/restart yourself. Do not manage other actors."

    prompt = "\n".join(
        [
            "SYSTEM (CCCC vNext)",
            f"- Identity: {actor_id} ({role}) in working group '{title}' ({group_id})",
            "- Style: Be terse. Use bullets. No fluff. Treat messages like orders/status reports.",
            "- Source of truth: The group ledger is the shared chat+audit log. Keep it clean and actionable.",
            "- Messaging:",
            "  - Read: cccc inbox --actor-id <you> --by <you> [--mark-read]",
            "  - Send: cccc send \"...\" --by <you> --to <target> (targets: @all, @peers, @foreman, or actor ids)",
            "  - Mark read: cccc read <event_id> --actor-id <you> --by <you>",
            "- Permissions:",
            f"  - {perms}".rstrip(),
            "- Scopes:",
            *(scope_lines or ["- (none attached yet)"]),
        ]
    ).strip() + "\n"

    _print_json({"ok": True, "result": {"group_id": group_id, "actor_id": actor_id, "prompt": prompt}})
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    if args.action == "status":
        resp = call_daemon({"op": "ping"})
        if resp.get("ok"):
            r = resp.get("result") if isinstance(resp.get("result"), dict) else {}
            print(f"ccccd: running pid={r.get('pid')} version={r.get('version')}")
            return 0
        print("ccccd: not running")
        return 1

    if args.action == "start":
        if _ensure_daemon_running():
            print("ccccd: running")
            return 0
        print("ccccd: failed to start")
        return 1

    if args.action == "stop":
        resp = call_daemon({"op": "shutdown"})
        if resp.get("ok"):
            print("ccccd: shutdown requested")
            return 0
        print("ccccd: not running")
        return 0

    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cccc", description="CCCC vNext (working group + scopes)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_attach = sub.add_parser("attach", help="Attach current path to a working group (auto-create if needed)")
    p_attach.add_argument("path", nargs="?", default=".", help="Path inside a repo/scope (default: .)")
    p_attach.add_argument("--group", dest="group_id", default="", help="Attach scope to an existing group_id (optional)")
    p_attach.set_defaults(func=cmd_attach)

    p_group = sub.add_parser("group", help="Working group operations")
    group_sub = p_group.add_subparsers(dest="action", required=True)

    p_group_create = group_sub.add_parser("create", help="Create an empty working group")
    p_group_create.add_argument("--title", default="working-group", help="Group title (default: working-group)")
    p_group_create.set_defaults(func=cmd_group_create)

    p_group_show = group_sub.add_parser("show", help="Show group metadata")
    p_group_show.add_argument("group_id", help="Target group_id")
    p_group_show.set_defaults(func=cmd_group_show)

    p_group_use = group_sub.add_parser("use", help="Set group's active scope (must already be attached)")
    p_group_use.add_argument("group_id", help="Target group_id")
    p_group_use.add_argument("path", nargs="?", default=".", help="Path inside target scope (default: .)")
    p_group_use.set_defaults(func=cmd_group_use)

    p_groups = sub.add_parser("groups", help="List known working groups")
    p_groups.set_defaults(func=cmd_groups)

    p_use = sub.add_parser("use", help="Set the active working group (for send/tail defaults)")
    p_use.add_argument("group_id", help="Target group_id")
    p_use.set_defaults(func=cmd_use)

    p_active = sub.add_parser("active", help="Show the active working group")
    p_active.set_defaults(func=cmd_active)

    p_actor = sub.add_parser("actor", help="Manage long-session actors in a working group")
    actor_sub = p_actor.add_subparsers(dest="action", required=True)

    p_actor_list = actor_sub.add_parser("list", help="List actors (default: active group)")
    p_actor_list.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_actor_list.set_defaults(func=cmd_actor_list)

    p_actor_add = actor_sub.add_parser("add", help="Add an actor (max 1 foreman per group)")
    p_actor_add.add_argument("actor_id", help="Actor id (e.g. peer-a, peer-b, foreman)")
    p_actor_add.add_argument("--role", choices=["peer", "foreman"], default="peer", help="Role (default: peer)")
    p_actor_add.add_argument("--title", default="", help="Display title (optional)")
    p_actor_add.add_argument("--by", default="user", help="Requester (default: user)")
    p_actor_add.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_actor_add.set_defaults(func=cmd_actor_add)

    p_actor_rm = actor_sub.add_parser("remove", help="Remove an actor")
    p_actor_rm.add_argument("actor_id", help="Actor id")
    p_actor_rm.add_argument("--by", default="user", help="Requester (default: user)")
    p_actor_rm.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_actor_rm.set_defaults(func=cmd_actor_remove)

    p_actor_role = actor_sub.add_parser("set-role", help="Change an actor role (max 1 foreman per group)")
    p_actor_role.add_argument("actor_id", help="Actor id")
    p_actor_role.add_argument("role", choices=["peer", "foreman"], help="New role")
    p_actor_role.add_argument("--by", default="user", help="Requester (default: user)")
    p_actor_role.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_actor_role.set_defaults(func=cmd_actor_set_role)

    p_actor_start = actor_sub.add_parser("start", help="Set actor enabled=true (desired run-state)")
    p_actor_start.add_argument("actor_id", help="Actor id")
    p_actor_start.add_argument("--by", default="user", help="Requester (default: user)")
    p_actor_start.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_actor_start.set_defaults(func=cmd_actor_start)

    p_actor_stop = actor_sub.add_parser("stop", help="Set actor enabled=false (desired run-state)")
    p_actor_stop.add_argument("actor_id", help="Actor id")
    p_actor_stop.add_argument("--by", default="user", help="Requester (default: user)")
    p_actor_stop.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_actor_stop.set_defaults(func=cmd_actor_stop)

    p_actor_restart = actor_sub.add_parser("restart", help="Record restart intent and keep enabled=true")
    p_actor_restart.add_argument("actor_id", help="Actor id")
    p_actor_restart.add_argument("--by", default="user", help="Requester (default: user)")
    p_actor_restart.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_actor_restart.set_defaults(func=cmd_actor_restart)

    p_inbox = sub.add_parser("inbox", help="List unread chat messages for an actor (like a group-chat inbox)")
    p_inbox.add_argument("--actor-id", required=True, help="Target actor id")
    p_inbox.add_argument("--by", default="user", help="Requester (default: user)")
    p_inbox.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_inbox.add_argument("--limit", type=int, default=50, help="Max messages to return (default: 50)")
    p_inbox.add_argument("--mark-read", action="store_true", help="Mark returned messages as read up to the last one")
    p_inbox.set_defaults(func=cmd_inbox)

    p_read = sub.add_parser("read", help="Mark a message event as read for an actor")
    p_read.add_argument("event_id", help="Target message event id")
    p_read.add_argument("--actor-id", required=True, help="Target actor id")
    p_read.add_argument("--by", default="user", help="Requester (default: user)")
    p_read.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_read.set_defaults(func=cmd_read)

    p_prompt = sub.add_parser("prompt", help="Render a concise SYSTEM prompt for a group actor")
    p_prompt.add_argument("--actor-id", required=True, help="Target actor id")
    p_prompt.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_prompt.set_defaults(func=cmd_prompt)

    p_send = sub.add_parser("send", help="Append a chat message into the active group ledger (or --group)")
    p_send.add_argument("text", help="Message text")
    p_send.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_send.add_argument("--by", default="user", help="Sender label (default: user)")
    p_send.add_argument(
        "--to",
        action="append",
        default=[],
        help="Recipients/selectors (repeatable, supports comma-separated, e.g. --to peer-a --to @foreman,@peers)",
    )
    p_send.add_argument("--path", default="", help="Send message under this scope (path inside repo/scope)")
    p_send.set_defaults(func=cmd_send)

    p_tail = sub.add_parser("tail", help="Tail the active group's ledger (or --group)")
    p_tail.add_argument("--group", default="", help="Target group_id (default: active group)")
    p_tail.add_argument("-n", "--lines", type=int, default=50, help="Show last N lines (default: 50)")
    p_tail.add_argument("-f", "--follow", action="store_true", help="Follow (like tail -f)")
    p_tail.set_defaults(func=cmd_tail)

    p_daemon = sub.add_parser("daemon", help="Manage ccccd daemon")
    p_daemon.add_argument("action", choices=["start", "stop", "status"], help="Action")
    p_daemon.set_defaults(func=cmd_daemon)

    p_ver = sub.add_parser("version", help="Show version")
    p_ver.set_defaults(func=cmd_version)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
