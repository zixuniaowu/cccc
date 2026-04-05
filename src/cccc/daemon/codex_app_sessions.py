from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from ..contracts.v1.message import ChatMessageData
from ..kernel.codex_events import append_codex_event
from ..kernel.group import load_group
from ..kernel.ledger import append_event
from ..kernel.message_sender_snapshot import build_sender_snapshot
from .runner_state_ops import headless_state_path, remove_headless_state
from ..util.fs import atomic_write_json
from ..util.process import pid_is_alive
from ..util.time import utc_now_iso

logger = logging.getLogger(__name__)


def _is_closed_stream_logging_error(exc: BaseException) -> bool:
    if not isinstance(exc, ValueError):
        return False
    message = str(exc or "").strip().lower()
    return "i/o operation on closed file" in message or "closed stream" in message


def _safe_logger_call(method: str, message: str, *args: Any, **kwargs: Any) -> None:
    log_method = getattr(logger, method, None)
    if not callable(log_method):
        return
    try:
        log_method(message, *args, **kwargs)
    except Exception as exc:
        if _is_closed_stream_logging_error(exc):
            return
        raise


def _jsonrpc_request(request_id: int, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": int(request_id), "method": method, "params": params}


@dataclass
class _PendingTurn:
    text: str
    event_id: str
    reply_to: Optional[str] = None


@dataclass
class CodexSessionState:
    status: str = "idle"
    current_task_id: Optional[str] = None
    updated_at: str = field(default_factory=utc_now_iso)
    thread_id: Optional[str] = None

    def to_headless_state(self, *, group_id: str, actor_id: str) -> Dict[str, Any]:
        return {
            "group_id": group_id,
            "actor_id": actor_id,
            "status": self.status,
            "current_task_id": self.current_task_id,
            "updated_at": self.updated_at,
        }


class CodexAppSession:
    def __init__(self, *, group_id: str, actor_id: str, cwd: Path, env: Dict[str, str], model: str = "gpt-5.4") -> None:
        self.group_id = str(group_id or "").strip()
        self.actor_id = str(actor_id or "").strip()
        self.cwd = cwd
        self.env = dict(env or {})
        self.model = str(model or "gpt-5.4").strip() or "gpt-5.4"
        self._proc: Optional[subprocess.Popen[str]] = None
        self._lock = threading.Lock()
        self._pending: Dict[int, "queue.Queue[Dict[str, Any]]"] = {}
        self._next_request_id = 1
        self._running = False
        self._session_state = CodexSessionState(status="idle")
        self._turn_queue: "queue.Queue[Optional[_PendingTurn]]" = queue.Queue()
        self._turn_done = threading.Event()
        self._active_turn_id = ""
        self._active_event_id = ""
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._turn_thread: Optional[threading.Thread] = None
        self._completed_stream_ids: set[str] = set()
        self._plan_activity_id = ""
        self._agent_message_phase_by_stream_id: Dict[str, str] = {}

    def _agent_message_phase(self, item_id: str, item: Optional[Dict[str, Any]] = None) -> str:
        stream_id = str(item_id or "").strip()
        if not stream_id:
            return ""
        if isinstance(item, dict):
            phase = str(item.get("phase") or "").strip().lower()
            if phase:
                self._agent_message_phase_by_stream_id[stream_id] = phase
                return phase
        return str(self._agent_message_phase_by_stream_id.get(stream_id) or "").strip().lower()

    def _persist_state(self) -> None:
        with self._lock:
            proc = self._proc
            running = bool(self._running and proc is not None and proc.poll() is None)
            state = self._session_state.to_headless_state(group_id=self.group_id, actor_id=self.actor_id)
            pid = int(proc.pid) if running and proc is not None else 0
        if not running or pid <= 0 or not pid_is_alive(pid):
            remove_headless_state(self.group_id, self.actor_id)
            return
        atomic_write_json(
            headless_state_path(self.group_id, self.actor_id),
            {
                "v": 1,
                "kind": "headless",
                "runtime": "codex",
                "pid": pid,
                **state,
            },
        )

    def _emit_activity(
        self,
        *,
        status: str,
        activity_id: str,
        kind: str,
        summary: str,
        turn_id: str = "",
        stream_id: str = "",
        item_id: str = "",
        detail: Optional[str] = None,
    ) -> None:
        normalized_status = str(status or "").strip()
        normalized_activity_id = str(activity_id or "").strip()
        normalized_kind = str(kind or "").strip()
        normalized_summary = str(summary or "").strip()
        if not normalized_status or not normalized_activity_id or not normalized_kind or not normalized_summary:
            return
        with self._lock:
            active_event_id = str(self._active_event_id or "").strip()
        self._emit(
            f"codex.activity.{normalized_status}",
            {
                "activity_id": normalized_activity_id,
                "kind": normalized_kind,
                "summary": normalized_summary,
                "detail": str(detail or "").strip() or None,
                "turn_id": str(turn_id or "").strip(),
                "stream_id": str(stream_id or "").strip(),
                "item_id": str(item_id or "").strip(),
                "event_id": active_event_id,
            },
        )

    @staticmethod
    def _trim_single_line(value: Any, *, limit: int = 120) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    def _emit_item_activity(self, *, status: str, turn_id: str, item: Dict[str, Any]) -> None:
        item_type = str(item.get("type") or "").strip()
        item_id = str(item.get("id") or "").strip()
        if not item_type or not item_id:
            return

        if item_type == "agentMessage":
            phase = str(item.get("phase") or "").strip().lower()
            if phase and phase != "final_answer":
                return
            summary = "replying" if status != "completed" else "reply ready"
            self._emit_activity(
                status=status,
                activity_id=f"reply:{item_id}",
                kind="reply",
                summary=summary,
                turn_id=turn_id,
                stream_id=item_id,
                item_id=item_id,
            )
            return

        if item_type == "reasoning":
            summary = "thinking" if status != "completed" else "thinking done"
            self._emit_activity(
                status=status,
                activity_id=f"reasoning:{item_id}",
                kind="thinking",
                summary=summary,
                turn_id=turn_id,
                item_id=item_id,
            )
            return

        if item_type == "commandExecution":
            command = self._trim_single_line(item.get("command") or "command")
            detail = self._trim_single_line(item.get("aggregatedOutput") or "", limit=160)
            self._emit_activity(
                status=status,
                activity_id=f"command:{item_id}",
                kind="command",
                summary=command or "command",
                detail=detail or None,
                turn_id=turn_id,
                item_id=item_id,
            )
            return

        if item_type == "fileChange":
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            targets = []
            for change in changes[:3]:
                if not isinstance(change, dict):
                    continue
                path = self._trim_single_line(change.get("path") or change.get("filePath") or "", limit=80)
                if path:
                    targets.append(path)
            summary = f"patch {', '.join(targets)}" if targets else "patch files"
            self._emit_activity(
                status=status,
                activity_id=f"file:{item_id}",
                kind="patch",
                summary=summary,
                turn_id=turn_id,
                item_id=item_id,
            )
            return

        if item_type == "mcpToolCall":
            server = self._trim_single_line(item.get("server") or "", limit=40)
            tool = self._trim_single_line(item.get("tool") or "tool", limit=60)
            summary = f"{server}:{tool}" if server else tool
            self._emit_activity(
                status=status,
                activity_id=f"mcp:{item_id}",
                kind="tool",
                summary=summary,
                turn_id=turn_id,
                item_id=item_id,
            )
            return

        if item_type == "dynamicToolCall":
            tool = self._trim_single_line(item.get("tool") or "tool", limit=80)
            self._emit_activity(
                status=status,
                activity_id=f"tool:{item_id}",
                kind="tool",
                summary=tool,
                turn_id=turn_id,
                item_id=item_id,
            )
            return

        if item_type == "webSearch":
            query = self._trim_single_line(item.get("query") or "web search", limit=100)
            self._emit_activity(
                status=status,
                activity_id=f"search:{item_id}",
                kind="search",
                summary=query,
                turn_id=turn_id,
                item_id=item_id,
            )
            return

        if item_type == "plan":
            text = self._trim_single_line(item.get("text") or "plan updated", limit=100)
            self._emit_activity(
                status=status,
                activity_id=f"plan:{item_id}",
                kind="plan",
                summary=text,
                turn_id=turn_id,
                item_id=item_id,
            )
            return

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            env = os.environ.copy()
            env.update(self.env)
            self._proc = subprocess.Popen(
                ["codex", "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.cwd),
                env=env,
                text=True,
                bufsize=1,
            )
            self._running = True

        self._stdout_thread = threading.Thread(target=self._stdout_loop, name=f"cccc-codex-out:{self.group_id}:{self.actor_id}", daemon=True)
        self._stderr_thread = threading.Thread(target=self._stderr_loop, name=f"cccc-codex-err:{self.group_id}:{self.actor_id}", daemon=True)
        self._turn_thread = threading.Thread(target=self._turn_loop, name=f"cccc-codex-turn:{self.group_id}:{self.actor_id}", daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        try:
            self._request(
                "initialize",
                {
                    "clientInfo": {"name": "cccc", "version": "1.0"},
                    "capabilities": {"experimentalApi": True},
                },
                timeout=10.0,
            )
            thread_resp = self._request(
                "thread/start",
                {
                    "cwd": str(self.cwd),
                    "approvalPolicy": "never",
                    "sandbox": "danger-full-access",
                    "model": self.model,
                    "personality": "pragmatic",
                },
                timeout=20.0,
            )
            thread = thread_resp.get("thread") if isinstance(thread_resp, dict) else {}
            thread_id = str((thread or {}).get("id") or "").strip()
            if not thread_id:
                raise RuntimeError("codex app-server returned empty thread id")
            with self._lock:
                self._session_state.thread_id = thread_id
                self._session_state.status = "idle"
                self._session_state.updated_at = utc_now_iso()
            self._persist_state()
            self._emit("codex.thread.started", {"thread_id": thread_id})
            self._turn_thread.start()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._running = False
            self._proc = None
            self._session_state.status = "stopped"
            self._session_state.current_task_id = None
            self._session_state.updated_at = utc_now_iso()
        self._persist_state()
        self._turn_done.set()
        try:
            self._turn_queue.put_nowait(None)
        except Exception:
            pass
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._emit("codex.session.stopped", {})

    def is_running(self) -> bool:
        with self._lock:
            proc = self._proc
            return bool(self._running and proc is not None and proc.poll() is None)

    def state(self) -> Dict[str, Any]:
        with self._lock:
            return self._session_state.to_headless_state(group_id=self.group_id, actor_id=self.actor_id)

    def _thread_id(self) -> str:
        with self._lock:
            return str(self._session_state.thread_id or "").strip()

    def submit_user_message(self, *, text: str, event_id: str, reply_to: Optional[str] = None) -> bool:
        if not self.is_running():
            return False
        payload = _PendingTurn(text=str(text or ""), event_id=str(event_id or "").strip(), reply_to=reply_to)
        try:
            self._turn_queue.put_nowait(payload)
            self._emit(
                "codex.turn.queued",
                {
                    "event_id": payload.event_id,
                    "reply_to": payload.reply_to,
                },
            )
            return True
        except Exception:
            return False

    def _request(self, method: str, params: Dict[str, Any], *, timeout: float) -> Dict[str, Any]:
        with self._lock:
            if not self._running or self._proc is None or self._proc.stdin is None:
                raise RuntimeError("codex session is not running")
            request_id = self._next_request_id
            self._next_request_id += 1
            result_q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1)
            self._pending[request_id] = result_q
            message = json.dumps(_jsonrpc_request(request_id, method, params), ensure_ascii=False)
            self._proc.stdin.write(message + "\n")
            self._proc.stdin.flush()
        try:
            response = result_q.get(timeout=timeout)
        except queue.Empty as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise RuntimeError(f"codex request timed out: {method}") from exc
        if "error" in response:
            raise RuntimeError(str((response.get("error") or {}).get("message") or f"codex request failed: {method}"))
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def _stdout_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw_line in proc.stdout:
                line = str(raw_line or "").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except Exception:
                    logger.debug("ignore non-json codex output: %s", line[:200])
                    continue
                if "id" in message:
                    request_id = int(message.get("id") or 0)
                    with self._lock:
                        result_q = self._pending.pop(request_id, None)
                    if result_q is not None:
                        result_q.put_nowait(message)
                    continue
                method = str(message.get("method") or "").strip()
                params = message.get("params")
                if method:
                    self._handle_notification(method, params if isinstance(params, dict) else {})
        except Exception:
            logger.exception("codex stdout loop failed: %s/%s", self.group_id, self.actor_id)
        finally:
            self.stop()

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw_line in proc.stderr:
                line = str(raw_line or "").rstrip()
                if line:
                    _safe_logger_call("info", "[codex-app %s/%s] %s", self.group_id, self.actor_id, line)
        except Exception as exc:
            if _is_closed_stream_logging_error(exc):
                return
            _safe_logger_call("exception", "codex stderr loop failed: %s/%s", self.group_id, self.actor_id)

    def _turn_loop(self) -> None:
        while self.is_running():
            try:
                payload = self._turn_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if payload is None:
                return
            thread_id = self._thread_id()
            if not thread_id:
                continue
            self._turn_done.clear()
            turn_id = ""
            try:
                response = self._request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": payload.text}],
                    },
                    timeout=30.0,
                )
                turn = response.get("turn") if isinstance(response, dict) else {}
                turn_id = str((turn or {}).get("id") or "").strip()
                with self._lock:
                    self._active_turn_id = turn_id
                    self._active_event_id = payload.event_id
                    self._session_state.status = "working"
                    self._session_state.current_task_id = turn_id or payload.event_id or None
                    self._session_state.updated_at = utc_now_iso()
                self._persist_state()
                self._emit(
                    "codex.turn.started",
                    {
                        "turn_id": turn_id,
                        "event_id": payload.event_id,
                        "reply_to": payload.reply_to,
                    },
                )
            except Exception as exc:
                logger.warning("codex turn start failed: group=%s actor=%s err=%s", self.group_id, self.actor_id, exc)
                with self._lock:
                    self._session_state.status = "idle"
                    self._active_event_id = ""
                    self._session_state.current_task_id = None
                    self._session_state.updated_at = utc_now_iso()
                self._persist_state()
                self._emit(
                    "codex.turn.failed",
                    {
                        "turn_id": turn_id,
                        "event_id": payload.event_id,
                        "error": str(exc),
                    },
                )
                continue
            self._turn_done.wait()

    def _handle_notification(self, method: str, params: Dict[str, Any]) -> None:
        now = utc_now_iso()
        with self._lock:
            active_event_id = str(self._active_event_id or "").strip()
        if method == "turn/started":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or "").strip()
            with self._lock:
                self._active_turn_id = turn_id
                self._session_state.status = "working"
                self._session_state.current_task_id = turn_id or None
                self._session_state.updated_at = now
            self._persist_state()
            self._emit("codex.turn.progress", {"turn_id": turn_id, "event_id": active_event_id, "status": "working"})
            self._emit_activity(
                status="started",
                activity_id=f"turn:{turn_id or active_event_id or 'current'}",
                kind="thinking",
                summary="thinking",
                turn_id=turn_id,
            )
            return

        if method == "turn/plan/updated":
            turn_id = str(params.get("turnId") or "").strip()
            steps = params.get("plan") if isinstance(params.get("plan"), list) else []
            explanation = self._trim_single_line(params.get("explanation") or "", limit=100)
            current = ""
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_text = self._trim_single_line(step.get("step") or "", limit=100)
                if not step_text:
                    continue
                if str(step.get("status") or "").strip() == "in_progress":
                    current = step_text
                    break
                if not current:
                    current = step_text
            summary = current or explanation or "plan updated"
            activity_id = self._plan_activity_id or f"plan:{turn_id or active_event_id or 'current'}"
            self._plan_activity_id = activity_id
            self._emit_activity(
                status="updated",
                activity_id=activity_id,
                kind="plan",
                summary=summary,
                detail=explanation or None,
                turn_id=turn_id,
            )
            return

        if method == "item/started":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            item_type = str(item.get("type") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if item_type == "agentMessage" and item_id:
                phase = self._agent_message_phase(item_id, item)
                payload = {
                    "turn_id": str(params.get("turnId") or ""),
                    "event_id": active_event_id,
                    "stream_id": item_id,
                    "item": item,
                }
                if phase:
                    payload["phase"] = phase
                self._emit("codex.message.started", payload)
            else:
                self._emit("codex.item.started", {"turn_id": str(params.get("turnId") or ""), "event_id": active_event_id, "stream_id": item_id, "item": item})
            self._emit_item_activity(status="started", turn_id=str(params.get("turnId") or ""), item=item)
            return

        if method == "item/agentMessage/delta":
            stream_id = str(params.get("itemId") or "").strip()
            delta = str(params.get("delta") or "")
            if not stream_id or not delta:
                return
            phase = self._agent_message_phase(stream_id)
            payload = {
                "turn_id": str(params.get("turnId") or ""),
                "event_id": active_event_id,
                "stream_id": stream_id,
                "delta": delta,
            }
            if phase:
                payload["phase"] = phase
            self._emit("codex.message.delta", payload)
            return

        if method == "item/reasoning/summaryTextDelta":
            item_id = str(params.get("itemId") or "").strip()
            turn_id = str(params.get("turnId") or "").strip()
            delta = self._trim_single_line(params.get("delta") or "", limit=120)
            if item_id and delta:
                self._emit_activity(
                    status="updated",
                    activity_id=f"reasoning:{item_id}",
                    kind="thinking",
                    summary=delta,
                    turn_id=turn_id,
                    item_id=item_id,
                )
            return

        if method == "item/commandExecution/outputDelta":
            item_id = str(params.get("itemId") or "").strip()
            turn_id = str(params.get("turnId") or "").strip()
            delta = self._trim_single_line(params.get("delta") or "", limit=120)
            if item_id and delta:
                self._emit_activity(
                    status="updated",
                    activity_id=f"command:{item_id}",
                    kind="command",
                    summary=delta,
                    turn_id=turn_id,
                    item_id=item_id,
                )
            return

        if method == "item/fileChange/outputDelta":
            item_id = str(params.get("itemId") or "").strip()
            turn_id = str(params.get("turnId") or "").strip()
            delta = self._trim_single_line(params.get("delta") or "", limit=120)
            if item_id and delta:
                self._emit_activity(
                    status="updated",
                    activity_id=f"file:{item_id}",
                    kind="patch",
                    summary=delta,
                    turn_id=turn_id,
                    item_id=item_id,
                )
            return

        if method == "item/mcpToolCall/progress":
            item_id = str(params.get("itemId") or "").strip()
            turn_id = str(params.get("turnId") or "").strip()
            message = self._trim_single_line(params.get("message") or "", limit=120)
            if item_id and message:
                self._emit_activity(
                    status="updated",
                    activity_id=f"mcp:{item_id}",
                    kind="tool",
                    summary=message,
                    turn_id=turn_id,
                    item_id=item_id,
                )
            return

        if method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            item_type = str(item.get("type") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if item_type == "agentMessage" and item_id:
                phase = self._agent_message_phase(item_id, item)
                self._agent_message_phase_by_stream_id.pop(item_id, None)
                text = str(item.get("text") or "")
                if phase != "commentary" and item_id not in self._completed_stream_ids:
                    self._completed_stream_ids.add(item_id)
                    self._append_actor_message(
                        stream_id=item_id,
                        text=text,
                        pending_event_id=active_event_id,
                    )
                payload = {
                    "turn_id": str(params.get("turnId") or ""),
                    "event_id": active_event_id,
                    "stream_id": item_id,
                    "text": text,
                }
                if phase:
                    payload["phase"] = phase
                self._emit("codex.message.completed", payload)
                self._emit_item_activity(status="completed", turn_id=str(params.get("turnId") or ""), item=item)
                return
            self._emit_item_activity(status="completed", turn_id=str(params.get("turnId") or ""), item=item)
            self._emit("codex.item.completed", {"turn_id": str(params.get("turnId") or ""), "event_id": active_event_id, "stream_id": item_id, "item": item})
            return

        if method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or "").strip()
            status = str(turn.get("status") or "completed").strip() or "completed"
            error = turn.get("error") if isinstance(turn.get("error"), dict) else None
            with self._lock:
                self._active_turn_id = ""
                self._active_event_id = ""
                self._session_state.status = "idle"
                self._session_state.current_task_id = None
                self._session_state.updated_at = now
            self._persist_state()
            self._completed_stream_ids.clear()
            self._agent_message_phase_by_stream_id.clear()
            if self._plan_activity_id:
                self._emit_activity(
                    status="completed",
                    activity_id=self._plan_activity_id,
                    kind="plan",
                    summary="plan finished",
                    turn_id=turn_id,
                )
                self._plan_activity_id = ""
            self._emit(
                "codex.turn.completed",
                {
                    "turn_id": turn_id,
                    "event_id": active_event_id,
                    "status": status,
                    "error": error,
                },
            )
            self._turn_done.set()
            return

    def _append_actor_message(self, *, stream_id: str, text: str, pending_event_id: str = "") -> None:
        group = load_group(self.group_id)
        if group is None:
            return
        try:
            append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group.group_id,
                scope_key=str(group.doc.get("active_scope_key") or "").strip(),
                by=self.actor_id,
                data=ChatMessageData(
                    text=str(text or ""),
                    format="plain",
                    to=["user"],
                    stream_id=str(stream_id or "").strip() or None,
                    pending_event_id=str(pending_event_id or "").strip() or None,
                    **build_sender_snapshot(group, by=self.actor_id),
                ).model_dump(),
            )
        except Exception:
            logger.exception("failed to append codex actor message: %s/%s", self.group_id, self.actor_id)

    def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        group = load_group(self.group_id)
        if group is None:
            return
        try:
            append_codex_event(
                group.path,
                group_id=self.group_id,
                actor_id=self.actor_id,
                event_type=event_type,
                data=data,
            )
        except Exception:
            logger.exception("failed to append codex event: %s/%s %s", self.group_id, self.actor_id, event_type)


class CodexAppSessionManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[tuple[str, str], CodexAppSession] = {}

    def start_actor(self, *, group_id: str, actor_id: str, cwd: Path, env: Dict[str, str], model: str = "gpt-5.4") -> CodexAppSession:
        key = (str(group_id or "").strip(), str(actor_id or "").strip())
        if not key[0] or not key[1]:
            raise ValueError("missing group_id/actor_id")
        with self._lock:
            session = self._sessions.get(key)
            if session is not None and session.is_running():
                return session
            session = CodexAppSession(group_id=key[0], actor_id=key[1], cwd=cwd, env=env, model=model)
            self._sessions[key] = session
        try:
            session.start()
        except Exception:
            with self._lock:
                if self._sessions.get(key) is session:
                    self._sessions.pop(key, None)
            raise
        return session

    def stop_actor(self, *, group_id: str, actor_id: str) -> None:
        key = (str(group_id or "").strip(), str(actor_id or "").strip())
        with self._lock:
            session = self._sessions.pop(key, None)
        if session is not None:
            session.stop()

    def stop_group(self, *, group_id: str) -> None:
        gid = str(group_id or "").strip()
        if not gid:
            return
        with self._lock:
            keys = [key for key in self._sessions if key[0] == gid]
            sessions = [self._sessions.pop(key) for key in keys]
        for session in sessions:
            session.stop()

    def stop_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.stop()

    def actor_running(self, group_id: str, actor_id: str) -> bool:
        key = (str(group_id or "").strip(), str(actor_id or "").strip())
        with self._lock:
            session = self._sessions.get(key)
        return bool(session and session.is_running())

    def group_running(self, group_id: str) -> bool:
        gid = str(group_id or "").strip()
        if not gid:
            return False
        with self._lock:
            sessions = [session for (session_gid, _), session in self._sessions.items() if session_gid == gid]
        return any(session.is_running() for session in sessions)

    def get_state(self, *, group_id: str, actor_id: str) -> Optional[Dict[str, Any]]:
        key = (str(group_id or "").strip(), str(actor_id or "").strip())
        with self._lock:
            session = self._sessions.get(key)
        return session.state() if session is not None and session.is_running() else None

    def submit_user_message(self, *, group_id: str, actor_id: str, text: str, event_id: str, reply_to: Optional[str] = None) -> bool:
        key = (str(group_id or "").strip(), str(actor_id or "").strip())
        with self._lock:
            session = self._sessions.get(key)
        if session is None:
            return False
        return session.submit_user_message(text=text, event_id=event_id, reply_to=reply_to)


SUPERVISOR = CodexAppSessionManager()
