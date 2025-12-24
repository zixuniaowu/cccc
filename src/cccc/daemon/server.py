from __future__ import annotations

import json
import os
import socket
import sys
import time
import signal
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .. import __version__
from ..contracts.v1 import ChatMessageData, DaemonError, DaemonRequest, DaemonResponse
from ..kernel.active import load_active, set_active_group_id
from ..kernel.group import ensure_group_for_scope, load_group
from ..kernel.group import attach_scope_to_group, create_group, delete_group, detach_scope_from_group, set_active_scope, update_group
from ..kernel.ledger import append_event
from ..kernel.registry import load_registry
from ..kernel.scope import detect_scope
from ..kernel.actors import add_actor, list_actors, remove_actor, resolve_recipient_tokens, set_actor_role, update_actor
from ..kernel.inbox import find_event, get_cursor, is_message_for_actor, set_cursor, unread_messages
from ..kernel.ledger_retention import compact as compact_ledger
from ..kernel.ledger_retention import snapshot as snapshot_ledger
from ..kernel.permissions import require_actor_permission, require_group_permission, require_inbox_permission
from ..paths import ensure_home
from ..runners import pty as pty_runner
from ..util.fs import atomic_write_json, atomic_write_text, read_json
from ..util.time import utc_now_iso
from .automation import AutomationManager
from .delivery import inject_system_prompt as deliver_system_prompt
from .delivery import pty_submit_text, render_delivery_text


AUTOMATION = AutomationManager()


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


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _best_effort_killpg(pid: int, sig: signal.Signals) -> None:
    if pid <= 0:
        return
    try:
        os.killpg(pid, sig)
    except Exception:
        try:
            os.kill(pid, sig)
        except Exception:
            pass


def _pty_state_path(group_id: str, actor_id: str) -> Path:
    home = ensure_home()
    return home / "groups" / str(group_id) / "state" / "runners" / "pty" / f"{actor_id}.json"


def _write_pty_state(group_id: str, actor_id: str, *, pid: int) -> None:
    p = _pty_state_path(group_id, actor_id)
    atomic_write_json(
        p,
        {
            "v": 1,
            "kind": "pty",
            "group_id": str(group_id),
            "actor_id": str(actor_id),
            "pid": int(pid),
            "started_at": utc_now_iso(),
        },
    )


def _remove_pty_state_if_pid(group_id: str, actor_id: str, *, pid: int) -> None:
    p = _pty_state_path(group_id, actor_id)
    if not p.exists():
        return
    doc = read_json(p)
    try:
        cur = int(doc.get("pid") or 0) if isinstance(doc, dict) else 0
    except Exception:
        cur = 0
    if cur and int(pid) and cur != int(pid):
        return
    try:
        p.unlink()
    except Exception:
        pass


def _cleanup_stale_pty_state(home: Path) -> None:
    base = home / "groups"
    if not base.exists():
        return
    for p in base.glob("*/state/runners/pty/*.json"):
        doc = read_json(p)
        if not isinstance(doc, dict) or str(doc.get("kind") or "") != "pty":
            try:
                p.unlink()
            except Exception:
                pass
            continue
        try:
            pid = int(doc.get("pid") or 0)
        except Exception:
            pid = 0
        if pid <= 0 or not _pid_alive(pid):
            try:
                p.unlink()
            except Exception:
                pass
            continue
        _best_effort_killpg(pid, signal.SIGTERM)
        deadline = time.time() + 1.0
        while time.time() < deadline and _pid_alive(pid):
            time.sleep(0.05)
        if _pid_alive(pid):
            _best_effort_killpg(pid, signal.SIGKILL)
        try:
            p.unlink()
        except Exception:
            pass


