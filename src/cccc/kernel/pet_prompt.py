from __future__ import annotations

from typing import Any, Dict

from .context import ContextStorage
from .group import Group
from .pet_actor import PET_ACTOR_ID
from .pet_task_proposals import build_task_proposal_summary_lines
from .pet_task_triage import build_task_triage_payload, join_task_briefs
from .prompt_files import HELP_FILENAME, load_builtin_help_markdown, read_group_prompt_file
from .system_prompt import render_role_system_prompt
from ..ports.mcp.utils.help_markdown import _select_help_markdown, parse_help_markdown


def load_pet_help_markdown(group: Group) -> str:
    pf = read_group_prompt_file(group, HELP_FILENAME)
    if pf.found and isinstance(pf.content, str) and pf.content.strip():
        return str(pf.content)
    return str(load_builtin_help_markdown() or "")


def build_pet_snapshot_text(group: Any, context_payload: Dict[str, Any]) -> str:
    parts: list[str] = []
    title = str(group.doc.get("title") or group.group_id or "").strip() or "unknown-group"
    state = str(group.doc.get("state") or "").strip() or "unknown"
    parts.append(f"Group: {title}")
    parts.append(f"Group State: {state}")

    tasks_summary = context_payload.get("tasks_summary") if isinstance(context_payload.get("tasks_summary"), dict) else {}
    if tasks_summary:
        parts.append(
            "Tasks: total={total}, active={active}, done={done}, archived={archived}".format(
                total=int(tasks_summary.get("total") or 0),
                active=int(tasks_summary.get("active") or 0),
                done=int(tasks_summary.get("done") or 0),
                archived=int(tasks_summary.get("archived") or 0),
            )
        )

    agent_states = context_payload.get("agent_states") if isinstance(context_payload.get("agent_states"), list) else []
    snapshots: list[str] = []
    for item in agent_states[:6]:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("id") or "").strip()
        hot = item.get("hot") if isinstance(item.get("hot"), dict) else {}
        active_task_id = str(hot.get("active_task_id") or "").strip()
        focus = str(hot.get("focus") or "").strip()
        if not agent_id:
            continue
        if active_task_id and focus:
            snapshots.append(f"{agent_id}: {active_task_id} | {focus}")
        elif active_task_id:
            snapshots.append(f"{agent_id}: {active_task_id}")
        elif focus:
            snapshots.append(f"{agent_id}: {focus}")
        else:
            snapshots.append(agent_id)
    if snapshots:
        parts.append(f"Agent Snapshot: {' ; '.join(snapshots)}")

    blocked_tasks = context_payload.get("blocked_tasks") if isinstance(context_payload.get("blocked_tasks"), list) else []
    if blocked_tasks:
        rendered = join_task_briefs(blocked_tasks)
        if rendered:
            parts.append(f"Blocked Tasks: {rendered}")

    waiting_user_tasks = (
        context_payload.get("waiting_user_tasks")
        if isinstance(context_payload.get("waiting_user_tasks"), list)
        else []
    )
    if waiting_user_tasks:
        rendered = join_task_briefs(waiting_user_tasks)
        if rendered:
            parts.append(f"Waiting User Tasks: {rendered}")

    handoff_tasks = context_payload.get("handoff_tasks") if isinstance(context_payload.get("handoff_tasks"), list) else []
    if handoff_tasks:
        rendered = join_task_briefs(handoff_tasks)
        if rendered:
            parts.append(f"Handoff Tasks: {rendered}")

    planned_backlog_tasks = (
        context_payload.get("planned_backlog_tasks")
        if isinstance(context_payload.get("planned_backlog_tasks"), list)
        else []
    )
    if planned_backlog_tasks:
        rendered = join_task_briefs(planned_backlog_tasks)
        if rendered:
            parts.append(f"Planned Backlog: {rendered}")

    task_proposal_candidates = (
        context_payload.get("task_proposal_candidates")
        if isinstance(context_payload.get("task_proposal_candidates"), list)
        else []
    )
    if task_proposal_candidates:
        rendered = " ; ".join(str(item or "").strip() for item in task_proposal_candidates[:2] if str(item or "").strip())
        if rendered:
            parts.append(f"Task Proposals: {rendered}")

    return "\n".join(parts)


