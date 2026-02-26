"""
CCCC MCP Server - IM-style Agent Collaboration Tools

Core tools exposed to agents:

cccc.* namespace (collaboration control plane):
- cccc_help: CCCC help playbook (authoritative; returns effective CCCC_HELP.md if present)
- cccc_bootstrap: One-call session bootstrap (group+help+project+context+inbox)
- cccc_inbox_list: Get unread messages (supports kind_filter)
- cccc_inbox_mark_read: Mark messages as read
- cccc_inbox_mark_all_read: Mark all current unread messages as read
- cccc_message_send: Send message
- cccc_message_reply: Reply to message
- cccc_file_send: Send a local file as an attachment
- cccc_blob_path: Resolve attachment blob path
- cccc_group_info: Get group info
- cccc_actor_list: Get actor list
- cccc_actor_profile_list: List reusable Actor Profiles
- cccc_actor_add: Add new actor (foreman only)
- cccc_actor_remove: Remove an actor (foreman/peer can only remove themselves)
- cccc_actor_start: Start actor
- cccc_actor_stop: Stop actor
- cccc_runtime_list: List available agent runtimes
- cccc_capability_search: Search capability registry (progressive disclosure)
- cccc_capability_enable: Enable/disable capability packs by scope
- cccc_capability_state: Read effective visible tool surface
- cccc_capability_uninstall: Uninstall cached external capability + revoke bindings
- cccc_capability_use: One-step enable+call convenience helper
- cccc_space_status: Read Group Space provider/binding/queue status
- cccc_space_capabilities: Read Group Space local file policy + ingest schema matrix
- cccc_space_bind: Bind or unbind Group Space provider mapping
- cccc_space_ingest: Enqueue and execute Group Space ingest job
- cccc_space_query: Query provider-backed Group Space knowledge
- cccc_space_sources: List/refresh/rename/delete provider sources for the bound notebook
- cccc_space_artifact: List/generate/download provider artifacts (can auto-save to repo space/artifacts)
- cccc_space_jobs: List/retry/cancel Group Space jobs
- cccc_space_sync: Read or run repo/space synchronization reconcile
- cccc_space_provider_auth: Control provider auth flow (status/start/cancel)
- cccc_space_provider_credential_status: Read provider credential status (masked)
- cccc_space_provider_credential_update: Update/clear provider credential
- cccc_automation_state: Read automation reminders/status visible to caller
- cccc_automation_manage: Manage automation reminders (MCP actor writes are notify-only)
- cccc_project_info: Get PROJECT.md content (project goals/constraints)

context.* namespace (state sync):
- cccc_context_get: Get full context
- cccc_context_sync: Batch sync operations
- cccc_vision_update / cccc_sketch_update: Vision/sketch
- cccc_milestone_*: Milestone management (create/update/complete/remove)
- cccc_task_*: Task management (list/create/update/delete)
- cccc_note_*: Note management (add/update/remove)
- cccc_reference_*: Reference management (add/update/remove)
- cccc_presence_*: Presence status (get/update/clear)

headless.* namespace (headless runner control):
- cccc_headless_status: Get headless session status
- cccc_headless_set_status: Update status (idle/working/waiting/stopped)
- cccc_headless_ack_message: Acknowledge processed message

notify.* namespace (system notifications, separate from chat):
- cccc_notify_send: Send system notification
- cccc_notify_ack: Acknowledge system notification

terminal.* namespace (diagnostics):
- cccc_terminal_tail: Tail an actor terminal transcript (group policy)

debug.* namespace (developer mode diagnostics):
- cccc_debug_snapshot: Get a structured debug snapshot (dev mode)
- cccc_debug_tail_logs: Tail local CCCC logs (dev mode)

All operations go through daemon IPC to ensure single-writer principle.
"""

from __future__ import annotations

import mimetypes
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...kernel.blobs import resolve_blob_attachment_path, store_blob_bytes
from ...kernel.actors import get_effective_role
from ...kernel.group import load_group
from ...kernel.inbox import is_message_for_actor
from ...kernel.ledger import read_last_lines
from ...kernel.memory_guide import build_memory_guide
from ...kernel.capabilities import CORE_TOOL_NAMES, BUILTIN_CAPABILITY_PACKS
from ...kernel.prompt_files import HELP_FILENAME, load_builtin_help_markdown as _load_builtin_help_markdown, read_group_prompt_file
from ...util.conv import coerce_bool
from .handlers.context import (
    _handle_context_namespace as _handle_context_namespace_impl,
    context_get,
    context_sync,
    milestone_complete,
    milestone_create,
    milestone_update,
    note_add,
    note_remove,
    note_update,
    presence_clear,
    presence_get,
    presence_update,
    reference_add,
    reference_remove,
    reference_update,
    sketch_update,
    task_create,
    task_list,
    task_update,
    vision_update,
)
from .handlers.debug import (
    _handle_debug_namespace as _handle_debug_namespace_impl,
    _handle_terminal_namespace as _handle_terminal_namespace_impl,
    debug_snapshot,
    debug_tail_logs,
    terminal_tail,
)
from .handlers.headless import (
    _handle_headless_namespace as _handle_headless_namespace_impl,
    headless_ack_message,
    headless_set_status,
    headless_status,
)
from .handlers.memory import _handle_memory_namespace as _handle_memory_namespace_impl
from .handlers.notify import (
    _handle_notify_namespace as _handle_notify_namespace_impl,
    notify_ack,
    notify_send,
)
from .utils.help_markdown import _select_help_markdown
from .utils.space_args import _infer_artifact_language_from_source, _normalize_space_query_options_mcp
from .common import (
    MCPError,
    _call_daemon_or_raise,
    _env_str,
    _resolve_caller_actor_id,
    _resolve_caller_from_by,
    _resolve_group_id,
    _resolve_self_actor_id,
)
from .toolspecs import MCP_TOOLS


_CCCC_HELP_BUILTIN = _load_builtin_help_markdown().strip()

def _append_runtime_skill_digest(markdown: str, *, group_id: str, actor_id: str) -> str:
    base = str(markdown or "")
    if not base.strip():
        return base
    if "## Active Skills (Runtime)" in base:
        return base
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    if not gid or not aid:
        return base
    try:
        state = _call_daemon_or_raise(
            {"op": "capability_state", "args": {"group_id": gid, "actor_id": aid, "by": aid}},
            timeout_s=3.0,
        )
    except Exception:
        return base
    active = state.get("active_skills") if isinstance(state, dict) else []
    pinned = state.get("pinned_skills") if isinstance(state, dict) else []
    active_list = active if isinstance(active, list) else []
    pinned_list = pinned if isinstance(pinned, list) else []
    if not active_list and not pinned_list:
        return base
    lines: List[str] = ["## Active Skills (Runtime)"]
    if pinned_list:
        lines.append("- pinned:")
        for item in pinned_list[:8]:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("capability_id") or "").strip()
            name = str(item.get("name") or sid).strip()
            desc = str(item.get("description_short") or "").strip()
            line = f"  - {name} ({sid})"
            if desc:
                line += f": {desc[:120]}"
            lines.append(line)
    if active_list:
        lines.append("- active_now:")
        for item in active_list[:8]:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("capability_id") or "").strip()
            name = str(item.get("name") or sid).strip()
            desc = str(item.get("description_short") or "").strip()
            line = f"  - {name} ({sid})"
            if desc:
                line += f": {desc[:120]}"
            lines.append(line)
    return base.rstrip() + "\n\n" + "\n".join(lines).rstrip() + "\n"

# =============================================================================
# Inbox Tools
# =============================================================================


def inbox_list(*, group_id: str, actor_id: str, limit: int = 50, kind_filter: str = "all") -> Dict[str, Any]:
    """Get actor's unread message list
    
    Args:
        kind_filter: "all" | "chat" | "notify"
    """
    return _call_daemon_or_raise({
        "op": "inbox_list",
        "args": {"group_id": group_id, "actor_id": actor_id, "by": actor_id, "limit": limit, "kind_filter": kind_filter},
    })


def inbox_mark_read(*, group_id: str, actor_id: str, event_id: str) -> Dict[str, Any]:
    """Mark messages as read up to specified event"""
    return _call_daemon_or_raise({
        "op": "inbox_mark_read",
        "args": {"group_id": group_id, "actor_id": actor_id, "event_id": event_id, "by": actor_id},
    })


