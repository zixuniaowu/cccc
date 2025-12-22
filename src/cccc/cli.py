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
from .kernel.group import attach_scope_to_group, create_group, ensure_group_for_scope, load_group, set_active_scope
from .kernel.ledger import append_event, follow, read_last_lines
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


def cmd_attach(args: argparse.Namespace) -> int:
    if _ensure_daemon_running():
        resp = call_daemon(
            {"op": "attach", "args": {"path": args.path, "by": "cli", "group_id": str(args.group_id or "")}}
        )
        if resp.get("ok"):
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
    _print_json(
        {"ok": True, "group_id": group.group_id, "scope_key": scope.scope_key, "title": group.doc.get("title")}
    )
    return 0


def cmd_group_create(args: argparse.Namespace) -> int:
    if _ensure_daemon_running():
        resp = call_daemon({"op": "group_create", "args": {"title": args.title, "by": "cli"}})
        if resp.get("ok"):
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
    _print_json({"ok": True, "group_id": group.group_id, "title": group.doc.get("title"), "event": ev})
    return 0


def cmd_group_show(args: argparse.Namespace) -> int:
    group = load_group(args.group_id)
    if group is None:
        _print_json({"ok": False, "error": f"group not found: {args.group_id}"})
        return 2
    _print_json({"ok": True, "group": group.doc})
    return 0


def cmd_group_use(args: argparse.Namespace) -> int:
    if _ensure_daemon_running():
        resp = call_daemon({"op": "group_use", "args": {"group_id": args.group_id, "path": args.path, "by": "cli"}})
        if resp.get("ok"):
            _print_json(resp)
            return 0

    group = load_group(args.group_id)
    if group is None:
        _print_json({"ok": False, "error": f"group not found: {args.group_id}"})
        return 2
    scope = detect_scope(Path(args.path))
    reg = load_registry()
    try:
        group = set_active_scope(reg, group, scope_key=scope.scope_key)
    except ValueError as e:
        _print_json({"ok": False, "error": str(e), "hint": "run: cccc attach <path> --group <group_id>"})
        return 2
    ev = append_event(
        group.ledger_path,
        kind="group.set_active_scope",
        group_id=group.group_id,
        scope_key=scope.scope_key,
        by="cli",
        data={"path": scope.url},
    )
    _print_json({"ok": True, "group_id": group.group_id, "active_scope_key": scope.scope_key, "event": ev})
    return 0


def cmd_groups(args: argparse.Namespace) -> int:
    resp = call_daemon({"op": "groups"})
    if resp.get("ok"):
        _print_json(resp)
        return 0
    reg = load_registry()
    groups = list(reg.groups.values())
    groups.sort(key=lambda g: (g.get("updated_at") or "", g.get("created_at") or ""), reverse=True)
    _print_json({"ok": True, "groups": groups})
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    group = load_group(args.group_id)
    if group is None:
        _print_json({"ok": False, "error": f"group not found: {args.group_id}"})
        return 2
    if _ensure_daemon_running():
        resp = call_daemon(
            {
                "op": "send",
                "args": {
                    "group_id": args.group_id,
                    "text": args.text,
                    "by": str(args.by or "user"),
                    "path": str(args.path or ""),
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
            _print_json({"ok": False, "error": f"scope not attached: {scope_key}", "hint": "cccc attach <path> --group <id>"})
            return 2
    if not scope_key:
        _print_json({"ok": False, "error": "missing scope_key (no active scope?)"})
        return 2
    event = append_event(
        group.ledger_path,
        kind="chat.message",
        group_id=group.group_id,
        scope_key=scope_key,
        by=str(args.by or "user"),
        data=ChatMessageData(text=args.text, format="plain").model_dump(),
    )
    _print_json({"ok": True, "event": event})
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    group = load_group(args.group_id)
    if group is None:
        _print_json({"ok": False, "error": f"group not found: {args.group_id}"})
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


def cmd_daemon(args: argparse.Namespace) -> int:
    if args.action == "status":
        resp = call_daemon({"op": "ping"})
        if resp.get("ok"):
            print(f"ccccd: running pid={resp.get('pid')} version={resp.get('version')}")
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

    p_send = sub.add_parser("send", help="Append a chat message into a group ledger")
    p_send.add_argument("group_id", help="Target group_id")
    p_send.add_argument("text", help="Message text")
    p_send.add_argument("--by", default="user", help="Sender label (default: user)")
    p_send.add_argument("--path", default="", help="Send message under this scope (path inside repo/scope)")
    p_send.set_defaults(func=cmd_send)

    p_tail = sub.add_parser("tail", help="Tail a group's ledger")
    p_tail.add_argument("group_id", help="Target group_id")
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