def build_pet_prompt_parts(group: Group, *, help_markdown: str, context_payload: Dict[str, Any]) -> Dict[str, str]:
    parsed = parse_help_markdown(help_markdown)
    persona = str(parsed.get("pet") or "").strip()
    selected_help = _select_help_markdown(help_markdown, role="peer", actor_id=PET_ACTOR_ID, include_pet=True)
    snapshot = build_pet_snapshot_text(group, context_payload)
    decision_contract = "\n".join(
        [
            "Pet Decision Contract:",
            "- You are the Web Pet actor. Reuse the normal peer workflow, tools, and context discipline.",
            "- Your reminder output surface is cccc_pet_decisions, not visible chat.",
            "- When you have current actionable reminders, call cccc_pet_decisions with action=replace and write the full decision list.",
            "- When there is no current actionable reminder, call cccc_pet_decisions with action=clear.",
            "- Do not emit low-signal status chatter, duplicate restarts, or reminder-like chat messages just to mirror state.",
            "- Judge from current evidence and context. Do not rely on fixed frontend keyword matching.",
            "- For task-board management, prefer proposing a structured task_proposal for the foreman instead of mutating shared tasks yourself.",
            "- When task pressure is high, prefer one high-value proposal over many noisy reminders.",
            "- For task triage, prioritize: waiting_user > handoff > blocked > planned backlog cleanup.",
            "- If Runtime Snapshot includes Task Proposals, treat them as high-signal candidate directions, then decide whether one is worth surfacing now.",
            "- Unless there is an urgent runtime failure, emit at most one task_proposal per review.",
        ]
    )
    prompt = "\n\n".join(
        [
            render_role_system_prompt(
                group=group,
                actor_id=PET_ACTOR_ID,
                role="peer",
                runtime_name="pet-peer",
                runner="pty",
            ).strip(),
            "Pet-Specific Help:\n" + str(selected_help or "").strip(),
            "Pet Persona:\n" + (persona or "(default pet peer persona)"),
            decision_contract,
            "Runtime Snapshot:\n" + snapshot,
        ]
    ).strip()
    return {
        "persona": persona,
        "help": selected_help,
        "snapshot": snapshot,
        "prompt": prompt,
        "source": "help" if persona else "default",
    }


def _load_pet_runtime_context(group: Group) -> Dict[str, Any]:
    try:
        storage = ContextStorage(group)
        tasks = storage.list_tasks()
        agents = storage.load_agents()
    except Exception:
        return {}

    active_count = 0
    done_count = 0
    archived_count = 0
    for task in tasks:
        status = str(getattr(getattr(task, "status", None), "value", getattr(task, "status", "")) or "").strip().lower()
        if status in {"done", "completed"}:
            done_count += 1
        elif status == "archived":
            archived_count += 1
        else:
            active_count += 1
    task_triage = build_task_triage_payload(tasks, limit=3)
    agent_states = []
    for agent in list(getattr(agents, "agents", []) or []):
        agent_states.append(
            {
                "id": str(getattr(agent, "id", "") or "").strip(),
                "hot": {
                    "active_task_id": str(getattr(getattr(agent, "hot", None), "active_task_id", "") or "").strip(),
                    "focus": str(getattr(getattr(agent, "hot", None), "focus", "") or "").strip(),
                },
            }
        )

    return {
        "tasks_summary": {
            "total": len(tasks),
            "active": active_count,
            "done": done_count,
            "archived": archived_count,
        },
        "agent_states": agent_states,
        "task_proposal_candidates": build_task_proposal_summary_lines(tasks, limit=2),
        **task_triage,
    }


def render_pet_system_prompt(group: Group, *, actor: Dict[str, Any], context_payload: Dict[str, Any] | None = None) -> str:
    help_markdown = load_pet_help_markdown(group)
    effective_context_payload = context_payload if isinstance(context_payload, dict) and context_payload else _load_pet_runtime_context(group)
    parts = build_pet_prompt_parts(group, help_markdown=help_markdown, context_payload=effective_context_payload)
    return str(parts.get("prompt") or "").strip() + "\n"
