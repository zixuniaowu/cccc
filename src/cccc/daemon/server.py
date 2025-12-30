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
from ..kernel.actors import add_actor, find_actor, list_actors, remove_actor, resolve_recipient_tokens, update_actor, get_effective_role
from ..kernel.blobs import resolve_blob_attachment_path
from ..kernel.inbox import find_event, get_cursor, get_quote_text, is_message_for_actor, set_cursor, unread_messages
from ..kernel.ledger_retention import compact as compact_ledger
from ..kernel.ledger_retention import snapshot as snapshot_ledger
from ..kernel.permissions import require_actor_permission, require_group_permission, require_inbox_permission
from ..paths import ensure_home
from ..runners import pty as pty_runner
from ..runners import headless as headless_runner
from ..util.fs import atomic_write_json, atomic_write_text, read_json
from ..util.time import utc_now_iso
from .automation import AutomationManager
from .delivery import (
    inject_system_prompt as deliver_system_prompt,
    get_headless_targets_for_message,
    pty_submit_text,
    render_delivery_text,
    deliver_message_with_preamble,
    queue_chat_message,
    queue_system_notify,
    flush_pending_messages,
    tick_delivery,
    clear_preamble_sent,
    THROTTLE,
)
from .ops.context_ops import (
    handle_context_get,
    handle_context_sync,
    handle_task_list,
    handle_presence_get,
)
from .ops.runner_ops import (
    handle_headless_status,
    handle_headless_set_status,
    handle_headless_ack_message,
    is_actor_running,
    is_group_running,
    stop_actor as runner_stop_actor,
    stop_group as runner_stop_group,
    stop_all as runner_stop_all,
)

import subprocess


SUPPORTED_RUNTIMES = (
    "amp",
    "auggie",
    "claude",
    "codex",
    "cursor",
    "droid",
    "gemini",
    "kilocode",
    "neovate",
    "opencode",
    "copilot",
    "custom",
)

AUTO_MCP_RUNTIMES = ("claude", "codex", "droid", "amp", "auggie", "neovate", "gemini")


