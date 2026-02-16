from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import shlex
import signal
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Literal, Optional, Union

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.concurrency import run_in_threadpool

from ... import __version__
from ...contracts.v1.actor import ActorSubmit, AgentRuntime, RunnerKind
from ...daemon.server import call_daemon, get_daemon_endpoint
from ...kernel.blobs import store_blob_bytes, resolve_blob_attachment_path
from ...kernel.group import load_group
from ...kernel.ledger import read_last_lines
from ...kernel.scope import detect_scope
from ...kernel.prompt_files import (
    DEFAULT_PREAMBLE_BODY,
    DEFAULT_STANDUP_TEMPLATE,
    HELP_FILENAME,
    PREAMBLE_FILENAME,
    STANDUP_FILENAME,
    delete_repo_prompt_file,
    load_builtin_help_markdown,
    read_repo_prompt_file,
    resolve_active_scope_root,
    write_repo_prompt_file,
)
from ...kernel.group_template import parse_group_template
from ...paths import ensure_home
from ...util.obslog import setup_root_json_logging
from ...util.conv import coerce_bool
from ...util.fs import atomic_write_text

logger = logging.getLogger("cccc.web")
_WEB_LOG_FH: Optional[Any] = None


def _default_runner_kind() -> str:
    try:
        from ...runners import pty as pty_runner

        return "pty" if bool(getattr(pty_runner, "PTY_SUPPORTED", True)) else "headless"
    except Exception:
        return "headless"


def _apply_web_logging(*, home: Path, level: str) -> None:
    global _WEB_LOG_FH
    try:
        d = home / "daemon"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "cccc-web.log"
        if _WEB_LOG_FH is None:
            _WEB_LOG_FH = p.open("a", encoding="utf-8")
        setup_root_json_logging(component="web", level=level, stream=_WEB_LOG_FH, force=True)
    except Exception:
        # Fall back to stderr if file logging isn't possible.
        try:
            setup_root_json_logging(component="web", level=level, force=True)
        except Exception:
            pass


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
    priority: Literal["normal", "attention"] = "normal"
    src_group_id: str = Field(default="")
    src_event_id: str = Field(default="")


class SendCrossGroupRequest(BaseModel):
    text: str
    by: str = Field(default="user")
    dst_group_id: str
    to: list[str] = Field(default_factory=list)
    priority: Literal["normal", "attention"] = "normal"


class ReplyRequest(BaseModel):
    text: str
    by: str = Field(default="user")
    to: list[str] = Field(default_factory=list)
    reply_to: str
    priority: Literal["normal", "attention"] = "normal"


class DebugClearLogsRequest(BaseModel):
    component: str
    group_id: str = Field(default="")
    by: str = Field(default="user")

class GroupTemplatePreviewRequest(BaseModel):
    template: str = Field(default="")
    by: str = Field(default="user")


class NewsAgentConfigRequest(BaseModel):
    group_id: str
    interests: str = "AI,科技,编程"
    schedule: str = "8,11,14,17,20"


class MarketAgentConfigRequest(BaseModel):
    group_id: str
    interests: str = "股市,美股,A股,港股,宏观,财报"
    schedule: str = "9,12,15,18,22"


class AILongAgentConfigRequest(BaseModel):
    group_id: str
    interests: str = "CCCC,框架,多Agent,协作,消息总线,语音播报"
    schedule: str = "10,16,21"


class AILongPreloadRequest(BaseModel):
    group_id: str
    interests: str = "CCCC,框架,多Agent,协作,消息总线,语音播报"
    force: bool = False
    script_key: str = ""
    topic: str = ""


class HorrorAgentConfigRequest(BaseModel):
    group_id: str
    interests: str = "深夜,公寓,都市传说,悬疑,心理惊悚"
    schedule: str = "21,23,1"


class TTSSynthesizeRequest(BaseModel):
    text: str = Field(default="")
    style: str = Field(default="general")
    lang: str = Field(default="zh-CN")
    # Browser engine is client-side; server endpoint currently proxies GPT-SoVITS.
    engine: Literal["gpt_sovits_v4"] = "gpt_sovits_v4"
    rate: float = Field(default=1.0)
    pitch: float = Field(default=1.0)
    volume: float = Field(default=1.0)


WEB_MAX_FILE_MB = 20
WEB_MAX_FILE_BYTES = WEB_MAX_FILE_MB * 1024 * 1024
WEB_MAX_TEMPLATE_BYTES = 2 * 1024 * 1024  # safety bound for template uploads


class ActorCreateRequest(BaseModel):
    actor_id: str
    # Note: role is auto-determined by position (first enabled = foreman)
    runner: RunnerKind = Field(default_factory=_default_runner_kind)
    runtime: AgentRuntime = Field(default="codex")
    title: str = Field(default="")
    command: Union[str, list[str]] = Field(default="")
    env: Dict[str, str] = Field(default_factory=dict)
    # Write-only runtime-only secrets (stored under CCCC_HOME/state; never persisted into ledger).
    # Values are never returned by the daemon; only keys can be listed via the dedicated endpoints.
    env_private: Optional[Dict[str, str]] = None
    default_scope_key: str = Field(default="")
    submit: ActorSubmit = Field(default="enter")
    by: str = Field(default="user")


class ActorUpdateRequest(BaseModel):
    by: str = Field(default="user")
    # Note: role is ignored - auto-determined by position
    title: Optional[str] = None
    command: Optional[Union[str, list[str]]] = None
    env: Optional[Dict[str, str]] = None
    default_scope_key: Optional[str] = None
    submit: Optional[ActorSubmit] = None
    runner: Optional[RunnerKind] = None
    runtime: Optional[AgentRuntime] = None
    enabled: Optional[bool] = None


class InboxReadRequest(BaseModel):
    event_id: str
    by: str = Field(default="user")


class UserAckRequest(BaseModel):
    by: str = Field(default="user")

class ProjectMdUpdateRequest(BaseModel):
    content: str = Field(default="")
    by: str = Field(default="user")

class RepoPromptUpdateRequest(BaseModel):
    content: str = Field(default="")
    by: str = Field(default="user")


class GroupUpdateRequest(BaseModel):
    title: Optional[str] = None
    topic: Optional[str] = None
    by: str = Field(default="user")


class GroupSettingsRequest(BaseModel):
    default_send_to: Optional[Literal["foreman", "broadcast"]] = None
    nudge_after_seconds: Optional[int] = None
    actor_idle_timeout_seconds: Optional[int] = None
    keepalive_delay_seconds: Optional[int] = None
    keepalive_max_per_actor: Optional[int] = None
    silence_timeout_seconds: Optional[int] = None
    help_nudge_interval_seconds: Optional[int] = None
    help_nudge_min_messages: Optional[int] = None
    min_interval_seconds: Optional[int] = None  # delivery throttle
    standup_interval_seconds: Optional[int] = None  # periodic review interval
    auto_mark_on_delivery: Optional[bool] = None  # auto-mark messages as read after delivery

    # Terminal transcript (group-scoped policy)
    terminal_transcript_visibility: Optional[Literal["off", "foreman", "all"]] = None
    terminal_transcript_notify_tail: Optional[bool] = None
    terminal_transcript_notify_lines: Optional[int] = None
    by: str = Field(default="user")

class ObservabilityUpdateRequest(BaseModel):
    by: str = Field(default="user")
    developer_mode: Optional[bool] = None
    log_level: Optional[str] = None
    terminal_transcript_per_actor_bytes: Optional[int] = None
    terminal_ui_scrollback_lines: Optional[int] = None


class GroupDeleteRequest(BaseModel):
    confirm: str = Field(default="")
    by: str = Field(default="user")


class IMSetRequest(BaseModel):
    group_id: str
    platform: Literal["telegram", "slack", "discord", "feishu", "dingtalk"]
    # Legacy single token field (backward compat for telegram/discord)
    token_env: str = ""
    token: str = ""
    # Dual token fields for Slack
    bot_token_env: str = ""  # xoxb- for outbound (Web API)
    app_token_env: str = ""  # xapp- for inbound (Socket Mode)
    # Feishu fields
    feishu_domain: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    # DingTalk fields
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_robot_code: str = ""


class IMActionRequest(BaseModel):
    group_id: str


def _is_env_var_name(value: str) -> bool:
    # Shell-friendly env var name (portable).
    return bool(re.fullmatch(r"[A-Z_][A-Z0-9_]*", (value or "").strip()))


