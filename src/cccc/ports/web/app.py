from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Literal, Optional, Union

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ... import __version__
from ...daemon.server import call_daemon
from ...kernel.group import load_group
from ...kernel.ledger import read_last_lines
from ...paths import ensure_home
from ...util.fs import atomic_write_text


class CreateGroupRequest(BaseModel):
    title: str = Field(default="working-group")
    topic: str = Field(default="")
    by: str = Field(default="user")


class AttachRequest(BaseModel):
    path: str
    by: str = Field(default="user")


class SendRequest(BaseModel):
    text: str
    by: str = Field(default="user")
    to: list[str] = Field(default_factory=list)
    path: str = Field(default="")


class ReplyRequest(BaseModel):
    text: str
    by: str = Field(default="user")
    to: list[str] = Field(default_factory=list)
    reply_to: str


class ActorCreateRequest(BaseModel):
    actor_id: str
    # Note: role is auto-determined by position (first enabled = foreman)
    runner: Literal["pty", "headless"] = Field(default="pty")
    runtime: Literal[
        "claude",
        "codex",
        "droid",
        "opencode",
        "copilot",
    ] = Field(default="codex")
    title: str = Field(default="")
    command: Union[str, list[str]] = Field(default="")
    env: Dict[str, str] = Field(default_factory=dict)
    default_scope_key: str = Field(default="")
    submit: Literal["enter", "newline", "none"] = Field(default="enter")
    by: str = Field(default="user")


class ActorUpdateRequest(BaseModel):
    by: str = Field(default="user")
    # Note: role is ignored - auto-determined by position
    title: Optional[str] = None
    command: Optional[Union[str, list[str]]] = None
    env: Optional[Dict[str, str]] = None
    default_scope_key: Optional[str] = None
    submit: Optional[Literal["enter", "newline", "none"]] = None
    runner: Optional[Literal["pty", "headless"]] = None
    runtime: Optional[
        Literal[
            "claude",
            "codex",
            "droid",
            "opencode",
            "copilot",
        ]
    ] = None
    enabled: Optional[bool] = None


class InboxReadRequest(BaseModel):
    event_id: str
    by: str = Field(default="user")

class ProjectMdUpdateRequest(BaseModel):
    content: str = Field(default="")
    by: str = Field(default="user")


class GroupUpdateRequest(BaseModel):
    title: Optional[str] = None
    topic: Optional[str] = None
    by: str = Field(default="user")


class GroupSettingsRequest(BaseModel):
    nudge_after_seconds: Optional[int] = None
    actor_idle_timeout_seconds: Optional[int] = None
    keepalive_delay_seconds: Optional[int] = None
    keepalive_max_per_actor: Optional[int] = None
    silence_timeout_seconds: Optional[int] = None
    min_interval_seconds: Optional[int] = None  # delivery throttle
    standup_interval_seconds: Optional[int] = None  # periodic review interval
    by: str = Field(default="user")


class GroupDeleteRequest(BaseModel):
    confirm: str = Field(default="")
    by: str = Field(default="user")


class IMSetRequest(BaseModel):
    group_id: str
    platform: Literal["telegram", "slack", "discord"]
    # Legacy single token field (backward compat for telegram/discord)
    token_env: str = ""
    token: str = ""
    # Dual token fields for Slack
    bot_token_env: str = ""  # xoxb- for outbound (Web API)
    app_token_env: str = ""  # xapp- for inbound (Socket Mode)


class IMActionRequest(BaseModel):
    group_id: str


def _is_env_var_name(value: str) -> bool:
    # Shell-friendly env var name (portable).
    return bool(re.fullmatch(r"[A-Z_][A-Z0-9_]*", (value or "").strip()))


def _normalize_command(cmd: Union[str, list[str], None]) -> Optional[list[str]]:
    if cmd is None:
        return None
    if isinstance(cmd, str):
        s = cmd.strip()
        return shlex.split(s) if s else []
    if isinstance(cmd, list) and all(isinstance(x, str) for x in cmd):
        return [str(x).strip() for x in cmd if str(x).strip()]
    raise HTTPException(status_code=400, detail={"code": "invalid_command", "message": "invalid command"})


def _require_token_if_configured(request: Request) -> Optional[JSONResponse]:
    token = str(os.environ.get("CCCC_WEB_TOKEN") or "").strip()
    if not token:
        return None
    auth = str(request.headers.get("authorization") or "").strip()
    if auth != f"Bearer {token}":
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": {"code": "unauthorized", "message": "missing/invalid token", "details": {}}},
        )
    return None


def _daemon(req: Dict[str, Any]) -> Dict[str, Any]:
    resp = call_daemon(req)
    if not resp.get("ok") and isinstance(resp.get("error"), dict) and resp["error"].get("code") == "daemon_unavailable":
        raise HTTPException(status_code=503, detail={"code": "daemon_unavailable", "message": "ccccd unavailable"})
    return resp


