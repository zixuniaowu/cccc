"""
CCCC MCP Server — IM-style Agent Collaboration Tools

Core tools exposed to agents:

cccc.* namespace (collaboration control plane):
- cccc_help: CCCC operational playbook (embedded in tool description)
- cccc_inbox_list: Get unread messages (supports kind_filter)
- cccc_inbox_mark_read: Mark messages as read
- cccc_message_send: Send message
- cccc_message_reply: Reply to message
- cccc_group_info: Get group info
- cccc_actor_list: Get actor list
- cccc_actor_add: Add new actor (foreman only)
- cccc_actor_remove: Remove actor (foreman only)
- cccc_actor_start: Start actor
- cccc_actor_stop: Stop actor
- cccc_runtime_list: List available agent runtimes
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

All operations go through daemon IPC to ensure single-writer principle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ...daemon.server import call_daemon


class MCPError(Exception):
    """MCP tool call error"""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _call_daemon_or_raise(req: Dict[str, Any]) -> Dict[str, Any]:
    """Call daemon, raise MCPError on failure"""
    resp = call_daemon(req)
    if not resp.get("ok"):
        err = resp.get("error") or {}
        if isinstance(err, dict):
            raise MCPError(
                code=str(err.get("code") or "daemon_error"),
                message=str(err.get("message") or "daemon error"),
                details=err.get("details") if isinstance(err.get("details"), dict) else {},
            )
        raise MCPError(code="daemon_error", message=str(err))
    return resp.get("result") if isinstance(resp.get("result"), dict) else {}


def _load_ops_playbook() -> str:
    """Load the CCCC ops playbook text for cccc_help."""
    try:
        import importlib.resources

        files = importlib.resources.files("cccc.resources")
        return (files / "cccc-ops.md").read_text(encoding="utf-8")
    except Exception:
        try:
            p = Path(__file__).resolve().parents[2] / "resources" / "cccc-ops.md"
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""


_CCCC_HELP_TEXT = _load_ops_playbook().strip()
_CCCC_HELP_DESCRIPTION = (
    "CCCC Help — Ops Playbook (authoritative)\n\n"
    "This tool's description intentionally embeds the full CCCC operational playbook so it stays in context.\n\n"
    + (_CCCC_HELP_TEXT if _CCCC_HELP_TEXT else "(missing playbook: cccc-ops.md)")
)


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


# =============================================================================
# Message Tools
# =============================================================================


def message_send(
    *, group_id: str, actor_id: str, text: str, to: Optional[List[str]] = None, reply_to: Optional[str] = None
) -> Dict[str, Any]:
    """Send a message"""
    if reply_to:
        return _call_daemon_or_raise({
            "op": "reply",
            "args": {"group_id": group_id, "text": text, "by": actor_id, "reply_to": reply_to, "to": to or []},
        })
    return _call_daemon_or_raise({
        "op": "send",
        "args": {"group_id": group_id, "text": text, "by": actor_id, "to": to or [], "path": ""},
    })


def message_reply(
    *, group_id: str, actor_id: str, reply_to: str, text: str, to: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Reply to a message"""
    return _call_daemon_or_raise({
        "op": "reply",
        "args": {"group_id": group_id, "text": text, "by": actor_id, "reply_to": reply_to, "to": to or []},
    })


# =============================================================================
# Group/Actor Info Tools
# =============================================================================


def group_info(*, group_id: str) -> Dict[str, Any]:
    """Get group information"""
    return _call_daemon_or_raise({"op": "group_show", "args": {"group_id": group_id}})


def actor_list(*, group_id: str) -> Dict[str, Any]:
    """Get actor list"""
    return _call_daemon_or_raise({"op": "actor_list", "args": {"group_id": group_id}})


