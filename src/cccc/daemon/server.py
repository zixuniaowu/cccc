from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .. import __version__
from ..contracts.v1 import ChatMessageData, DaemonError, DaemonRequest, DaemonResponse
from ..kernel.group import ensure_group_for_scope, load_group
from ..kernel.group import attach_scope_to_group, create_group, set_active_scope
from ..kernel.ledger import append_event
from ..kernel.registry import load_registry
from ..kernel.scope import detect_scope
from ..kernel.actors import add_actor, list_actors, remove_actor, set_actor_role, update_actor
from ..kernel.inbox import find_event, get_cursor, set_cursor, unread_messages
from ..kernel.permissions import require_actor_permission, require_inbox_permission
from ..paths import ensure_home
from ..util.fs import atomic_write_text, read_json
from ..util.time import utc_now_iso


@dataclass
class DaemonPaths:
    home: Path

    @property
    def daemon_dir(self) -> Path:
        return self.home / "daemon"

    @property
    def sock_path(self) -> Path:
        return self.daemon_dir / "ccccd.sock"

    @property
    def pid_path(self) -> Path:
        return self.daemon_dir / "ccccd.pid"

    @property
    def log_path(self) -> Path:
        return self.daemon_dir / "ccccd.log"


def default_paths() -> DaemonPaths:
    return DaemonPaths(home=ensure_home())


def _is_socket_alive(sock_path: Path) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            s.connect(str(sock_path))
            s.sendall(b'{"op":"ping"}\n')
            _ = s.recv(1024)
            return True
    except Exception:
        return False


def _write_pid(pid_path: Path) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(pid_path, str(os.getpid()) + "\n")


def _remove_stale_socket(sock_path: Path) -> None:
    try:
        if sock_path.exists() and not _is_socket_alive(sock_path):
            sock_path.unlink()
    except Exception:
        pass