async def _sse_tail(path: Path) -> AsyncIterator[bytes]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    inode = -1
    f = None

    def _open() -> None:
        nonlocal f, inode
        if f is not None:
            try:
                f.close()
            except Exception:
                pass
        f = path.open("r", encoding="utf-8", errors="replace")
        try:
            st = os.fstat(f.fileno())
            inode = int(getattr(st, "st_ino", -1) or -1)
        except Exception:
            inode = -1
        f.seek(0, 2)

    _open()
    assert f is not None

    while True:
        line = f.readline()
        if line:
            raw = line.rstrip("\n")
            if raw:
                yield b"event: ledger\n"
                yield b"data: " + raw.encode("utf-8", errors="replace") + b"\n\n"
            continue

        await asyncio.sleep(0.2)
        try:
            st = path.stat()
            cur_inode = int(getattr(st, "st_ino", -1) or -1)
            if inode != -1 and cur_inode != -1 and cur_inode != inode:
                _open()
                continue
            if st.st_size < f.tell():
                _open()
                continue
        except Exception:
            try:
                path.touch(exist_ok=True)
            except Exception:
                pass
            _open()


def create_app() -> FastAPI:
    app = FastAPI(title="cccc web", version=__version__)

    dist = str(os.environ.get("CCCC_WEB_DIST") or "").strip()
    dist_dir: Optional[Path] = None
    if dist:
        try:
            candidate = Path(dist).expanduser().resolve()
            if candidate.exists():
                dist_dir = candidate
        except Exception:
            dist_dir = None
    else:
        # Prefer packaged UI under `cccc/ports/web/dist`.
        try:
            packaged = Path(__file__).resolve().parent / "dist"
            if packaged.exists():
                dist_dir = packaged
        except Exception:
            dist_dir = None

        # Dev fallback: repo-root `web/dist`.
        if dist_dir is None:
            try:
                for parent in Path(__file__).resolve().parents:
                    candidate = parent / "web" / "dist"
                    if candidate.exists():
                        dist_dir = candidate
                        break
            except Exception:
                dist_dir = None
    if dist_dir is not None:
        app.mount("/ui", StaticFiles(directory=str(dist_dir), html=True), name="ui")

    cors = str(os.environ.get("CCCC_WEB_CORS_ORIGINS") or "").strip()
    if cors:
        allow_origins = [o.strip() for o in cors.split(",") if o.strip()]
        if allow_origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=allow_origins,
                allow_methods=["*"],
                allow_headers=["*"],
            )

    @app.middleware("http")
    async def _auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        blocked = _require_token_if_configured(request)
        if blocked is not None:
            return blocked
        return await call_next(request)

    @app.middleware("http")
    async def _ui_cache_control(request: Request, call_next):  # type: ignore[no-untyped-def]
        resp = await call_next(request)
        # Avoid "why didn't my UI update?" confusion during local development.
        # Vite config uses stable filenames, so we force revalidation.
        if str(request.url.path or "").startswith("/ui"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        if dist_dir is not None:
            return '<meta http-equiv="refresh" content="0; url=/ui/">'
        return (
            "<h3>cccc web</h3>"
            "<p>This is a minimal control-plane port. UI will live under <code>/ui</code> later.</p>"
            "<p>Try <code>/api/v1/ping</code> and <code>/api/v1/groups</code>.</p>"
        )

    @app.get("/api/v1/ping")
    async def ping() -> Dict[str, Any]:
        home = ensure_home()
        resp = _daemon({"op": "ping"})
        return {"ok": True, "result": {"home": str(home), "daemon": resp.get("result", {}), "version": __version__}}

    @app.get("/api/v1/health")
    async def health() -> Dict[str, Any]:
        """Health check endpoint for monitoring."""
        home = ensure_home()
        daemon_resp = _daemon({"op": "ping"})
        daemon_ok = daemon_resp.get("ok", False)
        
        return {
            "ok": daemon_ok,
            "result": {
                "version": __version__,
                "home": str(home),
                "daemon": "running" if daemon_ok else "stopped",
            }
        }

    @app.get("/api/v1/runtimes")
    async def runtimes() -> Dict[str, Any]:
        """List available agent runtimes on the system."""
        from ...kernel.runtime import detect_all_runtimes
        
        all_runtimes = detect_all_runtimes(primary_only=False)
        return {
            "ok": True,
            "result": {
                "runtimes": [
                    {
                        "name": rt.name,
                        "display_name": rt.display_name,
                        "command": rt.command,
                        "available": rt.available,
                        "path": rt.path,
                        "capabilities": rt.capabilities,
                    }
                    for rt in all_runtimes
                ],
                "available": [rt.name for rt in all_runtimes if rt.available],
            },
        }

    @app.get("/api/v1/fs/list")
    async def fs_list(path: str = "~", show_hidden: bool = False) -> Dict[str, Any]:
        """List directory contents for path picker UI."""
        try:
            target = Path(path).expanduser().resolve()
            if not target.exists():
                return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"Path not found: {path}"}}
            if not target.is_dir():
                return {"ok": False, "error": {"code": "NOT_DIR", "message": f"Not a directory: {path}"}}
            
            items = []
            try:
                for entry in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    if not show_hidden and entry.name.startswith("."):
                        continue
                    items.append({
                        "name": entry.name,
                        "path": str(entry),
                        "is_dir": entry.is_dir(),
                    })
            except PermissionError:
                return {"ok": False, "error": {"code": "PERMISSION_DENIED", "message": f"Cannot read: {path}"}}
            
            return {
                "ok": True,
                "result": {
                    "path": str(target),
                    "parent": str(target.parent) if target.parent != target else None,
                    "items": items[:100],  # Limit to 100 items
                },
            }
        except Exception as e:
            return {"ok": False, "error": {"code": "ERROR", "message": str(e)}}

    @app.get("/api/v1/fs/recent")
    async def fs_recent() -> Dict[str, Any]:
        """Get recent/common directories for quick selection."""
        home = Path.home()
        suggestions = []
        
        # Home directory
        suggestions.append({"name": "Home", "path": str(home), "icon": "🏠"})
        
        # Common dev directories
        for name in ["dev", "projects", "code", "src", "workspace", "repos", "github", "work"]:
            p = home / name
            if p.exists() and p.is_dir():
                suggestions.append({"name": name.title(), "path": str(p), "icon": "📁"})
        
        # Desktop and Documents
        for name, icon in [("Desktop", "🖥️"), ("Documents", "📄"), ("Downloads", "⬇️")]:
            p = home / name
            if p.exists() and p.is_dir():
                suggestions.append({"name": name, "path": str(p), "icon": icon})
        
        # Current working directory
        cwd = Path.cwd()
        if cwd != home:
            suggestions.append({"name": "Current Dir", "path": str(cwd), "icon": "📍"})
        
        return {"ok": True, "result": {"suggestions": suggestions[:10]}}

    @app.get("/api/v1/groups")
    async def groups() -> Dict[str, Any]:
        return _daemon({"op": "groups"})

    @app.post("/api/v1/groups")
    async def group_create(req: CreateGroupRequest) -> Dict[str, Any]:
        return _daemon({"op": "group_create", "args": {"title": req.title, "topic": req.topic, "by": req.by}})

    @app.get("/api/v1/groups/{group_id}")
    async def group_show(group_id: str) -> Dict[str, Any]:
        return _daemon({"op": "group_show", "args": {"group_id": group_id}})

    @app.put("/api/v1/groups/{group_id}")
    async def group_update(group_id: str, req: GroupUpdateRequest) -> Dict[str, Any]:
        """Update group metadata (title/topic)."""
        patch: Dict[str, Any] = {}
        if req.title is not None:
            patch["title"] = req.title
        if req.topic is not None:
            patch["topic"] = req.topic
        if not patch:
            return {"ok": True, "result": {"message": "no changes"}}
        return _daemon({"op": "group_update", "args": {"group_id": group_id, "by": req.by, "patch": patch}})

    @app.delete("/api/v1/groups/{group_id}")
    async def group_delete(group_id: str, confirm: str = "", by: str = "user") -> Dict[str, Any]:
        """Delete a group (requires confirm=group_id)."""
        if confirm != group_id:
            raise HTTPException(
                status_code=400,
                detail={"code": "confirmation_required", "message": f"confirm must equal group_id: {group_id}"}
            )
        return _daemon({"op": "group_delete", "args": {"group_id": group_id, "by": by}})

    @app.get("/api/v1/groups/{group_id}/context")
    async def group_context(group_id: str) -> Dict[str, Any]:
        """Get full group context (vision/sketch/milestones/tasks/notes/refs/presence)."""
        return _daemon({"op": "context_get", "args": {"group_id": group_id}})

    @app.get("/api/v1/groups/{group_id}/project_md")
    async def project_md_get(group_id: str) -> Dict[str, Any]:
        """Get PROJECT.md content for the group's active scope root (repo root)."""
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        scopes = group.doc.get("scopes") if isinstance(group.doc.get("scopes"), list) else []
        active_scope_key = str(group.doc.get("active_scope_key") or "")

        project_root: Optional[str] = None
        for sc in scopes:
            if not isinstance(sc, dict):
                continue
            sk = str(sc.get("scope_key") or "")
            if sk == active_scope_key:
                project_root = str(sc.get("url") or "")
                break
        if not project_root:
            if scopes and isinstance(scopes[0], dict):
                project_root = str(scopes[0].get("url") or "")
        if not project_root:
            return {"ok": True, "result": {"found": False, "path": None, "content": None, "error": "No scope attached to group. Use 'cccc attach <path>' first."}}

        root = Path(project_root).expanduser()
        if not root.exists() or not root.is_dir():
            return {"ok": True, "result": {"found": False, "path": str(root / "PROJECT.md"), "content": None, "error": f"Project root does not exist: {root}"}}

        project_md_path = root / "PROJECT.md"
        if not project_md_path.exists():
            project_md_path_lower = root / "project.md"
            if project_md_path_lower.exists():
                project_md_path = project_md_path_lower
            else:
                return {"ok": True, "result": {"found": False, "path": str(project_md_path), "content": None, "error": f"PROJECT.md not found at {project_md_path}"}}

        try:
            content = project_md_path.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "result": {"found": True, "path": str(project_md_path), "content": content}}
        except Exception as e:
            return {"ok": True, "result": {"found": False, "path": str(project_md_path), "content": None, "error": f"Failed to read PROJECT.md: {e}"}}

    @app.put("/api/v1/groups/{group_id}/project_md")
    async def project_md_put(group_id: str, req: ProjectMdUpdateRequest) -> Dict[str, Any]:
        """Create or update PROJECT.md in the group's active scope root (repo root)."""
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        scopes = group.doc.get("scopes") if isinstance(group.doc.get("scopes"), list) else []
        active_scope_key = str(group.doc.get("active_scope_key") or "")

        project_root: Optional[str] = None
        for sc in scopes:
            if not isinstance(sc, dict):
                continue
            sk = str(sc.get("scope_key") or "")
            if sk == active_scope_key:
                project_root = str(sc.get("url") or "")
                break
        if not project_root:
            if scopes and isinstance(scopes[0], dict):
                project_root = str(scopes[0].get("url") or "")
        if not project_root:
            return {"ok": False, "error": {"code": "NO_SCOPE", "message": "No scope attached to group. Use 'cccc attach <path>' first."}}

        root = Path(project_root).expanduser()
        if not root.exists() or not root.is_dir():
            return {"ok": False, "error": {"code": "INVALID_SCOPE", "message": f"Project root does not exist: {root}"}}

        # Write to existing file if present; otherwise create PROJECT.md.
        project_md_path = root / "PROJECT.md"
        if not project_md_path.exists():
            project_md_path_lower = root / "project.md"
            if project_md_path_lower.exists():
                project_md_path = project_md_path_lower

        try:
            atomic_write_text(project_md_path, str(req.content or ""), encoding="utf-8")
            content = project_md_path.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "result": {"found": True, "path": str(project_md_path), "content": content}}
        except Exception as e:
            return {"ok": False, "error": {"code": "WRITE_FAILED", "message": f"Failed to write PROJECT.md: {e}"}}

    @app.post("/api/v1/groups/{group_id}/context")
    async def group_context_sync(group_id: str, request: Request) -> Dict[str, Any]:
        """Update group context via batch operations.
        
        Body: {"ops": [{"op": "vision.update", "vision": "..."}, ...], "by": "user"}
        
        Supported ops:
        - vision.update: {"op": "vision.update", "vision": "..."}
        - sketch.update: {"op": "sketch.update", "sketch": "..."}
        - milestone.create/update/complete/remove
        - task.create/update/delete
        - note.add/update/remove
        - reference.add/update/remove
        - presence.update/clear
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail={"code": "invalid_json", "message": "invalid JSON body"})
        
        ops = body.get("ops") if isinstance(body.get("ops"), list) else []
        by = str(body.get("by") or "user")
        dry_run = bool(body.get("dry_run", False))
        
        return _daemon({
            "op": "context_sync",
            "args": {"group_id": group_id, "ops": ops, "by": by, "dry_run": dry_run}
        })

    @app.get("/api/v1/groups/{group_id}/settings")
    async def group_settings_get(group_id: str) -> Dict[str, Any]:
        """Get group automation settings."""
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})
        
        automation = group.doc.get("automation") if isinstance(group.doc.get("automation"), dict) else {}
        delivery = group.doc.get("delivery") if isinstance(group.doc.get("delivery"), dict) else {}
        return {
            "ok": True,
            "result": {
                "settings": {
                    "nudge_after_seconds": int(automation.get("nudge_after_seconds", 300)),
                    "actor_idle_timeout_seconds": int(automation.get("actor_idle_timeout_seconds", 600)),
                    "keepalive_delay_seconds": int(automation.get("keepalive_delay_seconds", 120)),
                    "keepalive_max_per_actor": int(automation.get("keepalive_max_per_actor", 3)),
                    "silence_timeout_seconds": int(automation.get("silence_timeout_seconds", 600)),
                    "min_interval_seconds": int(delivery.get("min_interval_seconds", 60)),
                    "standup_interval_seconds": int(automation.get("standup_interval_seconds", 900)),
                }
            }
        }

    @app.put("/api/v1/groups/{group_id}/settings")
    async def group_settings_update(group_id: str, req: GroupSettingsRequest) -> Dict[str, Any]:
        """Update group automation settings."""
        patch: Dict[str, Any] = {}
        if req.nudge_after_seconds is not None:
            patch["nudge_after_seconds"] = max(0, req.nudge_after_seconds)
        if req.actor_idle_timeout_seconds is not None:
            patch["actor_idle_timeout_seconds"] = max(0, req.actor_idle_timeout_seconds)
        if req.keepalive_delay_seconds is not None:
            patch["keepalive_delay_seconds"] = max(0, req.keepalive_delay_seconds)
        if req.keepalive_max_per_actor is not None:
            patch["keepalive_max_per_actor"] = max(0, req.keepalive_max_per_actor)
        if req.silence_timeout_seconds is not None:
            patch["silence_timeout_seconds"] = max(0, req.silence_timeout_seconds)
        if req.min_interval_seconds is not None:
            patch["min_interval_seconds"] = max(0, req.min_interval_seconds)
        if req.standup_interval_seconds is not None:
            patch["standup_interval_seconds"] = max(0, req.standup_interval_seconds)
        
        if not patch:
            return {"ok": True, "result": {"message": "no changes"}}
        
        return _daemon({
            "op": "group_settings_update",
            "args": {"group_id": group_id, "patch": patch, "by": req.by}
        })

    @app.post("/api/v1/groups/{group_id}/attach")
    async def group_attach(group_id: str, req: AttachRequest) -> Dict[str, Any]:
        return _daemon({"op": "attach", "args": {"path": req.path, "by": req.by, "group_id": group_id}})

    @app.delete("/api/v1/groups/{group_id}/scopes/{scope_key}")
    async def group_detach_scope(group_id: str, scope_key: str, by: str = "user") -> Dict[str, Any]:
        """Detach a scope from a group."""
        return _daemon({"op": "group_detach_scope", "args": {"group_id": group_id, "scope_key": scope_key, "by": by}})

    @app.get("/api/v1/groups/{group_id}/ledger/tail")
    async def ledger_tail(group_id: str, lines: int = 50, with_read_status: bool = False) -> Dict[str, Any]:
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})
        raw_lines = read_last_lines(group.ledger_path, int(lines))
        events = []
        for ln in raw_lines:
            try:
                events.append(json.loads(ln))
            except Exception:
                continue
        
        # Optionally include read status for chat.message events
        if with_read_status:
            from ...kernel.inbox import get_read_status
            for ev in events:
                if ev.get("kind") == "chat.message" and ev.get("id"):
                    ev["_read_status"] = get_read_status(group, str(ev["id"]))
        
        return {"ok": True, "result": {"events": events}}

    @app.get("/api/v1/groups/{group_id}/ledger/search")
    async def ledger_search(
        group_id: str,
        q: str = "",
        kind: str = "all",
        by: str = "",
        before: str = "",
        after: str = "",
        limit: int = 50,
        with_read_status: bool = False,
    ) -> Dict[str, Any]:
        """Search and paginate messages in the ledger.
        
        Query params:
        - q: Text search query (case-insensitive substring match)
        - kind: Filter by message type (all/chat/notify)
        - by: Filter by sender (actor_id or "user")
        - before: Return messages before this event_id (backward pagination)
        - after: Return messages after this event_id (forward pagination)
        - limit: Maximum number of messages to return (default 50, max 200)
        - with_read_status: Include read status for each message
        """
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})
        
        from ...kernel.inbox import search_messages, get_read_status
        
        # Validate and clamp limit
        limit = max(1, min(200, limit))
        
        # Validate kind filter
        kind_filter = kind if kind in ("all", "chat", "notify") else "all"
        
        events, has_more = search_messages(
            group,
            query=q,
            kind_filter=kind_filter,  # type: ignore
            by_filter=by,
            before_id=before,
            after_id=after,
            limit=limit,
        )
        
        # Optionally include read status
        if with_read_status:
            for ev in events:
                if ev.get("kind") == "chat.message" and ev.get("id"):
                    ev["_read_status"] = get_read_status(group, str(ev["id"]))
        
        return {
            "ok": True,
            "result": {
                "events": events,
                "has_more": has_more,
                "count": len(events),
            }
        }

    @app.get("/api/v1/groups/{group_id}/events/{event_id}/read_status")
    async def event_read_status(group_id: str, event_id: str) -> Dict[str, Any]:
        """Get read status for a specific event (which actors have read it)."""
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})
        
        from ...kernel.inbox import get_read_status
        status = get_read_status(group, event_id)
        return {"ok": True, "result": {"event_id": event_id, "read_status": status}}

    @app.get("/api/v1/groups/{group_id}/ledger/stream")
    async def ledger_stream(group_id: str) -> StreamingResponse:
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})
        return StreamingResponse(_sse_tail(group.ledger_path), media_type="text/event-stream")

    @app.post("/api/v1/groups/{group_id}/send")
    async def send(group_id: str, req: SendRequest) -> Dict[str, Any]:
        return _daemon(
            {
                "op": "send",
                "args": {"group_id": group_id, "text": req.text, "by": req.by, "to": list(req.to), "path": req.path},
            }
        )

    @app.post("/api/v1/groups/{group_id}/reply")
    async def reply(group_id: str, req: ReplyRequest) -> Dict[str, Any]:
        return _daemon(
            {
                "op": "reply",
                "args": {"group_id": group_id, "text": req.text, "by": req.by, "to": list(req.to), "reply_to": req.reply_to},
            }
        )

    @app.get("/api/v1/groups/{group_id}/actors")
    async def actors(group_id: str, include_unread: bool = False) -> Dict[str, Any]:
        return _daemon({"op": "actor_list", "args": {"group_id": group_id, "include_unread": include_unread}})

    @app.post("/api/v1/groups/{group_id}/actors")
    async def actor_create(group_id: str, req: ActorCreateRequest) -> Dict[str, Any]:
        command = _normalize_command(req.command) or []
        return _daemon(
            {
                "op": "actor_add",
                "args": {
                    "group_id": group_id,
                    "actor_id": req.actor_id,
                    # Note: role is auto-determined by position
                    "runner": req.runner,
                    "runtime": req.runtime,
                    "title": req.title,
                    "command": command,
                    "env": dict(req.env),
                    "default_scope_key": req.default_scope_key,
                    "submit": req.submit,
                    "by": req.by,
                },
            }
        )

    @app.post("/api/v1/groups/{group_id}/actors/{actor_id}")
    async def actor_update(group_id: str, actor_id: str, req: ActorUpdateRequest) -> Dict[str, Any]:
        patch: Dict[str, Any] = {}
        # Note: role is ignored - auto-determined by position
        if req.title is not None:
            patch["title"] = req.title
        if req.command is not None:
            patch["command"] = _normalize_command(req.command)
        if req.env is not None:
            patch["env"] = dict(req.env)
        if req.default_scope_key is not None:
            patch["default_scope_key"] = req.default_scope_key
        if req.submit is not None:
            patch["submit"] = req.submit
        if req.runner is not None:
            patch["runner"] = req.runner
        if req.runtime is not None:
            patch["runtime"] = req.runtime
        if req.enabled is not None:
            patch["enabled"] = bool(req.enabled)
        return _daemon({"op": "actor_update", "args": {"group_id": group_id, "actor_id": actor_id, "patch": patch, "by": req.by}})

    @app.delete("/api/v1/groups/{group_id}/actors/{actor_id}")
    async def actor_delete(group_id: str, actor_id: str, by: str = "user") -> Dict[str, Any]:
        return _daemon({"op": "actor_remove", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/actors/{actor_id}/start")
    async def actor_start(group_id: str, actor_id: str, by: str = "user") -> Dict[str, Any]:
        return _daemon({"op": "actor_start", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/actors/{actor_id}/stop")
    async def actor_stop(group_id: str, actor_id: str, by: str = "user") -> Dict[str, Any]:
        return _daemon({"op": "actor_stop", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/actors/{actor_id}/restart")
    async def actor_restart(group_id: str, actor_id: str, by: str = "user") -> Dict[str, Any]:
        return _daemon({"op": "actor_restart", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})

    @app.websocket("/api/v1/groups/{group_id}/actors/{actor_id}/term")
    async def actor_terminal(websocket: WebSocket, group_id: str, actor_id: str) -> None:
        token = str(os.environ.get("CCCC_WEB_TOKEN") or "").strip()
        if token:
            provided = str(websocket.query_params.get("token") or "").strip()
            if provided != token:
                await websocket.close(code=4401)
                return

        await websocket.accept()

        group = load_group(group_id)
        if group is None:
            await websocket.send_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
            await websocket.close(code=1008)
            return

        home = ensure_home()
        sock_path = home / "daemon" / "ccccd.sock"
        try:
            reader, writer = await asyncio.open_unix_connection(str(sock_path))
        except Exception:
            await websocket.send_json({"ok": False, "error": {"code": "daemon_unavailable", "message": "ccccd unavailable"}})
            await websocket.close(code=1011)
            return

        try:
            req = {"op": "term_attach", "args": {"group_id": group_id, "actor_id": actor_id}}
            writer.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
            line = await reader.readline()
            try:
                resp = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                resp = {}
            if not isinstance(resp, dict) or not resp.get("ok"):
                err = resp.get("error") if isinstance(resp.get("error"), dict) else {"code": "term_attach_failed", "message": "term attach failed"}
                await websocket.send_json({"ok": False, "error": err})
                await websocket.close(code=1008)
                return

            async def _pump_out() -> None:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    await websocket.send_bytes(data)

            async def _pump_in() -> None:
                while True:
                    raw = await websocket.receive_text()
                    if not raw:
                        continue
                    obj: Any = None
                    try:
                        obj = json.loads(raw)
                    except Exception:
                        obj = None
                    if not isinstance(obj, dict):
                        continue
                    t = str(obj.get("t") or "")
                    if t == "i":
                        data = str(obj.get("d") or "")
                        if data:
                            writer.write(data.encode("utf-8", errors="replace"))
                            await writer.drain()
                        continue
                    if t == "r":
                        try:
                            cols = int(obj.get("c") or 0)
                            rows = int(obj.get("r") or 0)
                        except Exception:
                            cols = 0
                            rows = 0
                        if cols > 0 and rows > 0:
                            await asyncio.to_thread(
                                call_daemon,
                                {"op": "term_resize", "args": {"group_id": group_id, "actor_id": actor_id, "cols": cols, "rows": rows}},
                            )
                        continue

            out_task = asyncio.create_task(_pump_out())
            in_task = asyncio.create_task(_pump_in())
            try:
                done, pending = await asyncio.wait({out_task, in_task}, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    try:
                        _ = t.result()
                    except Exception:
                        pass
                for t in pending:
                    t.cancel()
                try:
                    await asyncio.gather(*pending, return_exceptions=True)
                except Exception:
                    pass
            except WebSocketDisconnect:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @app.get("/api/v1/groups/{group_id}/inbox/{actor_id}")
    async def inbox_list(group_id: str, actor_id: str, by: str = "user", limit: int = 50) -> Dict[str, Any]:
        return _daemon({"op": "inbox_list", "args": {"group_id": group_id, "actor_id": actor_id, "by": by, "limit": int(limit)}})

    @app.post("/api/v1/groups/{group_id}/inbox/{actor_id}/read")
    async def inbox_mark_read(group_id: str, actor_id: str, req: InboxReadRequest) -> Dict[str, Any]:
        return _daemon(
            {"op": "inbox_mark_read", "args": {"group_id": group_id, "actor_id": actor_id, "event_id": req.event_id, "by": req.by}}
        )

    @app.post("/api/v1/groups/{group_id}/start")
    async def group_start(group_id: str, by: str = "user") -> Dict[str, Any]:
        return _daemon({"op": "group_start", "args": {"group_id": group_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/stop")
    async def group_stop(group_id: str, by: str = "user") -> Dict[str, Any]:
        return _daemon({"op": "group_stop", "args": {"group_id": group_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/state")
    async def group_set_state(group_id: str, state: str, by: str = "user") -> Dict[str, Any]:
        """Set group state (active/idle/paused) to control automation behavior."""
        return _daemon({"op": "group_set_state", "args": {"group_id": group_id, "state": state, "by": by}})

    # =========================================================================
    # IM Bridge API
    # =========================================================================

    @app.get("/api/im/status")
    async def im_status(group_id: str) -> Dict[str, Any]:
        """Get IM bridge status for a group."""
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        im_config = group.doc.get("im", {})
        platform = im_config.get("platform") if im_config else None

        # Check if running
        pid_path = group.path / "state" / "im_bridge.pid"
        pid = None
        running = False
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
                # Reap if this process started the bridge and it already exited.
                try:
                    waited_pid, _ = os.waitpid(pid, os.WNOHANG)
                    if waited_pid == pid:
                        pid = None
                        pid_path.unlink(missing_ok=True)
                    else:
                        os.kill(pid, 0)  # Check if process exists
                        running = True
                except (AttributeError, ChildProcessError):
                    os.kill(pid, 0)  # Check if process exists
                    running = True
            except (ValueError, ProcessLookupError, PermissionError):
                pid = None

        # Get subscriber count
        subscribers_path = group.path / "state" / "im_subscribers.json"
        subscriber_count = 0
        if subscribers_path.exists():
            try:
                subs = json.loads(subscribers_path.read_text(encoding="utf-8"))
                subscriber_count = sum(1 for s in subs.values() if isinstance(s, dict) and s.get("subscribed"))
            except Exception:
                pass

        return {
            "ok": True,
            "result": {
                "group_id": group_id,
                "configured": bool(im_config),
                "platform": platform,
                "running": running,
                "pid": pid,
                "subscribers": subscriber_count,
            }
        }

    @app.get("/api/im/config")
    async def im_config(group_id: str) -> Dict[str, Any]:
        """Get IM bridge configuration for a group."""
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        im_cfg = group.doc.get("im")
        return {"ok": True, "result": {"group_id": group_id, "im": im_cfg}}

    @app.post("/api/im/set")
    async def im_set(req: IMSetRequest) -> Dict[str, Any]:
        """Set IM bridge configuration for a group."""
        group = load_group(req.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {req.group_id}"})

        # Build IM config.
        # Note: Web UI historically used bot_token_env/app_token_env as a single input.
        # We accept either an env var name (e.g. TELEGRAM_BOT_TOKEN) or a raw token value.
        im_cfg: Dict[str, Any] = {"platform": req.platform}

        platform = str(req.platform or "").strip().lower()
        token_hint = str(req.bot_token_env or req.token_env or "").strip()

        if platform == "slack":
            if token_hint:
                if _is_env_var_name(token_hint):
                    im_cfg["bot_token_env"] = token_hint
                else:
                    im_cfg["bot_token"] = token_hint

            app_hint = str(req.app_token_env or "").strip()
            if app_hint:
                if _is_env_var_name(app_hint):
                    im_cfg["app_token_env"] = app_hint
                else:
                    im_cfg["app_token"] = app_hint

            # Backward compat: if only token_env provided, treat as bot_token_env.
            if req.token_env and not req.bot_token_env and _is_env_var_name(req.token_env):
                im_cfg.setdefault("bot_token_env", str(req.token_env).strip())

            if req.token:
                im_cfg.setdefault("bot_token", str(req.token).strip())
        else:
            # Telegram/Discord: single token.
            if token_hint:
                if _is_env_var_name(token_hint):
                    im_cfg["token_env"] = token_hint
                else:
                    im_cfg["token"] = token_hint

            if req.token:
                im_cfg["token"] = str(req.token).strip()

        # Update group doc and save
        group.doc["im"] = im_cfg
        group.save()

        return {"ok": True, "result": {"group_id": req.group_id, "im": im_cfg}}

    @app.post("/api/im/unset")
    async def im_unset(req: IMActionRequest) -> Dict[str, Any]:
        """Remove IM bridge configuration from a group."""
        group = load_group(req.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {req.group_id}"})

        if "im" in group.doc:
            del group.doc["im"]
            group.save()

        return {"ok": True, "result": {"group_id": req.group_id, "im": None}}

    @app.post("/api/im/start")
    async def im_start(req: IMActionRequest) -> Dict[str, Any]:
        """Start IM bridge for a group."""
        import subprocess

        group = load_group(req.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {req.group_id}"})

        # Check if already running
        pid_path = group.path / "state" / "im_bridge.pid"
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
                # If it's our child and already exited, reap and allow restart.
                try:
                    waited_pid, _ = os.waitpid(pid, os.WNOHANG)
                    if waited_pid == pid:
                        pid_path.unlink(missing_ok=True)
                    else:
                        os.kill(pid, 0)
                        return {"ok": False, "error": {"code": "already_running", "message": f"bridge already running (pid={pid})"}}
                except (AttributeError, ChildProcessError):
                    os.kill(pid, 0)
                    return {"ok": False, "error": {"code": "already_running", "message": f"bridge already running (pid={pid})"}}
            except (ValueError, ProcessLookupError, PermissionError):
                pass

        # Check IM config
        im_cfg = group.doc.get("im", {})
        if not im_cfg:
            return {"ok": False, "error": {"code": "no_im_config", "message": "no IM configuration"}}

        platform = im_cfg.get("platform", "telegram")

        # Prepare environment
        env = os.environ.copy()
        token_env = im_cfg.get("token_env")
        token = im_cfg.get("token")
        if token and token_env:
            env[token_env] = token
        elif token:
            default_env = {"telegram": "TELEGRAM_BOT_TOKEN", "slack": "SLACK_BOT_TOKEN", "discord": "DISCORD_BOT_TOKEN"}
            env[default_env.get(platform, "BOT_TOKEN")] = token

        # Start bridge as subprocess
        state_dir = group.path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        log_path = state_dir / "im_bridge.log"

        try:
            import sys
            log_file = log_path.open("a", encoding="utf-8")
            proc = subprocess.Popen(
                [sys.executable, "-m", "cccc.ports.im.bridge", req.group_id, platform],
                env=env,
                stdout=log_file,
                stderr=log_file,
                start_new_session=True,
            )
            # If the process exits immediately (common for missing token/deps), report failure.
            await asyncio.sleep(0.25)
            exit_code = proc.poll()
            if exit_code is not None:
                try:
                    proc.wait(timeout=0.1)
                except Exception:
                    pass
                return {
                    "ok": False,
                    "error": {
                        "code": "bridge_exited",
                        "message": f"bridge exited early (code={exit_code}). Check log: {log_path}",
                    },
                }

            pid_path.write_text(str(proc.pid), encoding="utf-8")
            return {"ok": True, "result": {"group_id": req.group_id, "platform": platform, "pid": proc.pid}}
        except Exception as e:
            return {"ok": False, "error": {"code": "start_failed", "message": str(e)}}

    @app.post("/api/im/stop")
    async def im_stop(req: IMActionRequest) -> Dict[str, Any]:
        """Stop IM bridge for a group."""
        import signal as sig

        group = load_group(req.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {req.group_id}"})

        stopped = 0
        pid_path = group.path / "state" / "im_bridge.pid"

        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
                try:
                    os.killpg(os.getpgid(pid), sig.SIGTERM)
                except Exception:
                    try:
                        os.kill(pid, sig.SIGTERM)
                    except Exception:
                        pass
                stopped += 1
            except Exception:
                pass
            try:
                pid_path.unlink(missing_ok=True)
            except Exception:
                pass

        return {"ok": True, "result": {"group_id": req.group_id, "stopped": stopped}}

    return app