def actor_add(
    *, group_id: str, by: str, actor_id: str,
    runtime: str = "codex", runner: str = "pty", title: str = "",
    command: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Add a new actor (foreman only). Role is auto-determined by position."""
    return _call_daemon_or_raise({
        "op": "actor_add",
        "args": {
            "group_id": group_id,
            "actor_id": actor_id,
            "runtime": runtime,
            "runner": runner,
            "title": title,
            "command": command or [],
            "env": env or {},
            "by": by,
        },
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


def group_set_state(*, group_id: str, by: str, state: str) -> Dict[str, Any]:
    """Set group state (active/idle/paused)"""
    return _call_daemon_or_raise({
        "op": "group_set_state",
        "args": {"group_id": group_id, "state": state, "by": by},
    })


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
# Context Tools (all via daemon IPC)
# =============================================================================


def context_get(*, group_id: str) -> Dict[str, Any]:
    """Get full context"""
    return _call_daemon_or_raise({"op": "context_get", "args": {"group_id": group_id}})


def context_sync(*, group_id: str, ops: List[Dict[str, Any]], dry_run: bool = False) -> Dict[str, Any]:
    """Batch sync context operations"""
    return _call_daemon_or_raise({
        "op": "context_sync",
        "args": {"group_id": group_id, "ops": ops, "dry_run": dry_run},
    })


def task_list(*, group_id: str, task_id: Optional[str] = None) -> Dict[str, Any]:
    """List tasks"""
    args: Dict[str, Any] = {"group_id": group_id}
    if task_id:
        args["task_id"] = task_id
    return _call_daemon_or_raise({"op": "task_list", "args": args})


def presence_get(*, group_id: str) -> Dict[str, Any]:
    """Get presence status"""
    return _call_daemon_or_raise({"op": "presence_get", "args": {"group_id": group_id}})


# Convenience wrappers (all delegate to context_sync)


def vision_update(*, group_id: str, vision: str) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{"op": "vision.update", "vision": vision}])


def sketch_update(*, group_id: str, sketch: str) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{"op": "sketch.update", "sketch": sketch}])


def milestone_create(*, group_id: str, name: str, description: str, status: str = "pending") -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{
        "op": "milestone.create", "name": name, "description": description, "status": status
    }])


def milestone_update(
    *, group_id: str, milestone_id: str,
    name: Optional[str] = None, description: Optional[str] = None, status: Optional[str] = None
) -> Dict[str, Any]:
    op: Dict[str, Any] = {"op": "milestone.update", "milestone_id": milestone_id}
    if name is not None:
        op["name"] = name
    if description is not None:
        op["description"] = description
    if status is not None:
        op["status"] = status
    return context_sync(group_id=group_id, ops=[op])


def milestone_complete(*, group_id: str, milestone_id: str, outcomes: str) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{
        "op": "milestone.complete", "milestone_id": milestone_id, "outcomes": outcomes
    }])


def milestone_remove(*, group_id: str, milestone_id: str) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{"op": "milestone.remove", "milestone_id": milestone_id}])


def task_create(
    *, group_id: str, name: str, goal: str, steps: List[Dict[str, str]],
    milestone_id: Optional[str] = None, assignee: Optional[str] = None
) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{
        "op": "task.create", "name": name, "goal": goal, "steps": steps,
        "milestone_id": milestone_id, "assignee": assignee
    }])


def task_update(
    *, group_id: str, task_id: str,
    status: Optional[str] = None, name: Optional[str] = None, goal: Optional[str] = None,
    assignee: Optional[str] = None, milestone_id: Optional[str] = None,
    step_id: Optional[str] = None, step_status: Optional[str] = None
) -> Dict[str, Any]:
    op: Dict[str, Any] = {"op": "task.update", "task_id": task_id}
    if status is not None:
        op["status"] = status
    if name is not None:
        op["name"] = name
    if goal is not None:
        op["goal"] = goal
    if assignee is not None:
        op["assignee"] = assignee
    if milestone_id is not None:
        op["milestone_id"] = milestone_id
    if step_id is not None and step_status is not None:
        op["step_id"] = step_id
        op["step_status"] = step_status
    return context_sync(group_id=group_id, ops=[op])


def task_delete(*, group_id: str, task_id: str) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{"op": "task.delete", "task_id": task_id}])


def note_add(*, group_id: str, content: str, ttl: int = 30) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{"op": "note.add", "content": content, "ttl": ttl}])


def note_update(*, group_id: str, note_id: str, content: Optional[str] = None, ttl: Optional[int] = None) -> Dict[str, Any]:
    op: Dict[str, Any] = {"op": "note.update", "note_id": note_id}
    if content is not None:
        op["content"] = content
    if ttl is not None:
        op["ttl"] = ttl
    return context_sync(group_id=group_id, ops=[op])


def note_remove(*, group_id: str, note_id: str) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{"op": "note.remove", "note_id": note_id}])


def reference_add(*, group_id: str, url: str, note: str, ttl: int = 30) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{"op": "reference.add", "url": url, "note": note, "ttl": ttl}])


def reference_update(
    *, group_id: str, reference_id: str,
    url: Optional[str] = None, note: Optional[str] = None, ttl: Optional[int] = None
) -> Dict[str, Any]:
    op: Dict[str, Any] = {"op": "reference.update", "reference_id": reference_id}
    if url is not None:
        op["url"] = url
    if note is not None:
        op["note"] = note
    if ttl is not None:
        op["ttl"] = ttl
    return context_sync(group_id=group_id, ops=[op])


def reference_remove(*, group_id: str, reference_id: str) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{"op": "reference.remove", "reference_id": reference_id}])


def presence_update(*, group_id: str, agent_id: str, status: str) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{"op": "presence.update", "agent_id": agent_id, "status": status}])


def presence_clear(*, group_id: str, agent_id: str) -> Dict[str, Any]:
    return context_sync(group_id=group_id, ops=[{"op": "presence.clear", "agent_id": agent_id}])


# =============================================================================
# Headless Runner Tools (for MCP-driven agents)
# =============================================================================


def headless_status(*, group_id: str, actor_id: str) -> Dict[str, Any]:
    """Get headless session status"""
    return _call_daemon_or_raise({
        "op": "headless_status",
        "args": {"group_id": group_id, "actor_id": actor_id},
    })


def headless_set_status(
    *, group_id: str, actor_id: str, status: str, task_id: Optional[str] = None
) -> Dict[str, Any]:
    """Update headless session status"""
    return _call_daemon_or_raise({
        "op": "headless_set_status",
        "args": {"group_id": group_id, "actor_id": actor_id, "status": status, "task_id": task_id},
    })


def headless_ack_message(*, group_id: str, actor_id: str, message_id: str) -> Dict[str, Any]:
    """Acknowledge processed message"""
    return _call_daemon_or_raise({
        "op": "headless_ack_message",
        "args": {"group_id": group_id, "actor_id": actor_id, "message_id": message_id},
    })


# =============================================================================
# System Notification Tools
# =============================================================================


def notify_send(
    *, group_id: str, actor_id: str, kind: str, title: str, message: str,
    target_actor_id: Optional[str] = None, priority: str = "normal", requires_ack: bool = False
) -> Dict[str, Any]:
    """Send system notification"""
    return _call_daemon_or_raise({
        "op": "system_notify",
        "args": {
            "group_id": group_id,
            "by": actor_id,
            "kind": kind,
            "priority": priority,
            "title": title,
            "message": message,
            "target_actor_id": target_actor_id,
            "requires_ack": requires_ack,
        },
    })


def notify_ack(*, group_id: str, actor_id: str, notify_event_id: str) -> Dict[str, Any]:
    """Acknowledge system notification"""
    return _call_daemon_or_raise({
        "op": "notify_ack",
        "args": {"group_id": group_id, "actor_id": actor_id, "notify_event_id": notify_event_id, "by": actor_id},
    })


# =============================================================================
# MCP Tool Definitions
# =============================================================================

MCP_TOOLS = [
    # cccc.* namespace - collaboration
    {
        "name": "cccc_help",
        "description": _CCCC_HELP_DESCRIPTION,
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cccc_inbox_list",
        "description": "Get your unread messages. Returns messages in chronological order. Supports filtering by type.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "actor_id": {"type": "string", "description": "Your actor ID"},
                "limit": {"type": "integer", "description": "Max messages to return (default 50)", "default": 50},
                "kind_filter": {
                    "type": "string",
                    "enum": ["all", "chat", "notify"],
                    "description": "Filter by type: all=everything, chat=messages only, notify=system notifications only",
                    "default": "all",
                },
            },
            "required": ["group_id", "actor_id"],
        },
    },
    {
        "name": "cccc_inbox_mark_read",
        "description": "Mark messages as read up to specified event (inclusive). Call after processing messages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "actor_id": {"type": "string", "description": "Your actor ID"},
                "event_id": {"type": "string", "description": "Event ID to mark as read up to"},
            },
            "required": ["group_id", "actor_id", "event_id"],
        },
    },
    {
        "name": "cccc_message_send",
        "description": "Send a message to other actors or user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "actor_id": {"type": "string", "description": "Your actor ID (sender)"},
                "text": {"type": "string", "description": "Message content"},
                "to": {"type": "array", "items": {"type": "string"}, "description": "Recipients. Options: user, @all, @peers, @foreman, or specific actor_id. Empty=broadcast."},
            },
            "required": ["group_id", "actor_id", "text"],
        },
    },
    {
        "name": "cccc_message_reply",
        "description": "Reply to a message. Automatically quotes the original message.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "actor_id": {"type": "string", "description": "Your actor ID (sender)"},
                "reply_to": {"type": "string", "description": "Event ID of message to reply to"},
                "text": {"type": "string", "description": "Reply content"},
                "to": {"type": "array", "items": {"type": "string"}, "description": "Recipients (optional, defaults to original sender)"},
            },
            "required": ["group_id", "actor_id", "reply_to", "text"],
        },
    },
    {
        "name": "cccc_group_info",
        "description": "Get working group information (title, scopes, actors, etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {"group_id": {"type": "string", "description": "Working group ID"}},
            "required": ["group_id"],
        },
    },
    {
        "name": "cccc_actor_list",
        "description": "Get list of all actors in the group.",
        "inputSchema": {
            "type": "object",
            "properties": {"group_id": {"type": "string", "description": "Working group ID"}},
            "required": ["group_id"],
        },
    },
    {
        "name": "cccc_actor_add",
        "description": "Add a new actor to the group. Only foreman can add actors. Role is auto-determined: first enabled actor = foreman, rest = peer. Use cccc_runtime_list first to see available runtimes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "by": {"type": "string", "description": "Your actor ID (must be foreman)"},
                "actor_id": {"type": "string", "description": "New actor ID (e.g. peer-impl, peer-test)"},
                "runtime": {
                    "type": "string",
                    "enum": ["claude", "codex", "droid", "opencode", "copilot"],
                    "description": "Agent runtime (auto-sets command)",
                    "default": "codex",
                },
                "runner": {
                    "type": "string",
                    "enum": ["pty", "headless"],
                    "description": "Runner type: pty (interactive terminal) or headless (MCP-only)",
                    "default": "pty",
                },
                "title": {"type": "string", "description": "Display title (optional)"},
                "command": {"type": "array", "items": {"type": "string"}, "description": "Command (optional, auto-set by runtime)"},
                "env": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Environment variables"},
            },
            "required": ["group_id", "by", "actor_id"],
        },
    },
    {
        "name": "cccc_actor_remove",
        "description": "Remove an actor from the group. Foreman and peer can only remove themselves. To remove a peer: tell them to finish up and call this on themselves.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "by": {"type": "string", "description": "Your actor ID"},
                "actor_id": {"type": "string", "description": "Actor ID to remove (must be yourself)"},
            },
            "required": ["group_id", "by", "actor_id"],
        },
    },
    {
        "name": "cccc_actor_start",
        "description": "Start an actor (set enabled=true). Only foreman can start actors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "by": {"type": "string", "description": "Your actor ID (must be foreman)"},
                "actor_id": {"type": "string", "description": "Actor ID to start"},
            },
            "required": ["group_id", "by", "actor_id"],
        },
    },
    {
        "name": "cccc_actor_stop",
        "description": "Stop an actor (set enabled=false). Foreman can stop any actor; peer can only stop themselves.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "by": {"type": "string", "description": "Your actor ID"},
                "actor_id": {"type": "string", "description": "Actor ID to stop"},
            },
            "required": ["group_id", "by", "actor_id"],
        },
    },
    {
        "name": "cccc_actor_restart",
        "description": "Restart an actor (stop + start, clears context). Foreman can restart any actor; peer can only restart themselves. Useful when context is too long or state is confused.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "by": {"type": "string", "description": "Your actor ID"},
                "actor_id": {"type": "string", "description": "Actor ID to restart"},
            },
            "required": ["group_id", "by", "actor_id"],
        },
    },
    {
        "name": "cccc_runtime_list",
        "description": "List available agent runtimes on the system. Call before cccc_actor_add to see which runtimes can be used.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "cccc_group_set_state",
        "description": "Set group state to control automation behavior. States: active (normal operation), idle (task complete, automation disabled), paused (user paused). Foreman should set to 'idle' when task is complete.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "by": {"type": "string", "description": "Your actor ID"},
                "state": {
                    "type": "string",
                    "enum": ["active", "idle", "paused"],
                    "description": "New state: active (work in progress), idle (task complete), paused (user paused)",
                },
            },
            "required": ["group_id", "by", "state"],
        },
    },
    {
        "name": "cccc_project_info",
        "description": "Get PROJECT.md content from the group's active scope. Use this to understand project goals, constraints, and context. Call at session start or when you need to align with project vision.",
        "inputSchema": {
            "type": "object",
            "properties": {"group_id": {"type": "string", "description": "Working group ID"}},
            "required": ["group_id"],
        },
    },
    # context.* namespace - state sync
    {
        "name": "cccc_context_get",
        "description": "Get full group context (vision/sketch/milestones/tasks/notes/references/presence). Call at session start.",
        "inputSchema": {
            "type": "object",
            "properties": {"group_id": {"type": "string", "description": "Working group ID"}},
            "required": ["group_id"],
        },
    },
    {
        "name": "cccc_context_sync",
        "description": "Batch sync context operations. Supported ops: vision.update, sketch.update, milestone.*, task.*, note.*, reference.*, presence.*",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "ops": {"type": "array", "items": {"type": "object"}, "description": "List of operations, each is {op: string, ...params}"},
                "dry_run": {"type": "boolean", "description": "Validate only without executing", "default": False},
            },
            "required": ["group_id", "ops"],
        },
    },
    {
        "name": "cccc_vision_update",
        "description": "Update project vision (one-sentence north star goal).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "vision": {"type": "string", "description": "Project vision"},
            },
            "required": ["group_id", "vision"],
        },
    },
    {
        "name": "cccc_sketch_update",
        "description": "Update execution sketch (static architecture/strategy, no TODOs/progress).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "sketch": {"type": "string", "description": "Execution sketch (markdown)"},
            },
            "required": ["group_id", "sketch"],
        },
    },
    {
        "name": "cccc_milestone_create",
        "description": "Create a milestone (coarse-grained phase, 2-6 total).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "name": {"type": "string", "description": "Milestone name"},
                "description": {"type": "string", "description": "Detailed description"},
                "status": {"type": "string", "enum": ["pending", "active", "done"], "description": "Status (default pending)", "default": "pending"},
            },
            "required": ["group_id", "name", "description"],
        },
    },
    {
        "name": "cccc_milestone_update",
        "description": "Update a milestone.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "milestone_id": {"type": "string", "description": "Milestone ID (M1, M2...)"},
                "name": {"type": "string", "description": "New name"},
                "description": {"type": "string", "description": "New description"},
                "status": {"type": "string", "enum": ["pending", "active", "done"], "description": "New status"},
            },
            "required": ["group_id", "milestone_id"],
        },
    },
    {
        "name": "cccc_milestone_complete",
        "description": "Complete a milestone and record outcomes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "milestone_id": {"type": "string", "description": "Milestone ID"},
                "outcomes": {"type": "string", "description": "Outcomes summary"},
            },
            "required": ["group_id", "milestone_id", "outcomes"],
        },
    },
    {
        "name": "cccc_milestone_remove",
        "description": "Remove a milestone.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "milestone_id": {"type": "string", "description": "Milestone ID"},
            },
            "required": ["group_id", "milestone_id"],
        },
    },
    {
        "name": "cccc_task_list",
        "description": "List all tasks or get single task details.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "task_id": {"type": "string", "description": "Task ID (optional, omit to list all)"},
            },
            "required": ["group_id"],
        },
    },
    {
        "name": "cccc_task_create",
        "description": "Create a task (deliverable work item with 3-7 steps).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "name": {"type": "string", "description": "Task name"},
                "goal": {"type": "string", "description": "Completion criteria"},
                "steps": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"name": {"type": "string"}, "acceptance": {"type": "string"}}, "required": ["name", "acceptance"]},
                    "description": "Step list (3-7 steps)",
                },
                "milestone_id": {"type": "string", "description": "Associated milestone ID"},
                "assignee": {"type": "string", "description": "Assignee actor ID"},
            },
            "required": ["group_id", "name", "goal", "steps"],
        },
    },
    {
        "name": "cccc_task_update",
        "description": "Update task status or step progress.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "task_id": {"type": "string", "description": "Task ID (T001, T002...)"},
                "status": {"type": "string", "enum": ["planned", "active", "done"], "description": "Task status"},
                "name": {"type": "string", "description": "New name"},
                "goal": {"type": "string", "description": "New completion criteria"},
                "assignee": {"type": "string", "description": "New assignee"},
                "milestone_id": {"type": "string", "description": "New associated milestone"},
                "step_id": {"type": "string", "description": "Step ID to update (S1, S2...)"},
                "step_status": {"type": "string", "enum": ["pending", "in_progress", "done"], "description": "New step status"},
            },
            "required": ["group_id", "task_id"],
        },
    },
    {
        "name": "cccc_task_delete",
        "description": "Delete a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "task_id": {"type": "string", "description": "Task ID"},
            },
            "required": ["group_id", "task_id"],
        },
    },
    {
        "name": "cccc_note_add",
        "description": "Add a note (lessons, discoveries, warnings). TTL controls retention.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "content": {"type": "string", "description": "Note content"},
                "ttl": {"type": "integer", "description": "TTL rounds (10=short, 30=normal, 100=long)", "default": 30, "minimum": 10, "maximum": 100},
            },
            "required": ["group_id", "content"],
        },
    },
    {
        "name": "cccc_note_update",
        "description": "Update note content or TTL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "note_id": {"type": "string", "description": "Note ID (N001, N002...)"},
                "content": {"type": "string", "description": "New content"},
                "ttl": {"type": "integer", "description": "New TTL", "minimum": 0, "maximum": 100},
            },
            "required": ["group_id", "note_id"],
        },
    },
    {
        "name": "cccc_note_remove",
        "description": "Remove a note.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "note_id": {"type": "string", "description": "Note ID"},
            },
            "required": ["group_id", "note_id"],
        },
    },
    {
        "name": "cccc_reference_add",
        "description": "Add a reference (useful file/URL). TTL controls retention.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "url": {"type": "string", "description": "File path or URL"},
                "note": {"type": "string", "description": "Why this is useful"},
                "ttl": {"type": "integer", "description": "TTL rounds (10=short, 30=normal, 100=long)", "default": 30, "minimum": 10, "maximum": 100},
            },
            "required": ["group_id", "url", "note"],
        },
    },
    {
        "name": "cccc_reference_update",
        "description": "Update a reference.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "reference_id": {"type": "string", "description": "Reference ID (R001, R002...)"},
                "url": {"type": "string", "description": "New URL"},
                "note": {"type": "string", "description": "New note"},
                "ttl": {"type": "integer", "description": "New TTL", "minimum": 0, "maximum": 100},
            },
            "required": ["group_id", "reference_id"],
        },
    },
    {
        "name": "cccc_reference_remove",
        "description": "Remove a reference.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "reference_id": {"type": "string", "description": "Reference ID"},
            },
            "required": ["group_id", "reference_id"],
        },
    },
    {
        "name": "cccc_presence_get",
        "description": "Get presence status of all agents.",
        "inputSchema": {
            "type": "object",
            "properties": {"group_id": {"type": "string", "description": "Working group ID"}},
            "required": ["group_id"],
        },
    },
    {
        "name": "cccc_presence_update",
        "description": "Update your presence status (what you're doing/thinking).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "agent_id": {"type": "string", "description": "Your agent ID"},
                "status": {"type": "string", "description": "Status description (1-2 sentences)"},
            },
            "required": ["group_id", "agent_id", "status"],
        },
    },
    {
        "name": "cccc_presence_clear",
        "description": "Clear your presence status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "agent_id": {"type": "string", "description": "Your agent ID"},
            },
            "required": ["group_id", "agent_id"],
        },
    },
    # headless.* namespace - headless runner control (for MCP-driven agents)
    {
        "name": "cccc_headless_status",
        "description": "Get headless session status. Only for runner=headless actors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "actor_id": {"type": "string", "description": "Your actor ID"},
            },
            "required": ["group_id", "actor_id"],
        },
    },
    {
        "name": "cccc_headless_set_status",
        "description": "Update headless session status. Report your current work state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "actor_id": {"type": "string", "description": "Your actor ID"},
                "status": {
                    "type": "string",
                    "enum": ["idle", "working", "waiting", "stopped"],
                    "description": "Status: idle=waiting for tasks, working=executing, waiting=blocked on decision, stopped=terminated",
                },
                "task_id": {"type": "string", "description": "Current task ID (optional)"},
            },
            "required": ["group_id", "actor_id", "status"],
        },
    },
    {
        "name": "cccc_headless_ack_message",
        "description": "Acknowledge a processed message. For headless loop message confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "actor_id": {"type": "string", "description": "Your actor ID"},
                "message_id": {"type": "string", "description": "Processed message event_id"},
            },
            "required": ["group_id", "actor_id", "message_id"],
        },
    },
    # notify.* namespace - system notifications
    {
        "name": "cccc_notify_send",
        "description": "Send system notification (for system-level agent communication, won't pollute chat log).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "actor_id": {"type": "string", "description": "Your actor ID (sender)"},
                "kind": {
                    "type": "string",
                    "enum": ["nudge", "keepalive", "actor_idle", "silence_check", "status_change", "error", "info"],
                    "description": "Notification type",
                },
                "title": {"type": "string", "description": "Notification title"},
                "message": {"type": "string", "description": "Notification content"},
                "target_actor_id": {"type": "string", "description": "Target actor ID (optional, omit=broadcast)"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "urgent"],
                    "description": "Priority (high/urgent delivered directly to PTY)",
                    "default": "normal",
                },
                "requires_ack": {"type": "boolean", "description": "Whether acknowledgment is required", "default": False},
            },
            "required": ["group_id", "actor_id", "kind", "title", "message"],
        },
    },
    {
        "name": "cccc_notify_ack",
        "description": "Acknowledge system notification (only when requires_ack=true).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Working group ID"},
                "actor_id": {"type": "string", "description": "Your actor ID"},
                "notify_event_id": {"type": "string", "description": "Notification event_id to acknowledge"},
            },
            "required": ["group_id", "actor_id", "notify_event_id"],
        },
    },
]


# =============================================================================
# Tool Call Handler
# =============================================================================


def handle_tool_call(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP tool call"""

    # cccc.* namespace
    if name == "cccc_help":
        return {
            "markdown": _load_ops_playbook(),
            "source": "cccc.resources/cccc-ops.md",
        }

    if name == "cccc_inbox_list":
        return inbox_list(
            group_id=str(arguments.get("group_id") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
            limit=int(arguments.get("limit") or 50),
            kind_filter=str(arguments.get("kind_filter") or "all"),
        )

    if name == "cccc_inbox_mark_read":
        return inbox_mark_read(
            group_id=str(arguments.get("group_id") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
            event_id=str(arguments.get("event_id") or ""),
        )

    if name == "cccc_message_send":
        to_raw = arguments.get("to")
        return message_send(
            group_id=str(arguments.get("group_id") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
            text=str(arguments.get("text") or ""),
            to=list(to_raw) if isinstance(to_raw, list) else [],
        )

    if name == "cccc_message_reply":
        to_raw = arguments.get("to")
        return message_reply(
            group_id=str(arguments.get("group_id") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
            reply_to=str(arguments.get("reply_to") or ""),
            text=str(arguments.get("text") or ""),
            to=list(to_raw) if isinstance(to_raw, list) else None,
        )

    if name == "cccc_group_info":
        return group_info(group_id=str(arguments.get("group_id") or ""))

    if name == "cccc_actor_list":
        return actor_list(group_id=str(arguments.get("group_id") or ""))

    if name == "cccc_actor_add":
        cmd_raw = arguments.get("command")
        env_raw = arguments.get("env")
        return actor_add(
            group_id=str(arguments.get("group_id") or ""),
            by=str(arguments.get("by") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
            # Note: role is auto-determined by position
            runtime=str(arguments.get("runtime") or "codex"),
            runner=str(arguments.get("runner") or "pty"),
            title=str(arguments.get("title") or ""),
            command=list(cmd_raw) if isinstance(cmd_raw, list) else None,
            env=dict(env_raw) if isinstance(env_raw, dict) else None,
        )

    if name == "cccc_actor_remove":
        return actor_remove(
            group_id=str(arguments.get("group_id") or ""),
            by=str(arguments.get("by") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
        )

    if name == "cccc_actor_start":
        return actor_start(
            group_id=str(arguments.get("group_id") or ""),
            by=str(arguments.get("by") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
        )

    if name == "cccc_actor_stop":
        return actor_stop(
            group_id=str(arguments.get("group_id") or ""),
            by=str(arguments.get("by") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
        )

    if name == "cccc_actor_restart":
        return actor_restart(
            group_id=str(arguments.get("group_id") or ""),
            by=str(arguments.get("by") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
        )

    if name == "cccc_runtime_list":
        return runtime_list()

    if name == "cccc_group_set_state":
        return group_set_state(
            group_id=str(arguments.get("group_id") or ""),
            by=str(arguments.get("by") or ""),
            state=str(arguments.get("state") or ""),
        )

    if name == "cccc_project_info":
        return project_info(group_id=str(arguments.get("group_id") or ""))

    # context.* namespace
    if name == "cccc_context_get":
        return context_get(group_id=str(arguments.get("group_id") or ""))

    if name == "cccc_context_sync":
        ops_raw = arguments.get("ops")
        return context_sync(
            group_id=str(arguments.get("group_id") or ""),
            ops=list(ops_raw) if isinstance(ops_raw, list) else [],
            dry_run=bool(arguments.get("dry_run")),
        )

    if name == "cccc_vision_update":
        return vision_update(
            group_id=str(arguments.get("group_id") or ""),
            vision=str(arguments.get("vision") or ""),
        )

    if name == "cccc_sketch_update":
        return sketch_update(
            group_id=str(arguments.get("group_id") or ""),
            sketch=str(arguments.get("sketch") or ""),
        )

    if name == "cccc_milestone_create":
        return milestone_create(
            group_id=str(arguments.get("group_id") or ""),
            name=str(arguments.get("name") or ""),
            description=str(arguments.get("description") or ""),
            status=str(arguments.get("status") or "pending"),
        )

    if name == "cccc_milestone_update":
        return milestone_update(
            group_id=str(arguments.get("group_id") or ""),
            milestone_id=str(arguments.get("milestone_id") or ""),
            name=arguments.get("name"),
            description=arguments.get("description"),
            status=arguments.get("status"),
        )

    if name == "cccc_milestone_complete":
        return milestone_complete(
            group_id=str(arguments.get("group_id") or ""),
            milestone_id=str(arguments.get("milestone_id") or ""),
            outcomes=str(arguments.get("outcomes") or ""),
        )

    if name == "cccc_milestone_remove":
        return milestone_remove(
            group_id=str(arguments.get("group_id") or ""),
            milestone_id=str(arguments.get("milestone_id") or ""),
        )

    if name == "cccc_task_list":
        return task_list(
            group_id=str(arguments.get("group_id") or ""),
            task_id=arguments.get("task_id"),
        )

    if name == "cccc_task_create":
        steps_raw = arguments.get("steps")
        return task_create(
            group_id=str(arguments.get("group_id") or ""),
            name=str(arguments.get("name") or ""),
            goal=str(arguments.get("goal") or ""),
            steps=list(steps_raw) if isinstance(steps_raw, list) else [],
            milestone_id=arguments.get("milestone_id"),
            assignee=arguments.get("assignee"),
        )

    if name == "cccc_task_update":
        return task_update(
            group_id=str(arguments.get("group_id") or ""),
            task_id=str(arguments.get("task_id") or ""),
            status=arguments.get("status"),
            name=arguments.get("name"),
            goal=arguments.get("goal"),
            assignee=arguments.get("assignee"),
            milestone_id=arguments.get("milestone_id"),
            step_id=arguments.get("step_id"),
            step_status=arguments.get("step_status"),
        )

    if name == "cccc_task_delete":
        return task_delete(
            group_id=str(arguments.get("group_id") or ""),
            task_id=str(arguments.get("task_id") or ""),
        )

    if name == "cccc_note_add":
        return note_add(
            group_id=str(arguments.get("group_id") or ""),
            content=str(arguments.get("content") or ""),
            ttl=int(arguments.get("ttl") or 30),
        )

    if name == "cccc_note_update":
        return note_update(
            group_id=str(arguments.get("group_id") or ""),
            note_id=str(arguments.get("note_id") or ""),
            content=arguments.get("content"),
            ttl=int(arguments["ttl"]) if "ttl" in arguments else None,
        )

    if name == "cccc_note_remove":
        return note_remove(
            group_id=str(arguments.get("group_id") or ""),
            note_id=str(arguments.get("note_id") or ""),
        )

    if name == "cccc_reference_add":
        return reference_add(
            group_id=str(arguments.get("group_id") or ""),
            url=str(arguments.get("url") or ""),
            note=str(arguments.get("note") or ""),
            ttl=int(arguments.get("ttl") or 30),
        )

    if name == "cccc_reference_update":
        return reference_update(
            group_id=str(arguments.get("group_id") or ""),
            reference_id=str(arguments.get("reference_id") or ""),
            url=arguments.get("url"),
            note=arguments.get("note"),
            ttl=int(arguments["ttl"]) if "ttl" in arguments else None,
        )

    if name == "cccc_reference_remove":
        return reference_remove(
            group_id=str(arguments.get("group_id") or ""),
            reference_id=str(arguments.get("reference_id") or ""),
        )

    if name == "cccc_presence_get":
        return presence_get(group_id=str(arguments.get("group_id") or ""))

    if name == "cccc_presence_update":
        return presence_update(
            group_id=str(arguments.get("group_id") or ""),
            agent_id=str(arguments.get("agent_id") or ""),
            status=str(arguments.get("status") or ""),
        )

    if name == "cccc_presence_clear":
        return presence_clear(
            group_id=str(arguments.get("group_id") or ""),
            agent_id=str(arguments.get("agent_id") or ""),
        )

    # headless.* namespace - headless runner control
    if name == "cccc_headless_status":
        return headless_status(
            group_id=str(arguments.get("group_id") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
        )

    if name == "cccc_headless_set_status":
        return headless_set_status(
            group_id=str(arguments.get("group_id") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
            status=str(arguments.get("status") or ""),
            task_id=arguments.get("task_id"),
        )

    if name == "cccc_headless_ack_message":
        return headless_ack_message(
            group_id=str(arguments.get("group_id") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
            message_id=str(arguments.get("message_id") or ""),
        )

    # notify.* namespace - system notifications
    if name == "cccc_notify_send":
        return notify_send(
            group_id=str(arguments.get("group_id") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
            kind=str(arguments.get("kind") or "info"),
            title=str(arguments.get("title") or ""),
            message=str(arguments.get("message") or ""),
            target_actor_id=arguments.get("target_actor_id"),
            priority=str(arguments.get("priority") or "normal"),
            requires_ack=bool(arguments.get("requires_ack")),
        )

    if name == "cccc_notify_ack":
        return notify_ack(
            group_id=str(arguments.get("group_id") or ""),
            actor_id=str(arguments.get("actor_id") or ""),
            notify_event_id=str(arguments.get("notify_event_id") or ""),
        )

    raise MCPError(code="unknown_tool", message=f"unknown tool: {name}")