def _maybe_autostart_running_groups() -> None:
    home = ensure_home()
    base = home / "groups"
    if not base.exists():
        return
    for p in base.glob("*/group.yaml"):
        gid = p.parent.name
        group = load_group(gid)
        if group is None:
            continue
        if not bool(group.doc.get("running", False)):
            continue
        group_scope_key = str(group.doc.get("active_scope_key") or "").strip()
        if not group_scope_key:
            group.doc["running"] = False
            try:
                group.save()
            except Exception:
                pass
            continue
        for actor in list_actors(group):
            if not isinstance(actor, dict):
                continue
            aid = str(actor.get("id") or "").strip()
            if not aid:
                continue
            if not bool(actor.get("enabled", True)):
                continue
            scope_key = str(actor.get("default_scope_key") or group_scope_key).strip()
            url = _find_scope_url(group, scope_key)
            if not url:
                continue
            cwd = Path(url).expanduser().resolve()
            if not cwd.exists():
                continue
            cmd = actor.get("command") if isinstance(actor.get("command"), list) else []
            env = actor.get("env") if isinstance(actor.get("env"), dict) else {}
            session = pty_runner.SUPERVISOR.start_actor(
                group_id=group.group_id, actor_id=aid, cwd=cwd, command=list(cmd or []), env=dict(env or {})
            )
            try:
                _write_pty_state(group.group_id, aid, pid=session.pid)
            except Exception:
                pass
            try:
                _inject_system_prompt(group, actor)
            except Exception:
                pass


def _maybe_compact_ledgers(home: Path) -> None:
    base = home / "groups"
    if not base.exists():
        return
    for p in base.glob("*/group.yaml"):
        gid = p.parent.name
        group = load_group(gid)
        if group is None:
            continue
        if not bool(group.doc.get("running", False)):
            continue
        try:
            _ = compact_ledger(group, reason="auto", force=False)
        except Exception:
            continue


def _inject_system_prompt(group: Any, actor: Dict[str, Any]) -> None:
    try:
        deliver_system_prompt(group, actor=actor)
    except Exception:
        pass


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


def _find_scope_url(group: Any, scope_key: str) -> str:
    wanted = str(scope_key or "").strip()
    if not wanted:
        return ""
    scopes = group.doc.get("scopes")
    if not isinstance(scopes, list):
        return ""
    for sc in scopes:
        if not isinstance(sc, dict):
            continue
        if str(sc.get("scope_key") or "").strip() != wanted:
            continue
        return str(sc.get("url") or "").strip()
    return ""