def inbox_mark_all_read(*, group_id: str, actor_id: str, kind_filter: str = "all") -> Dict[str, Any]:
    """Mark all currently-unread messages as read (safe: only up to current latest unread)."""
    return _call_daemon_or_raise({
        "op": "inbox_mark_all_read",
        "args": {"group_id": group_id, "actor_id": actor_id, "kind_filter": kind_filter, "by": actor_id},
    })


def bootstrap(
    *,
    group_id: str,
    actor_id: str,
    inbox_limit: int = 50,
    inbox_kind_filter: str = "all",
    ledger_tail_limit: int = 10,
    ledger_tail_max_chars: int = 8000,
) -> Dict[str, Any]:
    """One-call session bootstrap for agents.

    Returns:
    - group: group metadata
    - actors: actor list (roles + runtime)
    - help: effective CCCC help playbook (markdown + source)
    - project: PROJECT.md info
    - context: group context
    - inbox: unread messages
    - ledger_tail: recent chat.message tail (optional)
    """
    gi = group_info(group_id=group_id)
    group = gi.get("group") if isinstance(gi, dict) else None

    al = actor_list(group_id=group_id)
    actors = al.get("actors") if isinstance(al, dict) else None

    help_payload: Dict[str, Any] = {
        "markdown": _append_runtime_skill_digest(
            _select_help_markdown(_CCCC_HELP_BUILTIN, role=None, actor_id=None),
            group_id=group_id,
            actor_id=actor_id,
        ),
        "source": "cccc.resources/cccc-help.md",
    }
    try:
        g = load_group(str(group_id or "").strip())
        if g is not None:
            role = get_effective_role(g, str(actor_id or "").strip())
            pf = read_group_prompt_file(g, HELP_FILENAME)
            if pf.found and isinstance(pf.content, str) and pf.content.strip():
                help_payload = {
                    "markdown": _append_runtime_skill_digest(
                        _select_help_markdown(pf.content, role=role, actor_id=actor_id),
                        group_id=group_id,
                        actor_id=actor_id,
                    ),
                    "source": str(pf.path or ""),
                }
            else:
                help_payload = {
                    "markdown": _append_runtime_skill_digest(
                        _select_help_markdown(_CCCC_HELP_BUILTIN, role=role, actor_id=actor_id),
                        group_id=group_id,
                        actor_id=actor_id,
                    ),
                    "source": "cccc.resources/cccc-help.md",
                }
    except Exception:
        pass

    project = project_info(group_id=group_id)
    context = context_get(group_id=group_id)
    inbox = inbox_list(group_id=group_id, actor_id=actor_id, limit=int(inbox_limit or 50), kind_filter=inbox_kind_filter)

    # Recent chat tail (for resuming mid-task). Keep it small: only chat.message.
    ledger_tail: List[Dict[str, Any]] = []
    ledger_tail_truncated = False
    try:
        limit = int(ledger_tail_limit or 0)
        max_chars = int(ledger_tail_max_chars or 0)
        if limit > 0 and max_chars > 0:
            g = load_group(str(group_id or "").strip())
            if g is not None:
                # Read a reasonable tail of lines, then filter to chat.message and take the last N.
                # This avoids scanning the whole file while still being robust to non-chat events.
                read_lines = min(2000, max(200, limit * 10))
                lines = read_last_lines(g.ledger_path, read_lines)
                chat_events: List[Dict[str, Any]] = []
                for raw in lines:
                    try:
                        ev = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(ev, dict) or str(ev.get("kind") or "") != "chat.message":
                        continue
                    # Respect delivery semantics: only include messages visible to the caller (or sent by the caller).
                    # This prevents leaking directed messages via the convenience "ledger_tail" bootstrap field.
                    by = str(ev.get("by") or "").strip()
                    if by != str(actor_id or "").strip() and not is_message_for_actor(g, actor_id=actor_id, event=ev):
                        continue
                    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                    text = data.get("text") if isinstance(data, dict) else None
                    if not isinstance(text, str) or not text:
                        continue
                    to = data.get("to") if isinstance(data, dict) else None
                    to_list = [str(x) for x in to] if isinstance(to, list) else []
                    chat_events.append(
                        {
                            "id": str(ev.get("id") or ""),
                            "ts": str(ev.get("ts") or ""),
                            "by": by,
                            "to": to_list,
                            "text": text,
                        }
                    )
                chat_events = chat_events[-limit:]

                # Enforce a max total character budget across returned message texts.
                used = 0
                for ev in chat_events:
                    remaining = max_chars - used
                    if remaining <= 0:
                        ledger_tail_truncated = True
                        break
                    t = str(ev.get("text") or "")
                    if len(t) > remaining:
                        ev["text"] = t[:remaining]
                        ledger_tail_truncated = True
                        ledger_tail.append(ev)
                        break
                    ledger_tail.append(ev)
                    used += len(t)
    except Exception:
        ledger_tail = []
        ledger_tail_truncated = False

    last_event_id = ""
    try:
        msgs = inbox.get("messages") if isinstance(inbox, dict) else None
        if isinstance(msgs, list) and msgs:
            last_event_id = str((msgs[-1] if isinstance(msgs[-1], dict) else {}).get("id") or "").strip()
    except Exception:
        last_event_id = ""

    return {
        "group": group,
        "actors": actors,
        "help": help_payload,
        "project": project,
        "context": context,
        "inbox": inbox,
        "ledger_tail": ledger_tail,
        "ledger_tail_truncated": ledger_tail_truncated,
        "suggested_mark_read_event_id": last_event_id,
    }


# =============================================================================
# Message Tools
# =============================================================================


