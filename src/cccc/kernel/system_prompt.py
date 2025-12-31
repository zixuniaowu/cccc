from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .actors import get_effective_role, list_actors
from .group import Group


def render_system_prompt(*, group: Group, actor: Dict[str, Any]) -> str:
    """Render SYSTEM prompt for an actor.
    
    Design principles:
    - Minimal: Only session-specific context (identity, group, scopes)
    - No tool docs: Agent sees MCP tools automatically
    - Ops playbook lives in MCP: see cccc_help
    """
    group_id = str(group.group_id or "").strip()
    actor_id = str(actor.get("id") or "").strip()
    role = get_effective_role(group, actor_id)
    runner = str(actor.get("runner") or "pty").strip()

    title = str(group.doc.get("title") or group_id)
    topic = str(group.doc.get("topic") or "").strip()
    
    # Count actors
    actors = list_actors(group)
    enabled_actor_ids: List[str] = []
    for a in actors:
        if not isinstance(a, dict) or not bool(a.get("enabled", True)):
            continue
        aid = str(a.get("id") or "").strip()
        if aid:
            enabled_actor_ids.append(aid)
    actor_count = len(enabled_actor_ids)
    is_solo = actor_count <= 1
    
    foremen = [aid for aid in enabled_actor_ids if get_effective_role(group, aid) == "foreman"]

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

    # PROJECT.md hint (don't inline file content into the prompt by default)
    project_md_line = ""
    project_root = ""
    for sc in scopes:
        if not isinstance(sc, dict):
            continue
        sk = str(sc.get("scope_key") or "")
        if sk and sk == active_scope_key:
            project_root = str(sc.get("url") or "").strip()
            break
    if not project_root:
        for sc in scopes:
            if isinstance(sc, dict):
                project_root = str(sc.get("url") or "").strip()
                if project_root:
                    break
    if project_root:
        try:
            project_root_path = Path(project_root).expanduser()
            project_md_path = project_root_path / "PROJECT.md"
            project_md_lower = project_root_path / "project.md"
            if project_md_path.exists():
                project_md_line = f"project: PROJECT.md found ({project_md_path})"
            elif project_md_lower.exists():
                project_md_line = f"project: PROJECT.md found ({project_md_lower})"
            else:
                project_md_line = f"project: PROJECT.md missing (expected at {project_md_path})"
        except Exception:
            project_md_line = "project: PROJECT.md status unknown"
    else:
        project_md_line = "project: PROJECT.md missing (no scope attached)"

    # Build minimal prompt
    lines = [
        f"[CCCC] You are {actor_id} ({role}) in group '{title}'",
        f"group_id: {group_id}",
    ]
    runtime_name = str(actor.get("runtime") or "").strip()
    if runtime_name:
        lines.append(f"runtime: {runtime_name} ({runner})")
    else:
        lines.append(f"runtime: ({runner})")
    if topic:
        lines.append(f"topic: {topic}")
    
    # Team status
    if is_solo:
        lines.append(f"team: solo (you're the only actor)")
    else:
        show_ids = enabled_actor_ids[:8]
        suffix = "…" if len(enabled_actor_ids) > 8 else ""
        lines.append(f"team: {actor_count} actors ({', '.join(show_ids)}{suffix})")
        if foremen:
            lines.append(f"foreman: {', '.join(foremen)}")
    
    # Runner mode
    if runner == "headless":
        lines.append("runner: headless (MCP-only, no PTY)")

    if project_md_line:
        lines.append(project_md_line)
    
    # Scopes
    if scope_lines:
        lines.append("")
        lines.append("scopes (* = active):")
        lines.extend(scope_lines)
    
    # Instructions - emphasize MCP for messaging
    lines.extend([
        "",
        "---",
        "You have cccc_* MCP tools. See cccc_help for the CCCC ops playbook (authoritative).",
        "PROJECT.md is the project's constitution. Read it, follow it, and do NOT edit it unless the user explicitly asks you to.",
        "",
        "⚠️ IMPORTANT: Always use MCP tools for messaging:",
        "  - cccc_message_send(to, text) → send message",
        "  - cccc_message_reply(event_id, text) → reply to message",
        "  - Terminal output (stdout/stderr) is NOT delivered as a chat message; users may never see it",
        "  - Use shell commands only for repo work (build/test/etc); use MCP for CCCC control plane actions",
        "",
        "Quick start:",
        "0. cccc_bootstrap → group+actors+PROJECT.md+context+inbox (single call)",
        "1. Do work; communicate via MCP; keep inbox clean (cccc_inbox_mark_read / cccc_inbox_mark_all_read)",
        "2. If you need a peer: as foreman you can only add peers by cloning your own runtime config (same runtime/runner/command/env). Ask the user for different runtimes.",
        "",
        "Collaboration baseline (keep it human, but be accountable):",
        "1) Verification + DoD: Follow PROJECT.md. If DoD/acceptance is unclear, capture a short DoD in Context (notes/tasks) rather than editing PROJECT.md.",
        "2) Commitments live in tasks: If you claim 'done/fixed', also update tasks/steps/milestones and state what you verified (tests/files).",
        "3) Responsible review: Don't just agree; either say what you checked, or raise a concrete risk/question.",
        "Tone: Be conversational and direct (avoid corporate/bureaucratic phrasing). Light emotion is OK; stay respectful.",
    ])
    
    return "\n".join(lines) + "\n"