def handle_request(req: DaemonRequest) -> Tuple[DaemonResponse, bool]:
    op = str(req.op or "").strip()
    args = req.args or {}

    if op == "ping":
        return DaemonResponse(ok=True, result={"version": __version__, "pid": os.getpid(), "ts": utc_now_iso()}), False

    if op == "shutdown":
        try:
            pty_runner.SUPERVISOR.stop_all()
        except Exception:
            pass
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
        topic = str(args.get("topic") or "")
        group = create_group(reg, title=title, topic=topic)
        ev = append_event(
            group.ledger_path,
            kind="group.create",
            group_id=group.group_id,
            scope_key="",
            by=str(args.get("by") or "cli"),
            data={"title": group.doc.get("title", ""), "topic": group.doc.get("topic", "")},
        )
        return (
            DaemonResponse(ok=True, result={"group_id": group.group_id, "title": group.doc.get("title"), "event": ev}),
            False,
        )

    if op == "group_show":
        group_id = str(args.get("group_id") or "").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        return DaemonResponse(ok=True, result={"group": group.doc}), False

    if op == "group_update":
        group_id = str(args.get("group_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        patch = args.get("patch") if isinstance(args.get("patch"), dict) else {}
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        allowed = {"title", "topic"}
        unknown = set(patch.keys()) - allowed
        if unknown:
            return _error("invalid_patch", "invalid patch keys", details={"unknown_keys": sorted(unknown)}), False
        if not patch:
            return _error("invalid_patch", "empty patch"), False
        try:
            require_group_permission(group, by=by, action="group.update")
            reg = load_registry()
            group = update_group(reg, group, patch=dict(patch))
        except Exception as e:
            return _error("group_update_failed", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="group.update",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"patch": dict(patch)},
        )
        return DaemonResponse(ok=True, result={"group_id": group.group_id, "group": group.doc, "event": ev}), False

    if op == "group_detach_scope":
        group_id = str(args.get("group_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        scope_key = str(args.get("scope_key") or "").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        if not scope_key:
            return _error("missing_scope_key", "missing scope_key"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_group_permission(group, by=by, action="group.detach_scope")
            reg = load_registry()
            group = detach_scope_from_group(reg, group, scope_key=scope_key)
        except Exception as e:
            return _error("group_detach_scope_failed", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="group.detach_scope",
            group_id=group.group_id,
            scope_key=scope_key,
            by=by,
            data={"scope_key": scope_key},
        )
        return DaemonResponse(ok=True, result={"group_id": group.group_id, "event": ev}), False

    if op == "group_delete":
        group_id = str(args.get("group_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_group_permission(group, by=by, action="group.delete")
            pty_runner.SUPERVISOR.stop_group(group_id=group_id)
            reg = load_registry()
            delete_group(reg, group_id=group_id)
            active = load_active()
            if str(active.get("active_group_id") or "") == group_id:
                set_active_group_id("")
        except Exception as e:
            return _error("group_delete_failed", str(e)), False
        return DaemonResponse(ok=True, result={"group_id": group_id}), False

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
        out = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            gid = str(g.get("group_id") or "").strip()
            running = pty_runner.SUPERVISOR.group_running(gid) if gid else False
            item = dict(g)
            item["running"] = bool(running)
            out.append(item)
        return DaemonResponse(ok=True, result={"groups": out}), False

    if op == "group_start":
        group_id = str(args.get("group_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        group_scope_key = str(group.doc.get("active_scope_key") or "").strip()
        if not group_scope_key:
            return (
                _error(
                    "missing_project_root",
                    "missing project root for group (no active scope)",
                    details={"hint": "Attach a project root first (e.g. cccc attach <path> --group <id>)"},
                ),
                False,
            )
        try:
            require_group_permission(group, by=by, action="group.start")
            actors = list_actors(group)
            start_specs: list[tuple[str, Path, list[str], dict[str, str], Dict[str, Any]]] = []
            for actor in actors:
                if not isinstance(actor, dict):
                    continue
                aid = str(actor.get("id") or "").strip()
                if not aid:
                    continue
                if not bool(actor.get("enabled", True)):
                    continue

                scope_key = str(actor.get("default_scope_key") or group_scope_key).strip()
                url = _find_scope_url(group, scope_key)
                if not url:
                    return (
                        _error(
                            "scope_not_attached",
                            f"scope not attached: {scope_key}",
                            details={
                                "group_id": group.group_id,
                                "actor_id": aid,
                                "scope_key": scope_key,
                                "hint": "Attach this scope to the group (cccc attach <path> --group <id>)",
                            },
                        ),
                        False,
                    )
                cwd = Path(url).expanduser().resolve()
                if not cwd.exists():
                    return (
                        _error(
                            "invalid_project_root",
                            "project root path does not exist",
                            details={
                                "group_id": group.group_id,
                                "actor_id": aid,
                                "scope_key": scope_key,
                                "path": str(cwd),
                                "hint": "Re-attach a valid project root (cccc attach <path> --group <id>)",
                            },
                        ),
                        False,
                    )
                cmd = actor.get("command") if isinstance(actor.get("command"), list) else []
                env = actor.get("env") if isinstance(actor.get("env"), dict) else {}
                start_specs.append((aid, cwd, list(cmd or []), dict(env or {}), dict(actor)))

            started: list[str] = []
            for aid, cwd, cmd, env, actor in start_specs:
                session = pty_runner.SUPERVISOR.start_actor(group_id=group.group_id, actor_id=aid, cwd=cwd, command=cmd, env=env)
                try:
                    _write_pty_state(group.group_id, aid, pid=session.pid)
                except Exception:
                    pass
                try:
                    _inject_system_prompt(group, actor)
                except Exception:
                    pass
                started.append(aid)
            group.doc["running"] = True
            group.save()
        except Exception as e:
            return _error("group_start_failed", str(e)), False
        ev = append_event(group.ledger_path, kind="group.start", group_id=group.group_id, scope_key="", by=by, data={"started": started})
        return DaemonResponse(ok=True, result={"group_id": group.group_id, "started": started, "event": ev}), False

    if op == "group_stop":
        group_id = str(args.get("group_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_group_permission(group, by=by, action="group.stop")
            pty_runner.SUPERVISOR.stop_group(group_id=group.group_id)
            group.doc["running"] = False
            group.save()
            try:
                pdir = _pty_state_path(group.group_id, "_").parent
                for fp in pdir.glob("*.json"):
                    try:
                        fp.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception as e:
            return _error("group_stop_failed", str(e)), False
        ev = append_event(group.ledger_path, kind="group.stop", group_id=group.group_id, scope_key="", by=by, data={})
        return DaemonResponse(ok=True, result={"group_id": group.group_id, "event": ev}), False

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
        submit = str(args.get("submit") or "").strip()
        by = str(args.get("by") or "user").strip()
        command_raw = args.get("command")
        env_raw = args.get("env")
        default_scope_key = str(args.get("default_scope_key") or "").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_actor_permission(group, by=by, action="actor.add")
            if role not in ("foreman", "peer"):
                raise ValueError("invalid role")
            command: list[str] = []
            if isinstance(command_raw, list) and all(isinstance(x, str) for x in command_raw):
                command = [str(x) for x in command_raw if str(x).strip()]
            env: Dict[str, str] = {}
            if isinstance(env_raw, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in env_raw.items()):
                env = {str(k): str(v) for k, v in env_raw.items()}
            actor = add_actor(
                group,
                actor_id=actor_id,
                role=role,
                title=title,
                command=command,
                env=env,
                default_scope_key=default_scope_key,
                submit=submit or "enter",
            )
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
            pty_runner.SUPERVISOR.stop_actor(group_id=group.group_id, actor_id=actor_id)
            _remove_pty_state_if_pid(group.group_id, actor_id, pid=0)
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
        allowed = {"role", "title", "command", "env", "default_scope_key", "submit", "enabled"}
        unknown = set(patch.keys()) - allowed
        if unknown:
            return _error("invalid_patch", "invalid patch keys", details={"unknown_keys": sorted(unknown)}), False
        if not patch:
            return _error("invalid_patch", "empty patch"), False
        enabled_patched = "enabled" in patch
        try:
            require_actor_permission(group, by=by, action="actor.update", target_actor_id=actor_id)
            actor = update_actor(group, actor_id, patch)
        except Exception as e:
            return _error("actor_update_failed", str(e)), False
        if enabled_patched:
            if bool(actor.get("enabled", False)):
                if bool(group.doc.get("running", False)):
                    group_scope_key = str(group.doc.get("active_scope_key") or "").strip()
                    if not group_scope_key:
                        return (
                            _error(
                                "missing_project_root",
                                "missing project root for group (no active scope)",
                                details={"hint": "Attach a project root first (e.g. cccc attach <path> --group <id>)"},
                            ),
                            False,
                        )
                    scope_key = str(actor.get("default_scope_key") or group_scope_key).strip()
                    url = _find_scope_url(group, scope_key)
                    if not url:
                        return (
                            _error(
                                "scope_not_attached",
                                f"scope not attached: {scope_key}",
                                details={
                                    "group_id": group.group_id,
                                    "actor_id": actor_id,
                                    "scope_key": scope_key,
                                    "hint": "Attach this scope to the group (cccc attach <path> --group <id>)",
                                },
                            ),
                            False,
                        )
                    cwd = Path(url).expanduser().resolve()
                    if not cwd.exists():
                        return (
                            _error(
                                "invalid_project_root",
                                "project root path does not exist",
                                details={
                                    "group_id": group.group_id,
                                    "actor_id": actor_id,
                                    "scope_key": scope_key,
                                    "path": str(cwd),
                                    "hint": "Re-attach a valid project root (cccc attach <path> --group <id>)",
                                },
                            ),
                            False,
                        )
                    cmd = actor.get("command") if isinstance(actor.get("command"), list) else []
                    env = actor.get("env") if isinstance(actor.get("env"), dict) else {}
                    session = pty_runner.SUPERVISOR.start_actor(
                        group_id=group.group_id, actor_id=actor_id, cwd=cwd, command=list(cmd or []), env=dict(env or {})
                    )
                    try:
                        _write_pty_state(group.group_id, actor_id, pid=session.pid)
                    except Exception:
                        pass
                    try:
                        _inject_system_prompt(group, actor)
                    except Exception:
                        pass
            else:
                pty_runner.SUPERVISOR.stop_actor(group_id=group.group_id, actor_id=actor_id)
                _remove_pty_state_if_pid(group.group_id, actor_id, pid=0)
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
        if bool(group.doc.get("running", False)):
            group_scope_key = str(group.doc.get("active_scope_key") or "").strip()
            if not group_scope_key:
                return (
                    _error(
                        "missing_project_root",
                        "missing project root for group (no active scope)",
                        details={"hint": "Attach a project root first (e.g. cccc attach <path> --group <id>)"},
                    ),
                    False,
                )
            scope_key = str(actor.get("default_scope_key") or group_scope_key).strip()
            url = _find_scope_url(group, scope_key)
            if not url:
                return (
                    _error(
                        "scope_not_attached",
                        f"scope not attached: {scope_key}",
                        details={
                            "group_id": group.group_id,
                            "actor_id": actor_id,
                            "scope_key": scope_key,
                            "hint": "Attach this scope to the group (cccc attach <path> --group <id>)",
                        },
                    ),
                    False,
                )
            cwd = Path(url).expanduser().resolve()
            if not cwd.exists():
                return (
                    _error(
                        "invalid_project_root",
                        "project root path does not exist",
                        details={
                            "group_id": group.group_id,
                            "actor_id": actor_id,
                            "scope_key": scope_key,
                            "path": str(cwd),
                            "hint": "Re-attach a valid project root (cccc attach <path> --group <id>)",
                        },
                    ),
                    False,
                )
            cmd = actor.get("command") if isinstance(actor.get("command"), list) else []
            env = actor.get("env") if isinstance(actor.get("env"), dict) else {}
            session = pty_runner.SUPERVISOR.start_actor(
                group_id=group.group_id, actor_id=actor_id, cwd=cwd, command=list(cmd or []), env=dict(env or {})
            )
            try:
                _write_pty_state(group.group_id, actor_id, pid=session.pid)
            except Exception:
                pass
            try:
                _inject_system_prompt(group, actor)
            except Exception:
                pass
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
            pty_runner.SUPERVISOR.stop_actor(group_id=group.group_id, actor_id=actor_id)
            _remove_pty_state_if_pid(group.group_id, actor_id, pid=0)
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
            pty_runner.SUPERVISOR.stop_actor(group_id=group.group_id, actor_id=actor_id)
            _remove_pty_state_if_pid(group.group_id, actor_id, pid=0)
        except Exception as e:
            return _error("actor_restart_failed", str(e)), False
        if bool(group.doc.get("running", False)):
            group_scope_key = str(group.doc.get("active_scope_key") or "").strip()
            if not group_scope_key:
                return (
                    _error(
                        "missing_project_root",
                        "missing project root for group (no active scope)",
                        details={"hint": "Attach a project root first (e.g. cccc attach <path> --group <id>)"},
                    ),
                    False,
                )
            scope_key = str(actor.get("default_scope_key") or group_scope_key).strip()
            url = _find_scope_url(group, scope_key)
            if not url:
                return (
                    _error(
                        "scope_not_attached",
                        f"scope not attached: {scope_key}",
                        details={
                            "group_id": group.group_id,
                            "actor_id": actor_id,
                            "scope_key": scope_key,
                            "hint": "Attach this scope to the group (cccc attach <path> --group <id>)",
                        },
                    ),
                    False,
                )
            cwd = Path(url).expanduser().resolve()
            if not cwd.exists():
                return (
                    _error(
                        "invalid_project_root",
                        "project root path does not exist",
                        details={
                            "group_id": group.group_id,
                            "actor_id": actor_id,
                            "scope_key": scope_key,
                            "path": str(cwd),
                            "hint": "Re-attach a valid project root (cccc attach <path> --group <id>)",
                        },
                    ),
                    False,
                )
            cmd = actor.get("command") if isinstance(actor.get("command"), list) else []
            env = actor.get("env") if isinstance(actor.get("env"), dict) else {}
            session = pty_runner.SUPERVISOR.start_actor(
                group_id=group.group_id, actor_id=actor_id, cwd=cwd, command=list(cmd or []), env=dict(env or {})
            )
            try:
                _write_pty_state(group.group_id, actor_id, pid=session.pid)
            except Exception:
                pass
            try:
                _inject_system_prompt(group, actor)
            except Exception:
                pass
        ev = append_event(
            group.ledger_path,
            kind="actor.restart",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"actor_id": actor_id},
        )
        return DaemonResponse(ok=True, result={"actor": actor, "event": ev}), False

    if op == "term_resize":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        cols_raw = args.get("cols")
        rows_raw = args.get("rows")
        try:
            cols = int(cols_raw) if isinstance(cols_raw, int) else int(str(cols_raw or "0"))
        except Exception:
            cols = 0
        try:
            rows = int(rows_raw) if isinstance(rows_raw, int) else int(str(rows_raw or "0"))
        except Exception:
            rows = 0
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        if not actor_id:
            return _error("missing_actor_id", "missing actor_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        pty_runner.SUPERVISOR.resize(group_id=group_id, actor_id=actor_id, cols=cols, rows=rows)
        return DaemonResponse(ok=True, result={"group_id": group_id, "actor_id": actor_id, "cols": cols, "rows": rows}), False

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

    if op == "ledger_snapshot":
        group_id = str(args.get("group_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        reason = str(args.get("reason") or "manual").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_group_permission(group, by=by, action="group.update")
            snap = snapshot_ledger(group, reason=reason)
        except Exception as e:
            return _error("ledger_snapshot_failed", str(e)), False
        return DaemonResponse(ok=True, result={"snapshot": snap}), False

    if op == "ledger_compact":
        group_id = str(args.get("group_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        reason = str(args.get("reason") or "auto").strip()
        force = bool(args.get("force", False))
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_group_permission(group, by=by, action="group.update")
            res = compact_ledger(group, reason=reason, force=force)
        except Exception as e:
            return _error("ledger_compact_failed", str(e)), False
        return DaemonResponse(ok=True, result=res), False

    if op == "send":
        group_id = str(args.get("group_id") or "").strip()
        text = str(args.get("text") or "")
        by = str(args.get("by") or "user")
        to_raw = args.get("to")
        to_tokens: list[str] = []
        if isinstance(to_raw, list):
            to_tokens = [str(x).strip() for x in to_raw if isinstance(x, str) and str(x).strip()]
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False

        try:
            to = resolve_recipient_tokens(group, to_tokens)
        except Exception as e:
            return _error("invalid_recipient", str(e)), False

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
        # Keep group ordering IM-like by bumping the group's last activity timestamp.
        try:
            reg = load_registry()
            meta = reg.groups.get(group.group_id)
            if isinstance(meta, dict):
                meta["updated_at"] = str(ev.get("ts") or utc_now_iso())
                reg.save()
        except Exception:
            pass

        # Best-effort delivery into running actor PTYs (old cccc behavior).
        # We only auto-deliver when recipients are explicit (to != []).
        if to:
            delivery_text = render_delivery_text(by=by, to=to, text=text)
            for actor in list_actors(group):
                if not isinstance(actor, dict):
                    continue
                aid = str(actor.get("id") or "").strip()
                if not aid or aid == "user" or aid == by:
                    continue
                if not is_message_for_actor(group, actor_id=aid, event=ev):
                    continue
                delivered = pty_submit_text(group, actor_id=aid, text=delivery_text, file_fallback=True)
                if delivered:
                    try:
                        AUTOMATION.on_delivered_message(group, actor=actor, by=by)
                    except Exception:
                        pass
        return DaemonResponse(ok=True, result={"event": ev}), False

    return _error("unknown_op", f"unknown op: {op}"), False
def serve_forever(paths: Optional[DaemonPaths] = None) -> int:
    p = paths or default_paths()
    p.daemon_dir.mkdir(parents=True, exist_ok=True)

    _remove_stale_socket(p.sock_path)
    if p.sock_path.exists() and _is_socket_alive(p.sock_path):
        return 0

    # Best-effort cleanup of orphaned PTY actor processes from a previous daemon crash.
    try:
        _cleanup_stale_pty_state(p.home)
    except Exception:
        pass

    def _on_session_exit(session: pty_runner.PtySession) -> None:
        _remove_pty_state_if_pid(session.group_id, session.actor_id, pid=session.pid)

    try:
        pty_runner.SUPERVISOR.set_exit_hook(_on_session_exit)
    except Exception:
        pass

    # Restore groups that were previously started (desired run-state).
    try:
        _maybe_autostart_running_groups()
    except Exception:
        pass

    try:
        if p.sock_path.exists():
            p.sock_path.unlink()
    except Exception:
        pass

    stop_event = threading.Event()

    def _automation_loop() -> None:
        next_compact = 0.0
        while not stop_event.is_set():
            try:
                AUTOMATION.tick(home=p.home)
            except Exception:
                pass
            now = time.time()
            if now >= next_compact:
                next_compact = now + 60.0
                try:
                    _maybe_compact_ledgers(p.home)
                except Exception:
                    pass
            stop_event.wait(1.0)

    threading.Thread(target=_automation_loop, name="cccc-automation", daemon=True).start()

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
            raw = _recv_json_line(conn)
            try:
                req = DaemonRequest.model_validate(raw)
            except Exception as e:
                resp = _error("invalid_request", "invalid request", details={"error": str(e)})
                try:
                    _send_json(conn, resp.model_dump())
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
                continue

            op = str(req.op or "").strip()
            if op == "term_attach":
                args = req.args or {}
                group_id = str(args.get("group_id") or "").strip()
                actor_id = str(args.get("actor_id") or "").strip()
                if not group_id:
                    resp = _error("missing_group_id", "missing group_id")
                elif not actor_id:
                    resp = _error("missing_actor_id", "missing actor_id")
                elif not pty_runner.SUPERVISOR.actor_running(group_id, actor_id):
                    resp = _error("actor_not_running", "actor is not running")
                else:
                    resp = DaemonResponse(ok=True, result={"group_id": group_id, "actor_id": actor_id})
                try:
                    _send_json(conn, resp.model_dump())
                    if resp.ok:
                        pty_runner.SUPERVISOR.attach(group_id=group_id, actor_id=actor_id, sock=conn)
                        continue
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                continue

            try:
                resp, should_exit = handle_request(req)
                if should_exit:
                    stop_event.set()
                _send_json(conn, resp.model_dump())
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    stop_event.set()
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
