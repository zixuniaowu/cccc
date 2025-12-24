from __future__ import annotations

from typing import Any, Dict, List

from .group import Group


def render_system_prompt(*, group: Group, actor: Dict[str, Any]) -> str:
    group_id = str(group.group_id or "").strip()
    actor_id = str(actor.get("id") or "").strip()
    role = str(actor.get("role") or "").strip()

    title = str(group.doc.get("title") or group_id)
    topic = str(group.doc.get("topic") or "").strip()

    perms = ""
    if role == "foreman":
        perms = "You can manage other peers in this group (create/update/stop/restart)."
    elif role == "peer":
        perms = "You can only start/stop/restart yourself. Do not manage other agents."

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

    prompt = "\n".join(
        [
            "SYSTEM (CCCC vNext)",
            f"- Identity: {actor_id} ({role}) in working group '{title}' ({group_id})",
            *(["- Topic: " + topic] if topic else []),
            "- Style: Be terse. Use bullets. No fluff. Treat messages like orders/status reports.",
            "- Source of truth: The group ledger is the shared chat+audit log. Keep it clean and actionable.",
            "- Messaging:",
            "  - Read: cccc inbox --actor-id <you> --by <you> [--mark-read]",
            "  - Send: cccc send \"...\" --by <you> --to <target>",
            "    - targets: user/@user, @all, @peers, @foreman, actor-id, or actor title",
            "  - Mark read: cccc read <event_id> --actor-id <you> --by <you>",
            "  - Delivery: targeted messages are auto-injected into your PTY as:",
            "    - [cccc] <by> → <to>: <text>",
            "    - If multiline can't be injected, CCCC writes: $CCCC_HOME/groups/<group_id>/state/delivery/<you>.txt",
            "  - Automation: you may receive [cccc] NUDGE / SELF-CHECK prompts — follow them, keep replies terse.",
            "- Permissions:",
            f"  - {perms}".rstrip(),
            "- Scopes:",
            *(scope_lines or ["- (none attached yet)"]),
        ]
    ).strip() + "\n"
    return prompt
