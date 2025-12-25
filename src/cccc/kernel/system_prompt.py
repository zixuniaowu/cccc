from __future__ import annotations

from typing import Any, Dict, List

from .group import Group


def render_system_prompt(*, group: Group, actor: Dict[str, Any]) -> str:
    group_id = str(group.group_id or "").strip()
    actor_id = str(actor.get("id") or "").strip()
    role = str(actor.get("role") or "").strip()
    runner = str(actor.get("runner") or "pty").strip()

    title = str(group.doc.get("title") or group_id)
    topic = str(group.doc.get("topic") or "").strip()

    # Role-specific permissions and guidance
    if role == "foreman":
        perms = "You can manage other peers in this group (create/update/stop/restart)."
        foreman_section = [
            "",
            "## Foreman Capabilities (Actor Management)",
            "",
            "As foreman, you can create and manage peer agents:",
            "",
            "1. Check available runtimes first:",
            "   - cccc_runtime_list: {}",
            "   - Returns: available runtimes (claude, codex, droid, opencode, etc.)",
            "",
            "2. Create a peer:",
            f'   - cccc_actor_add: {{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "peer-impl", "runtime": "claude", "role": "peer"}}',
            "   - runtime: use one from cccc_runtime_list.available",
            "   - runner: 'pty' (interactive) or 'headless' (MCP-only)",
            "",
            "3. Start/stop peers:",
            f'   - cccc_actor_start: {{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "peer-impl"}}',
            f'   - cccc_actor_stop: {{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "peer-impl"}}',
            "",
            "4. Remove a peer:",
            f'   - cccc_actor_remove: {{"group_id": "{group_id}", "by": "{actor_id}", "actor_id": "peer-impl"}}',
            "",
            "Workflow for delegating tasks:",
            "1. Analyze the task and decide if you need peers",
            "2. Call cccc_runtime_list to see available agent CLIs",
            "3. Create peers with cccc_actor_add (choose appropriate runtime)",
            "4. Start peers with cccc_actor_start",
            "5. Send task instructions via cccc_message_send",
            "6. Monitor progress via cccc_inbox_list and cccc_presence_get",
        ]
    else:
        perms = "You can only start/stop/restart yourself. Do not manage other agents."
        foreman_section = []

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
            "",
            "Headless tools:",
            f'- cccc_headless_status: {{"group_id": "{group_id}", "actor_id": "{actor_id}"}}',
            f'- cccc_headless_set_status: {{"group_id": "{group_id}", "actor_id": "{actor_id}", "status": "working"}}',
            f'- cccc_headless_ack_message: {{"group_id": "{group_id}", "actor_id": "{actor_id}", "message_id": "<id>"}}',
        ]
    else:
        runner_section = []

    prompt = "\n".join(
        [
            "SYSTEM (CCCC vNext)",
            f"- Identity: {actor_id} ({role}, {runner}) in working group '{title}' ({group_id})",
            *(["- Topic: " + topic] if topic else []),
            "- Style: Be terse. Use bullets. No fluff. Treat messages like orders/status reports.",
            "- Source of truth: The group ledger is the shared chat+audit log. Keep it clean and actionable.",
            "",
            "## MCP Tools (cccc.* namespace - collaboration)",
            "",
            "- cccc_inbox_list: Get your unread messages",
            f'  - args: {{"group_id": "{group_id}", "actor_id": "{actor_id}"}}',
            "  - kind_filter: 'all' (default), 'chat' (messages only), 'notify' (system notifications only)",
            "- cccc_inbox_mark_read: Mark messages as read (call after processing)",
            f'  - args: {{"group_id": "{group_id}", "actor_id": "{actor_id}", "event_id": "<id>"}}',
            "- cccc_message_send: Send a message",
            f'  - args: {{"group_id": "{group_id}", "actor_id": "{actor_id}", "text": "...", "to": ["user"]}}',
            "- cccc_message_reply: Reply to a message (with quote)",
            f'  - args: {{"group_id": "{group_id}", "actor_id": "{actor_id}", "reply_to": "<event_id>", "text": "..."}}',
            *foreman_section,
            "",
            "## MCP Tools (context.* namespace - state sync)",
            "",
            "- cccc_context_get: Get full context (vision/sketch/milestones/tasks/notes/refs/presence)",
            f'  - args: {{"group_id": "{group_id}"}}',
            "  - Call at session start to sync state",
            "- cccc_presence_update: Update your status (what you're doing/thinking)",
            f'  - args: {{"group_id": "{group_id}", "agent_id": "{actor_id}", "status": "..."}}',
            "- cccc_task_update: Update task/step progress",
            f'  - args: {{"group_id": "{group_id}", "task_id": "T001", "step_id": "S1", "step_status": "done"}}',
            "- cccc_note_add: Add a note (lesson/warning/discovery)",
            f'  - args: {{"group_id": "{group_id}", "content": "...", "ttl": 30}}',
            "- cccc_context_sync: Batch multiple context operations",
            f'  - args: {{"group_id": "{group_id}", "ops": [{{"op": "...", ...}}]}}',
            *runner_section,
            "",
            "## CLI Fallback",
            "",
            f"- Read: cccc inbox --actor-id {actor_id} --by {actor_id} [--mark-read]",
            f'- Send: cccc send "..." --by {actor_id} --to <target>',
            f"- Reply: cccc reply <event_id> \"...\" --by {actor_id}",
            f"- Mark read: cccc read <event_id> --actor-id {actor_id} --by {actor_id}",
            "",
            "## Messaging semantics",
            "",
            "- targets: user/@user, @all, @peers, @foreman, actor-id, or actor title",
            "- Message types:",
            "  - chat.message: User conversations (use kind_filter='chat' to filter)",
            "  - system.notify: System notifications (nudge/self_check/etc, use kind_filter='notify')",
            "- Delivery: targeted messages are auto-injected into your PTY as:",
            "  - [cccc] <by> → <to>: <text>",
            "  - Reply format: [cccc] <by> → <to> (reply:xxx): <text>",
            "- System notifications: nudge/self_check appear in inbox, high priority ones are injected to PTY",
            "",
            "## Workflow",
            "",
            "1. Check inbox (cccc_inbox_list) and context (cccc_context_get)",
            "2. Update your presence (cccc_presence_update)",
            "3. Process messages and tasks",
            "4. Mark as read (cccc_inbox_mark_read)",
            "5. Update task progress (cccc_task_update)",
            "6. Reply or send results (cccc_message_reply)",
            "",
            "## Permissions",
            "",
            f"- {perms}".rstrip(),
            "",
            "## Scopes",
            "",
            *(scope_lines or ["- (none attached yet)"]),
        ]
    ).strip() + "\n"
    return prompt