def message_send(
    *,
    group_id: str,
    actor_id: str,
    text: str,
    to: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
    priority: str = "normal",
    reply_required: bool = False,
    dst_group_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a message"""
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    reply_required_flag = coerce_bool(reply_required, default=False)
    dst = str(dst_group_id or "").strip()
    if dst and dst != str(group_id or "").strip():
        if reply_to:
            raise MCPError(code="unsupported", message="cross-group reply is not supported; send a new message instead")
        return _call_daemon_or_raise({
            "op": "send_cross_group",
            "args": {
                "group_id": group_id,
                "dst_group_id": dst,
                "text": text,
                "by": actor_id,
                "to": to if to is not None else [],
                "priority": prio,
                "reply_required": reply_required_flag,
            },
        })
    if reply_to:
        return _call_daemon_or_raise({
            "op": "reply",
            "args": {
                "group_id": group_id,
                "text": text,
                "by": actor_id,
                "reply_to": reply_to,
                "to": to if to is not None else [],
                "priority": prio,
                "reply_required": reply_required_flag,
            },
        })
    return _call_daemon_or_raise({
        "op": "send",
        "args": {
            "group_id": group_id,
            "text": text,
            "by": actor_id,
            "to": to if to is not None else [],
            "path": "",
            "priority": prio,
            "reply_required": reply_required_flag,
        },
    })


def message_reply(
    *,
    group_id: str,
    actor_id: str,
    reply_to: str,
    text: str,
    to: Optional[List[str]] = None,
    priority: str = "normal",
    reply_required: bool = False,
) -> Dict[str, Any]:
    """Reply to a message"""
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    reply_required_flag = coerce_bool(reply_required, default=False)
    return _call_daemon_or_raise({
        "op": "reply",
        "args": {
            "group_id": group_id,
            "text": text,
            "by": actor_id,
            "reply_to": reply_to,
            "to": to if to is not None else [],
            "priority": prio,
            "reply_required": reply_required_flag,
        },
    })


def blob_path(*, group_id: str, rel_path: str) -> Dict[str, Any]:
    """Resolve a blob attachment path to an absolute filesystem path."""
    group = load_group(str(group_id or "").strip())
    if group is None:
        raise MCPError(code="group_not_found", message=f"group not found: {group_id}")
    abs_path = resolve_blob_attachment_path(group, rel_path=str(rel_path or "").strip())
    return {"path": str(abs_path)}


def file_send(
    *,
    group_id: str,
    actor_id: str,
    path: str,
    text: str = "",
    to: Optional[List[str]] = None,
    priority: str = "normal",
    reply_required: bool = False,
) -> Dict[str, Any]:
    """Send a local file as a chat.message attachment.

    Security: only files under the group's active scope root are allowed.
    """
    gid = str(group_id or "").strip()
    group = load_group(gid)
    if group is None:
        raise MCPError(code="group_not_found", message=f"group not found: {group_id}")

    scope_key = str(group.doc.get("active_scope_key") or "").strip()
    if not scope_key:
        raise MCPError(code="missing_scope", message="group has no active scope")
    scopes = group.doc.get("scopes")
    scope_url = ""
    if isinstance(scopes, list):
        for sc in scopes:
            if isinstance(sc, dict) and str(sc.get("scope_key") or "").strip() == scope_key:
                scope_url = str(sc.get("url") or "").strip()
                break
    if not scope_url:
        raise MCPError(code="missing_scope", message="active scope url not found")

    root = Path(scope_url).expanduser().resolve()
    src = Path(str(path or "").strip())
    if not src.is_absolute():
        src = (root / src).resolve()
    else:
        src = src.expanduser().resolve()

    try:
        src.relative_to(root)
    except ValueError:
        raise MCPError(code="invalid_path", message="path must be under the group's active scope root")
    if not src.exists() or not src.is_file():
        raise MCPError(code="not_found", message=f"file not found: {src}")

    try:
        raw = src.read_bytes()
    except Exception as e:
        raise MCPError(code="read_failed", message=str(e))

    mt, _ = mimetypes.guess_type(src.name)
    att = store_blob_bytes(group, data=raw, filename=src.name, mime_type=str(mt or ""))
    msg = str(text or "").strip() or f"[file] {att.get('title') or src.name}"
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    reply_required_flag = coerce_bool(reply_required, default=False)
    return _call_daemon_or_raise({
        "op": "send",
        "args": {
            "group_id": gid,
            "text": msg,
            "by": actor_id,
            "to": to if to is not None else [],
            "path": "",
            "attachments": [att],
            "priority": prio,
            "reply_required": reply_required_flag,
        },
    })


# =============================================================================
# Group/Actor Info Tools
# =============================================================================

def _sanitize_group_doc_for_agent(doc: Any) -> Dict[str, Any]:
    """Return a minimal, non-secret group view for agents.

    This intentionally excludes sensitive fields such as IM tokens and actor env.
    """
    if not isinstance(doc, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in ("group_id", "title", "topic", "created_at", "updated_at", "state", "active_scope_key"):
        if k in doc:
            out[k] = doc.get(k)
    out["running"] = coerce_bool(doc.get("running"), default=False)
    scopes = doc.get("scopes")
    if isinstance(scopes, list):
        safe_scopes: list[dict[str, Any]] = []
        for sc in scopes:
            if not isinstance(sc, dict):
                continue
            safe_scopes.append({
                "scope_key": sc.get("scope_key"),
                "url": sc.get("url"),
                "label": sc.get("label"),
                "git_remote": sc.get("git_remote"),
            })
        out["scopes"] = safe_scopes
    return out


def _sanitize_actors_for_agent(raw: Any) -> List[Dict[str, Any]]:
    """Return a minimal actor view for agents (no env/command)."""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        out.append({
            "id": a.get("id"),
            "role": a.get("role"),
            "title": a.get("title"),
            "enabled": coerce_bool(a.get("enabled"), default=True),
            "running": coerce_bool(a.get("running"), default=False),
            "runner": a.get("runner"),
            "runtime": a.get("runtime"),
            "submit": a.get("submit"),
            "unread_count": a.get("unread_count"),
            "updated_at": a.get("updated_at"),
            "created_at": a.get("created_at"),
        })
    return out


def group_info(*, group_id: str) -> Dict[str, Any]:
    """Get group information"""
    res = _call_daemon_or_raise({"op": "group_show", "args": {"group_id": group_id}})
    doc = res.get("group") if isinstance(res, dict) else None
    return {"group": _sanitize_group_doc_for_agent(doc)}


def group_list() -> Dict[str, Any]:
    """List working groups (metadata only)."""
    res = _call_daemon_or_raise({"op": "groups"})
    raw = res.get("groups") if isinstance(res, dict) else None
    if not isinstance(raw, list):
        raw = []
    out: List[Dict[str, Any]] = []
    for g in raw:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("group_id") or "").strip()
        if not gid:
            continue
        out.append(
            {
                "group_id": gid,
                "title": g.get("title") or "",
                "topic": g.get("topic") or "",
                "running": coerce_bool(g.get("running"), default=False),
                "state": g.get("state") or "",
                "updated_at": g.get("updated_at") or "",
                "created_at": g.get("created_at") or "",
            }
        )
    return {"groups": out}


def actor_list(*, group_id: str) -> Dict[str, Any]:
    """Get actor list"""
    res = _call_daemon_or_raise({"op": "actor_list", "args": {"group_id": group_id, "include_unread": True}})
    actors = res.get("actors") if isinstance(res, dict) else None
    return {"actors": _sanitize_actors_for_agent(actors)}


def actor_profile_list(*, by: str) -> Dict[str, Any]:
    """List reusable Actor Profiles."""
    return _call_daemon_or_raise({"op": "actor_profile_list", "args": {"by": by}})


def actor_add(
    *, group_id: str, by: str, actor_id: str,
    runtime: str = "codex", runner: str = "pty", title: str = "",
    command: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None, profile_id: str = ""
) -> Dict[str, Any]:
    """Add a new actor (foreman only). Role is auto-determined by position."""
    req_args: Dict[str, Any] = {
        "group_id": group_id,
        "actor_id": actor_id,
        "runtime": runtime,
        "runner": runner,
        "title": title,
        "command": command or [],
        "env": env or {},
        "by": by,
    }
    pid = str(profile_id or "").strip()
    if pid:
        req_args["profile_id"] = pid
    return _call_daemon_or_raise({
        "op": "actor_add",
        "args": req_args,
    })


def actor_remove(*, group_id: str, by: str, actor_id: str) -> Dict[str, Any]:
    """Remove an actor. Foreman/peer can only remove themselves."""
    return _call_daemon_or_raise({
        "op": "actor_remove",
        "args": {"group_id": group_id, "actor_id": actor_id, "by": by},
    })


def actor_start(*, group_id: str, by: str, actor_id: str) -> Dict[str, Any]:
    """Start an actor (set enabled=true). Foreman can start any; peer cannot start."""
    return _call_daemon_or_raise({
        "op": "actor_start",
        "args": {"group_id": group_id, "actor_id": actor_id, "by": by},
    })


def actor_stop(*, group_id: str, by: str, actor_id: str) -> Dict[str, Any]:
    """Stop an actor (set enabled=false). Foreman can stop any; peer can only stop self."""
    return _call_daemon_or_raise({
        "op": "actor_stop",
        "args": {"group_id": group_id, "actor_id": actor_id, "by": by},
    })


def actor_restart(*, group_id: str, by: str, actor_id: str) -> Dict[str, Any]:
    """Restart an actor (stop + start, clears context). Foreman can restart any; peer can only restart self."""
    return _call_daemon_or_raise({
        "op": "actor_restart",
        "args": {"group_id": group_id, "actor_id": actor_id, "by": by},
    })


def runtime_list() -> Dict[str, Any]:
    """List available agent runtimes on the system"""
    from ...kernel.runtime import detect_all_runtimes
    from ...kernel.settings import get_runtime_pool
    
    runtimes = detect_all_runtimes(primary_only=False)
    pool = get_runtime_pool()
    
    return {
        "runtimes": [
            {
                "name": rt.name,
                "display_name": rt.display_name,
                "command": rt.command,
                "available": rt.available,
                "path": rt.path,
                "capabilities": rt.capabilities,
            }
            for rt in runtimes
        ],
        "available": [rt.name for rt in runtimes if rt.available],
        "pool": [
            {
                "runtime": e.runtime,
                "priority": e.priority,
                "scenarios": e.scenarios,
                "notes": e.notes,
            }
            for e in pool
        ],
    }


def capability_search(
    *,
    group_id: str,
    actor_id: str,
    query: str = "",
    kind: str = "",
    source_id: str = "",
    trust_tier: str = "",
    qualification_status: str = "",
    limit: int = 30,
    include_external: bool = True,
) -> Dict[str, Any]:
    """Search capability registry (built-in packs + synced external catalogs)."""
    return _call_daemon_or_raise(
        {
            "op": "capability_search",
            "args": {
                "group_id": group_id,
                "actor_id": actor_id,
                "by": actor_id,
                "query": str(query or ""),
                "kind": str(kind or ""),
                "source_id": str(source_id or ""),
                "trust_tier": str(trust_tier or ""),
                "qualification_status": str(qualification_status or ""),
                "limit": int(limit or 30),
                "include_external": bool(include_external),
            },
        }
    )


def capability_enable(
    *,
    group_id: str,
    by: str,
    capability_id: str,
    scope: str = "session",
    enabled: bool = True,
    cleanup: bool = False,
    approve: bool = False,
    reason: str = "",
    ttl_seconds: int = 3600,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Enable/disable a capability for group/actor/session scope."""
    target_actor = str(actor_id or by).strip()
    return _call_daemon_or_raise(
        {
            "op": "capability_enable",
            "args": {
                "group_id": group_id,
                "by": str(by or ""),
                "actor_id": target_actor,
                "capability_id": str(capability_id or ""),
                "scope": str(scope or "session"),
                "enabled": bool(enabled),
                "cleanup": bool(cleanup),
                "approve": bool(approve),
                "reason": str(reason or ""),
                "ttl_seconds": int(ttl_seconds or 3600),
            },
        }
    )


def capability_state(*, group_id: str, actor_id: str) -> Dict[str, Any]:
    """Return effective capability exposure and visible tool names for caller scope."""
    return _call_daemon_or_raise(
        {
            "op": "capability_state",
            "args": {
                "group_id": group_id,
                "actor_id": actor_id,
                "by": actor_id,
            },
        }
    )


def capability_uninstall(
    *,
    group_id: str,
    by: str,
    capability_id: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Uninstall an external capability runtime cache entry and revoke bindings."""
    return _call_daemon_or_raise(
        {
            "op": "capability_uninstall",
            "args": {
                "group_id": group_id,
                "by": str(by or ""),
                "actor_id": str(by or ""),
                "capability_id": str(capability_id or ""),
                "reason": str(reason or ""),
            },
        }
    )


def capability_use(
    *,
    group_id: str,
    by: str,
    actor_id: Optional[str] = None,
    capability_id: str = "",
    tool_name: str = "",
    tool_arguments: Optional[Dict[str, Any]] = None,
    scope: str = "session",
    approve: bool = False,
    ttl_seconds: int = 3600,
    reason: str = "",
) -> Dict[str, Any]:
    """One-step capability use: enable then optionally call tool."""
    target_actor = str(actor_id or by).strip()
    cap_id = str(capability_id or "").strip()
    call_tool = str(tool_name or "").strip()
    tool_args = dict(tool_arguments) if isinstance(tool_arguments, dict) else {}

    if not cap_id and call_tool:
        candidates = [
            pack_id
            for pack_id, pack in BUILTIN_CAPABILITY_PACKS.items()
            if isinstance(pack, dict) and call_tool in set(pack.get("tool_names") or ())
        ]
        if len(candidates) == 1:
            cap_id = str(candidates[0])
        elif len(candidates) > 1:
            raise MCPError(
                code="capability_use_ambiguous_tool",
                message=f"tool maps to multiple capabilities: {call_tool}",
                details={"candidates": candidates},
            )

    if not cap_id:
        raise MCPError(
            code="missing_capability_id",
            message="missing capability_id (and could not infer from tool_name)",
            details={},
        )

    enable_result = capability_enable(
        group_id=group_id,
        by=by,
        actor_id=target_actor,
        capability_id=cap_id,
        scope=scope,
        enabled=True,
        approve=approve,
        ttl_seconds=ttl_seconds,
        reason=reason,
    )
    state = str(enable_result.get("state") or "").strip().lower()
    if state != "ready":
        return {
            "group_id": group_id,
            "actor_id": target_actor,
            "capability_id": cap_id,
            "enabled": False,
            "enable_result": enable_result,
            "tool_called": False,
        }

    if not call_tool:
        out = {
            "group_id": group_id,
            "actor_id": target_actor,
            "capability_id": cap_id,
            "enabled": True,
            "enable_result": enable_result,
            "tool_called": False,
        }
        skill_payload = enable_result.get("skill") if isinstance(enable_result, dict) else None
        if isinstance(skill_payload, dict):
            out["skill"] = skill_payload
        return out
    if call_tool == "cccc_capability_use":
        raise MCPError(
            code="capability_use_invalid_tool",
            message="cccc_capability_use cannot recursively call itself",
            details={},
        )

    if "group_id" not in tool_args:
        tool_args["group_id"] = group_id
    if "by" not in tool_args:
        tool_args["by"] = by
    if "actor_id" not in tool_args:
        tool_args["actor_id"] = target_actor

    tool_result = handle_tool_call(call_tool, tool_args)
    return {
        "group_id": group_id,
        "actor_id": target_actor,
        "capability_id": cap_id,
        "enabled": True,
        "enable_result": enable_result,
        "tool_called": True,
        "tool_name": call_tool,
        "tool_result": tool_result,
    }


def group_set_state(*, group_id: str, by: str, state: str) -> Dict[str, Any]:
    """Set group state (active/idle/paused/stopped)."""
    s = str(state or "").strip().lower()
    if s == "stopped":
        return _call_daemon_or_raise({
            "op": "group_stop",
            "args": {"group_id": group_id, "by": by},
        })
    return _call_daemon_or_raise({
        "op": "group_set_state",
        "args": {"group_id": group_id, "state": s, "by": by},
    })


def automation_state(*, group_id: str, by: str) -> Dict[str, Any]:
    """Read automation reminders/status visible to caller."""
    return _call_daemon_or_raise({
        "op": "group_automation_state",
        "args": {"group_id": group_id, "by": by},
    })


def automation_manage(
    *,
    group_id: str,
    by: str,
    actions: List[Dict[str, Any]],
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Manage automation reminders incrementally."""
    req_args: Dict[str, Any] = {
        "group_id": group_id,
        "by": by,
        "actions": actions,
    }
    if expected_version is not None:
        req_args["expected_version"] = int(expected_version)
    return _call_daemon_or_raise({"op": "group_automation_manage", "args": req_args})


def _assert_agent_notify_only_actions(actions: List[Dict[str, Any]]) -> None:
    for idx, action in enumerate(actions):
        action_type = str(action.get("type") or "").strip()
        if action_type in {"create_rule", "update_rule"}:
            rule = action.get("rule")
            if not isinstance(rule, dict):
                continue
            action_doc = rule.get("action")
            if not isinstance(action_doc, dict):
                continue
            kind = str(action_doc.get("kind") or "notify").strip()
            if kind != "notify":
                raise MCPError(
                    code="permission_denied",
                    message=f"actions[{idx}] uses action.kind={kind}; agents may only manage notify rules",
                )
            continue
        if action_type == "replace_all_rules":
            ruleset = action.get("ruleset")
            if not isinstance(ruleset, dict):
                continue
            rules = ruleset.get("rules")
            if not isinstance(rules, list):
                continue
            for j, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    continue
                action_doc = rule.get("action")
                if not isinstance(action_doc, dict):
                    continue
                kind = str(action_doc.get("kind") or "notify").strip()
                if kind != "notify":
                    raise MCPError(
                        code="permission_denied",
                        message=f"actions[{idx}].rules[{j}] uses action.kind={kind}; agents may only manage notify rules",
                    )


def _assert_action_trigger_compat(actions: List[Dict[str, Any]]) -> None:
    def _validate_rule(rule: Dict[str, Any], *, loc: str) -> None:
        action_doc = rule.get("action")
        trigger_doc = rule.get("trigger")
        if not isinstance(action_doc, dict) or not isinstance(trigger_doc, dict):
            return
        action_kind = str(action_doc.get("kind") or "notify").strip()
        trigger_kind = str(trigger_doc.get("kind") or "").strip()
        if action_kind in {"group_state", "actor_control"} and trigger_kind != "at":
            raise MCPError(
                code="invalid_request",
                message=f"{loc} uses action.kind={action_kind}; only one-time trigger.kind=at is allowed",
            )

    for idx, action in enumerate(actions):
        action_type = str(action.get("type") or "").strip()
        if action_type in {"create_rule", "update_rule"}:
            rule = action.get("rule")
            if isinstance(rule, dict):
                _validate_rule(rule, loc=f"actions[{idx}].rule")
            continue
        if action_type == "replace_all_rules":
            ruleset = action.get("ruleset")
            if not isinstance(ruleset, dict):
                continue
            rules = ruleset.get("rules")
            if not isinstance(rules, list):
                continue
            for j, rule in enumerate(rules):
                if isinstance(rule, dict):
                    _validate_rule(rule, loc=f"actions[{idx}].rules[{j}]")


def _map_simple_automation_op_to_action(arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    op = str(arguments.get("op") or "").strip().lower()
    if not op:
        return None
    if op == "create":
        rule = arguments.get("rule")
        if not isinstance(rule, dict):
            raise MCPError(code="invalid_request", message="op=create requires reminder object (rule)")
        return {"type": "create_rule", "rule": rule}
    if op == "update":
        rule = arguments.get("rule")
        if not isinstance(rule, dict):
            raise MCPError(code="invalid_request", message="op=update requires reminder object (rule)")
        return {"type": "update_rule", "rule": rule}
    if op == "enable":
        rule_id = str(arguments.get("rule_id") or "").strip()
        if not rule_id:
            raise MCPError(code="invalid_request", message="op=enable requires rule_id")
        return {"type": "set_rule_enabled", "rule_id": rule_id, "enabled": True}
    if op == "disable":
        rule_id = str(arguments.get("rule_id") or "").strip()
        if not rule_id:
            raise MCPError(code="invalid_request", message="op=disable requires rule_id")
        return {"type": "set_rule_enabled", "rule_id": rule_id, "enabled": False}
    if op == "delete":
        rule_id = str(arguments.get("rule_id") or "").strip()
        if not rule_id:
            raise MCPError(code="invalid_request", message="op=delete requires rule_id")
        return {"type": "delete_rule", "rule_id": rule_id}
    if op == "replace_all":
        ruleset = arguments.get("ruleset")
        if not isinstance(ruleset, dict):
            raise MCPError(code="invalid_request", message="op=replace_all requires reminder set object (ruleset)")
        return {"type": "replace_all_rules", "ruleset": ruleset}
    raise MCPError(
        code="invalid_request",
        message="op must be one of: create, update, enable, disable, delete, replace_all",
    )


def im_bind(*, group_id: str, key: str) -> Dict[str, Any]:
    """Bind an IM chat using a one-time key from /subscribe."""
    k = str(key or "").strip()
    if not k:
        raise MCPError(code="missing_key", message="key is required")
    return _call_daemon_or_raise({
        "op": "im_bind_chat",
        "args": {"group_id": group_id, "key": k},
    })


def space_status(*, group_id: str, provider: str = "notebooklm") -> Dict[str, Any]:
    """Get Group Space status (provider + binding + queue summary)."""
    return _call_daemon_or_raise(
        {
            "op": "group_space_status",
            "args": {"group_id": group_id, "provider": str(provider or "notebooklm")},
        }
    )


def space_capabilities(*, group_id: str, provider: str = "notebooklm") -> Dict[str, Any]:
    """Get Group Space capabilities (local file policy + ingest schema/examples)."""
    return _call_daemon_or_raise(
        {
            "op": "group_space_capabilities",
            "args": {"group_id": group_id, "provider": str(provider or "notebooklm")},
        }
    )


def space_bind(
    *,
    group_id: str,
    by: str,
    provider: str = "notebooklm",
    action: str = "bind",
    remote_space_id: str = "",
) -> Dict[str, Any]:
    """Bind or unbind Group Space provider for a group."""
    return _call_daemon_or_raise(
        {
            "op": "group_space_bind",
            "args": {
                "group_id": group_id,
                "provider": str(provider or "notebooklm"),
                "action": str(action or "bind"),
                "remote_space_id": str(remote_space_id or ""),
                "by": str(by or "user"),
            },
        }
    )


def space_ingest(
    *,
    group_id: str,
    by: str,
    provider: str = "notebooklm",
    kind: str = "context_sync",
    payload: Optional[Dict[str, Any]] = None,
    idempotency_key: str = "",
) -> Dict[str, Any]:
    """Submit a Group Space ingest job."""
    return _call_daemon_or_raise(
        {
            "op": "group_space_ingest",
            "args": {
                "group_id": group_id,
                "provider": str(provider or "notebooklm"),
                "kind": str(kind or "context_sync"),
                "payload": dict(payload or {}),
                "idempotency_key": str(idempotency_key or ""),
                "by": str(by or "user"),
            },
        }
    )


def space_query(
    *,
    group_id: str,
    provider: str = "notebooklm",
    query: str,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Query Group Space knowledge provider."""
    return _call_daemon_or_raise(
        {
            "op": "group_space_query",
            "args": {
                "group_id": group_id,
                "provider": str(provider or "notebooklm"),
                "query": str(query or ""),
                "options": dict(options or {}),
            },
        }
    )


def space_sources(
    *,
    group_id: str,
    by: str,
    provider: str = "notebooklm",
    action: str = "list",
    source_id: str = "",
    new_title: str = "",
) -> Dict[str, Any]:
    """List/refresh/rename/delete Group Space provider sources."""
    return _call_daemon_or_raise(
        {
            "op": "group_space_sources",
            "args": {
                "group_id": group_id,
                "provider": str(provider or "notebooklm"),
                "action": str(action or "list"),
                "source_id": str(source_id or ""),
                "new_title": str(new_title or ""),
                "by": str(by or "user"),
            },
        }
    )


def space_artifact(
    *,
    group_id: str,
    by: str,
    provider: str = "notebooklm",
    action: str = "list",
    kind: str = "",
    options: Optional[Dict[str, Any]] = None,
    wait: bool = False,
    save_to_space: bool = True,
    output_path: str = "",
    output_format: str = "",
    artifact_id: str = "",
    timeout_seconds: float = 600.0,
    initial_interval: float = 2.0,
    max_interval: float = 10.0,
) -> Dict[str, Any]:
    """List/generate/download Group Space provider artifacts."""
    action_v = str(action or "list")
    kind_v = str(kind or "")
    wait_v = bool(wait)
    if action_v == "generate" and wait_v and str(kind_v).strip().lower() in {"audio", "video"}:
        # Audio/video generation often runs far beyond typical MCP request timeouts.
        # Force async mode to avoid client-side timeouts while preserving eventual notify/download flow.
        wait_v = False
    timeout_v = float(timeout_seconds)
    req = {
        "op": "group_space_artifact",
        "args": {
            "group_id": group_id,
            "provider": str(provider or "notebooklm"),
            "action": action_v,
            "kind": kind_v,
            "options": dict(options or {}),
            "wait": wait_v,
            "save_to_space": bool(save_to_space),
            "output_path": str(output_path or ""),
            "output_format": str(output_format or ""),
            "artifact_id": str(artifact_id or ""),
            "timeout_seconds": timeout_v,
            "initial_interval": float(initial_interval),
            "max_interval": float(max_interval),
            "by": str(by or "user"),
        },
    }
    daemon_timeout = 60.0
    if action_v == "generate":
        if wait_v:
            daemon_timeout = max(180.0, timeout_v + 60.0)
        else:
            daemon_timeout = 120.0
    return _call_daemon_or_raise(req, timeout_s=daemon_timeout)


def space_jobs(
    *,
    group_id: str,
    by: str,
    provider: str = "notebooklm",
    action: str = "list",
    job_id: str = "",
    state: str = "",
    limit: int = 50,
) -> Dict[str, Any]:
    """List/retry/cancel Group Space jobs."""
    return _call_daemon_or_raise(
        {
            "op": "group_space_jobs",
            "args": {
                "group_id": group_id,
                "provider": str(provider or "notebooklm"),
                "action": str(action or "list"),
                "job_id": str(job_id or ""),
                "state": str(state or ""),
                "limit": int(limit or 50),
                "by": str(by or "user"),
            },
        }
    )


def space_sync(
    *,
    group_id: str,
    by: str,
    provider: str = "notebooklm",
    action: str = "run",
    force: bool = False,
) -> Dict[str, Any]:
    """Run Group Space file sync reconcile or read current sync state."""
    return _call_daemon_or_raise(
        {
            "op": "group_space_sync",
            "args": {
                "group_id": group_id,
                "provider": str(provider or "notebooklm"),
                "action": str(action or "run"),
                "force": bool(force),
                "by": str(by or "user"),
            },
        }
    )


def space_provider_auth(
    *,
    provider: str = "notebooklm",
    by: str,
    action: str = "status",
    timeout_seconds: int = 900,
) -> Dict[str, Any]:
    """Control Group Space provider auth flow (status/start/cancel)."""
    req: Dict[str, Any] = {
        "provider": str(provider or "notebooklm"),
        "by": str(by or "user"),
        "action": str(action or "status"),
    }
    if str(action or "status") == "start":
        req["timeout_seconds"] = max(60, min(int(timeout_seconds or 900), 1800))
    return _call_daemon_or_raise({"op": "group_space_provider_auth", "args": req})


def space_provider_credential_status(*, provider: str = "notebooklm", by: str) -> Dict[str, Any]:
    """Read Group Space provider credential status (masked metadata)."""
    return _call_daemon_or_raise(
        {
            "op": "group_space_provider_credential_status",
            "args": {"provider": str(provider or "notebooklm"), "by": str(by or "user")},
        }
    )


def space_provider_credential_update(
    *,
    provider: str = "notebooklm",
    by: str,
    auth_json: str = "",
    clear: bool = False,
) -> Dict[str, Any]:
    """Update/clear Group Space provider credential."""
    return _call_daemon_or_raise(
        {
            "op": "group_space_provider_credential_update",
            "args": {
                "provider": str(provider or "notebooklm"),
                "by": str(by or "user"),
                "auth_json": str(auth_json or ""),
                "clear": bool(clear),
            },
        }
    )


def project_info(*, group_id: str) -> Dict[str, Any]:
    """Get PROJECT.md content for the group's active scope"""
    from pathlib import Path
    from ...kernel.group import load_group
    
    group = load_group(group_id)
    if group is None:
        raise MCPError(code="group_not_found", message=f"group not found: {group_id}")
    
    # Get active scope's project root
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
        # Fallback: try first scope
        if scopes and isinstance(scopes[0], dict):
            project_root = str(scopes[0].get("url") or "")
    
    if not project_root:
        return {
            "found": False,
            "path": None,
            "content": None,
            "error": "No scope attached to group. Use 'cccc attach <path>' first.",
        }
    
    # Look for PROJECT.md in project root
    project_md_path = Path(project_root) / "PROJECT.md"
    if not project_md_path.exists():
        # Also try lowercase
        project_md_path_lower = Path(project_root) / "project.md"
        if project_md_path_lower.exists():
            project_md_path = project_md_path_lower
        else:
            return {
                "found": False,
                "path": str(project_md_path),
                "content": None,
                "error": f"PROJECT.md not found at {project_md_path}",
            }
    
    try:
        content = project_md_path.read_text(encoding="utf-8", errors="replace")
        return {
            "found": True,
            "path": str(project_md_path),
            "content": content,
        }
    except Exception as e:
        return {
            "found": False,
            "path": str(project_md_path),
            "content": None,
            "error": f"Failed to read PROJECT.md: {e}",
        }


# =============================================================================
# Tool Call Handler
# =============================================================================


def _handle_cccc_namespace(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if name == "cccc_help":
        gid = _env_str("CCCC_GROUP_ID")
        aid = _env_str("CCCC_ACTOR_ID")
        role: Optional[str] = None
        if gid:
            g = load_group(gid)
            if g is not None:
                if aid:
                    try:
                        role = get_effective_role(g, aid)
                    except Exception:
                        role = None
                pf = read_group_prompt_file(g, HELP_FILENAME)
                if pf.found and isinstance(pf.content, str) and pf.content.strip():
                    return {
                        "markdown": _append_runtime_skill_digest(
                            _select_help_markdown(pf.content, role=role, actor_id=aid),
                            group_id=gid,
                            actor_id=aid,
                        ),
                        "source": str(pf.path or ""),
                    }
        return {
            "markdown": _append_runtime_skill_digest(
                _select_help_markdown(_CCCC_HELP_BUILTIN, role=role, actor_id=aid),
                group_id=gid,
                actor_id=aid,
            ),
            "source": "cccc.resources/cccc-help.md",
        }

    if name == "cccc_inbox_list":
        gid = _resolve_group_id(arguments)
        aid = _resolve_self_actor_id(arguments)
        return inbox_list(
            group_id=gid,
            actor_id=aid,
            limit=min(max(int(arguments.get("limit") or 50), 1), 1000),
            kind_filter=str(arguments.get("kind_filter") or "all"),
        )

    if name == "cccc_bootstrap":
        gid = _resolve_group_id(arguments)
        aid = _resolve_self_actor_id(arguments)
        return bootstrap(
            group_id=gid,
            actor_id=aid,
            inbox_limit=min(max(int(arguments.get("inbox_limit") or 50), 1), 1000),
            inbox_kind_filter=str(arguments.get("inbox_kind_filter") or "all"),
            ledger_tail_limit=min(max(int(arguments.get("ledger_tail_limit") or 10), 0), 1000),
            ledger_tail_max_chars=min(max(int(arguments.get("ledger_tail_max_chars") or 8000), 0), 100000),
        )

    if name == "cccc_inbox_mark_read":
        gid = _resolve_group_id(arguments)
        aid = _resolve_self_actor_id(arguments)
        return inbox_mark_read(
            group_id=gid,
            actor_id=aid,
            event_id=str(arguments.get("event_id") or ""),
        )

    if name == "cccc_inbox_mark_all_read":
        gid = _resolve_group_id(arguments)
        aid = _resolve_self_actor_id(arguments)
        return inbox_mark_all_read(
            group_id=gid,
            actor_id=aid,
            kind_filter=str(arguments.get("kind_filter") or "all"),
        )

    if name == "cccc_message_send":
        gid = _resolve_group_id(arguments)
        aid = _resolve_self_actor_id(arguments)
        to_raw = arguments.get("to")
        if isinstance(to_raw, list):
            to_val: Optional[List[str]] = [str(x).strip() for x in to_raw if str(x).strip()]
        elif isinstance(to_raw, str) and to_raw.strip():
            to_val = [to_raw.strip()]
        else:
            to_val = None
        return message_send(
            group_id=gid,
            dst_group_id=arguments.get("dst_group_id"),
            actor_id=aid,
            text=str(arguments.get("text") or ""),
            to=to_val,
            priority=str(arguments.get("priority") or "normal"),
            reply_required=coerce_bool(arguments.get("reply_required"), default=False),
        )

    if name == "cccc_message_reply":
        gid = _resolve_group_id(arguments)
        aid = _resolve_self_actor_id(arguments)
        to_raw = arguments.get("to")
        if isinstance(to_raw, list):
            to_val_reply: Optional[List[str]] = [str(x).strip() for x in to_raw if str(x).strip()]
        elif isinstance(to_raw, str) and to_raw.strip():
            to_val_reply = [to_raw.strip()]
        else:
            to_val_reply = None
        reply_to = str(arguments.get("event_id") or arguments.get("reply_to") or "").strip()
        return message_reply(
            group_id=gid,
            actor_id=aid,
            reply_to=reply_to,
            text=str(arguments.get("text") or ""),
            to=to_val_reply,
            priority=str(arguments.get("priority") or "normal"),
            reply_required=coerce_bool(arguments.get("reply_required"), default=False),
        )

    if name == "cccc_file_send":
        gid = _resolve_group_id(arguments)
        aid = _resolve_self_actor_id(arguments)
        to_raw = arguments.get("to")
        if isinstance(to_raw, list):
            to_val_file: Optional[List[str]] = [str(x).strip() for x in to_raw if str(x).strip()]
        elif isinstance(to_raw, str) and to_raw.strip():
            to_val_file = [to_raw.strip()]
        else:
            to_val_file = None
        return file_send(
            group_id=gid,
            actor_id=aid,
            path=str(arguments.get("path") or ""),
            text=str(arguments.get("text") or ""),
            to=to_val_file,
            priority=str(arguments.get("priority") or "normal"),
            reply_required=coerce_bool(arguments.get("reply_required"), default=False),
        )

    if name == "cccc_blob_path":
        gid = _resolve_group_id(arguments)
        return blob_path(
            group_id=gid,
            rel_path=str(arguments.get("rel_path") or ""),
        )

    if name == "cccc_group_info":
        gid = _resolve_group_id(arguments)
        return group_info(group_id=gid)

    if name == "cccc_group_list":
        return group_list()

    if name == "cccc_actor_list":
        gid = _resolve_group_id(arguments)
        return actor_list(group_id=gid)

    if name == "cccc_actor_profile_list":
        by = _resolve_caller_from_by(arguments)
        return actor_profile_list(by=by)

    if name == "cccc_actor_add":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        cmd_raw = arguments.get("command")
        env_raw = arguments.get("env")
        return actor_add(
            group_id=gid,
            by=by,
            actor_id=str(arguments.get("actor_id") or ""),
            runtime=str(arguments.get("runtime") or "codex"),
            runner=str(arguments.get("runner") or "pty"),
            title=str(arguments.get("title") or ""),
            command=list(cmd_raw) if isinstance(cmd_raw, list) else None,
            env=dict(env_raw) if isinstance(env_raw, dict) else None,
            profile_id=str(arguments.get("profile_id") or ""),
        )

    if name == "cccc_actor_remove":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        target = str(arguments.get("actor_id") or "").strip() or by
        return actor_remove(group_id=gid, by=by, actor_id=target)

    if name == "cccc_actor_start":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        return actor_start(
            group_id=gid,
            by=by,
            actor_id=str(arguments.get("actor_id") or ""),
        )

    if name == "cccc_actor_stop":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        return actor_stop(
            group_id=gid,
            by=by,
            actor_id=str(arguments.get("actor_id") or ""),
        )

    if name == "cccc_actor_restart":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        return actor_restart(
            group_id=gid,
            by=by,
            actor_id=str(arguments.get("actor_id") or ""),
        )

    if name == "cccc_runtime_list":
        return runtime_list()

    if name == "cccc_capability_search":
        gid = _resolve_group_id(arguments)
        aid = _resolve_self_actor_id(arguments)
        return capability_search(
            group_id=gid,
            actor_id=aid,
            query=str(arguments.get("query") or ""),
            kind=str(arguments.get("kind") or ""),
            source_id=str(arguments.get("source_id") or ""),
            trust_tier=str(arguments.get("trust_tier") or ""),
            qualification_status=str(arguments.get("qualification_status") or ""),
            limit=min(max(int(arguments.get("limit") or 30), 1), 200),
            include_external=coerce_bool(arguments.get("include_external"), default=True),
        )

    if name == "cccc_capability_enable":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        actor_id = str(arguments.get("actor_id") or by).strip()
        return capability_enable(
            group_id=gid,
            by=by,
            actor_id=actor_id,
            capability_id=str(arguments.get("capability_id") or ""),
            scope=str(arguments.get("scope") or "session"),
            enabled=coerce_bool(arguments.get("enabled"), default=True),
            cleanup=coerce_bool(arguments.get("cleanup"), default=False),
            approve=coerce_bool(arguments.get("approve"), default=False),
            reason=str(arguments.get("reason") or ""),
            ttl_seconds=min(max(int(arguments.get("ttl_seconds") or 3600), 60), 24 * 3600),
        )

    if name == "cccc_capability_state":
        gid = _resolve_group_id(arguments)
        aid = _resolve_self_actor_id(arguments)
        return capability_state(group_id=gid, actor_id=aid)

    if name == "cccc_capability_uninstall":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        return capability_uninstall(
            group_id=gid,
            by=by,
            capability_id=str(arguments.get("capability_id") or ""),
            reason=str(arguments.get("reason") or ""),
        )

    if name == "cccc_capability_use":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        actor_id = str(arguments.get("actor_id") or by).strip()
        raw_tool_args = arguments.get("tool_arguments")
        tool_args = dict(raw_tool_args) if isinstance(raw_tool_args, dict) else {}
        return capability_use(
            group_id=gid,
            by=by,
            actor_id=actor_id,
            capability_id=str(arguments.get("capability_id") or ""),
            tool_name=str(arguments.get("tool_name") or ""),
            tool_arguments=tool_args,
            scope=str(arguments.get("scope") or "session"),
            approve=coerce_bool(arguments.get("approve"), default=False),
            ttl_seconds=min(max(int(arguments.get("ttl_seconds") or 3600), 60), 24 * 3600),
            reason=str(arguments.get("reason") or ""),
        )

    if name == "cccc_space_status":
        gid = _resolve_group_id(arguments)
        return space_status(
            group_id=gid,
            provider=str(arguments.get("provider") or "notebooklm"),
        )

    if name == "cccc_space_capabilities":
        gid = _resolve_group_id(arguments)
        return space_capabilities(
            group_id=gid,
            provider=str(arguments.get("provider") or "notebooklm"),
        )

    if name == "cccc_space_bind":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        return space_bind(
            group_id=gid,
            by=by,
            provider=str(arguments.get("provider") or "notebooklm"),
            action=str(arguments.get("action") or "bind"),
            remote_space_id=str(arguments.get("remote_space_id") or ""),
        )

    if name == "cccc_space_ingest":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        payload_raw = arguments.get("payload")
        payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
        if not payload:
            for key in (
                "source_type",
                "type",
                "url",
                "content",
                "text",
                "file_id",
                "mime_type",
                "title",
                "file_path",
                "path",
            ):
                if key not in arguments:
                    continue
                value = arguments.get(key)
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    payload[key] = text
            source_type = str(payload.get("source_type") or payload.get("type") or "").strip().lower()
            if source_type in {"file", "local_file", "path"} and (not str(payload.get("file_path") or "").strip()):
                file_path = str(payload.get("path") or payload.get("url") or "").strip()
                if file_path:
                    payload["file_path"] = file_path
        kind = str(arguments.get("kind") or "").strip()
        if not kind:
            resource_hints = {
                "source_type",
                "type",
                "url",
                "content",
                "text",
                "file_id",
                "mime_type",
                "title",
                "file_path",
                "path",
            }
            kind = "resource_ingest" if any(k in payload for k in resource_hints) else "context_sync"
        return space_ingest(
            group_id=gid,
            by=by,
            provider=str(arguments.get("provider") or "notebooklm"),
            kind=kind,
            payload=payload,
            idempotency_key=str(arguments.get("idempotency_key") or ""),
        )

    if name == "cccc_space_query":
        gid = _resolve_group_id(arguments)
        options = _normalize_space_query_options_mcp(arguments)
        return space_query(
            group_id=gid,
            provider=str(arguments.get("provider") or "notebooklm"),
            query=str(arguments.get("query") or ""),
            options=options,
        )

    if name == "cccc_space_sources":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        return space_sources(
            group_id=gid,
            by=by,
            provider=str(arguments.get("provider") or "notebooklm"),
            action=str(arguments.get("action") or "list"),
            source_id=str(arguments.get("source_id") or ""),
            new_title=str(arguments.get("new_title") or ""),
        )

    if name == "cccc_space_artifact":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        options_raw = arguments.get("options")
        options = dict(options_raw) if isinstance(options_raw, dict) else {}
        source_hint = str(arguments.get("source") or "").strip()
        if source_hint and ("source" not in options):
            options["source"] = source_hint
        language_hint = str(arguments.get("language") or arguments.get("lang") or "").strip()
        if not language_hint:
            language_hint = str(options.get("language") or options.get("lang") or "").strip()
        if not language_hint:
            language_hint = str(os.environ.get("CCCC_SPACE_ARTIFACT_LANGUAGE") or "").strip()
        if not language_hint and source_hint:
            language_hint = _infer_artifact_language_from_source(source_hint)
        if language_hint and ("language" not in options):
            options["language"] = language_hint
        action_raw = str(arguments.get("action") or "").strip()
        if action_raw:
            action = action_raw
        else:
            has_generate_intent = bool(source_hint) or ("wait" in arguments) or ("save_to_space" in arguments) or bool(options)
            has_download_intent = bool(str(arguments.get("artifact_id") or "").strip() or str(arguments.get("output_path") or "").strip())
            if has_generate_intent:
                action = "generate"
            elif has_download_intent:
                action = "download"
            else:
                action = "list"
        timeout_raw = arguments.get("timeout_seconds")
        initial_raw = arguments.get("initial_interval")
        max_raw = arguments.get("max_interval")
        timeout_seconds = 600.0
        initial_interval = 2.0
        max_interval = 10.0
        try:
            if timeout_raw is not None:
                timeout_seconds = float(timeout_raw)
            if initial_raw is not None:
                initial_interval = float(initial_raw)
            if max_raw is not None:
                max_interval = float(max_raw)
        except Exception:
            raise MCPError(
                code="invalid_request",
                message="timeout_seconds/initial_interval/max_interval must be numbers",
            )
        return space_artifact(
            group_id=gid,
            by=by,
            provider=str(arguments.get("provider") or "notebooklm"),
            action=action,
            kind=str(arguments.get("kind") or ""),
            options=options,
            wait=coerce_bool(arguments.get("wait"), default=False),
            save_to_space=coerce_bool(arguments.get("save_to_space"), default=True),
            output_path=str(arguments.get("output_path") or ""),
            output_format=str(arguments.get("output_format") or ""),
            artifact_id=str(arguments.get("artifact_id") or ""),
            timeout_seconds=timeout_seconds,
            initial_interval=initial_interval,
            max_interval=max_interval,
        )

    if name == "cccc_space_jobs":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        return space_jobs(
            group_id=gid,
            by=by,
            provider=str(arguments.get("provider") or "notebooklm"),
            action=str(arguments.get("action") or "list"),
            job_id=str(arguments.get("job_id") or ""),
            state=str(arguments.get("state") or ""),
            limit=min(max(int(arguments.get("limit") or 50), 1), 500),
        )

    if name == "cccc_space_sync":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_from_by(arguments)
        return space_sync(
            group_id=gid,
            by=by,
            provider=str(arguments.get("provider") or "notebooklm"),
            action=str(arguments.get("action") or "run"),
            force=bool(arguments.get("force") is True),
        )

    if name == "cccc_space_provider_auth":
        by = _resolve_caller_from_by(arguments)
        timeout_raw = arguments.get("timeout_seconds")
        timeout_seconds = 900
        if timeout_raw is not None:
            try:
                timeout_seconds = int(timeout_raw)
            except Exception:
                raise MCPError(code="invalid_request", message="timeout_seconds must be an integer")
        return space_provider_auth(
            provider=str(arguments.get("provider") or "notebooklm"),
            by=by,
            action=str(arguments.get("action") or "status"),
            timeout_seconds=timeout_seconds,
        )

    if name == "cccc_space_provider_credential_status":
        by = _resolve_caller_from_by(arguments)
        return space_provider_credential_status(
            provider=str(arguments.get("provider") or "notebooklm"),
            by=by,
        )

    if name == "cccc_space_provider_credential_update":
        by = _resolve_caller_from_by(arguments)
        return space_provider_credential_update(
            provider=str(arguments.get("provider") or "notebooklm"),
            by=by,
            auth_json=str(arguments.get("auth_json") or ""),
            clear=coerce_bool(arguments.get("clear"), default=False),
        )

    if name == "cccc_group_set_state":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_actor_id(arguments)
        return group_set_state(
            group_id=gid,
            by=by,
            state=str(arguments.get("state") or ""),
        )

    if name == "cccc_automation_state":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_actor_id(arguments)
        return automation_state(group_id=gid, by=by)

    if name == "cccc_automation_manage":
        gid = _resolve_group_id(arguments)
        by = _resolve_caller_actor_id(arguments)
        actions: List[Dict[str, Any]] = []
        mapped = _map_simple_automation_op_to_action(arguments)
        if isinstance(mapped, dict):
            actions.append(mapped)
        actions_raw = arguments.get("actions")
        if isinstance(actions_raw, list):
            for i, action in enumerate(actions_raw):
                if not isinstance(action, dict):
                    raise MCPError(code="invalid_request", message=f"actions[{i}] must be an object")
                actions.append(action)
        if not actions:
            raise MCPError(code="invalid_request", message="provide op (simple mode) or actions[] (advanced mode)")
        _assert_action_trigger_compat(actions)
        if by != "user":
            _assert_agent_notify_only_actions(actions)
        expected_version_raw = arguments.get("expected_version")
        expected_version: Optional[int] = None
        if expected_version_raw is not None:
            try:
                expected_version = int(expected_version_raw)
            except Exception:
                raise MCPError(code="invalid_request", message="expected_version must be an integer")
        return automation_manage(group_id=gid, by=by, actions=actions, expected_version=expected_version)

    if name == "cccc_project_info":
        gid = _resolve_group_id(arguments)
        return project_info(group_id=gid)

    if name == "cccc_im_bind":
        gid = _resolve_group_id(arguments)
        return im_bind(group_id=gid, key=str(arguments.get("key") or ""))

    return None


def _handle_context_namespace(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _handle_context_namespace_impl(
        name,
        arguments,
        resolve_group_id=_resolve_group_id,
        resolve_self_actor_id=_resolve_self_actor_id,
        coerce_bool=coerce_bool,
        context_get_fn=context_get,
        context_sync_fn=context_sync,
        vision_update_fn=vision_update,
        sketch_update_fn=sketch_update,
        milestone_create_fn=milestone_create,
        milestone_update_fn=milestone_update,
        milestone_complete_fn=milestone_complete,
        task_list_fn=task_list,
        task_create_fn=task_create,
        task_update_fn=task_update,
        note_add_fn=note_add,
        note_update_fn=note_update,
        note_remove_fn=note_remove,
        reference_add_fn=reference_add,
        reference_update_fn=reference_update,
        reference_remove_fn=reference_remove,
        presence_get_fn=presence_get,
        presence_update_fn=presence_update,
        presence_clear_fn=presence_clear,
    )


def _handle_headless_namespace(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _handle_headless_namespace_impl(
        name,
        arguments,
        resolve_group_id=_resolve_group_id,
        resolve_self_actor_id=_resolve_self_actor_id,
        headless_status_fn=headless_status,
        headless_set_status_fn=headless_set_status,
        headless_ack_message_fn=headless_ack_message,
    )


def _handle_notify_namespace(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _handle_notify_namespace_impl(
        name,
        arguments,
        resolve_group_id=_resolve_group_id,
        resolve_self_actor_id=_resolve_self_actor_id,
        notify_send_fn=notify_send,
        notify_ack_fn=notify_ack,
        coerce_bool_fn=coerce_bool,
    )


def _handle_terminal_namespace(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _handle_terminal_namespace_impl(
        name,
        arguments,
        resolve_group_id=_resolve_group_id,
        resolve_self_actor_id=_resolve_self_actor_id,
        terminal_tail_fn=terminal_tail,
        coerce_bool_fn=coerce_bool,
    )


def _handle_memory_namespace(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _handle_memory_namespace_impl(
        name,
        arguments,
        resolve_group_id=_resolve_group_id,
        coerce_bool=coerce_bool,
        call_daemon_or_raise=_call_daemon_or_raise,
        mcp_error_cls=MCPError,
        build_memory_guide=build_memory_guide,
    )


def _handle_debug_namespace(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _handle_debug_namespace_impl(
        name,
        arguments,
        resolve_group_id=_resolve_group_id,
        resolve_self_actor_id=_resolve_self_actor_id,
        debug_snapshot_fn=debug_snapshot,
        debug_tail_logs_fn=debug_tail_logs,
    )


def handle_tool_call(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP tool call."""
    for handler in (
        _handle_cccc_namespace,
        _handle_context_namespace,
        _handle_memory_namespace,
        _handle_headless_namespace,
        _handle_notify_namespace,
        _handle_terminal_namespace,
        _handle_debug_namespace,
    ):
        out = handler(name, arguments)
        if out is not None:
            return out
    # Dynamic capability tools are resolved by daemon capability runtime.
    gid = _env_str("CCCC_GROUP_ID")
    aid = _env_str("CCCC_ACTOR_ID")
    if gid and aid:
        try:
            return _call_daemon_or_raise(
                {
                    "op": "capability_tool_call",
                    "args": {
                        "group_id": gid,
                        "actor_id": aid,
                        "by": aid,
                        "tool_name": str(name or ""),
                        "arguments": arguments if isinstance(arguments, dict) else {},
                    },
                },
                timeout_s=120.0,
            )
        except MCPError as e:
            if e.code != "capability_tool_not_found":
                raise
    raise MCPError(code="unknown_tool", message=f"unknown tool: {name}")


def list_tools_for_caller() -> List[Dict[str, Any]]:
    """Resolve visible tool specs for current caller scope.

    Behavior:
    1) full profile opt-out via CCCC_MCP_TOOL_PROFILE=full
    2) default: core + enabled capability packs from daemon capability_state
    3) daemon failure fallback: core-only
    """
    gid = _env_str("CCCC_GROUP_ID")
    aid = _env_str("CCCC_ACTOR_ID")
    profile = str(os.environ.get("CCCC_MCP_TOOL_PROFILE") or "").strip().lower()
    state: Dict[str, Any] = {}
    if gid and aid:
        try:
            state = _call_daemon_or_raise(
                {
                    "op": "capability_state",
                    "args": {"group_id": gid, "actor_id": aid, "by": aid},
                },
                timeout_s=4.0,
            )
        except Exception:
            state = {}

    if profile == "full":
        visible = {str(spec.get("name") or "").strip() for spec in MCP_TOOLS if isinstance(spec, dict)}
    else:
        tools_raw = state.get("visible_tools") if isinstance(state, dict) else []
        if not isinstance(tools_raw, list):
            tools_raw = []
        visible = {str(x).strip() for x in tools_raw if str(x).strip()}
        if not visible:
            visible = set(CORE_TOOL_NAMES)

    dynamic_raw = state.get("dynamic_tools") if isinstance(state, dict) else []
    dynamic_specs: List[Dict[str, Any]] = []
    if isinstance(dynamic_raw, list):
        for item in dynamic_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            schema = item.get("inputSchema")
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}, "required": []}
            dynamic_specs.append(
                {
                    "name": name,
                    "description": str(item.get("description") or "").strip()
                    or f"Dynamic capability tool ({name})",
                    "inputSchema": schema,
                }
            )

    out = [spec for spec in MCP_TOOLS if str(spec.get("name") or "") in visible]
    existing = {str(spec.get("name") or "") for spec in out}
    for spec in dynamic_specs:
        name = str(spec.get("name") or "")
        if name and name not in existing:
            out.append(spec)
            existing.add(name)
    return out
