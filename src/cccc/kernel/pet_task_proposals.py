from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .pet_task_triage import enum_text, task_brief, trim_task_text


def _proposal(
    *,
    priority: int,
    reason: str,
    summary: str,
    operation: str,
    task: Any,
    status: str = "",
    assignee: str = "",
) -> Dict[str, Any]:
    task_id = str(getattr(task, "id", "") or "").strip()
    title = trim_task_text(getattr(task, "title", "") or "", max_len=120)
    return {
        "priority": int(priority),
        "reason": reason,
        "summary": summary,
        "action": {
            "type": "task_proposal",
            "operation": operation,
            "task_id": task_id,
            "title": title,
            "status": status,
            "assignee": assignee or str(getattr(task, "assignee", "") or "").strip(),
        },
    }


def build_task_proposal_candidates(tasks: Iterable[Any]) -> List[Dict[str, Any]]:
    proposals: List[Dict[str, Any]] = []
    for task in tasks:
        status = enum_text(getattr(task, "status", ""))
        waiting_on = enum_text(getattr(task, "waiting_on", ""))
        blocked_by = list(getattr(task, "blocked_by", []) or [])
        handoff_to = str(getattr(task, "handoff_to", "") or "").strip()
        assignee = str(getattr(task, "assignee", "") or "").strip()
        title = trim_task_text(getattr(task, "title", "") or "", max_len=72)
        brief = task_brief(task)

        if status in {"done", "completed", "archived"}:
            continue

        if waiting_on == "user":
            next_status = "active" if status == "planned" else status or "active"
            proposals.append(
                _proposal(
                    priority=100,
                    reason="waiting_user",
                    summary=f"{brief} 正在等待用户，建议 foreman 优先闭环或推进。",
                    operation="move" if next_status else "update",
                    task=task,
                    status=next_status,
                    assignee=assignee,
                )
            )
            continue

        if handoff_to:
            proposals.append(
                _proposal(
                    priority=90,
                    reason="handoff",
                    summary=f"{brief} 已移交给 {handoff_to}，建议 foreman 跟进接手情况。",
                    operation="handoff",
                    task=task,
                    assignee=handoff_to,
                )
            )
            continue

        if blocked_by or waiting_on in {"actor", "external"}:
            blocker_text = ""
            if blocked_by:
                blocker_text = f"（blocked_by={', '.join(str(item) for item in blocked_by[:3])}）"
            proposals.append(
                _proposal(
                    priority=80,
                    reason="blocked",
                    summary=f"{brief} 当前受阻{blocker_text}，建议 foreman 协调解阻塞。",
                    operation="update",
                    task=task,
                    assignee=assignee,
                )
            )
            continue

        if status == "planned" and not assignee:
            proposals.append(
                _proposal(
                    priority=70,
                    reason="planned_backlog",
                    summary=f"{brief} 仍在 planned 且无人负责，建议 foreman 判断是否启动或清理。",
                    operation="update",
                    task=task,
                )
            )

    proposals.sort(key=lambda item: (-int(item.get("priority") or 0), str(item.get("reason") or ""), str(((item.get("action") or {}).get("task_id") or ""))))
    return proposals


def build_task_proposal_summary_lines(tasks: Iterable[Any], *, limit: int = 2) -> List[str]:
    lines: List[str] = []
    for item in build_task_proposal_candidates(tasks)[:limit]:
        summary = str(item.get("summary") or "").strip()
        if summary:
            lines.append(summary)
    return lines