def _is_mcp_installed(runtime: str) -> bool:
    """Check if cccc MCP server is already installed for the runtime."""
    try:
        if runtime == "claude":
            result = subprocess.run(
                ["claude", "mcp", "get", "cccc"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        
        elif runtime == "codex":
            result = subprocess.run(
                ["codex", "mcp", "get", "cccc"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        
        elif runtime == "droid":
            # droid doesn't have 'get', use list and grep
            result = subprocess.run(
                ["droid", "mcp", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0 and "cccc" in (result.stdout or "")

        elif runtime == "amp":
            settings_path = Path.home() / ".config" / "amp" / "settings.json"
            if not settings_path.exists():
                return False
            doc = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(doc, dict):
                return False
            servers = doc.get("amp.mcpServers")
            return isinstance(servers, dict) and "cccc" in servers
        
        elif runtime == "auggie":
            settings_path = Path.home() / ".augment" / "settings.json"
            if not settings_path.exists():
                return False
            doc = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(doc, dict):
                return False
            servers = doc.get("mcpServers")
            return isinstance(servers, dict) and "cccc" in servers

        elif runtime == "neovate":
            config_path = Path.home() / ".neovate" / "config.json"
            if not config_path.exists():
                return False
            doc = json.loads(config_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(doc, dict):
                return False
            servers = doc.get("mcpServers")
            return isinstance(servers, dict) and "cccc" in servers
        
        elif runtime == "gemini":
            settings_path = Path.home() / ".gemini" / "settings.json"
            if not settings_path.exists():
                return False
            doc = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(doc, dict):
                return False
            servers = doc.get("mcpServers")
            return isinstance(servers, dict) and "cccc" in servers
    
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception:
        pass
    
    return False


def _ensure_mcp_installed(runtime: str, cwd: Path) -> bool:
    """Ensure MCP server is installed for the given runtime.
    
    Uses CLI commands to install MCP at user level:
    - claude: claude mcp add -s user cccc -- cccc mcp
    - codex: codex mcp add cccc -- cccc mcp (always user level)
    - droid: droid mcp add cccc -- cccc mcp (always user level)
    - amp: amp mcp add cccc cccc mcp (user config)
    - auggie: auggie mcp add cccc -- cccc mcp (user config)
    - neovate: neovate mcp add -g cccc cccc mcp (global config)
    - gemini: gemini mcp add -s user cccc cccc mcp (user config)
    
    Checks if already installed first to avoid unnecessary subprocess calls.
    
    Returns True if MCP was installed or already exists, False on error.
    """
    if runtime not in AUTO_MCP_RUNTIMES:
        return True  # Manual MCP config (or unsupported): skip
    
    # Check if already installed
    if _is_mcp_installed(runtime):
        return True
    
    try:
        if runtime == "claude":
            # Claude Code: user scope (available in all projects)
            result = subprocess.run(
                ["claude", "mcp", "add", "-s", "user", "cccc", "--", "cccc", "mcp"],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0
        
        elif runtime == "codex":
            # Codex CLI: user level (~/.codex/)
            result = subprocess.run(
                ["codex", "mcp", "add", "cccc", "--", "cccc", "mcp"],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0
        
        elif runtime == "droid":
            # Droid: user level
            result = subprocess.run(
                ["droid", "mcp", "add", "cccc", "--", "cccc", "mcp"],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0

        elif runtime == "amp":
            # Amp: user config (~/.config/amp/settings.json)
            result = subprocess.run(
                ["amp", "mcp", "add", "cccc", "cccc", "mcp"],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0
        
        elif runtime == "auggie":
            # Auggie: user config (~/.augment/settings.json)
            result = subprocess.run(
                ["auggie", "mcp", "add", "cccc", "--", "cccc", "mcp"],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0

        elif runtime == "neovate":
            # Neovate Code: global config (~/.neovate/config.json)
            result = subprocess.run(
                ["neovate", "mcp", "add", "-g", "cccc", "cccc", "mcp"],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0
        
        elif runtime == "gemini":
            # Gemini CLI: user scope (~/.gemini/settings.json)
            result = subprocess.run(
                ["gemini", "mcp", "add", "-s", "user", "cccc", "cccc", "mcp"],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0
    
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    return False


def _prepare_pty_env(env: Dict[str, Any]) -> Dict[str, str]:
    """Prepare environment variables for PTY session.
    
    Key modifications:
    - Disable bracketed paste mode via INPUTRC to prevent readline from
      interfering with programmatic text input
    - This is critical for reliable message delivery to CLI applications
    
    Returns a new dict with string values only.
    """
    result = {str(k): str(v) for k, v in env.items() if isinstance(k, str)}
    
    # Create a temporary inputrc file to disable bracketed paste
    # This is more reliable than sending escape sequences
    home = ensure_home()
    inputrc_path = home / "daemon" / "inputrc"
    try:
        inputrc_path.parent.mkdir(parents=True, exist_ok=True)
        inputrc_content = "set enable-bracketed-paste off\n"
        if not inputrc_path.exists() or inputrc_path.read_text() != inputrc_content:
            inputrc_path.write_text(inputrc_content)
        result["INPUTRC"] = str(inputrc_path)
    except Exception:
        pass
    
    return result


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


def _proc_cccc_home(pid: int) -> Optional[Path]:
    """
    Best-effort: read CCCC_HOME for a pid (Linux /proc only).
    Returns resolved home path, or None if unavailable.
    """
    try:
        env_path = Path("/proc") / str(pid) / "environ"
        raw = env_path.read_bytes()
    except Exception:
        return None
    cccc_home = None
    try:
        for item in raw.split(b"\x00"):
            if item.startswith(b"CCCC_HOME="):
                cccc_home = item.split(b"=", 1)[1].decode("utf-8", "ignore").strip()
                break
    except Exception:
        cccc_home = None
    if cccc_home:
        try:
            return Path(cccc_home).expanduser().resolve()
        except Exception:
            return None
    # If env var isn't present, it defaults to ~/.cccc.
    try:
        return (Path.home() / ".cccc").resolve()
    except Exception:
        return None


def _stop_im_bridges_for_group(home: Path, *, group_id: str) -> int:
    """Stop IM bridge processes for a specific group_id. Returns number of pids signaled."""
    gid = str(group_id or "").strip()
    if not gid:
        return 0

    killed: set[int] = set()

    # Stop by pid file first (fast path).
    pid_path = home / "groups" / gid / "state" / "im_bridge.pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            if pid > 0:
                _best_effort_killpg(pid, signal.SIGTERM)
                killed.add(pid)
        except Exception:
            pass
        try:
            pid_path.unlink(missing_ok=True)
        except Exception:
            pass

    # Also scan for orphan processes (pid file may be missing/stale).
    proc = Path("/proc")
    if proc.exists():
        for d in proc.iterdir():
            if not d.is_dir() or not d.name.isdigit():
                continue
            pid = int(d.name)
            if pid in killed:
                continue
            try:
                cmdline = (d / "cmdline").read_bytes().decode("utf-8", "ignore")
            except Exception:
                continue
            if "cccc.ports.im.bridge" not in cmdline or gid not in cmdline:
                continue
            ph = _proc_cccc_home(pid)
            if ph is None:
                continue
            try:
                if ph != home.resolve():
                    continue
            except Exception:
                continue
            _best_effort_killpg(pid, signal.SIGTERM)
            killed.add(pid)

    return len(killed)


def _stop_all_im_bridges(home: Path) -> int:
    """Stop all IM bridge processes for this CCCC_HOME. Returns number of pids signaled."""
    killed: set[int] = set()

    # Stop by pid files under this home.
    base = home / "groups"
    if base.exists():
        for pid_path in base.glob("*/state/im_bridge.pid"):
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
                if pid > 0:
                    _best_effort_killpg(pid, signal.SIGTERM)
                    killed.add(pid)
            except Exception:
                pass
            try:
                pid_path.unlink(missing_ok=True)
            except Exception:
                pass

    # Scan /proc for any remaining bridge processes bound to this home.
    proc = Path("/proc")
    if proc.exists():
        for d in proc.iterdir():
            if not d.is_dir() or not d.name.isdigit():
                continue
            pid = int(d.name)
            if pid in killed:
                continue
            try:
                cmdline = (d / "cmdline").read_bytes().decode("utf-8", "ignore")
            except Exception:
                continue
            if "cccc.ports.im.bridge" not in cmdline:
                continue
            ph = _proc_cccc_home(pid)
            if ph is None:
                continue
            try:
                if ph != home.resolve():
                    continue
            except Exception:
                continue
            _best_effort_killpg(pid, signal.SIGTERM)
            killed.add(pid)

    return len(killed)


def _cleanup_invalid_im_bridges(home: Path) -> Dict[str, int]:
    """
    Cleanup stale/broken IM bridge state for this CCCC_HOME.

    We only remove/stop things that are clearly invalid:
    - Stale pidfiles (pid not alive)
    - Running bridge processes whose group.yaml no longer exists
    """
    killed = 0
    stale_pidfiles = 0

    base = home / "groups"
    if base.exists():
        for pid_path in base.glob("*/state/im_bridge.pid"):
            gid = pid_path.parent.parent.name
            group_yaml = base / gid / "group.yaml"
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
            except Exception:
                pid = 0

            if pid <= 0 or not _pid_alive(pid):
                stale_pidfiles += 1
                try:
                    pid_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue

            if not group_yaml.exists():
                _best_effort_killpg(pid, signal.SIGTERM)
                killed += 1
                try:
                    pid_path.unlink(missing_ok=True)
                except Exception:
                    pass

    # Also scan /proc for orphan bridge processes (pidfile may be missing).
    proc = Path("/proc")
    if proc.exists():
        for d in proc.iterdir():
            if not d.is_dir() or not d.name.isdigit():
                continue
            pid = int(d.name)
            try:
                cmdline = (d / "cmdline").read_bytes().decode("utf-8", "ignore")
            except Exception:
                continue
            if "cccc.ports.im.bridge" not in cmdline:
                continue

            # Only touch processes that belong to this CCCC_HOME.
            ph = _proc_cccc_home(pid)
            if ph is None:
                continue
            try:
                if ph != home.resolve():
                    continue
            except Exception:
                continue

            # Parse group_id from argv.
            argv = [a for a in cmdline.split("\x00") if a]
            try:
                i = argv.index("cccc.ports.im.bridge")
            except ValueError:
                continue
            if i + 1 >= len(argv):
                continue
            gid = str(argv[i + 1] or "").strip()
            if not gid.startswith("g_"):
                continue

            group_yaml = home / "groups" / gid / "group.yaml"
            if not group_yaml.exists():
                _best_effort_killpg(pid, signal.SIGTERM)
                killed += 1

    return {"killed": killed, "stale_pidfiles": stale_pidfiles}


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


def _headless_state_path(group_id: str, actor_id: str) -> Path:
    home = ensure_home()
    return home / "groups" / str(group_id) / "state" / "runners" / "headless" / f"{actor_id}.json"


def _write_headless_state(group_id: str, actor_id: str) -> None:
    p = _headless_state_path(group_id, actor_id)
    atomic_write_json(
        p,
        {
            "v": 1,
            "kind": "headless",
            "group_id": str(group_id),
            "actor_id": str(actor_id),
            "started_at": utc_now_iso(),
        },
    )


def _remove_headless_state(group_id: str, actor_id: str) -> None:
    p = _headless_state_path(group_id, actor_id)
    try:
        if p.exists():
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
            runner_kind = str(actor.get("runner") or "pty").strip()
            scope_key = str(actor.get("default_scope_key") or group_scope_key).strip()
            url = _find_scope_url(group, scope_key)
            if not url:
                continue
            cwd = Path(url).expanduser().resolve()
            if not cwd.exists():
                continue
            cmd = actor.get("command") if isinstance(actor.get("command"), list) else []
            env = actor.get("env") if isinstance(actor.get("env"), dict) else {}

            runtime = str(actor.get("runtime") or "codex").strip()
            if runtime not in SUPPORTED_RUNTIMES:
                continue
            if runtime == "custom" and runner_kind != "headless" and not cmd:
                continue
            _ensure_mcp_installed(runtime, cwd)

            if runner_kind == "headless":
                headless_runner.SUPERVISOR.start_actor(
                    group_id=group.group_id, actor_id=aid, cwd=cwd, env=dict(env or {})
                )
                try:
                    _write_headless_state(group.group_id, aid)
                except Exception:
                    pass
            else:
                session = pty_runner.SUPERVISOR.start_actor(
                    group_id=group.group_id, actor_id=aid, cwd=cwd, command=list(cmd or []), env=_prepare_pty_env(env)
                )
                try:
                    _write_pty_state(group.group_id, aid, pid=session.pid)
                except Exception:
                    pass

            # Ensure fresh sessions always receive the lazy preamble on first delivery
            clear_preamble_sent(group, aid)
            THROTTLE.clear_actor(group.group_id, aid)
            # NOTE: 不在启动时注入 system prompt（lazy preamble）


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


def _normalize_attachments(group: Any, raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("attachments must be a list")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("invalid attachment (must be object)")
        rel_path = str(item.get("path") or "").strip()
        if not rel_path:
            raise ValueError("attachment missing path")
        abs_path = resolve_blob_attachment_path(group, rel_path=rel_path)
        if not abs_path.exists() or not abs_path.is_file():
            raise ValueError(f"attachment not found: {rel_path}")
        try:
            size = int(abs_path.stat().st_size)
        except Exception:
            size = int(item.get("bytes") or 0)
        out.append(
            {
                "kind": str(item.get("kind") or "file"),
                "path": rel_path,
                "title": str(item.get("title") or ""),
                "mime_type": str(item.get("mime_type") or ""),
                "bytes": size,
                "sha256": str(item.get("sha256") or ""),
            }
        )
    return out


def handle_request(req: DaemonRequest) -> Tuple[DaemonResponse, bool]:
    op = str(req.op or "").strip()
    args = req.args or {}

    if op == "ping":
        return DaemonResponse(ok=True, result={"version": __version__, "pid": os.getpid(), "ts": utc_now_iso()}), False

    if op == "shutdown":
        try:
            _stop_all_im_bridges(ensure_home())
        except Exception:
            pass
        try:
            pty_runner.SUPERVISOR.stop_all()
            headless_runner.SUPERVISOR.stop_all()
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

    if op == "group_settings_update":
        group_id = str(args.get("group_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        patch = args.get("patch") if isinstance(args.get("patch"), dict) else {}
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        
        # Define allowed keys and their target sections
        delivery_keys = {"min_interval_seconds"}
        automation_keys = {"nudge_after_seconds", "actor_idle_timeout_seconds", "keepalive_delay_seconds", 
                          "keepalive_max_per_actor", "silence_timeout_seconds", "standup_interval_seconds"}
        allowed = delivery_keys | automation_keys
        
        unknown = set(patch.keys()) - allowed
        if unknown:
            return _error("invalid_patch", "invalid patch keys", details={"unknown_keys": sorted(unknown)}), False
        if not patch:
            return _error("invalid_patch", "empty patch"), False
        try:
            require_group_permission(group, by=by, action="group.settings_update")
            
            # Update delivery settings
            delivery_patch = {k: v for k, v in patch.items() if k in delivery_keys}
            if delivery_patch:
                delivery = group.doc.get("delivery") if isinstance(group.doc.get("delivery"), dict) else {}
                for k, v in delivery_patch.items():
                    delivery[k] = int(v)
                group.doc["delivery"] = delivery
            
            # Update automation settings
            automation_patch = {k: v for k, v in patch.items() if k in automation_keys}
            if automation_patch:
                automation = group.doc.get("automation") if isinstance(group.doc.get("automation"), dict) else {}
                for k, v in automation_patch.items():
                    automation[k] = int(v)
                group.doc["automation"] = automation
            
            group.save()
        except Exception as e:
            return _error("group_settings_update_failed", str(e)), False
        
        # Return combined settings
        combined_settings = {}
        combined_settings.update(group.doc.get("delivery") or {})
        combined_settings.update(group.doc.get("automation") or {})
        
        ev = append_event(
            group.ledger_path,
            kind="group.settings_update",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"patch": dict(patch)},
        )
        return DaemonResponse(ok=True, result={"group_id": group.group_id, "settings": combined_settings, "event": ev}), False

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
            _stop_im_bridges_for_group(ensure_home(), group_id=group_id)
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
            running = (
                (pty_runner.SUPERVISOR.group_running(gid) if gid else False)
                or (headless_runner.SUPERVISOR.group_running(gid) if gid else False)
            )
            item = dict(g)
            item["running"] = bool(running)
            # Load full group doc to get state
            if gid:
                full_group = load_group(gid)
                if full_group is not None:
                    item["state"] = full_group.doc.get("state", "active")
            out.append(item)
        return DaemonResponse(ok=True, result={"groups": out}), False

    if op == "group_start":
        # Batch operation: start ALL actors in the group
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
            start_specs: list[tuple[str, Path, list[str], dict[str, str], Dict[str, Any], str]] = []
            for actor in actors:
                if not isinstance(actor, dict):
                    continue
                aid = str(actor.get("id") or "").strip()
                if not aid:
                    continue
                # Start ALL actors, not just enabled ones

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
                runner_kind = str(actor.get("runner") or "pty").strip()
                start_specs.append((aid, cwd, list(cmd or []), dict(env or {}), dict(actor), runner_kind))

            started: list[str] = []
            for aid, cwd, cmd, env, actor, runner_kind in start_specs:
                # Set enabled=true for all actors being started
                try:
                    update_actor(group, aid, {"enabled": True})
                except Exception:
                    pass
                
                # Ensure MCP is installed for the runtime BEFORE starting the actor
                runtime = str(actor.get("runtime") or "codex").strip()
                if runtime not in SUPPORTED_RUNTIMES:
                    return (
                        _error(
                            "unsupported_runtime",
                            f"unsupported runtime: {runtime}",
                            details={
                                "group_id": group.group_id,
                                "actor_id": aid,
                                "runtime": runtime,
                                "supported": list(SUPPORTED_RUNTIMES),
                                "hint": "Change the actor runtime to a supported one.",
                            },
                        ),
                        False,
                    )
                if runtime == "custom" and runner_kind != "headless" and not cmd:
                    return (
                        _error(
                            "missing_command",
                            "custom runtime requires a command (PTY runner)",
                            details={
                                "group_id": group.group_id,
                                "actor_id": aid,
                                "runtime": runtime,
                                "hint": "Set actor.command (or switch runner to headless).",
                            },
                        ),
                        False,
                    )
                _ensure_mcp_installed(runtime, cwd)
                
                if runner_kind == "headless":
                    headless_runner.SUPERVISOR.start_actor(
                        group_id=group.group_id, actor_id=aid, cwd=cwd, env=env
                    )
                    try:
                        _write_headless_state(group.group_id, aid)
                    except Exception:
                        pass
                else:
                    session = pty_runner.SUPERVISOR.start_actor(
                        group_id=group.group_id, actor_id=aid, cwd=cwd, command=cmd, env=_prepare_pty_env(env)
                    )
                    try:
                        _write_pty_state(group.group_id, aid, pid=session.pid)
                    except Exception:
                        pass
                
                # Clear preamble state so system prompt will be injected on first message
                clear_preamble_sent(group, aid)
                # Clear throttle state for fresh start
                THROTTLE.clear_actor(group.group_id, aid)
                
                started.append(aid)
        except Exception as e:
            return _error("group_start_failed", str(e)), False
        if started:
            try:
                group.doc["running"] = True
                group.save()
            except Exception:
                pass
        ev = append_event(group.ledger_path, kind="group.start", group_id=group.group_id, scope_key="", by=by, data={"started": started})
        return DaemonResponse(ok=True, result={"group_id": group.group_id, "started": started, "event": ev}), False

    if op == "group_stop":
        # Batch operation: stop ALL actors in the group
        group_id = str(args.get("group_id") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_group_permission(group, by=by, action="group.stop")
            
            # Set enabled=false for all actors
            actors = list_actors(group)
            stopped: list[str] = []
            for actor in actors:
                if not isinstance(actor, dict):
                    continue
                aid = str(actor.get("id") or "").strip()
                if not aid:
                    continue
                try:
                    update_actor(group, aid, {"enabled": False})
                    stopped.append(aid)
                except Exception:
                    pass
            
            # Stop both PTY and headless runners
            pty_runner.SUPERVISOR.stop_group(group_id=group.group_id)
            headless_runner.SUPERVISOR.stop_group(group_id=group.group_id)
            
            # Clean up PTY state files
            try:
                pdir = _pty_state_path(group.group_id, "_").parent
                for fp in pdir.glob("*.json"):
                    try:
                        fp.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
            # Clean up headless state files
            try:
                hdir = _headless_state_path(group.group_id, "_").parent
                for fp in hdir.glob("*.json"):
                    try:
                        fp.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception as e:
            return _error("group_stop_failed", str(e)), False
        try:
            group.doc["running"] = False
            group.save()
        except Exception:
            pass
        ev = append_event(group.ledger_path, kind="group.stop", group_id=group.group_id, scope_key="", by=by, data={"stopped": stopped})
        return DaemonResponse(ok=True, result={"group_id": group.group_id, "stopped": stopped, "event": ev}), False

    if op == "group_set_state":
        group_id = str(args.get("group_id") or "").strip()
        state = str(args.get("state") or "").strip()
        by = str(args.get("by") or "user").strip()
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        if not state:
            return _error("missing_state", "missing state"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        try:
            require_group_permission(group, by=by, action="group.set_state")
            from ..kernel.group import set_group_state, get_group_state
            old_state = get_group_state(group)
            group = set_group_state(group, state=state)
            new_state = get_group_state(group)
        except Exception as e:
            return _error("group_set_state_failed", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="group.set_state",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"old_state": old_state, "new_state": new_state},
        )
        return DaemonResponse(ok=True, result={"group_id": group.group_id, "state": new_state, "event": ev}), False

    if op == "actor_list":
        group_id = str(args.get("group_id") or "").strip()
        include_unread = bool(args.get("include_unread", False))
        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False
        actors = list_actors(group)
        # Add effective role and running status for each actor
        for actor in actors:
            aid = str(actor.get("id") or "")
            if aid:
                # Add effective role (based on position)
                actor["role"] = get_effective_role(group, aid)
                # Add actual running status (is the process running?)
                runner_kind = str(actor.get("runner") or "pty").strip()
                if runner_kind == "pty":
                    actor["running"] = pty_runner.SUPERVISOR.actor_running(group_id, aid)
                elif runner_kind == "headless":
                    actor["running"] = headless_runner.SUPERVISOR.actor_running(group_id, aid)
                else:
                    actor["running"] = False
        # Optionally include unread message count for each actor
        if include_unread:
            from ..kernel.inbox import unread_count
            for actor in actors:
                aid = str(actor.get("id") or "")
                if aid:
                    actor["unread_count"] = unread_count(group, actor_id=aid)
        return DaemonResponse(ok=True, result={"actors": actors}), False

    if op == "actor_add":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        title = str(args.get("title") or "").strip()
        submit = str(args.get("submit") or "").strip()
        runner = str(args.get("runner") or "pty").strip()
        runtime = str(args.get("runtime") or "codex").strip()
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
            # Note: role is auto-determined by position (first enabled = foreman)
            if runner not in ("pty", "headless"):
                raise ValueError("invalid runner (must be 'pty' or 'headless')")
            if runtime not in SUPPORTED_RUNTIMES:
                raise ValueError("invalid runtime")
            
            # Auto-generate actor_id if not provided (use runtime as prefix)
            if not actor_id:
                from ..kernel.actors import generate_actor_id
                actor_id = generate_actor_id(group, runtime=runtime)
            
            command: list[str] = []
            if isinstance(command_raw, list) and all(isinstance(x, str) for x in command_raw):
                command = [str(x) for x in command_raw if str(x).strip()]
            # Auto-set command based on runtime if not provided
            if not command:
                from ..kernel.runtime import get_runtime_command_with_flags
                command = get_runtime_command_with_flags(runtime)
            if runtime == "custom" and runner != "headless" and not command:
                raise ValueError("custom runtime requires a command (PTY runner)")
            env: Dict[str, str] = {}
            if isinstance(env_raw, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in env_raw.items()):
                env = {str(k): str(v) for k, v in env_raw.items()}
            actor = add_actor(
                group,
                actor_id=actor_id,
                title=title,
                command=command,
                env=env,
                default_scope_key=default_scope_key,
                submit=submit or "enter",
                runner=runner,  # type: ignore
                runtime=runtime,  # type: ignore
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
        # New actors should not inherit historical unread messages.
        # Initialize their read cursor to "now" (the actor.add event) so inbox starts empty.
        try:
            set_cursor(group, actor_id, event_id=str(ev.get("id") or ""), ts=str(ev.get("ts") or ""))
        except Exception:
            pass
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
                        group_id=group.group_id, actor_id=actor_id, cwd=cwd, command=list(cmd or []), env=_prepare_pty_env(env)
                    )
                    try:
                        _write_pty_state(group.group_id, actor_id, pid=session.pid)
                    except Exception:
                        pass
                    # NOTE: 不在启动时注入 system prompt（lazy preamble）
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
        
        # Start the actor process immediately (no group.running check needed)
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
        runner_kind = str(actor.get("runner") or "pty").strip()

        # Ensure MCP is installed for the runtime BEFORE starting the actor
        runtime = str(actor.get("runtime") or "codex").strip()
        if runtime not in SUPPORTED_RUNTIMES:
            return (
                _error(
                    "unsupported_runtime",
                    f"unsupported runtime: {runtime}",
                    details={
                        "group_id": group.group_id,
                        "actor_id": actor_id,
                        "runtime": runtime,
                        "supported": list(SUPPORTED_RUNTIMES),
                        "hint": "Change the actor runtime to a supported one.",
                    },
                ),
                False,
            )
        if runtime == "custom" and runner_kind != "headless" and not cmd:
            return (
                _error(
                    "missing_command",
                    "custom runtime requires a command (PTY runner)",
                    details={
                        "group_id": group.group_id,
                        "actor_id": actor_id,
                        "runtime": runtime,
                        "hint": "Set actor.command (or switch runner to headless).",
                    },
                ),
                False,
            )
        _ensure_mcp_installed(runtime, cwd)

        if runner_kind == "headless":
            # Start headless session (no PTY, MCP-driven)
            headless_runner.SUPERVISOR.start_actor(
                group_id=group.group_id, actor_id=actor_id, cwd=cwd, env=dict(env or {})
            )
            try:
                _write_headless_state(group.group_id, actor_id)
            except Exception:
                pass
        else:
            # Start PTY session (interactive terminal)
            session = pty_runner.SUPERVISOR.start_actor(
                group_id=group.group_id, actor_id=actor_id, cwd=cwd, command=list(cmd or []), env=_prepare_pty_env(env)
            )
            try:
                _write_pty_state(group.group_id, actor_id, pid=session.pid)
            except Exception:
                pass
        
        # Clear preamble state so system prompt will be injected on first message
        clear_preamble_sent(group, actor_id)
        # Clear throttle state for fresh start
        THROTTLE.clear_actor(group.group_id, actor_id)

        try:
            group.doc["running"] = True
            group.save()
        except Exception:
            pass
        
        ev = append_event(
            group.ledger_path,
            kind="actor.start",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"actor_id": actor_id, "runner": str(actor.get("runner") or "pty")},
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
            runner_kind = str(actor.get("runner") or "pty").strip()
            if runner_kind == "headless":
                headless_runner.SUPERVISOR.stop_actor(group_id=group.group_id, actor_id=actor_id)
                _remove_headless_state(group.group_id, actor_id)
            else:
                pty_runner.SUPERVISOR.stop_actor(group_id=group.group_id, actor_id=actor_id)
                _remove_pty_state_if_pid(group.group_id, actor_id, pid=0)
        except Exception as e:
            return _error("actor_stop_failed", str(e)), False
        try:
            any_enabled = any(
                bool(a.get("enabled", True))
                for a in list_actors(group)
                if isinstance(a, dict) and str(a.get("id") or "").strip()
            )
            if not any_enabled:
                group.doc["running"] = False
                group.save()
        except Exception:
            pass
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
            runner_kind = str(actor.get("runner") or "pty").strip()
            # Stop existing session
            if runner_kind == "headless":
                headless_runner.SUPERVISOR.stop_actor(group_id=group.group_id, actor_id=actor_id)
                _remove_headless_state(group.group_id, actor_id)
            else:
                pty_runner.SUPERVISOR.stop_actor(group_id=group.group_id, actor_id=actor_id)
                _remove_pty_state_if_pid(group.group_id, actor_id, pid=0)
            # Clear preamble state so system prompt will be re-injected on restart
            clear_preamble_sent(group, actor_id)
            # Clear throttle state for fresh start
            THROTTLE.clear_actor(group.group_id, actor_id)
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
            runner_kind = str(actor.get("runner") or "pty").strip()

            if runner_kind == "headless":
                headless_runner.SUPERVISOR.start_actor(
                    group_id=group.group_id, actor_id=actor_id, cwd=cwd, env=dict(env or {})
                )
                try:
                    _write_headless_state(group.group_id, actor_id)
                except Exception:
                    pass
            else:
                session = pty_runner.SUPERVISOR.start_actor(
                    group_id=group.group_id, actor_id=actor_id, cwd=cwd, command=list(cmd or []), env=_prepare_pty_env(env)
                )
                try:
                    _write_pty_state(group.group_id, actor_id, pid=session.pid)
                except Exception:
                    pass
                # NOTE: 不在重启时注入 system prompt（lazy preamble）
        ev = append_event(
            group.ledger_path,
            kind="actor.restart",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={"actor_id": actor_id, "runner": str(actor.get("runner") or "pty")},
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
        kind_filter = str(args.get("kind_filter") or "all").strip()
        if kind_filter not in ("all", "chat", "notify"):
            kind_filter = "all"
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
        msgs = unread_messages(group, actor_id=actor_id, limit=limit, kind_filter=kind_filter)  # type: ignore
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
        if str(ev.get("kind") or "") not in ("chat.message", "system.notify"):
            return _error("invalid_event_kind", "event kind must be chat.message or system.notify"), False
        if not is_message_for_actor(group, actor_id=actor_id, event=ev):
            return _error("event_not_for_actor", f"event is not addressed to actor: {actor_id}"), False
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
        by = str(args.get("by") or "user").strip()
        to_raw = args.get("to")
        to_tokens: list[str] = []
        if isinstance(to_raw, list):
            to_tokens = [str(x).strip() for x in to_raw if isinstance(x, str) and str(x).strip()]

        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False

        # If group is idle, wake it on "human" messages (non-actor sender).
        # This keeps idle stable against agent chatter / throttled deliveries.
        try:
            from ..kernel.group import get_group_state, set_group_state
            if get_group_state(group) == "idle":
                is_actor_sender = isinstance(find_actor(group, by), dict)
                if by and by != "system" and not is_actor_sender:
                    group = set_group_state(group, state="active")
        except Exception:
            pass

        try:
            to = resolve_recipient_tokens(group, to_tokens)
        except Exception as e:
            return _error("invalid_recipient", str(e)), False

        # Auto-extract @mentions from message text if no explicit recipients
        if not to:
            import re
            # Match @word patterns (but not @all, @peers, @foreman which are special)
            mention_pattern = re.compile(r'@(\w[\w-]*)')
            mentions = mention_pattern.findall(text)
            if mentions:
                # Filter to valid actor IDs
                actors = list_actors(group)
                actor_ids = {str(a.get("id") or "") for a in actors if isinstance(a, dict)}
                valid_mentions = [m for m in mentions if m in actor_ids or m in ("all", "peers", "foreman")]
                if valid_mentions:
                    # Convert to proper format
                    mention_tokens = []
                    for m in valid_mentions:
                        if m in ("all", "peers", "foreman"):
                            mention_tokens.append(f"@{m}")
                        else:
                            mention_tokens.append(m)
                    try:
                        to = resolve_recipient_tokens(group, mention_tokens)
                    except Exception:
                        pass  # Ignore invalid mentions

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

        try:
            attachments = _normalize_attachments(group, args.get("attachments"))
        except Exception as e:
            return _error("invalid_attachments", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="chat.message",
            group_id=group.group_id,
            scope_key=scope_key,
            by=by,
            data=ChatMessageData(text=text, format="plain", to=to, attachments=attachments).model_dump(),
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

        # Best-effort delivery into running actors.
        # If no explicit recipients, deliver to all actors (@all behavior)
        effective_to = to if to else ["@all"]
        event_id = str(ev.get("id") or "").strip()
        event_ts = str(ev.get("ts") or "").strip()
        delivery_text = text
        if attachments:
            lines = ["[cccc] Attachments:"]
            for a in attachments[:8]:
                title = str(a.get("title") or a.get("path") or "file").strip()
                b = int(a.get("bytes") or 0)
                p = str(a.get("path") or "").strip()
                lines.append(f"- {title} ({b} bytes) [{p}]")
            if len(attachments) > 8:
                lines.append(f"- … ({len(attachments) - 8} more)")
            delivery_text = (delivery_text.rstrip("\n") + "\n\n" + "\n".join(lines)).strip()
        for actor in list_actors(group):
            if not isinstance(actor, dict):
                continue
            aid = str(actor.get("id") or "").strip()
            if not aid or aid == "user" or aid == by:
                continue
            # Check if message is for this actor (handles @all, @peers, @foreman, etc.)
            ev_with_effective_to = dict(ev)
            ev_with_effective_to["data"] = dict(ev.get("data") or {})
            ev_with_effective_to["data"]["to"] = effective_to
            if not is_message_for_actor(group, actor_id=aid, event=ev_with_effective_to):
                continue
            # PTY runner: queue message for throttled delivery
            runner_kind = str(actor.get("runner") or "pty").strip()
            if runner_kind != "headless":
                queue_chat_message(
                    group,
                    actor_id=aid,
                    event_id=event_id,
                    by=by,
                    to=effective_to,
                    text=delivery_text,
                    ts=event_ts,
                )
        # Headless runners: notify via system.notify event (daemon writes to ledger)
        try:
            ev_for_headless = dict(ev)
            ev_for_headless["data"] = dict(ev.get("data") or {})
            ev_for_headless["data"]["to"] = effective_to
            headless_targets = get_headless_targets_for_message(group, event=ev_for_headless, by=by)
            for aid in headless_targets:
                append_event(
                    group.ledger_path,
                    kind="system.notify",
                    group_id=group.group_id,
                    scope_key="",
                    by="system",
                    data={
                        "kind": "info",
                        "priority": "high",
                        "title": "New message",
                        "message": f"New message from {by}. Check your inbox.",
                        "target_actor_id": aid,
                        "requires_ack": False,
                        "context": {"event_id": event_id, "from": by},
                    },
                )
        except Exception:
            pass

        # Trigger automation: auto-transition idle -> active on new message
        try:
            AUTOMATION.on_new_message(group)
        except Exception:
            pass

        # Immediately try to flush pending messages (don't wait for tick)
        try:
            for actor in list_actors(group):
                if not isinstance(actor, dict):
                    continue
                aid = str(actor.get("id") or "").strip()
                if not aid:
                    continue
                runner_kind = str(actor.get("runner") or "pty").strip()
                if runner_kind == "pty" and pty_runner.SUPERVISOR.actor_running(group_id, aid):
                    flush_pending_messages(group, actor_id=aid)
        except Exception:
            pass

        return DaemonResponse(ok=True, result={"event": ev}), False

    if op == "reply":
        group_id = str(args.get("group_id") or "").strip()
        text = str(args.get("text") or "")
        by = str(args.get("by") or "user").strip()
        reply_to = str(args.get("reply_to") or "").strip()
        to_raw = args.get("to")
        to_tokens: list[str] = []
        if isinstance(to_raw, list):
            to_tokens = [str(x).strip() for x in to_raw if isinstance(x, str) and str(x).strip()]

        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        if not reply_to:
            return _error("missing_reply_to", "missing reply_to event_id"), False

        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False

        # If group is idle, wake it on "human" replies (non-actor sender).
        try:
            from ..kernel.group import get_group_state, set_group_state
            if get_group_state(group) == "idle":
                is_actor_sender = isinstance(find_actor(group, by), dict)
                if by and by != "system" and not is_actor_sender:
                    group = set_group_state(group, state="active")
        except Exception:
            pass

        # 查找被回复的消息
        original = find_event(group, reply_to)
        if original is None:
            return _error("event_not_found", f"event not found: {reply_to}"), False

        # 获取引用文本
        quote_text = get_quote_text(group, reply_to, max_len=100)

        # 如果没有指定收件人，默认回复给原消息发送者
        if not to_tokens:
            original_by = str(original.get("by") or "").strip()
            if original_by:
                to_tokens = ["user"] if original_by == "user" else [original_by]

        try:
            to = resolve_recipient_tokens(group, to_tokens)
        except Exception as e:
            return _error("invalid_recipient", str(e)), False

        scope_key = str(group.doc.get("active_scope_key") or "").strip()
        try:
            attachments = _normalize_attachments(group, args.get("attachments"))
        except Exception as e:
            return _error("invalid_attachments", str(e)), False
        ev = append_event(
            group.ledger_path,
            kind="chat.message",
            group_id=group.group_id,
            scope_key=scope_key,
            by=by,
            data=ChatMessageData(
                text=text,
                format="plain",
                to=to,
                reply_to=reply_to,
                quote_text=quote_text,
                attachments=attachments,
            ).model_dump(),
        )

        # 更新 group 活跃时间
        try:
            reg = load_registry()
            meta = reg.groups.get(group.group_id)
            if isinstance(meta, dict):
                meta["updated_at"] = str(ev.get("ts") or utc_now_iso())
                reg.save()
        except Exception:
            pass

        # Best-effort delivery into running actors.
        # If no explicit recipients, deliver to all actors (@all behavior).
        effective_to = to if to else ["@all"]
        ev_with_effective_to = dict(ev)
        ev_with_effective_to["data"] = dict(ev.get("data") or {})
        ev_with_effective_to["data"]["to"] = effective_to

        event_id = str(ev.get("id") or "").strip()
        event_ts = str(ev.get("ts") or "").strip()
        delivery_text = text
        if attachments:
            lines = ["[cccc] Attachments:"]
            for a in attachments[:8]:
                title = str(a.get("title") or a.get("path") or "file").strip()
                b = int(a.get("bytes") or 0)
                p = str(a.get("path") or "").strip()
                lines.append(f"- {title} ({b} bytes) [{p}]")
            if len(attachments) > 8:
                lines.append(f"- … ({len(attachments) - 8} more)")
            delivery_text = (delivery_text.rstrip("\n") + "\n\n" + "\n".join(lines)).strip()

        for actor in list_actors(group):
            if not isinstance(actor, dict):
                continue
            aid = str(actor.get("id") or "").strip()
            if not aid or aid == "user" or aid == by:
                continue
            if not is_message_for_actor(group, actor_id=aid, event=ev_with_effective_to):
                continue
            # PTY runner: queue message for throttled delivery
            runner_kind = str(actor.get("runner") or "pty").strip()
            if runner_kind != "headless":
                queue_chat_message(
                    group,
                    actor_id=aid,
                    event_id=event_id,
                    by=by,
                    to=effective_to,
                    text=delivery_text,
                    reply_to=reply_to,
                    quote_text=quote_text,
                    ts=event_ts,
                )

        # Headless runners: notify via system.notify event (daemon writes to ledger)
        try:
            headless_targets = get_headless_targets_for_message(group, event=ev_with_effective_to, by=by)
            for aid in headless_targets:
                append_event(
                    group.ledger_path,
                    kind="system.notify",
                    group_id=group.group_id,
                    scope_key="",
                    by="system",
                    data={
                        "kind": "info",
                        "priority": "high",
                        "title": "New message",
                        "message": f"New message from {by}. Check your inbox.",
                        "target_actor_id": aid,
                        "requires_ack": False,
                        "context": {"event_id": event_id, "from": by},
                    },
                )
        except Exception:
            pass

        # Trigger automation: auto-transition idle -> active on new message
        try:
            AUTOMATION.on_new_message(group)
        except Exception:
            pass

        # Immediately try to flush pending messages (don't wait for tick)
        try:
            for actor in list_actors(group):
                if not isinstance(actor, dict):
                    continue
                aid = str(actor.get("id") or "").strip()
                if not aid:
                    continue
                runner_kind = str(actor.get("runner") or "pty").strip()
                if runner_kind == "pty" and pty_runner.SUPERVISOR.actor_running(group_id, aid):
                    flush_pending_messages(group, actor_id=aid)
        except Exception:
            pass

        return DaemonResponse(ok=True, result={"event": ev}), False

    # ==========================================================================
    # Context Operations (delegated to ops/context_ops.py)
    # ==========================================================================

    if op == "context_get":
        return handle_context_get(args), False

    if op == "context_sync":
        return handle_context_sync(args), False

    if op == "task_list":
        return handle_task_list(args), False

    if op == "presence_get":
        return handle_presence_get(args), False

    # ==========================================================================
    # Headless Runner Operations (delegated to ops/runner_ops.py)
    # ==========================================================================

    if op == "headless_status":
        return handle_headless_status(args), False

    if op == "headless_set_status":
        return handle_headless_set_status(args), False

    if op == "headless_ack_message":
        return handle_headless_ack_message(args), False

    # ==========================================================================
    # System Notification Operations
    # ==========================================================================

    if op == "system_notify":
        group_id = str(args.get("group_id") or "").strip()
        by = str(args.get("by") or "system").strip()
        kind = str(args.get("kind") or "info").strip()
        priority = str(args.get("priority") or "normal").strip()
        title = str(args.get("title") or "").strip()
        message = str(args.get("message") or "").strip()
        target_actor_id = str(args.get("target_actor_id") or "").strip() or None
        requires_ack = bool(args.get("requires_ack", False))
        context = args.get("context") if isinstance(args.get("context"), dict) else {}

        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False

        # 验证 kind 和 priority
        valid_kinds = {"nudge", "keepalive", "actor_idle", "silence_check", "standup", "status_change", "error", "info"}
        valid_priorities = {"low", "normal", "high", "urgent"}
        if kind not in valid_kinds:
            kind = "info"
        if priority not in valid_priorities:
            priority = "normal"

        ev = append_event(
            group.ledger_path,
            kind="system.notify",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={
                "kind": kind,
                "priority": priority,
                "title": title,
                "message": message,
                "target_actor_id": target_actor_id,
                "requires_ack": requires_ack,
                "context": context,
            },
        )

        # Best-effort 投递到 PTY（仅 high/urgent 优先级）
        if priority in ("high", "urgent"):
            event_id = str(ev.get("id") or "").strip()
            event_ts = str(ev.get("ts") or "").strip()
            for actor in list_actors(group):
                if not isinstance(actor, dict):
                    continue
                aid = str(actor.get("id") or "").strip()
                if not aid or aid == "user":
                    continue
                # 检查是否是目标 actor
                if target_actor_id and aid != target_actor_id:
                    continue
                # 只投递给 PTY runner
                runner_kind = str(actor.get("runner") or "pty").strip()
                if runner_kind != "pty":
                    continue
                # Queue for throttled delivery
                queue_system_notify(
                    group,
                    actor_id=aid,
                    event_id=event_id,
                    notify_kind=kind,
                    title=title,
                    message=message,
                    ts=event_ts,
                )

        return DaemonResponse(ok=True, result={"event": ev}), False

    if op == "notify_ack":
        group_id = str(args.get("group_id") or "").strip()
        actor_id = str(args.get("actor_id") or "").strip()
        notify_event_id = str(args.get("notify_event_id") or "").strip()
        by = str(args.get("by") or "user").strip()

        if not group_id:
            return _error("missing_group_id", "missing group_id"), False
        if not actor_id:
            return _error("missing_actor_id", "missing actor_id"), False
        if not notify_event_id:
            return _error("missing_notify_event_id", "missing notify_event_id"), False

        group = load_group(group_id)
        if group is None:
            return _error("group_not_found", f"group not found: {group_id}"), False

        # 验证通知事件存在
        notify_ev = find_event(group, notify_event_id)
        if notify_ev is None:
            return _error("event_not_found", f"event not found: {notify_event_id}"), False
        if str(notify_ev.get("kind") or "") != "system.notify":
            return _error("invalid_event_kind", "event is not a system.notify"), False

        ev = append_event(
            group.ledger_path,
            kind="system.notify_ack",
            group_id=group.group_id,
            scope_key="",
            by=by,
            data={
                "notify_event_id": notify_event_id,
                "actor_id": actor_id,
            },
        )

        return DaemonResponse(ok=True, result={"event": ev}), False

    return _error("unknown_op", f"unknown op: {op}"), False
def serve_forever(paths: Optional[DaemonPaths] = None) -> int:
    p = paths or default_paths()
    p.daemon_dir.mkdir(parents=True, exist_ok=True)

    _remove_stale_socket(p.sock_path)
    if p.sock_path.exists() and _is_socket_alive(p.sock_path):
        return 0

    # Cleanup stale IM bridge state from previous runs/crashes.
    try:
        res = _cleanup_invalid_im_bridges(p.home)
        if res.get("killed") or res.get("stale_pidfiles"):
            print(f"[im] cleanup: killed={res.get('killed')} stale_pidfiles={res.get('stale_pidfiles')}", file=sys.stderr)
    except Exception:
        pass

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

    # Graceful shutdown on SIGTERM/SIGINT
    def _signal_handler(signum: int, frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    def _automation_loop() -> None:
        next_compact = 0.0
        while not stop_event.is_set():
            try:
                AUTOMATION.tick(home=p.home)
            except Exception:
                pass
            # Tick delivery for all groups with running actors (flush pending messages)
            try:
                base = p.home / "groups"
                if base.exists():
                    for gp in base.glob("*/group.yaml"):
                        gid = gp.parent.name
                        group = load_group(gid)
                        if group is None:
                            continue
                        # Check if any actor is running (instead of group.running)
                        group_is_running = pty_runner.SUPERVISOR.group_running(gid)
                        if not group_is_running:
                            continue
                        try:
                            tick_delivery(group)
                        except Exception:
                            pass
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
        s.settimeout(1.0)  # Allow periodic check of stop_event
        _write_pid(p.pid_path)

        should_exit = False
        while not should_exit and not stop_event.is_set():
            try:
                conn, _ = s.accept()
            except socket.timeout:
                continue
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
                try:
                    _send_json(conn, resp.model_dump())
                except (BrokenPipeError, ConnectionResetError, OSError):
                    # Client disconnected before response was sent - not an error
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    stop_event.set()
    
    # Graceful shutdown: stop all running actors
    try:
        _stop_all_im_bridges(p.home)
    except Exception:
        pass
    try:
        pty_runner.SUPERVISOR.stop_all()
    except Exception:
        pass
    try:
        headless_runner.SUPERVISOR.stop_all()
    except Exception:
        pass
    
    try:
        if p.sock_path.exists():
            p.sock_path.unlink()
    except Exception:
        pass
    try:
        if p.pid_path.exists():
            p.pid_path.unlink()
    except Exception:
        pass
    return 0


def call_daemon(req: Dict[str, Any], *, paths: Optional[DaemonPaths] = None, timeout_s: float = 60.0) -> Dict[str, Any]:
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
