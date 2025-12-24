from __future__ import annotations

import asyncio
import json
import os
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


class ActorCreateRequest(BaseModel):
    actor_id: str
    role: str = Field(default="peer")
    title: str = Field(default="")
    command: Union[str, list[str]] = Field(default="")
    env: Dict[str, str] = Field(default_factory=dict)
    default_scope_key: str = Field(default="")
    submit: Literal["enter", "newline", "none"] = Field(default="enter")
    by: str = Field(default="user")


class ActorUpdateRequest(BaseModel):
    by: str = Field(default="user")
    role: Optional[str] = None
    title: Optional[str] = None
    command: Optional[Union[str, list[str]]] = None
    env: Optional[Dict[str, str]] = None
    default_scope_key: Optional[str] = None
    submit: Optional[Literal["enter", "newline", "none"]] = None
    enabled: Optional[bool] = None


class InboxReadRequest(BaseModel):
    event_id: str
    by: str = Field(default="user")


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

    @app.get("/api/v1/groups")
    async def groups() -> Dict[str, Any]:
        return _daemon({"op": "groups"})

    @app.post("/api/v1/groups")
    async def group_create(req: CreateGroupRequest) -> Dict[str, Any]:
        return _daemon({"op": "group_create", "args": {"title": req.title, "topic": req.topic, "by": req.by}})

    @app.get("/api/v1/groups/{group_id}")
    async def group_show(group_id: str) -> Dict[str, Any]:
        return _daemon({"op": "group_show", "args": {"group_id": group_id}})

    @app.post("/api/v1/groups/{group_id}/attach")
    async def group_attach(group_id: str, req: AttachRequest) -> Dict[str, Any]:
        return _daemon({"op": "attach", "args": {"path": req.path, "by": req.by, "group_id": group_id}})

    @app.get("/api/v1/groups/{group_id}/ledger/tail")
    async def ledger_tail(group_id: str, lines: int = 50) -> Dict[str, Any]:
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
        return {"ok": True, "result": {"events": events}}

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

    @app.get("/api/v1/groups/{group_id}/actors")
    async def actors(group_id: str) -> Dict[str, Any]:
        return _daemon({"op": "actor_list", "args": {"group_id": group_id}})

    @app.post("/api/v1/groups/{group_id}/actors")
    async def actor_create(group_id: str, req: ActorCreateRequest) -> Dict[str, Any]:
        command = _normalize_command(req.command) or []
        return _daemon(
            {
                "op": "actor_add",
                "args": {
                    "group_id": group_id,
                    "actor_id": req.actor_id,
                    "role": req.role,
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
        if req.role is not None:
            patch["role"] = req.role
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

    return app