def _recv_json_line(conn: socket.socket) -> Dict[str, Any]:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 2_000_000:
            break
    line = buf.split(b"\n", 1)[0]
    try:
        return json.loads(line.decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _send_json(conn: socket.socket, obj: Dict[str, Any]) -> None:
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    conn.sendall(data)


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def handle_request(req: DaemonRequest) -> Tuple[DaemonResponse, bool]:
    op = str(req.op or "").strip()
    args = req.args or {}

    if op == "ping":
        return DaemonResponse(ok=True, result={"version": __version__, "pid": os.getpid(), "ts": utc_now_iso()}), False

    if op == "shutdown":
        return DaemonResponse(ok=True, result={"message": "shutting down"}), True

    if op == "attach":
        path = Path(str(args.get("path") or "."))
        scope = detect_scope(path)
        reg = load_registry()
        requested_group_id = str(args.get("group_id") or "").strip()
        if requested_group_id:
            group = load_group(requested_group_id)
            if group is None:
                return {"ok": False, "error": f"group not found: {requested_group_id}"}, False
            group = attach_scope_to_group(reg, group, scope, set_active=True)
        else:
            group = ensure_group_for_scope(reg, scope)
        append_event(
            group.ledger_path,
            kind="group.attach",
            group_id=group.group_id,
            scope_key=scope.scope_key,
            by=str(args.get("by") or "cli"),
            data={"url": scope.url, "label": scope.label, "git_remote": scope.git_remote},
        )
        return (
            DaemonResponse(
                ok=True,
                result={"group_id": group.group_id, "scope_key": scope.scope_key, "title": group.doc.get("title")},
            ),
            False,
        )

    if op == "group_create":
        reg = load_registry()
        title = str(args.get("title") or "working-group")
        group = create_group(reg, title=title)
        ev = append_event(
            group.ledger_path,
            kind="group.create",
            group_id=group.group_id,
            scope_key="",
            by=str(args.get("by") or "cli"),
            data={"title": group.doc.get("title", "")},
        )
        return (
            DaemonResponse(ok=True, result={"group_id": group.group_id, "title": group.doc.get("title"), "event": ev}),
            False,
        )

    if op == "group_use":
        group_id = str(args.get("group_id") or "").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        path = Path(str(args.get("path") or "."))
        scope = detect_scope(path)
        reg = load_registry()
        try:
            group = set_active_scope(reg, group, scope_key=scope.scope_key)
        except ValueError as e:
            return (
                _error(
                    "scope_not_attached",
                    str(e),
                    details={"hint": "attach scope first (cccc attach <path> --group <id>)"},
                ),
                False,
            )
        ev = append_event(
            group.ledger_path,
            kind="group.set_active_scope",
            group_id=group.group_id,
            scope_key=scope.scope_key,
            by=str(args.get("by") or "cli"),
            data={"path": scope.url},
        )
        return (
            DaemonResponse(
                ok=True,
                result={"group_id": group.group_id, "active_scope_key": scope.scope_key, "event": ev},
            ),
            False,
        )

    if op == "groups":
        reg = load_registry()
        groups = list(reg.groups.values())
        groups.sort(key=lambda g: (g.get("updated_at") or "", g.get("created_at") or ""), reverse=True)
        return DaemonResponse(ok=True, result={"groups": groups}), False

    if op == "actor_list":
        group_id = str(args.get("group_id") or "").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        return DaemonResponse(ok=True, result={"actors": list_actors(group)}), False

    if op == "actor_add":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        role = str(args.get("role") or "").strip()
        title = str(args.get("title") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_actor_permission(group, by=by, action="actor.add")
            if role not in ("foreman", "peer"):
                raise ValueError("invalid role")
            actor = add_actor(group, actor_id=actor_id, role=role, title=title)
        except Exception as e:
            return _error("actor_add_failed", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="actor.add",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"actor": actor},
        )
        return DaemonResponse(ok=True, result={"actor": actor, "event": ev}), False

    if op == "actor_remove":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_actor_permission(group, by=by, action="actor.remove", target_actor_id=actor_id)
            remove_actor(group, actor_id)
        except Exception as e:
            return _error("actor_remove_failed", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="actor.remove",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"actor_id": actor_id},
        )
        return DaemonResponse(ok=True, result={"actor_id": actor_id, "event": ev}), False

    if op == "actor_set_role":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        role = str(args.get("role") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_actor_permission(group, by=by, action="actor.update", target_actor_id=actor_id)
            if role not in ("foreman", "peer"):
                raise ValueError("invalid role")
            actor = set_actor_role(group, actor_id, role)
        except Exception as e:
            return _error("actor_set_role_failed", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="actor.set_role",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"actor_id": actor_id, "role": role},
        )
        return DaemonResponse(ok=True, result={"actor": actor, "event": ev}), False

    if op == "actor_update":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        patch = args.get("patch") if isinstance(args.get("patch"), dict) else {}
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_actor_permission(group, by=by, action="actor.update", target_actor_id=actor_id)
            actor = update_actor(group, actor_id, patch)
        except Exception as e:
            return _error("actor_update_failed", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="actor.update",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"actor_id": actor_id, "patch": patch},
        )
        return DaemonResponse(ok=True, result={"actor": actor, "event": ev}), False

    if op == "actor_start":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_actor_permission(group, by=by, action="actor.start", target_actor_id=actor_id)
            actor = update_actor(group, actor_id, {"enabled": True})
        except Exception as e:
            return _error("actor_start_failed", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="actor.start",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"actor_id": actor_id},
        )
        return DaemonResponse(ok=True, result={"actor": actor, "event": ev}), False

    if op == "actor_stop":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_actor_permission(group, by=by, action="actor.stop", target_actor_id=actor_id)
            actor = update_actor(group, actor_id, {"enabled": False})
        except Exception as e:
            return _error("actor_stop_failed", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="actor.stop",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"actor_id": actor_id},
        )
        return DaemonResponse(ok=True, result={"actor": actor, "event": ev}), False

    if op == "actor_restart":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_actor_permission(group, by=by, action="actor.restart", target_actor_id=actor_id)
            actor = update_actor(group, actor_id, {"enabled": True})
        except Exception as e:
            return _error("actor_restart_failed", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="actor.restart",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"actor_id": actor_id},
        )
        return DaemonResponse(ok=True, result={"actor": actor, "event": ev}), False

    if op == "inbox_list":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        limit_raw = args.get("limit")
        limit = int(limit_raw) if isinstance(limit_raw, int) else 50
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        if not actor_id:
            return _error("missing_actor_id", "missing actor_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_inbox_permission(group, by=by, target_actor_id=actor_id)
        except Exception as e:
            return _error("permission_denied", str(e)), False
        msgs = unread_messages(group, actor_id=actor_id, limit=limit)
        cur_event_id, cur_ts = get_cursor(group, actor_id)
        return DaemonResponse(ok=True, result={"messages": msgs, "cursor": {"event_id": cur_event_id, "ts": cur_ts}}), False

    if op == "inbox_mark_read":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        event_id = str(args.get("event_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        if not actor_id:
            return _error("missing_actor_id", "missing actor_id"), False
        if not event_id:
            return _error("missing_event_id", "missing event_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_inbox_permission(group, by=by, target_actor_id=actor_id)
        except Exception as e:
            return _error("permission_denied", str(e)), False
        ev = find_event(group, event_id)
        if ev is None:
            return _error("event_not_found", f"event not found: {event_id}"), False
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
        return DaemonResponse(ok=True, result={"cursor": cursor, "event": read_ev}), False

    if op == "send":
        group_id = str(args.get("group_id") or "").strip()
        text = str(args.get("text") or "")
        by = str(args.get("by") or "user")
        to_raw = args.get("to")
        to: list[str] = []
        if isinstance(to_raw, list):
            to = [str(x).strip() for x in to_raw if isinstance(x, str) and str(x).strip()]
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        path = str(args.get("path") or "").strip()
        if path:
            scope = detect_scope(Path(path))
            scope_key = scope.scope_key
            scopes = group.doc.get("scopes")
            attached = False
            if isinstance(scopes, list):
                attached = any(isinstance(item, dict) and item.get("scope_key") == scope_key for item in scopes)
            if not attached:
                return (
                    _error(
                        "scope_not_attached",
                        f"scope not attached: {scope_key}",
                        details={"hint": "cccc attach <path> --group <id>"},
                    ),
                    False,
                )
        else:
            scope_key = str(group.doc.get("active_scope_key") or "").strip()
        if not scope_key:
            scope_key = ""
        ev = append_event(
            group.ledger_path,
            kind="chat.message",
            group_id=group.group_id,
            scope_key=scope_key,
            by=by,
            data=ChatMessageData(text=text, format="plain", to=to).model_dump(),
        )
        return DaemonResponse(ok=True, result={"event": ev}), False

    return _error("unknown_op", f"unknown op: {op}"), False


def serve_forever(paths: Optional[DaemonPaths] = None) -> int:
    p = paths or default_paths()
    p.daemon_dir.mkdir(parents=True, exist_ok=True)

    _remove_stale_socket(p.sock_path)
    if p.sock_path.exists() and _is_socket_alive(p.sock_path):
        return 0

    try:
        if p.sock_path.exists():
            p.sock_path.unlink()
    except Exception:
        pass

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.bind(str(p.sock_path))
        s.listen(50)
        _write_pid(p.pid_path)

        should_exit = False
        while not should_exit:
            try:
                conn, _ = s.accept()
            except KeyboardInterrupt:
                break
            except Exception:
                continue
            with conn:
                raw = _recv_json_line(conn)
                try:
                    req = DaemonRequest.model_validate(raw)
                except Exception as e:
                    resp = _error("invalid_request", "invalid request", details={"error": str(e)})
                    _send_json(conn, resp.model_dump())
                    continue
                resp, should_exit = handle_request(req)
                _send_json(conn, resp.model_dump())

    try:
        if p.sock_path.exists():
            p.sock_path.unlink()
    except Exception:
        pass
    return 0


def call_daemon(req: Dict[str, Any], *, paths: Optional[DaemonPaths] = None, timeout_s: float = 1.0) -> Dict[str, Any]:
    p = paths or default_paths()
    try:
        request = DaemonRequest.model_validate(req)
    except Exception as e:
        return DaemonResponse(
            ok=False,
            error=DaemonError(code="invalid_request", message="invalid request", details={"error": str(e)}),
        ).model_dump()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_s)
            s.connect(str(p.sock_path))
            s.sendall((json.dumps(request.model_dump(), ensure_ascii=False) + "\n").encode("utf-8"))
            data = s.recv(4_000_000)
        line = (data or b"").split(b"\n", 1)[0]
        obj = json.loads(line.decode("utf-8", errors="replace"))
        resp = DaemonResponse.model_validate(obj)
        return resp.model_dump()
    except Exception:
        return DaemonResponse(ok=False, error=DaemonError(code="daemon_unavailable", message="daemon unavailable")).model_dump()


def read_pid(paths: Optional[DaemonPaths] = None) -> int:
    p = paths or default_paths()
    try:
        txt = p.pid_path.read_text(encoding="utf-8").strip()
        return int(txt) if txt.isdigit() else 0
    except Exception:
        return 0
