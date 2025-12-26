from __future__ import annotations

from typing import Any, Dict, List

from .actors import get_effective_role, list_actors
from .group import Group


def render_system_prompt(*, group: Group, actor: Dict[str, Any]) -> str:
    """Render SYSTEM prompt for an actor.
    
    Key design principles:
    - Foreman is a "coordinator", not a "manager"
    - All actors are independent experts with their own judgment
    - Foreman has extra coordination responsibilities, but also does real work
    - Peer is not a "subordinate" - they participate in discussions and can challenge decisions
    """
    group_id = str(group.group_id or "").strip()
    actor_id = str(actor.get("id") or "").strip()
    role = get_effective_role(group, actor_id)
    runner = str(actor.get("runner") or "pty").strip()

    title = str(group.doc.get("title") or group_id)
    topic = str(group.doc.get("topic") or "").strip()
    
    # Count actors to determine team size
    actors = list_actors(group)
    actor_count = len([a for a in actors if isinstance(a, dict) and a.get("enabled", True)])
    is_solo = actor_count <= 1

    # Role-specific sections
    if role == "foreman":
        if is_solo:
            role_desc = "You are the foreman (and currently the only actor). You handle all work directly."
            team_guidance = [
                "",
                "## Team Size Decision",
                "",
                "You are currently working solo. Decide based on task complexity:",
                "- Simple, well-defined task → Work alone",
                "- Complex, multi-domain, or parallelizable → Consider creating peers",
                "- Check PROJECT.md for team mode hints (if available)",
                "",
                "To create a peer:",
                "1. Call cccc_runtime_list to see available runtimes",
                f'2. Call cccc_actor_add: {{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "peer-xxx", "runtime": "claude"}}',
                "3. Call cccc_actor_start to start the peer",
                "4. Send task instructions via cccc_message_send",
            ]
        else:
            role_desc = "You are the foreman (coordinator). You coordinate the team AND do real work."
            team_guidance = [
                "",
                "## Team Coordination",
                "",
                "As foreman, you have coordination responsibilities:",
                "- Monitor overall progress and blockers",
                "- Help resolve conflicts or ambiguities",
                "- Receive system notifications (actor_idle, silence_check)",
                "",
                "But you are NOT a manager:",
                "- You do real implementation work, not just delegation",
                "- Peers are independent experts, not subordinates",
                "- Peers can challenge your decisions - listen to them",
                "",
                "## Peer Lifecycle",
                "",
                "You are responsible for peer lifecycle:",
                "- Create peers when needed (cccc_actor_add + cccc_actor_start)",
                "- When a peer's task is complete, tell them to finish up and exit",
                "- Peers remove themselves (cccc_actor_remove) - you don't force-remove them",
                "- Keep the team lean: more actors = more communication overhead",
            ]
        
        perms = "You can: add actors, start/stop/restart any actor, remove yourself."
        foreman_tools = [
            "",
            "## Actor Management Tools",
            "",
            f'- cccc_runtime_list: {{}} → See available runtimes',
            f'- cccc_actor_add: {{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "peer-xxx", "runtime": "claude"}}',
            f'- cccc_actor_start: {{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "peer-xxx"}}',
            f'- cccc_actor_stop: {{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "peer-xxx"}}',
            f'- cccc_actor_restart: {{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "peer-xxx"}}',
            f'- cccc_actor_remove: {{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "{actor_id}"}} (self only)',
        ]
    else:
        role_desc = "You are a peer (team member). You are an independent expert, not a subordinate."
        team_guidance = [
            "",
            "## Your Role",
            "",
            "You are an independent expert in this team:",
            "- Participate in discussions, share your professional judgment",
            "- You can challenge foreman's decisions if you disagree",
            "- Proactively raise issues or suggest improvements",
            "- You're not just executing orders - think critically",
            "",
            "## Task Completion",
            "",
            "When foreman tells you your task is complete:",
            "1. Finish any cleanup work",
            "2. Report completion to foreman",
            f'3. Remove yourself: cccc_actor_remove({{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "{actor_id}"}})',
            "",
            "This is normal task completion, not punishment.",
        ]
        perms = "You can: stop/restart/remove yourself. You cannot add or start other actors."
        foreman_tools = []

    scopes = group.doc.get("scopes") if isinstance(group.doc.get("scopes"), list) else []
    active_scope_key = str(group.doc.get("active_scope_key") or "")
    scope_lines: List[str] = []
    for sc in scopes:
        if not isinstance(sc, dict):
            continue
        sk = str(sc.get("scope_key") or "")
        url = str(sc.get("url") or "")
        label = str(sc.get("label") or sk)
        mark = " (active)" if sk and sk == active_scope_key else ""
        if url:
            scope_lines.append(f"- {label}: {url}{mark}")

    # Runner-specific sections
    if runner == "headless":
        runner_section = [
            "",
            "## Headless Runner Mode",
            "",
            "You are running in headless mode (no PTY). Your workflow:",
            "1. Poll inbox periodically or respond to system.notify events",
            "2. Use cccc_headless_set_status to report your state:",
            "   - 'idle': waiting for tasks",
            "   - 'working': executing a task",
            "   - 'waiting': blocked on decision/approval",
            "3. Use cccc_headless_ack_message after processing each message",
        ]
    else:
        runner_section = []

    prompt = "\n".join(
        [
            "SYSTEM (CCCC vNext)",
            f"- Identity: {actor_id} ({role}, {runner}) in working group '{title}' ({group_id})",
            f"- Role: {role_desc}",
            *(["- Topic: " + topic] if topic else []),
            "- Style: Be concise. Use bullets. Treat messages like team chat.",
            "",
            "## Communication",
            "",
            "- cccc_inbox_list: Get unread messages",
            f'  - args: {{"group_id": "{group_id}", "actor_id": "{actor_id}"}}',
            "- cccc_inbox_mark_read: Mark messages as read",
            f'  - args: {{"group_id": "{group_id}", "actor_id": "{actor_id}", "event_id": "<id>"}}',
            "- cccc_message_send: Send a message",
            f'  - args: {{"group_id": "{group_id}", "actor_id": "{actor_id}", "text": "...", "to": ["user"]}}',
            "- cccc_message_reply: Reply to a message",
            f'  - args: {{"group_id": "{group_id}", "actor_id": "{actor_id}", "reply_to": "<event_id>", "text": "..."}}',
            "",
            "Recipients: user, @all, @peers, @foreman, or specific actor_id",
            *team_guidance,
            *foreman_tools,
            "",
            "## Context & Project Info",
            "",
            "- cccc_project_info: Get PROJECT.md (project goals, team mode hints)",
            f'  - args: {{"group_id": "{group_id}"}}',
            "  - Call at session start to understand project context",
            "- cccc_context_get: Get full context (vision/sketch/milestones/tasks)",
            f'  - args: {{"group_id": "{group_id}"}}',
            "- cccc_presence_update: Update your status",
            f'  - args: {{"group_id": "{group_id}", "agent_id": "{actor_id}", "status": "..."}}',
            "- cccc_task_update: Update task progress",
            f'  - args: {{"group_id": "{group_id}", "task_id": "T001", "step_id": "S1", "step_status": "done"}}',
            *runner_section,
            "",
            "## Workflow",
            "",
            "1. Call cccc_project_info to understand project goals",
            "2. Call cccc_context_get to sync state",
            "3. Check inbox (cccc_inbox_list)",
            "4. Update presence (cccc_presence_update)",
            "5. Do work, update task progress",
            "6. Mark messages as read, send results",
            "",
            "## Permissions",
            "",
            f"- {perms}",
            "",
            "## Scopes",
            "",
            *(scope_lines or ["- (none attached yet)"]),
        ]
    ).strip() + "\n"
    return prompt