def _normalize_feishu_domain(value: str) -> str:
    """
    Normalize the Feishu/Lark OpenAPI domain.

    Feishu (CN): https://open.feishu.cn
    Lark (Global): https://open.larkoffice.com
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    v = raw.strip().lower().rstrip("/")
    if v.endswith("/open-apis"):
        v = v[: -len("/open-apis")].rstrip("/")
    if v in ("feishu", "cn", "china", "open.feishu.cn", "https://open.feishu.cn"):
        return "https://open.feishu.cn"
    if v in (
        "lark",
        "global",
        "intl",
        "international",
        "open.larkoffice.com",
        "https://open.larkoffice.com",
        # Historical alias used in some SDKs/docs.
        "open.larksuite.com",
        "https://open.larksuite.com",
    ):
        return "https://open.larkoffice.com"
    if not (v.startswith("http://") or v.startswith("https://")):
        v = "https://" + v
    # Allow only known domains in the Web UI to avoid surprising network targets.
    if v in ("https://open.feishu.cn", "https://open.larkoffice.com", "https://open.larksuite.com"):
        if v == "https://open.larksuite.com":
            return "https://open.larkoffice.com"
        return v
    return ""


def _normalize_command(cmd: Union[str, list[str], None]) -> Optional[list[str]]:
    if cmd is None:
        return None
    if isinstance(cmd, str):
        s = cmd.strip()
        return shlex.split(s, posix=(os.name != "nt")) if s else []
    if isinstance(cmd, list) and all(isinstance(x, str) for x in cmd):
        return [str(x).strip() for x in cmd if str(x).strip()]
    raise HTTPException(status_code=400, detail={"code": "invalid_command", "message": "invalid command"})


def _is_truthy_env(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _web_mode() -> Literal["normal", "exhibit"]:
    """Return the web server mode.

    - normal: read/write control plane (default)
    - exhibit: read-only "public console" mode
    """
    mode = str(os.environ.get("CCCC_WEB_MODE") or "").strip().lower()
    if mode in ("exhibit", "readonly", "read-only", "ro"):
        return "exhibit"
    if _is_truthy_env(str(os.environ.get("CCCC_WEB_READONLY") or "")):
        return "exhibit"
    return "normal"


def _require_token_if_configured(request: Request) -> Optional[JSONResponse]:
    token = str(os.environ.get("CCCC_WEB_TOKEN") or "").strip()
    if not token:
        return None

    # Skip auth for static UI assets (frontend code is public, only protect API)
    path = str(request.url.path or "")
    if path.startswith("/ui/") or path == "/ui":
        return None

    auth = str(request.headers.get("authorization") or "").strip()
    if auth == f"Bearer {token}":
        return None

    cookie = str(request.cookies.get("cccc_web_token") or "").strip()
    if cookie == token:
        return None

    q = str(request.query_params.get("token") or "").strip()
    if q == token:
        return None

    return JSONResponse(
        status_code=401,
        content={"ok": False, "error": {"code": "unauthorized", "message": "missing/invalid token", "details": {}}},
    )


async def _daemon(req: Dict[str, Any]) -> Dict[str, Any]:
    resp = await run_in_threadpool(call_daemon, req)
    if not resp.get("ok") and isinstance(resp.get("error"), dict) and resp["error"].get("code") == "daemon_unavailable":
        raise HTTPException(status_code=503, detail={"code": "daemon_unavailable", "message": "ccccd unavailable"})
    return resp


def _pid_alive(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _best_effort_terminate_pid(pid: int) -> None:
    pid = int(pid or 0)
    if pid <= 0:
        return
    if os.name == "nt":
        try:
            import subprocess as sp

            sp.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=sp.DEVNULL,
                stderr=sp.DEVNULL,
                check=False,
                timeout=3.0,
            )
            return
        except Exception:
            pass
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _background_python_executable(executable: str) -> str:
    """Prefer pythonw.exe on Windows to avoid transient console popups."""
    exe = str(executable or "").strip()
    if os.name != "nt" or not exe:
        return exe
    try:
        p = Path(exe)
        pyw = p.with_name("pythonw.exe")
        if pyw.exists():
            return str(pyw)
    except Exception:
        pass
    return exe


def _proc_cccc_home(pid: int) -> Optional[Path]:
    """Best-effort CCCC_HOME lookup on Linux /proc."""
    if int(pid or 0) <= 0:
        return None
    try:
        raw = (Path("/proc") / str(int(pid)) / "environ").read_bytes()
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
    try:
        return (Path.home() / ".cccc").resolve()
    except Exception:
        return None


def _find_group_module_pids(
    *,
    home: Path,
    module: str,
    group_id: str,
    command_contains: Optional[list[str]] = None,
) -> list[int]:
    """Find pids by python module + group id. Works on Linux (/proc) and Windows (CIM)."""
    mod = str(module or "").strip()
    gid = str(group_id or "").strip()
    if not mod or not gid:
        return []
    tokens = [str(t or "").strip() for t in (command_contains or []) if str(t or "").strip()]

    found: set[int] = set()
    proc = Path("/proc")
    if proc.exists():
        for d in proc.iterdir():
            if not d.is_dir() or not d.name.isdigit():
                continue
            try:
                pid = int(d.name)
            except Exception:
                continue
            try:
                cmdline = (d / "cmdline").read_bytes().decode("utf-8", "ignore")
            except Exception:
                continue
            if mod not in cmdline or gid not in cmdline:
                continue
            if tokens and any(tok not in cmdline for tok in tokens):
                continue
            ph = _proc_cccc_home(pid)
            if ph is None:
                continue
            try:
                if ph != home.resolve():
                    continue
            except Exception:
                continue
            found.add(pid)
        return sorted(found)

    if os.name != "nt":
        return sorted(found)

    try:
        import subprocess as sp

        run_kwargs: Dict[str, Any] = {}
        if os.name == "nt":
            flags = int(getattr(sp, "CREATE_NO_WINDOW", 0))
            if flags:
                run_kwargs["creationflags"] = flags

        ps = sp.run(
            [
                "powershell",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='SilentlyContinue';"
                "Get-CimInstance Win32_Process | "
                "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3.0,
            **run_kwargs,
        )
        if ps.returncode != 0:
            return sorted(found)
        raw = str(ps.stdout or "").strip()
        if not raw:
            return sorted(found)
        doc = json.loads(raw)
        rows = doc if isinstance(doc, list) else [doc]
        for row in rows:
            if not isinstance(row, dict):
                continue
            cmdline = str(row.get("CommandLine") or "")
            if not cmdline or mod not in cmdline or gid not in cmdline:
                continue
            if tokens and any(tok not in cmdline for tok in tokens):
                continue
            try:
                pid = int(row.get("ProcessId") or 0)
            except Exception:
                pid = 0
            if pid > 0:
                found.add(pid)
    except Exception:
        pass
    return sorted(found)


def _parse_env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _tts_text_lang(lang: str) -> str:
    normalized = str(lang or "").strip().lower()
    if normalized.startswith("zh"):
        return "zh"
    if normalized.startswith("ja"):
        return "ja"
    if normalized.startswith("en"):
        return "en"
    if normalized.startswith("ko"):
        return "ko"
    return "auto"


def _extract_audio_bytes_from_json(payload: Dict[str, Any]) -> Optional[bytes]:
    b64_candidates: list[str] = []
    for key in ("audio", "audio_base64", "data", "wav", "wav_base64"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            b64_candidates.append(value.strip())
    for item in b64_candidates:
        try:
            v = item.split(",", 1)[1] if item.startswith("data:") and "," in item else item
            return base64.b64decode(v, validate=False)
        except Exception:
            continue
    return None


def _probe_tcp_endpoint(endpoint: str, timeout_sec: float = 0.5) -> bool:
    raw = str(endpoint or "").strip()
    if not raw:
        return False
    try:
        parsed = urllib.parse.urlparse(raw if "://" in raw else f"http://{raw}")
        host = str(parsed.hostname or "").strip()
        if not host:
            return False
        port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
        if port <= 0:
            return False
        with socket.create_connection((host, port), timeout=max(0.2, min(3.0, float(timeout_sec)))):
            return True
    except Exception:
        return False


def _gpt_sovits_payload(req: TTSSynthesizeRequest) -> Dict[str, Any]:
    speed_default = _parse_env_float("CCCC_TTS_GPTSOVITS_SPEED", 1.0)
    speed_horror = _parse_env_float("CCCC_TTS_GPTSOVITS_SPEED_HORROR", 1.04)
    speed = speed_horror if str(req.style or "").strip().lower() == "horror" else speed_default
    # Apply front-end expressive rate as a light multiplier, but keep it stable.
    speed *= max(0.82, min(1.24, float(req.rate or 1.0)))

    payload: Dict[str, Any] = {
        "text": str(req.text or "").strip(),
        "text_lang": _tts_text_lang(req.lang),
        "media_type": "wav",
        "streaming_mode": False,
        "speed_factor": max(0.75, min(1.35, speed)),
    }

    ref_audio = str(os.environ.get("CCCC_TTS_GPTSOVITS_REF_AUDIO") or "").strip()
    if not ref_audio:
        candidates = [
            Path.cwd().parent / "gpt-sovits" / "reference" / "ref.wav",
            Path.home() / "dev" / "gpt-sovits" / "reference" / "ref.wav",
            Path.home() / "gpt-sovits" / "reference" / "ref.wav",
        ]
        for p in candidates:
            try:
                if p.is_file():
                    ref_audio = str(p)
                    break
            except Exception:
                continue
    prompt_text = str(os.environ.get("CCCC_TTS_GPTSOVITS_PROMPT_TEXT") or "").strip()
    prompt_lang = str(os.environ.get("CCCC_TTS_GPTSOVITS_PROMPT_LANG") or "").strip() or "zh"
    top_k = _parse_env_float("CCCC_TTS_GPTSOVITS_TOP_K", 20.0)
    top_p = _parse_env_float("CCCC_TTS_GPTSOVITS_TOP_P", 0.85)
    temperature = _parse_env_float("CCCC_TTS_GPTSOVITS_TEMPERATURE", 0.65)

    if ref_audio:
        payload["ref_audio_path"] = ref_audio
        # GPT-SoVITS api_v2 requires prompt_lang even when prompt_text is empty.
        payload["prompt_lang"] = prompt_lang
    if prompt_text:
        payload["prompt_text"] = prompt_text

    payload["top_k"] = int(max(1, min(200, round(top_k))))
    payload["top_p"] = max(0.1, min(1.0, top_p))
    payload["temperature"] = max(0.1, min(1.2, temperature))

    split_method = str(os.environ.get("CCCC_TTS_GPTSOVITS_SPLIT_METHOD") or "").strip()
    if split_method:
        payload["text_split_method"] = split_method
    return payload


def _synthesize_via_gpt_sovits(
    req: TTSSynthesizeRequest,
    *,
    timeout_override: Optional[float] = None,
) -> tuple[bytes, str]:
    endpoint = str(os.environ.get("CCCC_TTS_GPTSOVITS_URL") or "http://127.0.0.1:9880/tts").strip()
    timeout_sec = float(timeout_override) if timeout_override is not None else _parse_env_float("CCCC_TTS_GPTSOVITS_TIMEOUT_SEC", 40.0)
    if not endpoint:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "tts_unconfigured",
                "message": "GPT-SoVITS endpoint is not configured",
                "details": {"env": "CCCC_TTS_GPTSOVITS_URL"},
            },
        )
    body = json.dumps(_gpt_sovits_payload(req), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "audio/*,application/octet-stream,application/json",
            "Connection": "close",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(3.0, timeout_sec)) as resp:
            content_type = str(resp.headers.get("Content-Type") or "application/octet-stream").lower()
            data = resp.read()
    except urllib.error.HTTPError as e:
        err_text = ""
        try:
            err_text = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            err_text = ""
        raise HTTPException(
            status_code=502,
            detail={
                "code": "tts_upstream_http_error",
                "message": f"GPT-SoVITS returned HTTP {int(getattr(e, 'code', 502) or 502)}",
                "details": {"endpoint": endpoint, "body": err_text},
            },
        ) from e
    except urllib.error.URLError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "tts_upstream_unreachable",
                "message": f"cannot connect GPT-SoVITS endpoint: {endpoint}",
                "details": {"reason": str(e.reason)},
            },
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "tts_proxy_error",
                "message": f"TTS proxy failed: {e}",
                "details": {"endpoint": endpoint},
            },
        ) from e

    if not data:
        raise HTTPException(
            status_code=502,
            detail={"code": "tts_empty_audio", "message": "GPT-SoVITS returned empty audio", "details": {"endpoint": endpoint}},
        )

    if "application/json" in content_type or data[:1] in (b"{", b"["):
        try:
            doc = json.loads(data.decode("utf-8", "ignore"))
        except Exception:
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        audio_bytes = _extract_audio_bytes_from_json(doc)
        if audio_bytes:
            return audio_bytes, "audio/wav"
        err_msg = str(doc.get("message") or doc.get("error") or "").strip()
        raise HTTPException(
            status_code=502,
            detail={
                "code": "tts_invalid_json_payload",
                "message": err_msg or "GPT-SoVITS JSON response does not contain audio",
                "details": {"endpoint": endpoint},
            },
        )

    if "audio/" not in content_type:
        content_type = "audio/wav"
    return data, content_type


def _split_ai_long_chunks(text: str, max_chars: int = 90) -> list[str]:
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return []
    if len(raw) <= max_chars:
        return [raw]

    # Prefer sentence boundaries first.
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?；;])\s*", raw) if p.strip()]
    if not parts:
        parts = [raw]

    chunks: list[str] = []
    buf = ""

    def _push(value: str) -> None:
        t = str(value or "").strip()
        if t:
            chunks.append(t)

    for part in parts:
        if len(part) > max_chars:
            sub_parts = [p.strip() for p in re.split(r"(?<=[，、,:：])\s*", part) if p.strip()]
        else:
            sub_parts = [part]

        for sub in sub_parts:
            if not sub:
                continue
            if len(sub) > max_chars:
                if buf:
                    _push(buf)
                    buf = ""
                start = 0
                while start < len(sub):
                    _push(sub[start : start + max_chars])
                    start += max_chars
                continue
            candidate = f"{buf} {sub}".strip() if buf else sub
            if len(candidate) <= max_chars:
                buf = candidate
            else:
                _push(buf)
                buf = sub

    _push(buf)
    return [c for c in chunks if c]


def _ai_long_preload_dir(group: Any) -> Path:
    return Path(group.path) / "state" / "ai_long_preload"


def _list_ai_long_scripts() -> list[Dict[str, Any]]:
    try:
        from ...ports.news.agent import PREPARED_LONGFORM_SCRIPTS
    except Exception:
        return []
    out: list[Dict[str, Any]] = []
    for key, item in PREPARED_LONGFORM_SCRIPTS.items():
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or key).strip()
        aliases = [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()]
        sections = item.get("sections") or []
        section_count = len(sections) if isinstance(sections, list) else 0
        out.append(
            {
                "key": str(key),
                "title": title,
                "aliases": aliases,
                "sections": section_count,
            }
        )
    return out


def _read_ai_long_manifest(group: Any) -> Dict[str, Any]:
    manifest_path = _ai_long_preload_dir(group) / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "ai_long_preload_not_ready", "message": "AI 长文预加载音频不存在"},
        )
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "ai_long_preload_manifest_invalid", "message": f"manifest parse failed: {e}"},
        ) from e
    if not isinstance(doc, dict):
        raise HTTPException(
            status_code=500,
            detail={"code": "ai_long_preload_manifest_invalid", "message": "manifest is not an object"},
        )
    chunks = doc.get("chunks")
    if not isinstance(chunks, list):
        raise HTTPException(
            status_code=500,
            detail={"code": "ai_long_preload_manifest_invalid", "message": "manifest chunks missing"},
        )
    return doc


def _build_topic_longform_script(topic: str) -> tuple[str, list[str]]:
    t = str(topic or "").strip()
    if not t:
        return "AI专题长文", []
    title = f"{t}：系统长文说明"
    sections = [
        f"这一期我们围绕“{t}”做一篇完整说明，不做快讯，而是从背景、原理、应用和边界四个层面串起来，让你可以一次听懂这个主题到底在解决什么问题。",
        f"先看背景。{t} 之所以被频繁讨论，是因为过去方案在成本、稳定性和可扩展性上都存在瓶颈。业务一旦规模化，旧流程会在响应速度和维护复杂度上迅速暴露短板。",
        f"再看核心原理。你可以把 {t} 理解成一套分层系统：上层处理任务目标和编排，中层负责状态流转与策略控制，底层负责计算执行与结果回传。三层协同决定最终体验。",
        f"在工程实现上，{t} 通常不是单点能力，而是组合能力。它往往依赖数据管线、调度策略和缓存机制共同工作，只有把这些环节打通，效果才会稳定而且可复用。",
        f"应用层面，{t} 在内容生产、自动化协作、知识检索和人机交互这几类场景里价值最明显。它的优势不是替代人，而是把重复流程标准化，把关键决策留给人来确认。",
        f"如果你在团队里落地 {t}，建议先从小范围闭环开始：先定义输入和输出，再明确验收标准，然后做一条可观察的最小链路。先跑通，再扩展，避免一上来就全量改造。",
        f"与此同时也要关注边界。{t} 的结果质量高度依赖数据质量和流程约束，缺少监控与回滚机制时，系统会在高压场景下出现漂移，导致输出不稳定或者难以复盘。",
        f"成本方面，{t} 的投入通常集中在前期设计和中期调优。只要架构分层清晰、指标可观测，后期边际成本会逐步下降，整体收益会从单点效率提升扩展到全流程提效。",
        f"从趋势看，{t} 接下来会更强调“可解释、可治理、可协作”。也就是说不仅要做得快，还要讲得清楚、查得出来、改得动，这样才能进入长期可持续迭代阶段。",
        f"最后做个总结：{t} 的真正价值，在于把零散能力收敛成可复用的系统能力。你可以先把它当成一个工程框架，而不是一次性功能，这样更容易持续打磨并形成自己的方法论。",
    ]
    return title, sections


def create_app() -> FastAPI:
    app = FastAPI(title="cccc web", version=__version__)
    home = ensure_home()
    web_mode = _web_mode()
    read_only = web_mode == "exhibit"
    try:
        exhibit_cache_ttl_s = float(str(os.environ.get("CCCC_WEB_EXHIBIT_CACHE_SECONDS") or "1.0").strip() or "1.0")
    except Exception:
        exhibit_cache_ttl_s = 1.0
    exhibit_allow_terminal = _is_truthy_env(str(os.environ.get("CCCC_WEB_EXHIBIT_ALLOW_TERMINAL") or ""))

    # Tiny in-process cache for high-fanout read endpoints (exhibit mode only).
    cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
    inflight: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
    cache_lock = asyncio.Lock()
    # Protect GPT-SoVITS CPU backend from concurrent overload.
    tts_synth_lock = asyncio.Lock()
    ai_long_preload_lock = asyncio.Lock()
    ai_long_preload_state: Dict[str, Dict[str, Any]] = {}
    ai_long_preload_tasks: Dict[str, asyncio.Task[None]] = {}

    async def _set_ai_long_preload_state(group_id: str, **updates: Any) -> Dict[str, Any]:
        gid = str(group_id or "").strip()
        if not gid:
            return {}
        async with ai_long_preload_lock:
            state = dict(ai_long_preload_state.get(gid) or {})
            state.update(updates)
            state["group_id"] = gid
            state["updated_at"] = int(time.time())
            ai_long_preload_state[gid] = state
            return dict(state)

    async def _get_ai_long_preload_state(group_id: str) -> Dict[str, Any]:
        gid = str(group_id or "").strip()
        async with ai_long_preload_lock:
            state = dict(ai_long_preload_state.get(gid) or {})
            task = ai_long_preload_tasks.get(gid)
            state.setdefault("group_id", gid)
            state.setdefault("status", "idle")
            state.setdefault("title", "")
            state.setdefault("interests", "")
            state.setdefault("script_key", "")
            state.setdefault("topic", "")
            state.setdefault("message", "")
            state.setdefault("script_hash", "")
            state.setdefault("total_chunks", 0)
            state.setdefault("completed_chunks", 0)
            state.setdefault("script_chars", 0)
            state.setdefault("manifest_ready", False)
            state["running"] = bool(task is not None and not task.done())
            return state

    async def _cached_json(key: str, ttl_s: float, fetcher) -> Dict[str, Any]:  # type: ignore[no-untyped-def]
        if not read_only or ttl_s <= 0:
            return await fetcher()
        now = time.monotonic()
        fut: asyncio.Future[Dict[str, Any]] | None = None
        do_fetch = False
        async with cache_lock:
            hit = cache.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
            fut = inflight.get(key)
            if fut is None or fut.done():
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                inflight[key] = fut
                do_fetch = True
        if fut is not None and not do_fetch:
            return await fut
        try:
            val = await fetcher()
            async with cache_lock:
                cache[key] = (time.monotonic() + ttl_s, val)
                if fut is not None and not fut.done():
                    fut.set_result(val)
            return val
        except Exception as e:
            async with cache_lock:
                if fut is not None and not fut.done():
                    fut.set_exception(e)
            raise
        finally:
            async with cache_lock:
                inflight.pop(key, None)

    # Some environments don't register the standard PWA manifest extension.
    mimetypes.add_type("application/manifest+json", ".webmanifest")

    # Configure web logging (best-effort) based on daemon observability settings.
    try:
        resp = call_daemon({"op": "observability_get"})
        obs = (resp.get("result") or {}).get("observability") if resp.get("ok") else None
        level = "INFO"
        if isinstance(obs, dict):
            level = str(obs.get("log_level") or "INFO").strip().upper() or "INFO"
            if obs.get("developer_mode") and level == "INFO":
                level = "DEBUG"
        _apply_web_logging(home=home, level=level)
    except Exception:
        try:
            _apply_web_logging(home=home, level="INFO")
        except Exception:
            pass

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
        token = str(os.environ.get("CCCC_WEB_TOKEN") or "").strip()

        blocked = _require_token_if_configured(request)
        if blocked is not None:
            return blocked

        resp = await call_next(request)
        # Set cookie only when the token is actually provided (enables WebSocket auth without leaking the secret).
        if token and str(request.cookies.get("cccc_web_token") or "").strip() != token:
            auth = str(request.headers.get("authorization") or "").strip()
            q = str(request.query_params.get("token") or "").strip()
            if auth != f"Bearer {token}" and q != token:
                return resp
            # Detect real protocol: env override > proxy header > request scheme
            # Set CCCC_WEB_SECURE=1 when behind HTTPS proxy that doesn't send X-Forwarded-Proto
            force_secure = str(os.environ.get("CCCC_WEB_SECURE") or "").strip().lower() in ("1", "true", "yes")
            forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").strip().lower()
            actual_scheme = "https" if force_secure else (forwarded_proto if forwarded_proto in ("http", "https") else str(getattr(request.url, "scheme", "") or "").lower())
            resp.set_cookie(
                key="cccc_web_token",
                value=token,
                httponly=True,
                samesite="none" if actual_scheme == "https" else "lax",
                secure=actual_scheme == "https",
                path="/",
            )
        return resp

    @app.middleware("http")
    async def _read_only_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        if read_only:
            m = str(request.method or "").upper()
            if m not in ("GET", "HEAD", "OPTIONS"):
                return JSONResponse(
                    status_code=403,
                    content={
                        "ok": False,
                        "error": {
                            "code": "read_only",
                            "message": "CCCC Web is running in read-only (exhibit) mode.",
                            "details": {},
                        },
                    },
                )
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def _handle_fastapi_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code") or "http_error")
            msg = str(detail.get("message") or "HTTP error")
            details: Any = detail.get("details") if "details" in detail else detail
        else:
            code = "http_error"
            msg = str(detail) if detail else "HTTP error"
            details = detail
        return JSONResponse(status_code=int(getattr(exc, "status_code", 500) or 500), content={"ok": False, "error": {"code": code, "message": msg, "details": details}})

    @app.exception_handler(StarletteHTTPException)
    async def _handle_starlette_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if int(getattr(exc, "status_code", 500) or 500) == 404 else "http_error"
        msg = str(getattr(exc, "detail", "") or "HTTP error")
        return JSONResponse(status_code=int(getattr(exc, "status_code", 500) or 500), content={"ok": False, "error": {"code": code, "message": msg, "details": {}}})

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo request inputs back to the client in validation errors (could include secrets).
        safe: list[dict[str, Any]] = []
        try:
            for err in exc.errors():
                if not isinstance(err, dict):
                    continue
                out: dict[str, Any] = {}
                for k in ("loc", "msg", "type"):
                    if k in err:
                        out[k] = err.get(k)
                if out:
                    safe.append(out)
        except Exception:
            safe = []
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": {"code": "validation_error", "message": "invalid request", "details": safe}},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_exception(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception in cccc web")
        return JSONResponse(status_code=500, content={"ok": False, "error": {"code": "internal_error", "message": "internal error", "details": {}}})

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

    @app.get("/favicon.ico")
    async def favicon_ico() -> Any:
        if dist_dir is not None and (dist_dir / "favicon.ico").exists():
            return FileResponse(dist_dir / "favicon.ico")
        raise HTTPException(status_code=404)

    @app.get("/favicon.png")
    async def favicon_png() -> Any:
        if dist_dir is not None and (dist_dir / "favicon.png").exists():
            return FileResponse(dist_dir / "favicon.png")
        raise HTTPException(status_code=404)

    @app.get("/api/v1/ping")
    async def ping() -> Dict[str, Any]:
        home = ensure_home()
        resp = await _daemon({"op": "ping"})
        return {
            "ok": True,
            "result": {
                "home": str(home),
                "daemon": resp.get("result", {}),
                "version": __version__,
                "web": {"mode": web_mode, "read_only": read_only},
            },
        }

    @app.get("/api/v1/server/lan-ip")
    async def lan_ip() -> Dict[str, Any]:
        """Return the server's LAN IP address for cross-device access."""
        import socket

        ip = None
        try:
            # UDP connect trick — finds the outbound interface IP without sending data
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        return {"ok": True, "result": {"lan_ip": ip}}

    @app.get("/api/v1/health")
    async def health() -> Dict[str, Any]:
        """Health check endpoint for monitoring."""
        home = ensure_home()
        daemon_resp = await _daemon({"op": "ping"})
        daemon_ok = daemon_resp.get("ok", False)
        
        return {
            "ok": daemon_ok,
            "result": {
                "version": __version__,
                "home": str(home),
                "daemon": "running" if daemon_ok else "stopped",
            }
        }

    @app.get("/api/v1/observability")
    async def observability_get() -> Dict[str, Any]:
        """Get global observability settings (developer mode, log level)."""
        return await _daemon({"op": "observability_get"})

    @app.put("/api/v1/observability")
    async def observability_update(req: ObservabilityUpdateRequest) -> Dict[str, Any]:
        """Update global observability settings (daemon-owned persistence)."""
        patch: Dict[str, Any] = {}
        if req.developer_mode is not None:
            patch["developer_mode"] = bool(req.developer_mode)
        if req.log_level is not None:
            patch["log_level"] = str(req.log_level or "").strip().upper()
        if req.terminal_transcript_per_actor_bytes is not None:
            patch.setdefault("terminal_transcript", {})["per_actor_bytes"] = int(req.terminal_transcript_per_actor_bytes)
        if req.terminal_ui_scrollback_lines is not None:
            patch.setdefault("terminal_ui", {})["scrollback_lines"] = int(req.terminal_ui_scrollback_lines)

        resp = await _daemon({"op": "observability_update", "args": {"by": req.by, "patch": patch}})

        # Apply web-side logging immediately as well (best-effort).
        try:
            obs = (resp.get("result") or {}).get("observability") if resp.get("ok") else None
            if isinstance(obs, dict):
                level = str(obs.get("log_level") or "INFO").strip().upper() or "INFO"
                if obs.get("developer_mode") and level == "INFO":
                    level = "DEBUG"
                _apply_web_logging(home=ensure_home(), level=level)
        except Exception:
            pass

        return resp

    # ---------------------------------------------------------------------
    # Terminal transcript endpoints (group-scoped)
    # ---------------------------------------------------------------------

    @app.get("/api/v1/groups/{group_id}/terminal/tail")
    async def terminal_tail(
        group_id: str,
        actor_id: str,
        max_chars: int = 8000,
        strip_ansi: bool = True,
        compact: bool = True,
    ) -> Dict[str, Any]:
        """Tail an actor's terminal transcript (subject to group policy)."""
        return await _daemon(
            {
                "op": "terminal_tail",
                "args": {
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "max_chars": int(max_chars or 8000),
                    "strip_ansi": bool(strip_ansi),
                    "compact": bool(compact),
                    "by": "user",
                },
            }
        )

    @app.post("/api/v1/groups/{group_id}/terminal/clear")
    async def terminal_clear(group_id: str, actor_id: str) -> Dict[str, Any]:
        """Clear (truncate) an actor's in-memory terminal transcript ring buffer."""
        return await _daemon(
            {
                "op": "terminal_clear",
                "args": {
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "by": "user",
                },
            }
        )

    # ---------------------------------------------------------------------
    # Debug endpoints (developer mode only; gated by daemon)
    # ---------------------------------------------------------------------

    @app.get("/api/v1/debug/snapshot")
    async def debug_snapshot(group_id: str) -> Dict[str, Any]:
        """Get a structured debug snapshot for a group (developer mode only)."""
        return await _daemon({"op": "debug_snapshot", "args": {"group_id": group_id, "by": "user"}})

    @app.get("/api/v1/debug/tail_logs")
    async def debug_tail_logs(component: str, group_id: str = "", lines: int = 200) -> Dict[str, Any]:
        """Tail local CCCC logs (developer mode only)."""
        return await _daemon(
            {
                "op": "debug_tail_logs",
                "args": {
                    "component": str(component or ""),
                    "group_id": str(group_id or ""),
                    "lines": int(lines or 200),
                    "by": "user",
                },
            }
        )

    @app.post("/api/v1/debug/clear_logs")
    async def debug_clear_logs(req: DebugClearLogsRequest) -> Dict[str, Any]:
        """Clear (truncate) local CCCC logs (developer mode only)."""
        return await _daemon(
            {
                "op": "debug_clear_logs",
                "args": {
                    "component": str(req.component or ""),
                    "group_id": str(req.group_id or ""),
                    "by": str(req.by or "user"),
                },
            }
        )

    @app.get("/api/v1/runtimes")
    async def runtimes() -> Dict[str, Any]:
        """List available agent runtimes on the system."""
        if read_only:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "read_only",
                    "message": "System discovery endpoints are disabled in read-only (exhibit) mode.",
                    "details": {"endpoint": "runtimes"},
                },
            )
        from ...kernel.runtime import detect_all_runtimes, get_runtime_command_with_flags
        
        all_runtimes = detect_all_runtimes(primary_only=False)
        return {
            "ok": True,
            "result": {
                "runtimes": [
                    {
                        "name": rt.name,
                        "display_name": rt.display_name,
                        "command": rt.command,
                        "recommended_command": " ".join(get_runtime_command_with_flags(rt.name)),
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
        if read_only:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "read_only",
                    "message": "File system endpoints are disabled in read-only (exhibit) mode.",
                    "details": {"endpoint": "fs_list"},
                },
            )
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
        if read_only:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "read_only",
                    "message": "File system endpoints are disabled in read-only (exhibit) mode.",
                    "details": {"endpoint": "fs_recent"},
                },
            )
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

    @app.get("/api/v1/fs/scope_root")
    async def fs_scope_root(path: str = "") -> Dict[str, Any]:
        """Resolve the effective scope root for a path (git root if applicable)."""
        if read_only:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "read_only",
                    "message": "File system endpoints are disabled in read-only (exhibit) mode.",
                    "details": {"endpoint": "fs_scope_root"},
                },
            )
        p = Path(str(path or "")).expanduser()
        if not str(path or "").strip():
            return {"ok": False, "error": {"code": "missing_path", "message": "missing path"}}
        if not p.exists() or not p.is_dir():
            return {"ok": False, "error": {"code": "invalid_path", "message": f"path does not exist: {p}"}}
        try:
            scope = detect_scope(p)
            return {
                "ok": True,
                "result": {
                    "path": str(p.resolve()),
                    "scope_root": str(scope.url),
                    "scope_key": str(scope.scope_key),
                    "git_remote": str(scope.git_remote or ""),
                },
            }
        except Exception as e:
            return {"ok": False, "error": {"code": "resolve_failed", "message": str(e)}}

    @app.get("/api/v1/groups")
    async def groups() -> Dict[str, Any]:
        async def _fetch() -> Dict[str, Any]:
            return await _daemon({"op": "groups"})

        ttl = max(0.0, min(5.0, exhibit_cache_ttl_s))
        return await _cached_json("groups", ttl, _fetch)

    @app.post("/api/v1/groups")
    async def group_create(req: CreateGroupRequest) -> Dict[str, Any]:
        return await _daemon({"op": "group_create", "args": {"title": req.title, "topic": req.topic, "by": req.by}})

    @app.post("/api/v1/groups/from_template")
    async def group_create_from_template(
        path: str = Form(...),
        title: str = Form("working-group"),
        topic: str = Form(""),
        by: str = Form("user"),
        file: UploadFile = File(...),
    ) -> Dict[str, Any]:
        raw = await file.read()
        if len(raw) > WEB_MAX_TEMPLATE_BYTES:
            raise HTTPException(status_code=413, detail={"code": "template_too_large", "message": "template too large"})
        template_text = raw.decode("utf-8", errors="replace")
        return await _daemon(
            {
                "op": "group_create_from_template",
                "args": {"path": path, "title": title, "topic": topic, "by": by, "template": template_text},
            }
        )

    @app.post("/api/v1/templates/preview")
    async def template_preview(file: UploadFile = File(...)) -> Dict[str, Any]:
        raw = await file.read()
        if len(raw) > WEB_MAX_TEMPLATE_BYTES:
            return {"ok": False, "error": {"code": "template_too_large", "message": "template too large"}}
        template_text = raw.decode("utf-8", errors="replace")
        try:
            tpl = parse_group_template(template_text)
        except Exception as e:
            return {"ok": False, "error": {"code": "invalid_template", "message": str(e)}}

        def _prompt_preview(value: Any, limit: int = 2000) -> Dict[str, Any]:
            if value is None:
                return {"source": "builtin"}
            raw_text = str(value)
            out = raw_text.strip()
            if len(out) > limit:
                out = out[:limit] + "\n…"
            return {"source": "repo", "chars": len(raw_text), "preview": out}

        return {
            "ok": True,
            "result": {
                "template": {
                    "kind": tpl.kind,
                    "v": tpl.v,
                    "title": tpl.title,
                    "topic": tpl.topic,
                    "exported_at": tpl.exported_at,
                    "cccc_version": tpl.cccc_version,
                    "actors": [
                        {
                            "id": a.actor_id,
                            "title": a.title,
                            "runtime": a.runtime,
                            "runner": a.runner,
                            "command": a.command,
                            "submit": a.submit,
                            "enabled": bool(a.enabled),
                        }
                        for a in tpl.actors
                    ],
                    "settings": tpl.settings.model_dump(),
                    "prompts": {
                        "preamble": _prompt_preview(tpl.prompts.preamble),
                        "help": _prompt_preview(tpl.prompts.help),
                        "standup": _prompt_preview(tpl.prompts.standup),
                    },
                }
            },
        }

    @app.get("/api/v1/groups/{group_id}")
    async def group_show(group_id: str) -> Dict[str, Any]:
        gid = str(group_id or "").strip()

        async def _fetch() -> Dict[str, Any]:
            return await _daemon({"op": "group_show", "args": {"group_id": gid}})

        ttl = max(0.0, min(5.0, exhibit_cache_ttl_s))
        return await _cached_json(f"group:{gid}", ttl, _fetch)

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
        return await _daemon({"op": "group_update", "args": {"group_id": group_id, "by": req.by, "patch": patch}})

    @app.delete("/api/v1/groups/{group_id}")
    async def group_delete(group_id: str, confirm: str = "", by: str = "user") -> Dict[str, Any]:
        """Delete a group (requires confirm=group_id)."""
        if confirm != group_id:
            raise HTTPException(
                status_code=400,
                detail={"code": "confirmation_required", "message": f"confirm must equal group_id: {group_id}"}
            )
        return await _daemon({"op": "group_delete", "args": {"group_id": group_id, "by": by}})

    @app.get("/api/v1/groups/{group_id}/context")
    async def group_context(group_id: str) -> Dict[str, Any]:
        """Get full group context (vision/sketch/milestones/tasks/notes/refs/presence)."""
        gid = str(group_id or "").strip()

        async def _fetch() -> Dict[str, Any]:
            return await _daemon({"op": "context_get", "args": {"group_id": gid}})

        ttl = max(0.0, min(5.0, exhibit_cache_ttl_s))
        return await _cached_json(f"context:{gid}", ttl, _fetch)

    @app.get("/api/v1/groups/{group_id}/template/export")
    async def group_template_export(group_id: str) -> Dict[str, Any]:
        return await _daemon({"op": "group_template_export", "args": {"group_id": group_id}})

    @app.post("/api/v1/groups/{group_id}/template/preview")
    async def group_template_preview(group_id: str, req: GroupTemplatePreviewRequest) -> Dict[str, Any]:
        return await _daemon({"op": "group_template_preview", "args": {"group_id": group_id, "template": req.template, "by": req.by}})

    @app.post("/api/v1/groups/{group_id}/template/preview_upload")
    async def group_template_preview_upload(
        group_id: str,
        by: str = Form("user"),
        file: UploadFile = File(...),
    ) -> Dict[str, Any]:
        raw = await file.read()
        if len(raw) > WEB_MAX_TEMPLATE_BYTES:
            raise HTTPException(status_code=413, detail={"code": "template_too_large", "message": "template too large"})
        template_text = raw.decode("utf-8", errors="replace")
        return await _daemon({"op": "group_template_preview", "args": {"group_id": group_id, "template": template_text, "by": by}})

    @app.post("/api/v1/groups/{group_id}/template/import_replace")
    async def group_template_import_replace(
        group_id: str,
        confirm: str = Form(""),
        by: str = Form("user"),
        file: UploadFile = File(...),
    ) -> Dict[str, Any]:
        raw = await file.read()
        if len(raw) > WEB_MAX_TEMPLATE_BYTES:
            raise HTTPException(status_code=413, detail={"code": "template_too_large", "message": "template too large"})
        template_text = raw.decode("utf-8", errors="replace")
        return await _daemon(
            {
                "op": "group_template_import_replace",
                "args": {"group_id": group_id, "confirm": confirm, "by": by, "template": template_text},
            }
        )

    @app.get("/api/v1/groups/{group_id}/tasks")
    async def group_tasks(group_id: str, task_id: Optional[str] = None) -> Dict[str, Any]:
        """List tasks (or fetch a single task when task_id is provided)."""
        args: Dict[str, Any] = {"group_id": group_id}
        if task_id:
            args["task_id"] = task_id
        return await _daemon({"op": "task_list", "args": args})

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

    def _prompt_kind_to_filename(kind: str) -> str:
        k = str(kind or "").strip().lower()
        if k == "preamble":
            return PREAMBLE_FILENAME
        if k == "help":
            return HELP_FILENAME
        if k == "standup":
            return STANDUP_FILENAME
        raise HTTPException(status_code=400, detail={"code": "invalid_kind", "message": f"unknown prompt kind: {kind}"})

    def _builtin_prompt_markdown(kind: str) -> str:
        k = str(kind or "").strip().lower()
        if k == "preamble":
            return str(DEFAULT_PREAMBLE_BODY or "").strip()
        if k == "help":
            return str(load_builtin_help_markdown() or "").strip()
        if k == "standup":
            return str(DEFAULT_STANDUP_TEMPLATE or "").strip()
        return ""

    @app.get("/api/v1/groups/{group_id}/prompts")
    async def prompts_get(group_id: str) -> Dict[str, Any]:
        """Get effective group prompt markdown (preamble/help/standup) and repo override status."""
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        root = resolve_active_scope_root(group)
        scope_root = str(root) if root is not None else None

        def _one(kind: str) -> Dict[str, Any]:
            filename = _prompt_kind_to_filename(kind)
            pf = read_repo_prompt_file(group, filename)
            repo_content = str(pf.content or "").strip() if pf.found else ""
            if repo_content:
                return {
                    "kind": kind,
                    "source": "repo",
                    "filename": filename,
                    "path": pf.path,
                    "content": repo_content,
                }
            return {
                "kind": kind,
                "source": "builtin",
                "filename": filename,
                "path": pf.path,
                "content": _builtin_prompt_markdown(kind),
            }

        return {
            "ok": True,
            "result": {
                "scope_root": scope_root,
                "preamble": _one("preamble"),
                "help": _one("help"),
                "standup": _one("standup"),
            },
        }

    @app.put("/api/v1/groups/{group_id}/prompts/{kind}")
    async def prompts_put(group_id: str, kind: str, req: RepoPromptUpdateRequest) -> Dict[str, Any]:
        """Create or update a group prompt override file in the repo root (active scope)."""
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        filename = _prompt_kind_to_filename(kind)
        if resolve_active_scope_root(group) is None:
            return {"ok": False, "error": {"code": "NO_SCOPE", "message": "No scope attached to group. Use 'cccc attach <path>' first."}}

        try:
            pf = write_repo_prompt_file(group, filename, str(req.content or ""))
            return {"ok": True, "result": {"kind": kind, "source": "repo", "filename": filename, "path": pf.path, "content": pf.content or ""}}
        except Exception as e:
            return {"ok": False, "error": {"code": "WRITE_FAILED", "message": f"Failed to write {filename}: {e}"}}

    @app.delete("/api/v1/groups/{group_id}/prompts/{kind}")
    async def prompts_delete(group_id: str, kind: str, confirm: str = "") -> Dict[str, Any]:
        """Reset a group prompt override by deleting the repo file (requires confirm=kind)."""
        if str(confirm or "").strip().lower() != str(kind or "").strip().lower():
            raise HTTPException(status_code=400, detail={"code": "confirmation_required", "message": f"confirm must equal kind: {kind}"})

        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        filename = _prompt_kind_to_filename(kind)
        if resolve_active_scope_root(group) is None:
            return {"ok": False, "error": {"code": "NO_SCOPE", "message": "No scope attached to group. Use 'cccc attach <path>' first."}}

        try:
            pf = delete_repo_prompt_file(group, filename)
            return {"ok": True, "result": {"kind": kind, "source": "builtin", "filename": filename, "path": pf.path, "content": _builtin_prompt_markdown(kind)}}
        except Exception as e:
            return {"ok": False, "error": {"code": "DELETE_FAILED", "message": f"Failed to delete {filename}: {e}"}}

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
        dry_run = coerce_bool(body.get("dry_run"), default=False)
        
        return await _daemon({
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
        from ...kernel.terminal_transcript import get_terminal_transcript_settings
        from ...kernel.messaging import get_default_send_to

        tt = get_terminal_transcript_settings(group.doc)
        return {
            "ok": True,
            "result": {
                "settings": {
                    "default_send_to": get_default_send_to(group.doc),
                    "nudge_after_seconds": int(automation.get("nudge_after_seconds", 300)),
                    "actor_idle_timeout_seconds": int(automation.get("actor_idle_timeout_seconds", 600)),
                    "keepalive_delay_seconds": int(automation.get("keepalive_delay_seconds", 120)),
                    "keepalive_max_per_actor": int(automation.get("keepalive_max_per_actor", 3)),
                    "silence_timeout_seconds": int(automation.get("silence_timeout_seconds", 600)),
                    "help_nudge_interval_seconds": int(automation.get("help_nudge_interval_seconds", 600)),
                    "help_nudge_min_messages": int(automation.get("help_nudge_min_messages", 10)),
                    "min_interval_seconds": int(delivery.get("min_interval_seconds", 0)),
                    "standup_interval_seconds": int(automation.get("standup_interval_seconds", 900)),
                    "auto_mark_on_delivery": coerce_bool(automation.get("auto_mark_on_delivery"), default=False),
                    "terminal_transcript_visibility": str(tt.get("visibility") or "foreman"),
                    "terminal_transcript_notify_tail": coerce_bool(tt.get("notify_tail"), default=False),
                    "terminal_transcript_notify_lines": int(tt.get("notify_lines", 20)),
                }
            }
        }

    @app.put("/api/v1/groups/{group_id}/settings")
    async def group_settings_update(group_id: str, req: GroupSettingsRequest) -> Dict[str, Any]:
        """Update group automation settings."""
        patch: Dict[str, Any] = {}
        if req.default_send_to is not None:
            patch["default_send_to"] = str(req.default_send_to)
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
        if req.help_nudge_interval_seconds is not None:
            patch["help_nudge_interval_seconds"] = max(0, req.help_nudge_interval_seconds)
        if req.help_nudge_min_messages is not None:
            patch["help_nudge_min_messages"] = max(0, req.help_nudge_min_messages)
        if req.min_interval_seconds is not None:
            patch["min_interval_seconds"] = max(0, req.min_interval_seconds)
        if req.standup_interval_seconds is not None:
            patch["standup_interval_seconds"] = max(0, req.standup_interval_seconds)
        if req.auto_mark_on_delivery is not None:
            patch["auto_mark_on_delivery"] = bool(req.auto_mark_on_delivery)

        # Terminal transcript policy (group-scoped)
        if req.terminal_transcript_visibility is not None:
            patch["terminal_transcript_visibility"] = str(req.terminal_transcript_visibility)
        if req.terminal_transcript_notify_tail is not None:
            patch["terminal_transcript_notify_tail"] = bool(req.terminal_transcript_notify_tail)
        if req.terminal_transcript_notify_lines is not None:
            patch["terminal_transcript_notify_lines"] = max(1, min(80, int(req.terminal_transcript_notify_lines)))
        
        if not patch:
            return {"ok": True, "result": {"message": "no changes"}}
        
        return await _daemon({
            "op": "group_settings_update",
            "args": {"group_id": group_id, "patch": patch, "by": req.by}
        })

    @app.post("/api/v1/groups/{group_id}/attach")
    async def group_attach(group_id: str, req: AttachRequest) -> Dict[str, Any]:
        return await _daemon({"op": "attach", "args": {"path": req.path, "by": req.by, "group_id": group_id}})

    @app.delete("/api/v1/groups/{group_id}/scopes/{scope_key}")
    async def group_detach_scope(group_id: str, scope_key: str, by: str = "user") -> Dict[str, Any]:
        """Detach a scope from a group."""
        return await _daemon({"op": "group_detach_scope", "args": {"group_id": group_id, "scope_key": scope_key, "by": by}})

    @app.get("/api/v1/groups/{group_id}/ledger/tail")
    async def ledger_tail(
        group_id: str,
        lines: int = 50,
        with_read_status: bool = False,
        with_ack_status: bool = False,
    ) -> Dict[str, Any]:
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
        
        # Optionally include read status for chat.message events (batch optimized)
        if with_read_status:
            from ...kernel.inbox import get_read_status_batch
            status_map = get_read_status_batch(group, events)
            for ev in events:
                event_id = str(ev.get("id") or "")
                if event_id in status_map:
                    ev["_read_status"] = status_map[event_id]

        # Optionally include ack status for attention chat.message events (batch optimized)
        if with_ack_status:
            from ...kernel.inbox import get_ack_status_batch
            ack_map = get_ack_status_batch(group, events)
            for ev in events:
                event_id = str(ev.get("id") or "")
                if event_id in ack_map:
                    ev["_ack_status"] = ack_map[event_id]
        
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
        with_ack_status: bool = False,
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
        
        from ...kernel.inbox import search_messages, get_read_status_batch
        
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
        
        # Optionally include read status (batch optimized)
        if with_read_status:
            status_map = get_read_status_batch(group, events)
            for ev in events:
                event_id = str(ev.get("id") or "")
                if event_id in status_map:
                    ev["_read_status"] = status_map[event_id]

        # Optionally include ack status (batch optimized)
        if with_ack_status:
            from ...kernel.inbox import get_ack_status_batch
            ack_map = get_ack_status_batch(group, events)
            for ev in events:
                event_id = str(ev.get("id") or "")
                if event_id in ack_map:
                    ev["_ack_status"] = ack_map[event_id]
        
        return {
            "ok": True,
            "result": {
                "events": events,
                "has_more": has_more,
                "count": len(events),
            }
        }

    @app.get("/api/v1/groups/{group_id}/ledger/window")
    async def ledger_window(
        group_id: str,
        center: str,
        kind: str = "chat",
        before: int = 30,
        after: int = 30,
        with_read_status: bool = False,
        with_ack_status: bool = False,
    ) -> Dict[str, Any]:
        """Return a bounded window of events around a center event_id.

        This is used for "jump-to message" deep links and search result navigation.
        """
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        from ...kernel.inbox import find_event, search_messages, get_read_status_batch

        center_id = str(center or "").strip()
        if not center_id:
            raise HTTPException(status_code=400, detail={"code": "missing_center", "message": "missing center event_id"})

        center_event = find_event(group, center_id)
        if center_event is None:
            raise HTTPException(status_code=404, detail={"code": "event_not_found", "message": f"event not found: {center_id}"})

        # Validate and clamp window sizes
        before = max(0, min(200, int(before)))
        after = max(0, min(200, int(after)))

        kind_filter = kind if kind in ("all", "chat", "notify") else "chat"

        if kind_filter == "chat" and str(center_event.get("kind") or "") != "chat.message":
            raise HTTPException(status_code=400, detail={"code": "invalid_center_kind", "message": "center event kind must be chat.message for kind=chat"})

        before_events, has_more_before = search_messages(
            group,
            query="",
            kind_filter=kind_filter,  # type: ignore
            before_id=center_id,
            limit=before,
        )
        after_events, has_more_after = search_messages(
            group,
            query="",
            kind_filter=kind_filter,  # type: ignore
            after_id=center_id,
            limit=after,
        )

        events = [*before_events, center_event, *after_events]

        if with_read_status:
            status_map = get_read_status_batch(group, events)
            for ev in events:
                event_id = str(ev.get("id") or "")
                if event_id in status_map:
                    ev["_read_status"] = status_map[event_id]

        if with_ack_status:
            from ...kernel.inbox import get_ack_status_batch
            ack_map = get_ack_status_batch(group, events)
            for ev in events:
                event_id = str(ev.get("id") or "")
                if event_id in ack_map:
                    ev["_ack_status"] = ack_map[event_id]

        return {
            "ok": True,
            "result": {
                "center_id": center_id,
                "center_index": len(before_events),
                "events": events,
                "has_more_before": has_more_before,
                "has_more_after": has_more_after,
                "count": len(events),
            },
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
        from .streams import sse_ledger_tail, create_sse_response
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})
        return create_sse_response(sse_ledger_tail(group.ledger_path))

    @app.get("/api/v1/events/stream")
    async def global_events_stream() -> StreamingResponse:
        """SSE stream for global events (group created/deleted, etc.)."""
        from .streams import sse_global_events_tail, create_sse_response
        return create_sse_response(sse_global_events_tail(home))

    @app.post("/api/v1/groups/{group_id}/send")
    async def send(group_id: str, req: SendRequest) -> Dict[str, Any]:
        return await _daemon(
            {
                "op": "send",
                "args": {
                    "group_id": group_id,
                    "text": req.text,
                    "by": req.by,
                    "to": list(req.to),
                    "path": req.path,
                    "priority": req.priority,
                    "src_group_id": req.src_group_id,
                    "src_event_id": req.src_event_id,
                },
            }
        )

    @app.post("/api/v1/groups/{group_id}/send_cross_group")
    async def send_cross_group(group_id: str, req: SendCrossGroupRequest) -> Dict[str, Any]:
        """Send a message to another group with provenance.

        This creates a source chat.message in the current group and forwards a copy into the destination group
        with (src_group_id, src_event_id) set.
        """
        return await _daemon(
            {
                "op": "send_cross_group",
                "args": {
                    "group_id": group_id,
                    "dst_group_id": req.dst_group_id,
                    "text": req.text,
                    "by": req.by,
                    "to": list(req.to),
                    "priority": req.priority,
                },
            }
        )

    @app.post("/api/v1/groups/{group_id}/reply")
    async def reply(group_id: str, req: ReplyRequest) -> Dict[str, Any]:
        return await _daemon(
            {
                "op": "reply",
                "args": {
                    "group_id": group_id,
                    "text": req.text,
                    "by": req.by,
                    "to": list(req.to),
                    "reply_to": req.reply_to,
                    "priority": req.priority,
                },
            }
        )

    @app.post("/api/v1/groups/{group_id}/events/{event_id}/ack")
    async def chat_ack(group_id: str, event_id: str, req: UserAckRequest) -> Dict[str, Any]:
        # Web UI can only ACK as user (no impersonation).
        if str(req.by or "").strip() != "user":
            raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "ack is only supported as user in the web UI"})
        return await _daemon(
            {
                "op": "chat_ack",
                "args": {"group_id": group_id, "event_id": event_id, "actor_id": "user", "by": "user"},
            }
        )

    @app.post("/api/v1/groups/{group_id}/send_upload")
    async def send_upload(
        group_id: str,
        by: str = Form("user"),
        text: str = Form(""),
        to_json: str = Form("[]"),
        path: str = Form(""),
        priority: str = Form("normal"),
        files: list[UploadFile] = File(default_factory=list),
    ) -> Dict[str, Any]:
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        try:
            parsed_to = json.loads(to_json or "[]")
        except Exception:
            parsed_to = []
        to_list = [str(x).strip() for x in (parsed_to if isinstance(parsed_to, list) else []) if str(x).strip()]

        # Preflight recipients before storing attachments (avoid orphan blobs on invalid/no-op sends).
        from ...kernel.actors import resolve_recipient_tokens, list_actors
        from ...kernel.messaging import get_default_send_to, enabled_recipient_actor_ids, targets_any_agent
        try:
            canonical_to = resolve_recipient_tokens(group, to_list)
        except Exception as e:
            raise HTTPException(status_code=400, detail={"code": "invalid_recipient", "message": str(e)})
        if to_list and not canonical_to:
            raise HTTPException(status_code=400, detail={"code": "invalid_recipient", "message": "invalid recipient"})

        raw_text = str(text or "").strip()
        if not canonical_to and not to_list and raw_text:
            import re
            mention_pattern = re.compile(r"@(\w[\w-]*)")
            mentions = mention_pattern.findall(raw_text)
            if mentions:
                actor_ids = {str(a.get("id") or "").strip() for a in list_actors(group) if isinstance(a, dict)}
                mention_tokens: list[str] = []
                for m in mentions:
                    if not m:
                        continue
                    if m in ("all", "peers", "foreman"):
                        mention_tokens.append(f"@{m}")
                    elif m in actor_ids:
                        mention_tokens.append(m)
                if mention_tokens:
                    try:
                        canonical_to = resolve_recipient_tokens(group, mention_tokens)
                    except Exception:
                        canonical_to = []

        if not canonical_to and not to_list and get_default_send_to(group.doc) == "foreman":
            canonical_to = ["@foreman"]

        if targets_any_agent(canonical_to):
            matched_enabled = enabled_recipient_actor_ids(group, canonical_to)
            if not matched_enabled:
                wanted = " ".join(canonical_to) if canonical_to else "@all"
                raise HTTPException(
                    status_code=400,
                    detail={"code": "no_enabled_recipients", "message": f"no enabled agents match recipients: {wanted}"},
                )

        attachments: list[dict[str, Any]] = []
        for f in files or []:
            raw = await f.read()
            if len(raw) > WEB_MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={"code": "file_too_large", "message": f"file too large (> {WEB_MAX_FILE_MB}MB)"},
                )
            attachments.append(
                store_blob_bytes(
                    group,
                    data=raw,
                    filename=str(getattr(f, "filename", "") or "file"),
                    mime_type=str(getattr(f, "content_type", "") or ""),
                )
            )

        msg_text = str(text or "").strip()
        if not msg_text and attachments:
            if len(attachments) == 1:
                msg_text = f"[file] {attachments[0].get('title') or 'file'}"
            else:
                msg_text = f"[files] {len(attachments)} attachments"

        prio = str(priority or "normal").strip() or "normal"
        if prio not in ("normal", "attention"):
            raise HTTPException(status_code=400, detail={"code": "invalid_priority", "message": "priority must be 'normal' or 'attention'"})

        return await _daemon(
            {
                "op": "send",
                "args": {
                    "group_id": group_id,
                    "text": msg_text,
                    "by": by,
                    "to": canonical_to,
                    "path": path,
                    "attachments": attachments,
                    "priority": prio,
                },
            }
        )

    @app.post("/api/v1/groups/{group_id}/reply_upload")
    async def reply_upload(
        group_id: str,
        by: str = Form("user"),
        text: str = Form(""),
        to_json: str = Form("[]"),
        reply_to: str = Form(""),
        priority: str = Form("normal"),
        files: list[UploadFile] = File(default_factory=list),
    ) -> Dict[str, Any]:
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        reply_to_id = str(reply_to or "").strip()
        if not reply_to_id:
            raise HTTPException(status_code=400, detail={"code": "missing_reply_to", "message": "missing reply_to"})

        try:
            parsed_to = json.loads(to_json or "[]")
        except Exception:
            parsed_to = []
        to_list = [str(x).strip() for x in (parsed_to if isinstance(parsed_to, list) else []) if str(x).strip()]

        # Preflight recipients before storing attachments (avoid orphan blobs on invalid/no-op sends).
        from ...kernel.actors import resolve_recipient_tokens
        from ...kernel.inbox import find_event
        from ...kernel.messaging import default_reply_recipients, enabled_recipient_actor_ids, targets_any_agent

        original = find_event(group, reply_to_id)
        if original is None:
            raise HTTPException(status_code=404, detail={"code": "event_not_found", "message": f"event not found: {reply_to_id}"})

        try:
            canonical_to = resolve_recipient_tokens(group, to_list)
        except Exception as e:
            raise HTTPException(status_code=400, detail={"code": "invalid_recipient", "message": str(e)})
        if to_list and not canonical_to:
            raise HTTPException(status_code=400, detail={"code": "invalid_recipient", "message": "invalid recipient"})

        if not canonical_to and not to_list:
            canonical_to = resolve_recipient_tokens(group, default_reply_recipients(group, by=by, original_event=original))

        if targets_any_agent(canonical_to):
            matched_enabled = enabled_recipient_actor_ids(group, canonical_to)
            if by and by in matched_enabled:
                matched_enabled = [aid for aid in matched_enabled if aid != by]
            if not matched_enabled:
                wanted = " ".join(canonical_to) if canonical_to else "@all"
                raise HTTPException(
                    status_code=400,
                    detail={"code": "no_enabled_recipients", "message": f"no enabled agents match recipients: {wanted}"},
                )

        attachments: list[dict[str, Any]] = []
        for f in files or []:
            raw = await f.read()
            if len(raw) > WEB_MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={"code": "file_too_large", "message": f"file too large (> {WEB_MAX_FILE_MB}MB)"},
                )
            attachments.append(
                store_blob_bytes(
                    group,
                    data=raw,
                    filename=str(getattr(f, "filename", "") or "file"),
                    mime_type=str(getattr(f, "content_type", "") or ""),
                )
            )

        msg_text = str(text or "").strip()
        if not msg_text and attachments:
            if len(attachments) == 1:
                msg_text = f"[file] {attachments[0].get('title') or 'file'}"
            else:
                msg_text = f"[files] {len(attachments)} attachments"

        prio = str(priority or "normal").strip() or "normal"
        if prio not in ("normal", "attention"):
            raise HTTPException(status_code=400, detail={"code": "invalid_priority", "message": "priority must be 'normal' or 'attention'"})

        return await _daemon(
            {
                "op": "reply",
                "args": {
                    "group_id": group_id,
                    "text": msg_text,
                    "by": by,
                    "to": canonical_to,
                    "reply_to": reply_to_id,
                    "attachments": attachments,
                    "priority": prio,
                },
            }
        )

    @app.get("/api/v1/groups/{group_id}/blobs/{blob_name}")
    async def blob_download(group_id: str, blob_name: str) -> FileResponse:
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})
        name = str(blob_name or "").strip()
        if not name or "/" in name or "\\" in name or ".." in name:
            raise HTTPException(status_code=400, detail={"code": "invalid_blob", "message": "invalid blob name"})

        rel = f"state/blobs/{name}"
        try:
            abs_path = resolve_blob_attachment_path(group, rel_path=rel)
        except Exception:
            raise HTTPException(status_code=400, detail={"code": "invalid_blob", "message": "invalid blob name"})

        if not abs_path.exists() or not abs_path.is_file():
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "blob not found"})

        download_name = name
        if len(name) > 64 and "_" in name:
            # blob name format: <sha256>_<filename>
            download_name = name.split("_", 1)[1] or name
        return FileResponse(path=abs_path, filename=download_name)

    @app.get("/api/v1/groups/{group_id}/actors")
    async def actors(group_id: str, include_unread: bool = False) -> Dict[str, Any]:
        gid = str(group_id or "").strip()
        async def _fetch() -> Dict[str, Any]:
            return await _daemon({"op": "actor_list", "args": {"group_id": gid, "include_unread": include_unread}})

        ttl = max(0.0, min(5.0, exhibit_cache_ttl_s))
        return await _cached_json(f"actors:{gid}:{int(bool(include_unread))}", ttl, _fetch)

    @app.post("/api/v1/groups/{group_id}/actors")
    async def actor_create(group_id: str, req: ActorCreateRequest) -> Dict[str, Any]:
        command = _normalize_command(req.command) or []
        env_private = dict(req.env_private) if isinstance(req.env_private, dict) else None
        return await _daemon(
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
                    "env_private": env_private,
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
        return await _daemon({"op": "actor_update", "args": {"group_id": group_id, "actor_id": actor_id, "patch": patch, "by": req.by}})

    @app.delete("/api/v1/groups/{group_id}/actors/{actor_id}")
    async def actor_delete(group_id: str, actor_id: str, by: str = "user") -> Dict[str, Any]:
        return await _daemon({"op": "actor_remove", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/actors/{actor_id}/start")
    async def actor_start(group_id: str, actor_id: str, by: str = "user") -> Dict[str, Any]:
        return await _daemon({"op": "actor_start", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/actors/{actor_id}/stop")
    async def actor_stop(group_id: str, actor_id: str, by: str = "user") -> Dict[str, Any]:
        return await _daemon({"op": "actor_stop", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/actors/{actor_id}/restart")
    async def actor_restart(group_id: str, actor_id: str, by: str = "user") -> Dict[str, Any]:
        return await _daemon({"op": "actor_restart", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})

    @app.get("/api/v1/groups/{group_id}/actors/{actor_id}/env_private")
    async def actor_env_private_keys(group_id: str, actor_id: str, by: str = "user") -> Dict[str, Any]:
        """List configured private env keys (never returns values)."""
        if read_only:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "read_only",
                    "message": "Private env endpoints are disabled in read-only (exhibit) mode.",
                    "details": {"endpoint": "actor_env_private_keys"},
                },
            )
        return await _daemon({"op": "actor_env_private_keys", "args": {"group_id": group_id, "actor_id": actor_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/actors/{actor_id}/env_private")
    async def actor_env_private_update(request: Request, group_id: str, actor_id: str) -> Dict[str, Any]:
        """Update private env (runtime-only). Values are never returned."""
        if read_only:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "read_only",
                    "message": "Private env endpoints are disabled in read-only (exhibit) mode.",
                    "details": {"endpoint": "actor_env_private_update"},
                },
            )
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": "invalid JSON body", "details": {}})
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": "request body must be an object", "details": {}})

        by = str(payload.get("by") or "user").strip() or "user"
        clear = bool(payload.get("clear") is True)

        set_raw = payload.get("set")
        unset_raw = payload.get("unset")

        if set_raw is not None and not isinstance(set_raw, dict):
            raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": "set must be an object", "details": {}})
        if unset_raw is not None and not isinstance(unset_raw, list):
            raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": "unset must be a list", "details": {}})

        set_vars: Dict[str, str] = {}
        if isinstance(set_raw, dict):
            for k, v in set_raw.items():
                kk = str(k or "").strip()
                if not kk:
                    continue
                # Keep value as string; never echo it back.
                if v is None:
                    continue
                set_vars[kk] = str(v)

        unset_keys: list[str] = []
        if isinstance(unset_raw, list):
            for item in unset_raw:
                kk = str(item or "").strip()
                if kk:
                    unset_keys.append(kk)

        return await _daemon(
            {
                "op": "actor_env_private_update",
                "args": {
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "by": by,
                    "set": set_vars,
                    "unset": unset_keys,
                    "clear": clear,
                },
            }
        )

    @app.websocket("/api/v1/groups/{group_id}/actors/{actor_id}/term")
    async def actor_terminal(websocket: WebSocket, group_id: str, actor_id: str) -> None:
        token = str(os.environ.get("CCCC_WEB_TOKEN") or "").strip()
        if token:
            provided = str(websocket.query_params.get("token") or "").strip()
            cookie = ""
            try:
                cookie = str(getattr(websocket, "cookies", {}) or {}).get("cccc_web_token") or ""
            except Exception:
                cookie = ""
            if provided != token and str(cookie).strip() != token:
                await websocket.close(code=4401)
                return

        await websocket.accept()

        if read_only and not exhibit_allow_terminal:
            try:
                await websocket.send_json(
                    {
                        "ok": False,
                        "error": {
                            "code": "read_only_terminal",
                            "message": "Terminal is disabled in read-only (exhibit) mode.",
                            "details": {},
                        },
                    }
                )
            except Exception:
                pass
            try:
                await websocket.close(code=1000)
            except Exception:
                pass
            return

        group = load_group(group_id)
        if group is None:
            await websocket.send_json({"ok": False, "error": {"code": "group_not_found", "message": f"group not found: {group_id}"}})
            await websocket.close(code=1008)
            return

        try:
            ep = get_daemon_endpoint()
            transport = str(ep.get("transport") or "").strip().lower()
            if transport == "tcp":
                host = str(ep.get("host") or "127.0.0.1").strip() or "127.0.0.1"
                port = int(ep.get("port") or 0)
                reader, writer = await asyncio.open_connection(host, port)
            else:
                home = ensure_home()
                sock_path = home / "daemon" / "ccccd.sock"
                path = str(ep.get("path") or sock_path)
                reader, writer = await asyncio.open_unix_connection(path)
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
                        if read_only:
                            continue
                        data = str(obj.get("d") or "")
                        if data:
                            writer.write(data.encode("utf-8", errors="replace"))
                            await writer.drain()
                        continue
                    if t == "r":
                        if read_only:
                            continue
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
        return await _daemon({"op": "inbox_list", "args": {"group_id": group_id, "actor_id": actor_id, "by": by, "limit": int(limit)}})

    @app.post("/api/v1/groups/{group_id}/inbox/{actor_id}/read")
    async def inbox_mark_read(group_id: str, actor_id: str, req: InboxReadRequest) -> Dict[str, Any]:
        return await _daemon(
            {"op": "inbox_mark_read", "args": {"group_id": group_id, "actor_id": actor_id, "event_id": req.event_id, "by": req.by}}
        )

    @app.post("/api/v1/groups/{group_id}/start")
    async def group_start(group_id: str, by: str = "user") -> Dict[str, Any]:
        return await _daemon({"op": "group_start", "args": {"group_id": group_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/stop")
    async def group_stop(group_id: str, by: str = "user") -> Dict[str, Any]:
        return await _daemon({"op": "group_stop", "args": {"group_id": group_id, "by": by}})

    @app.post("/api/v1/groups/{group_id}/state")
    async def group_set_state(group_id: str, state: str, by: str = "user") -> Dict[str, Any]:
        """Set group state (active/idle/paused) to control automation behavior."""
        return await _daemon({"op": "group_set_state", "args": {"group_id": group_id, "state": state, "by": by}})

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
            except (ValueError, ProcessLookupError, PermissionError, OSError):
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

        prev_im = group.doc.get("im") if isinstance(group.doc.get("im"), dict) else {}
        prev_enabled = coerce_bool(prev_im.get("enabled"), default=False) if isinstance(prev_im, dict) else False

        # Build IM config.
        # Note: Web UI historically used bot_token_env/app_token_env as a single input.
        # We accept either an env var name (e.g. TELEGRAM_BOT_TOKEN) or a raw token value.
        im_cfg: Dict[str, Any] = {"platform": req.platform}
        if prev_enabled:
            im_cfg["enabled"] = True

        platform = str(req.platform or "").strip().lower()
        prev_files = prev_im.get("files") if isinstance(prev_im, dict) else None
        if isinstance(prev_files, dict):
            # Preserve non-credential settings, if any (so "Set" doesn't silently drop them).
            im_cfg["files"] = prev_files
        else:
            # Default file-transfer policy (also used by CLI).
            default_max_mb = 20 if platform in ("telegram", "slack") else 10
            im_cfg["files"] = {"enabled": True, "max_mb": default_max_mb}

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
        elif platform == "feishu":
            # Feishu: app_id + app_secret
            dom = _normalize_feishu_domain(req.feishu_domain)
            if dom:
                im_cfg["feishu_domain"] = dom
            app_id = str(req.feishu_app_id or "").strip()
            app_secret = str(req.feishu_app_secret or "").strip()
            if app_id:
                if _is_env_var_name(app_id):
                    im_cfg["feishu_app_id_env"] = app_id
                else:
                    im_cfg["feishu_app_id"] = app_id
            if app_secret:
                if _is_env_var_name(app_secret):
                    im_cfg["feishu_app_secret_env"] = app_secret
                else:
                    im_cfg["feishu_app_secret"] = app_secret
        elif platform == "dingtalk":
            # DingTalk: app_key + app_secret + optional robot_code
            app_key = str(req.dingtalk_app_key or "").strip()
            app_secret = str(req.dingtalk_app_secret or "").strip()
            robot_code = str(req.dingtalk_robot_code or "").strip()
            if app_key:
                if _is_env_var_name(app_key):
                    im_cfg["dingtalk_app_key_env"] = app_key
                else:
                    im_cfg["dingtalk_app_key"] = app_key
            if app_secret:
                if _is_env_var_name(app_secret):
                    im_cfg["dingtalk_app_secret_env"] = app_secret
                else:
                    im_cfg["dingtalk_app_secret"] = app_secret
            if robot_code:
                if _is_env_var_name(robot_code):
                    im_cfg["dingtalk_robot_code_env"] = robot_code
                else:
                    im_cfg["dingtalk_robot_code"] = robot_code
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
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                pass

        # Check IM config
        im_cfg = group.doc.get("im", {})
        if not im_cfg:
            return {"ok": False, "error": {"code": "no_im_config", "message": "no IM configuration"}}

        # Persist desired run-state for restart/autostart.
        if isinstance(im_cfg, dict):
            im_cfg["enabled"] = True
            group.doc["im"] = im_cfg
            group.save()

        platform = im_cfg.get("platform", "telegram")

        # Prepare environment
        env = os.environ.copy()

        if platform == "feishu":
            # Feishu: set FEISHU_APP_ID and FEISHU_APP_SECRET
            app_id = im_cfg.get("feishu_app_id") or ""
            app_secret = im_cfg.get("feishu_app_secret") or ""
            app_id_env = im_cfg.get("feishu_app_id_env") or ""
            app_secret_env = im_cfg.get("feishu_app_secret_env") or ""
            # Set actual values to default env var names
            if app_id:
                env["FEISHU_APP_ID"] = app_id
            if app_secret:
                env["FEISHU_APP_SECRET"] = app_secret
            # Also set to custom env var names if specified
            if app_id_env and app_id:
                env[app_id_env] = app_id
            if app_secret_env and app_secret:
                env[app_secret_env] = app_secret
        elif platform == "dingtalk":
            # DingTalk: set DINGTALK_APP_KEY, DINGTALK_APP_SECRET, DINGTALK_ROBOT_CODE
            app_key = im_cfg.get("dingtalk_app_key") or ""
            app_secret = im_cfg.get("dingtalk_app_secret") or ""
            robot_code = im_cfg.get("dingtalk_robot_code") or ""
            app_key_env = im_cfg.get("dingtalk_app_key_env") or ""
            app_secret_env = im_cfg.get("dingtalk_app_secret_env") or ""
            robot_code_env = im_cfg.get("dingtalk_robot_code_env") or ""
            # Set actual values to default env var names
            if app_key:
                env["DINGTALK_APP_KEY"] = app_key
            if app_secret:
                env["DINGTALK_APP_SECRET"] = app_secret
            if robot_code:
                env["DINGTALK_ROBOT_CODE"] = robot_code
            # Also set to custom env var names if specified
            if app_key_env and app_key:
                env[app_key_env] = app_key
            if app_secret_env and app_secret:
                env[app_secret_env] = app_secret
            if robot_code_env and robot_code:
                env[robot_code_env] = robot_code
        else:
            # Telegram/Slack/Discord: token-based
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
            python_exec = _background_python_executable(sys.executable)
            popen_kwargs: Dict[str, Any] = {
                "env": env,
                "stdout": log_file,
                "stderr": log_file,
                "start_new_session": True,
            }
            if os.name == "nt":
                flags = 0
                flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))
                flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if flags:
                    popen_kwargs["creationflags"] = flags
            proc = subprocess.Popen(
                [python_exec, "-m", "cccc.ports.im.bridge", req.group_id, platform],
                **popen_kwargs,
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

        # Persist desired run-state for restart/autostart.
        im_cfg = group.doc.get("im")
        if isinstance(im_cfg, dict):
            im_cfg["enabled"] = False
            group.doc["im"] = im_cfg
            try:
                group.save()
            except Exception:
                pass

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

    # ── Broadcast Agent endpoints (news / market / ai_long / horror) ──

    BROADCAST_SPECS: Dict[str, Dict[str, str]] = {
        "news": {
            "cfg_key": "news_agent",
            "pid_name": "news_agent.pid",
            "log_name": "news_agent.log",
            "mode": "news",
            "default_interests": "AI,科技,编程",
            "default_schedule": "8,11,14,17,20",
        },
        "market": {
            "cfg_key": "market_agent",
            "pid_name": "market_agent.pid",
            "log_name": "market_agent.log",
            "mode": "market",
            "default_interests": "股市,美股,A股,港股,宏观,财报",
            "default_schedule": "9,12,15,18,22",
        },
        "ai_long": {
            "cfg_key": "ai_long_agent",
            "pid_name": "ai_long_agent.pid",
            "log_name": "ai_long_agent.log",
            "mode": "ai_long",
            "default_interests": "CCCC,框架,多Agent,协作,消息总线,语音播报",
            "default_schedule": "10,16,21",
        },
        "horror": {
            "cfg_key": "horror_agent",
            "pid_name": "horror_agent.pid",
            "log_name": "horror_agent.log",
            "mode": "horror",
            "default_interests": "深夜,公寓,都市传说,悬疑,心理惊悚",
            "default_schedule": "21,23,1",
        },
    }

    def _broadcast_tokens(kind: str) -> list[str]:
        spec = BROADCAST_SPECS.get(kind, BROADCAST_SPECS["news"])
        return [f"--mode={spec.get('mode', 'news')}"]

    def _stop_broadcast_kind(group: Any, kind: str, *, persist_cfg: bool) -> int:
        spec = BROADCAST_SPECS.get(kind, BROADCAST_SPECS["news"])
        cfg_key = str(spec.get("cfg_key") or "news_agent")
        mode = str(spec.get("mode") or "news")
        pid_name = str(spec.get("pid_name") or "news_agent.pid")
        gid = str(getattr(group, "group_id", "") or getattr(group, "path", Path("")).name or "").strip()

        if persist_cfg:
            cfg = group.doc.get(cfg_key)
            if isinstance(cfg, dict):
                cfg["enabled"] = False
                group.doc[cfg_key] = cfg
                try:
                    group.save()
                except Exception:
                    pass

        killed: set[int] = set()
        pid_path = group.path / "state" / pid_name
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
                if pid > 0:
                    _best_effort_terminate_pid(pid)
                    killed.add(pid)
            except Exception:
                pass
            try:
                pid_path.unlink(missing_ok=True)
            except Exception:
                pass

        for pid in _find_group_module_pids(
            home=home,
            module="cccc.ports.news",
            group_id=gid,
            command_contains=[f"--mode={mode}"],
        ):
            if pid in killed:
                continue
            _best_effort_terminate_pid(pid)
            killed.add(pid)
        return len(killed)

    async def _broadcast_status(kind: str, group_id: str) -> Dict[str, Any]:
        if not group_id:
            return {"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id"}}
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        spec = BROADCAST_SPECS.get(kind, BROADCAST_SPECS["news"])
        cfg_key = str(spec.get("cfg_key") or "news_agent")
        pid_name = str(spec.get("pid_name") or "news_agent.pid")
        mode = str(spec.get("mode") or "news")
        default_interests = str(spec.get("default_interests") or "")
        default_schedule = str(spec.get("default_schedule") or "")

        cfg = group.doc.get(cfg_key) or {}
        enabled = bool(cfg.get("enabled"))
        running = False
        pid = 0

        pid_path = group.path / "state" / pid_name
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
                os.kill(pid, 0)
                running = True
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                running = False
                pid = 0

        if not running:
            orphan_pids = [
                p
                for p in _find_group_module_pids(
                    home=home,
                    module="cccc.ports.news",
                    group_id=group_id,
                    command_contains=[f"--mode={mode}"],
                )
                if _pid_alive(p)
            ]
            if orphan_pids:
                pid = int(orphan_pids[0])
                running = True
                try:
                    pid_path.parent.mkdir(parents=True, exist_ok=True)
                    pid_path.write_text(str(pid), encoding="utf-8")
                except Exception:
                    pass

        return {
            "ok": True,
            "result": {
                "group_id": group_id,
                "kind": kind,
                "enabled": enabled,
                "running": running,
                "pid": pid,
                "interests": cfg.get("interests", default_interests),
                "schedule": cfg.get("schedule", default_schedule),
            },
        }

    async def _broadcast_start(kind: str, *, group_id: str, interests: str, schedule: str) -> Dict[str, Any]:
        import subprocess as sp

        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})

        spec = BROADCAST_SPECS.get(kind, BROADCAST_SPECS["news"])
        cfg_key = str(spec.get("cfg_key") or "news_agent")
        pid_name = str(spec.get("pid_name") or "news_agent.pid")
        log_name = str(spec.get("log_name") or "news_agent.log")
        mode = str(spec.get("mode") or "news")

        # Keep channels independent: only one broadcast stream can run at a time.
        for other_kind in BROADCAST_SPECS:
            if other_kind == kind:
                continue
            _stop_broadcast_kind(group, other_kind, persist_cfg=True)

        pid_path = group.path / "state" / pid_name
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
                os.kill(pid, 0)
                return {"ok": False, "error": {"code": "already_running", "message": f"{kind} agent already running (pid={pid})"}}
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                try:
                    pid_path.unlink(missing_ok=True)
                except Exception:
                    pass

        orphan_pids = [
            p
            for p in _find_group_module_pids(
                home=home,
                module="cccc.ports.news",
                group_id=group_id,
                command_contains=[f"--mode={mode}"],
            )
            if _pid_alive(p)
        ]
        if orphan_pids:
            pid = int(orphan_pids[0])
            try:
                pid_path.parent.mkdir(parents=True, exist_ok=True)
                pid_path.write_text(str(pid), encoding="utf-8")
            except Exception:
                pass
            return {"ok": False, "error": {"code": "already_running", "message": f"{kind} agent already running (pid={pid})"}}

        cfg = group.doc.get(cfg_key) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["enabled"] = True
        cfg["interests"] = interests
        cfg["schedule"] = schedule
        group.doc[cfg_key] = cfg
        group.save()

        state_dir = group.path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        log_path = state_dir / log_name

        try:
            import sys as _sys

            env = os.environ.copy()
            env["CCCC_GROUP_ID"] = group_id
            env["CCCC_API"] = f"http://127.0.0.1:{env.get('CCCC_PORT', '8848')}/api/v1"
            env["NEWS_AGENT_PID_PATH"] = str(pid_path)
            env["NEWS_AGENT_RUNTIME"] = "gemini"
            env["NEWS_AGENT_GEMINI_MODEL"] = str(
                os.environ.get("NEWS_AGENT_GEMINI_MODEL")
                or os.environ.get("CCCC_GEMINI_MODEL")
                or "gemini-2.5-flash-lite"
            ).strip()
            env["NEWS_AGENT_MODE"] = mode
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env.pop("CLAUDECODE", None)
            python_exec = _background_python_executable(_sys.executable)

            log_file = log_path.open("a", encoding="utf-8")
            popen_kwargs: Dict[str, Any] = {
                "env": env,
                "stdout": log_file,
                "stderr": log_file,
                "stdin": sp.DEVNULL,
                "start_new_session": True,
            }
            if os.name == "nt":
                flags = 0
                flags |= int(getattr(sp, "CREATE_NEW_PROCESS_GROUP", 0))
                flags |= int(getattr(sp, "DETACHED_PROCESS", 0))
                flags |= int(getattr(sp, "CREATE_NO_WINDOW", 0))
                if flags:
                    popen_kwargs["creationflags"] = flags

            proc = sp.Popen(
                [
                    python_exec,
                    "-m",
                    "cccc.ports.news",
                    group_id,
                    interests,
                    schedule,
                    f"--mode={mode}",
                ],
                **popen_kwargs,
            )
            await asyncio.sleep(0.25)
            exit_code = proc.poll()
            if exit_code is not None:
                return {
                    "ok": False,
                    "error": {"code": "agent_exited", "message": f"{kind} agent exited early (code={exit_code}). Check log: {log_path}"},
                }

            pid_path.write_text(str(proc.pid), encoding="utf-8")
            return {"ok": True, "result": {"group_id": group_id, "kind": kind, "pid": proc.pid}}
        except Exception as e:
            return {"ok": False, "error": {"code": "start_failed", "message": str(e)}}

    async def _broadcast_stop(kind: str, *, group_id: str) -> Dict[str, Any]:
        group = load_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {group_id}"})
        stopped = _stop_broadcast_kind(group, kind, persist_cfg=True)
        return {"ok": True, "result": {"group_id": group_id, "kind": kind, "stopped": stopped}}

    async def _run_ai_long_preload(
        group_id: str,
        interests: str,
        force: bool,
        *,
        script_key: str = "",
        topic: str = "",
    ) -> None:
        gid = str(group_id or "").strip()
        if not gid:
            return
        try:
            group = load_group(gid)
            if group is None:
                raise RuntimeError(f"group not found: {gid}")

            preload_dir = _ai_long_preload_dir(group)
            preload_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = preload_dir / "manifest.json"
            await _set_ai_long_preload_state(
                gid,
                status="preparing",
                message="正在准备长文稿",
                interests=interests,
                script_key=script_key,
                topic=topic,
                manifest_ready=False,
                error="",
            )

            from ...ports.news.agent import (
                AI_LONG_PREFIX,
                PREPARED_LONGFORM_SCRIPTS,
                _fetch_longform_script_once,
                _select_prepared_longform_script,
                _strip_brief_prefix,
            )
            resolved_interests = str(interests or "").strip()
            resolved_topic = str(topic or "").strip()
            resolved_script_key = str(script_key or "").strip()

            prepared: Optional[tuple[str, list[str]]] = None
            if resolved_script_key:
                candidate = PREPARED_LONGFORM_SCRIPTS.get(resolved_script_key)
                if isinstance(candidate, dict):
                    title = str(candidate.get("title") or resolved_script_key).strip()
                    raw_sections = candidate.get("sections") or []
                    sections = [str(s).strip() for s in raw_sections if str(s).strip()] if isinstance(raw_sections, list) else []
                    if sections:
                        prepared = (title, sections)

            title = ""
            sections: list[str] = []
            if resolved_topic and not resolved_script_key:
                # Topic mode: always produce a closed long-form around the chosen theme.
                title, sections = _build_topic_longform_script(resolved_topic)
                resolved_interests = resolved_topic
            elif prepared:
                title, sections = prepared
            else:
                prepared = await run_in_threadpool(_select_prepared_longform_script, resolved_interests)
                if prepared:
                    title, sections = prepared
                else:
                    # news agent helper prints progress logs; redirect to avoid cp932 console encoding issues on Windows.
                    def _fetch_once_silent() -> tuple[str, list[str]]:
                        sink = io.StringIO()
                        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                            return _fetch_longform_script_once(resolved_interests, None)

                    title, sections = await run_in_threadpool(_fetch_once_silent)
            clean_sections: list[str] = []
            for section in sections:
                t = _strip_brief_prefix(section)
                if t:
                    clean_sections.append(t)
            if not clean_sections:
                raise RuntimeError("没有可用于播报的长文稿内容")

            script_text = f"{AI_LONG_PREFIX} {' '.join(clean_sections)}".strip()
            script_hash = hashlib.sha1(script_text.encode("utf-8")).hexdigest()
            chunks = _split_ai_long_chunks(script_text, 90)
            if not chunks:
                raise RuntimeError("长文分段失败")
            total = len(chunks)

            def _write_preload_manifest(
                chunks_payload: list[Dict[str, Any]],
                *,
                complete: bool,
                completed_top_chunks: int,
            ) -> None:
                manifest_doc = {
                    "group_id": gid,
                    "title": title,
                    "interests": interests,
                    "script_key": resolved_script_key,
                    "topic": resolved_topic,
                    "script_hash": script_hash,
                    "script_chars": len(script_text),
                    "script_text": script_text,
                    "expected_total_chunks": total,
                    "completed_top_chunks": max(0, int(completed_top_chunks)),
                    "complete": bool(complete),
                    "chunks": chunks_payload,
                    "created_at": int(time.time()),
                }
                manifest_path.write_text(json.dumps(manifest_doc, ensure_ascii=False, indent=2), encoding="utf-8")

            existing_manifest: Dict[str, Any] = {}
            if manifest_path.exists():
                try:
                    existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    existing_manifest = {}
            reusable_chunks: list[Dict[str, Any]] = []
            resumed_top_chunks = 0
            if not force and isinstance(existing_manifest, dict):
                old_hash = str(existing_manifest.get("script_hash") or "").strip()
                old_chunks = existing_manifest.get("chunks")
                if old_hash == script_hash and isinstance(old_chunks, list) and old_chunks:
                    available_old: list[Dict[str, Any]] = []
                    for item in old_chunks:
                        if not isinstance(item, dict):
                            break
                        rel = str(item.get("file") or "").strip()
                        if not rel:
                            break
                        chunk_path = preload_dir / rel
                        if not chunk_path.is_file():
                            break
                        saved_text = str(item.get("text") or "").strip()
                        media_type = str(item.get("media_type") or "audio/wav")
                        size = 0
                        try:
                            size = int(chunk_path.stat().st_size)
                        except Exception:
                            size = int(item.get("bytes") or 0)
                        src_idx = -1
                        try:
                            src_idx = int(item.get("source_chunk_index"))  # type: ignore[arg-type]
                        except Exception:
                            src_idx = -1
                        available_old.append(
                            {
                                "index": len(available_old),
                                "text": saved_text,
                                "file": rel,
                                "media_type": media_type,
                                "bytes": max(0, size),
                                "source_chunk_index": src_idx,
                            }
                        )

                    # Prefer explicit source chunk markers when available.
                    if available_old and all(int(it.get("source_chunk_index", -1)) >= 0 for it in available_old):
                        last_idx = -1
                        ok = True
                        for it in available_old:
                            src_idx = int(it.get("source_chunk_index", -1))
                            if src_idx < last_idx:
                                ok = False
                                break
                            if src_idx > last_idx + 1:
                                ok = False
                                break
                            last_idx = max(last_idx, src_idx)
                            reusable_chunks.append(dict(it))
                        if ok:
                            resumed_top_chunks = max(0, last_idx + 1)
                        else:
                            reusable_chunks = []
                            resumed_top_chunks = 0
                    elif available_old:
                        # Backward-compatible inference for manifests without source_chunk_index.
                        def _norm_text(v: str) -> str:
                            return re.sub(r"\s+", "", str(v or "").strip())

                        i = 0
                        j = 0
                        while i < total and j < len(available_old):
                            target = _norm_text(str(chunks[i] or ""))
                            if not target:
                                i += 1
                                continue
                            acc = ""
                            start_j = j
                            matched = False
                            while j < len(available_old):
                                seg_text = _norm_text(str(available_old[j].get("text") or ""))
                                if not seg_text:
                                    j += 1
                                    continue
                                acc += seg_text
                                if acc == target:
                                    for k in range(start_j, j + 1):
                                        item_k = dict(available_old[k])
                                        item_k["source_chunk_index"] = i
                                        item_k["index"] = len(reusable_chunks)
                                        item_k["text"] = str(item_k.get("text") or "").strip() or str(chunks[i] or "")
                                        reusable_chunks.append(item_k)
                                    resumed_top_chunks = i + 1
                                    j += 1
                                    matched = True
                                    break
                                if not target.startswith(acc):
                                    matched = False
                                    break
                                j += 1
                            if not matched:
                                break
                            i += 1

                    if resumed_top_chunks >= total and total > 0:
                        await _set_ai_long_preload_state(
                            gid,
                            status="ready",
                            message="已复用后台预加载音频",
                            title=str(existing_manifest.get("title") or title),
                            script_hash=script_hash,
                            total_chunks=total,
                            completed_chunks=total,
                            script_chars=len(script_text),
                            manifest_ready=True,
                            error="",
                        )
                        return

            if reusable_chunks:
                keep_names = {str(item.get("file") or "").strip() for item in reusable_chunks}
                for old in preload_dir.glob("chunk_*.*"):
                    if old.name in keep_names:
                        continue
                    try:
                        old.unlink()
                    except Exception:
                        pass
            else:
                for old in preload_dir.glob("chunk_*.*"):
                    try:
                        old.unlink()
                    except Exception:
                        pass

            resumed_chunks = max(0, resumed_top_chunks)
            await _set_ai_long_preload_state(
                gid,
                status="synthesizing",
                message="正在继续后台转音频" if resumed_chunks > 0 else "正在后台转音频",
                title=title,
                script_hash=script_hash,
                total_chunks=total,
                completed_chunks=resumed_chunks,
                script_chars=len(script_text),
                manifest_ready=False,
                error="",
            )

            manifest_chunks: list[Dict[str, Any]] = list(reusable_chunks)
            if manifest_chunks:
                _write_preload_manifest(manifest_chunks, complete=False, completed_top_chunks=resumed_chunks)
            preload_timeout_base = _parse_env_float("CCCC_TTS_GPTSOVITS_PRELOAD_TIMEOUT_SEC", 55.0)
            preload_timeout_step = _parse_env_float("CCCC_TTS_GPTSOVITS_PRELOAD_TIMEOUT_STEP_SEC", 12.0)

            def _split_segment_for_retry(segment: str) -> tuple[str, str]:
                s = str(segment or "").strip()
                if len(s) < 2:
                    return s, ""
                mid = len(s) // 2
                punct = set("，。、；：,.!?！？ ")
                best = -1
                best_dist = 10**9
                lo = max(1, mid - 24)
                hi = min(len(s) - 1, mid + 24)
                for i in range(lo, hi):
                    if s[i] not in punct:
                        continue
                    d = abs(i - mid)
                    if d < best_dist:
                        best_dist = d
                        best = i
                split_at = best if best > 0 else mid
                left = s[:split_at].strip()
                right = s[split_at:].strip()
                if not left or not right:
                    split_at = mid
                    left = s[:split_at].strip()
                    right = s[split_at:].strip()
                return left, right

            async def _synthesize_segment(segment: str, depth: int = 0) -> list[tuple[str, bytes, str]]:
                text = str(segment or "").strip()
                if not text:
                    return []
                req = TTSSynthesizeRequest(
                    text=text,
                    style="ai_long",
                    lang="zh-CN",
                    engine="gpt_sovits_v4",
                    rate=1.0,
                    pitch=1.0,
                    volume=1.0,
                )
                last_error: Optional[Exception] = None
                for attempt in range(3):
                    try:
                        timeout_this_try = max(
                            12.0,
                            float(preload_timeout_base) + float(preload_timeout_step) * float(attempt) + float(depth) * 4.0,
                        )

                        def _synth_once() -> tuple[bytes, str]:
                            return _synthesize_via_gpt_sovits(req, timeout_override=timeout_this_try)

                        async with tts_synth_lock:
                            audio_bytes, media_type = await run_in_threadpool(_synth_once)
                        return [(text, audio_bytes, media_type)]
                    except Exception as e:
                        last_error = e
                        if attempt >= 2:
                            break
                        await asyncio.sleep(0.6 + attempt * 0.8)
                err_text = str(last_error or "").lower()
                retryable_timeout = "timed out" in err_text or "timeout" in err_text
                if retryable_timeout and depth < 4 and len(text) >= 36:
                    left, right = _split_segment_for_retry(text)
                    if left and right and left != text and right != text:
                        left_items = await _synthesize_segment(left, depth + 1)
                        right_items = await _synthesize_segment(right, depth + 1)
                        return [*left_items, *right_items]
                if retryable_timeout:
                    # Skip pathological segments instead of failing the whole preload job.
                    return []
                if last_error is not None:
                    raise last_error
                return []

            for idx in range(len(manifest_chunks), total):
                chunk = chunks[idx]
                segment_items = await _synthesize_segment(chunk, 0)
                for seg_text, audio_bytes, media_type in segment_items:
                    file_idx = len(manifest_chunks)
                    ext = "wav"
                    if "mpeg" in media_type or "mp3" in media_type:
                        ext = "mp3"
                    file_name = f"chunk_{file_idx:03d}.{ext}"
                    chunk_path = preload_dir / file_name
                    chunk_path.write_bytes(audio_bytes)
                    manifest_chunks.append(
                        {
                            "index": file_idx,
                            "text": seg_text,
                            "file": file_name,
                            "media_type": media_type,
                            "bytes": len(audio_bytes),
                            "source_chunk_index": idx,
                        }
                    )
                _write_preload_manifest(manifest_chunks, complete=False, completed_top_chunks=idx + 1)
                await _set_ai_long_preload_state(gid, completed_chunks=idx + 1)

            if not manifest_chunks:
                raise RuntimeError("未能生成任何可播放音频片段")
            _write_preload_manifest(manifest_chunks, complete=True, completed_top_chunks=total)
            await _set_ai_long_preload_state(
                gid,
                status="ready",
                message=f"预加载完成，共 {len(manifest_chunks)} 段",
                manifest_ready=True,
                completed_chunks=total,
                total_chunks=total,
                error="",
            )
        except asyncio.CancelledError:
            await _set_ai_long_preload_state(
                gid,
                status="idle",
                message="预加载已取消",
                manifest_ready=False,
                error="cancelled",
            )
            raise
        except Exception as e:
            await _set_ai_long_preload_state(
                gid,
                status="error",
                message="后台预加载失败",
                manifest_ready=False,
                error=str(e),
            )
        finally:
            async with ai_long_preload_lock:
                task = ai_long_preload_tasks.get(gid)
                if task is asyncio.current_task():
                    ai_long_preload_tasks.pop(gid, None)

    @app.get("/api/news/status")
    async def news_status(group_id: str = "") -> Dict[str, Any]:
        return await _broadcast_status("news", group_id)

    @app.post("/api/news/start")
    async def news_start(req: NewsAgentConfigRequest) -> Dict[str, Any]:
        return await _broadcast_start("news", group_id=req.group_id, interests=req.interests, schedule=req.schedule)

    @app.post("/api/news/stop")
    async def news_stop(req: IMActionRequest) -> Dict[str, Any]:
        return await _broadcast_stop("news", group_id=req.group_id)

    @app.get("/api/market/status")
    async def market_status(group_id: str = "") -> Dict[str, Any]:
        return await _broadcast_status("market", group_id)

    @app.post("/api/market/start")
    async def market_start(req: MarketAgentConfigRequest) -> Dict[str, Any]:
        return await _broadcast_start("market", group_id=req.group_id, interests=req.interests, schedule=req.schedule)

    @app.post("/api/market/stop")
    async def market_stop(req: IMActionRequest) -> Dict[str, Any]:
        return await _broadcast_stop("market", group_id=req.group_id)

    @app.get("/api/ai_long/status")
    async def ai_long_status(group_id: str = "") -> Dict[str, Any]:
        return await _broadcast_status("ai_long", group_id)

    @app.get("/api/ai_long/scripts")
    async def ai_long_scripts() -> Dict[str, Any]:
        return {"ok": True, "result": {"scripts": _list_ai_long_scripts()}}

    @app.post("/api/ai_long/start")
    async def ai_long_start(req: AILongAgentConfigRequest) -> Dict[str, Any]:
        return await _broadcast_start("ai_long", group_id=req.group_id, interests=req.interests, schedule=req.schedule)

    @app.post("/api/ai_long/stop")
    async def ai_long_stop(req: IMActionRequest) -> Dict[str, Any]:
        return await _broadcast_stop("ai_long", group_id=req.group_id)

    @app.get("/api/ai_long/preload/status")
    async def ai_long_preload_status(group_id: str = "") -> Dict[str, Any]:
        gid = str(group_id or "").strip()
        if not gid:
            return {"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id"}}
        group = load_group(gid)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {gid}"})
        state = await _get_ai_long_preload_state(gid)
        # Recover persisted ready state after server restart.
        if not state.get("running"):
            manifest_path = _ai_long_preload_dir(group) / "manifest.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    chunks = manifest.get("chunks")
                    if isinstance(chunks, list) and chunks:
                        chunk_count = len(chunks)
                        expected_total = int(manifest.get("expected_total_chunks") or 0)
                        completed_top = int(manifest.get("completed_top_chunks") or 0)
                        total = max(expected_total if expected_total > 0 else 0, completed_top, chunk_count)
                        complete = bool(manifest.get("complete")) or (expected_total > 0 and chunk_count >= expected_total)
                        if complete:
                            state.update(
                                {
                                    "status": "ready",
                                    "title": str(manifest.get("title") or state.get("title") or ""),
                                    "interests": str(manifest.get("interests") or state.get("interests") or ""),
                                    "script_hash": str(manifest.get("script_hash") or state.get("script_hash") or ""),
                                    "script_chars": int(manifest.get("script_chars") or 0),
                                    "total_chunks": total,
                                    "completed_chunks": total,
                                    "manifest_ready": True,
                                    "message": str(state.get("message") or "已存在后台预加载音频"),
                                }
                            )
                        else:
                            state.update(
                                {
                                    "status": "idle",
                                    "title": str(manifest.get("title") or state.get("title") or ""),
                                    "interests": str(manifest.get("interests") or state.get("interests") or ""),
                                    "script_hash": str(manifest.get("script_hash") or state.get("script_hash") or ""),
                                    "script_chars": int(manifest.get("script_chars") or 0),
                                    "total_chunks": total,
                                    "completed_chunks": min(total, completed_top if completed_top > 0 else chunk_count),
                                    "manifest_ready": False,
                                    "message": str(state.get("message") or "检测到未完成预加载，可继续"),
                                }
                            )
                except Exception:
                    pass
        return {"ok": True, "result": state}

    @app.post("/api/ai_long/preload/start")
    async def ai_long_preload_start(req: AILongPreloadRequest) -> Dict[str, Any]:
        gid = str(req.group_id or "").strip()
        if not gid:
            return {"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id"}}
        group = load_group(gid)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {gid}"})

        async with ai_long_preload_lock:
            existing = ai_long_preload_tasks.get(gid)
            if existing is not None and not existing.done():
                state = dict(ai_long_preload_state.get(gid) or {})
                return {
                    "ok": False,
                    "error": {"code": "already_running", "message": "AI 长文预加载正在进行中"},
                    "result": state,
                }
            script_key = str(req.script_key or "").strip()
            topic = str(req.topic or "").strip()
            interests = str(req.interests or "").strip()
            if topic:
                interests = topic
            task = asyncio.create_task(
                _run_ai_long_preload(
                    gid,
                    interests,
                    bool(req.force),
                    script_key=script_key,
                    topic=topic,
                )
            )
            ai_long_preload_tasks[gid] = task
            ai_long_preload_state[gid] = {
                "group_id": gid,
                "status": "queued",
                "title": "",
                "interests": interests,
                "script_key": script_key,
                "topic": topic,
                "message": "已进入后台预加载队列",
                "script_hash": "",
                "total_chunks": 0,
                "completed_chunks": 0,
                "script_chars": 0,
                "manifest_ready": False,
                "running": True,
                "updated_at": int(time.time()),
                "error": "",
            }
        return {"ok": True, "result": {"group_id": gid, "started": True}}

    @app.get("/api/ai_long/preload/manifest")
    async def ai_long_preload_manifest(group_id: str = "") -> Dict[str, Any]:
        gid = str(group_id or "").strip()
        if not gid:
            return {"ok": False, "error": {"code": "missing_group_id", "message": "missing group_id"}}
        group = load_group(gid)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {gid}"})
        doc = _read_ai_long_manifest(group)
        chunks_out: list[Dict[str, Any]] = []
        for item in doc.get("chunks") or []:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index") or len(chunks_out))
            chunks_out.append(
                {
                    "index": idx,
                    "text": str(item.get("text") or ""),
                    "media_type": str(item.get("media_type") or "audio/wav"),
                    "bytes": int(item.get("bytes") or 0),
                }
            )
        return {
            "ok": True,
            "result": {
                "group_id": gid,
                "title": str(doc.get("title") or ""),
                "interests": str(doc.get("interests") or ""),
                "script_key": str(doc.get("script_key") or ""),
                "topic": str(doc.get("topic") or ""),
                "script_hash": str(doc.get("script_hash") or ""),
                "script_chars": int(doc.get("script_chars") or 0),
                "chunks": chunks_out,
            },
        }

    @app.get("/api/ai_long/preload/chunk")
    async def ai_long_preload_chunk(group_id: str = "", index: int = 0) -> Response:
        gid = str(group_id or "").strip()
        if not gid:
            raise HTTPException(status_code=400, detail={"code": "missing_group_id", "message": "missing group_id"})
        group = load_group(gid)
        if group is None:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": f"group not found: {gid}"})
        doc = _read_ai_long_manifest(group)
        chunks = doc.get("chunks") or []
        if not isinstance(chunks, list) or index < 0 or index >= len(chunks):
            raise HTTPException(status_code=404, detail={"code": "chunk_not_found", "message": f"chunk index out of range: {index}"})
        chunk = chunks[index]
        if not isinstance(chunk, dict):
            raise HTTPException(status_code=500, detail={"code": "chunk_invalid", "message": "chunk manifest invalid"})
        rel = str(chunk.get("file") or "").strip()
        if not rel:
            raise HTTPException(status_code=500, detail={"code": "chunk_invalid", "message": "chunk file missing"})
        chunk_path = _ai_long_preload_dir(group) / rel
        if not chunk_path.exists() or not chunk_path.is_file():
            raise HTTPException(status_code=404, detail={"code": "chunk_file_missing", "message": f"chunk file missing: {rel}"})
        media_type = str(chunk.get("media_type") or "audio/wav")
        return FileResponse(path=chunk_path, media_type=media_type, headers={"Cache-Control": "no-store"})

    @app.get("/api/horror/status")
    async def horror_status(group_id: str = "") -> Dict[str, Any]:
        return await _broadcast_status("horror", group_id)

    @app.post("/api/horror/start")
    async def horror_start(req: HorrorAgentConfigRequest) -> Dict[str, Any]:
        return await _broadcast_start("horror", group_id=req.group_id, interests=req.interests, schedule=req.schedule)

    @app.post("/api/horror/stop")
    async def horror_stop(req: IMActionRequest) -> Dict[str, Any]:
        return await _broadcast_stop("horror", group_id=req.group_id)

    @app.get("/api/tts/providers")
    async def tts_providers() -> Dict[str, Any]:
        gpt_url = str(os.environ.get("CCCC_TTS_GPTSOVITS_URL") or "http://127.0.0.1:9880/tts").strip()
        gpt_available = False
        if gpt_url:
            gpt_available = await run_in_threadpool(_probe_tcp_endpoint, gpt_url, 0.5)
        return {
            "ok": True,
            "result": {
                "default_engine": "browser",
                "providers": [
                    {"engine": "browser", "label": "Browser TTS", "available": True},
                    {
                        "engine": "gpt_sovits_v4",
                        "label": "GPT-SoVITS v4",
                        "available": bool(gpt_available),
                        "endpoint": gpt_url,
                    },
                ],
            },
        }

    @app.post("/api/tts/synthesize")
    async def tts_synthesize(req: TTSSynthesizeRequest) -> Response:
        text = str(req.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail={"code": "invalid_text", "message": "text is required"})
        if req.engine != "gpt_sovits_v4":
            raise HTTPException(status_code=400, detail={"code": "unsupported_engine", "message": f"unsupported engine: {req.engine}"})
        preload_active = any(
            task is not None and not bool(task.done())
            for task in ai_long_preload_tasks.values()
        )
        default_wait_sec = 8.0 if preload_active else 0.35
        wait_sec = _parse_env_float("CCCC_TTS_GPTSOVITS_QUEUE_WAIT_SEC", default_wait_sec)
        acquired = False
        try:
            await asyncio.wait_for(tts_synth_lock.acquire(), timeout=max(0.0, wait_sec))
            acquired = True
        except TimeoutError as e:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "tts_busy",
                    "message": "TTS backend is busy, please retry shortly",
                    "details": {"queue_wait_sec": wait_sec, "preload_active": preload_active},
                },
            ) from e
        try:
            data, media_type = await run_in_threadpool(_synthesize_via_gpt_sovits, req)
        finally:
            if acquired:
                tts_synth_lock.release()
        return Response(content=data, media_type=media_type, headers={"Cache-Control": "no-store"})

    return app
