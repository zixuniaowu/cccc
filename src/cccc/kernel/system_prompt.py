from __future__ import annotations

from typing import Any, Dict, List

from .actors import get_effective_role, list_actors
from .group import Group


def render_system_prompt(*, group: Group, actor: Dict[str, Any]) -> str:
    """Render SYSTEM prompt for an actor.
    
    Design principles:
    - Minimal: Only session-specific context (identity, group, scopes)
    - No tool docs: Agent sees MCP tools automatically
    - Skills document (auto-loaded) provides workflow guidance
    """
    group_id = str(group.group_id or "").strip()
    actor_id = str(actor.get("id") or "").strip()
    role = get_effective_role(group, actor_id)
    runner = str(actor.get("runner") or "pty").strip()

    title = str(group.doc.get("title") or group_id)
    topic = str(group.doc.get("topic") or "").strip()
    
    # Count actors
    actors = list_actors(group)
    enabled_actors = [a for a in actors if isinstance(a, dict) and a.get("enabled", True)]
    actor_count = len(enabled_actors)
    is_solo = actor_count <= 1
    
    # List other actors
    other_actors = [str(a.get("id") or "") for a in enabled_actors if str(a.get("id") or "") != actor_id]

    # Scopes
    scopes = group.doc.get("scopes") if isinstance(group.doc.get("scopes"), list) else []
    active_scope_key = str(group.doc.get("active_scope_key") or "")
    scope_lines: List[str] = []
    for sc in scopes:
        if not isinstance(sc, dict):
            continue
        sk = str(sc.get("scope_key") or "")
        url = str(sc.get("url") or "")
        label = str(sc.get("label") or sk)
        mark = " *" if sk and sk == active_scope_key else ""
        if url:
            scope_lines.append(f"  {label}: {url}{mark}")

    # Build minimal prompt
    lines = [
        f"[CCCC] You are {actor_id} ({role}) in group '{title}'",
        f"group_id: {group_id}",
    ]
    if topic:
        lines.append(f"topic: {topic}")
    
    # Team status
    if is_solo:
        lines.append(f"team: solo (you're the only actor)")
    else:
        lines.append(f"team: {actor_count} actors ({', '.join(other_actors)})")
    
    # Runner mode
    if runner == "headless":
        lines.append("runner: headless (MCP-only, no PTY)")
    
    # Scopes
    if scope_lines:
        lines.append("")
        lines.append("scopes (* = active):")
        lines.extend(scope_lines)
    
    # Instructions - emphasize MCP for messaging
    lines.extend([
        "",
        "---",
        "You have cccc_* MCP tools. See cccc-ops skill for workflow guidance.",
        "",
        "⚠️ IMPORTANT: Always use MCP tools for messaging:",
        "  - cccc_message_send(to, text) → send message",
        "  - cccc_message_reply(event_id, text) → reply to message",
        "  - Terminal output (stdout/stderr) is NOT delivered as a chat message; users may never see it",
        "  - Do NOT use bash/shell commands for cccc operations",
        "",
        "Quick start:",
        "1. cccc_project_info → understand project goals",
        "2. cccc_context_get → sync state", 
        "3. cccc_inbox_list → check messages",
        "4. Do work, communicate via MCP, mark messages read",
    ])
    
    return "\n".join(lines) + "\n"
