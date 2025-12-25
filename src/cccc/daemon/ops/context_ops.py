"""
Context operations for daemon.

所有 context 操作都通过 daemon 执行，保证单写者原则。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...contracts.v1 import DaemonResponse, DaemonError
from ...kernel.group import load_group
from ...kernel.context import (
    ContextStorage,
    Context,
    Milestone,
    MilestoneStatus,
    Task,
    TaskStatus,
    Step,
    StepStatus,
    Note,
    Reference,
)


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_storage(group_id: str) -> Optional[ContextStorage]:
    """获取 group 的 context storage"""
    group = load_group(group_id)
    if group is None:
        return None
    return ContextStorage(group)


def _task_to_dict(task: Task) -> Dict[str, Any]:
    """Convert Task to dict"""
    current_step = task.current_step
    return {
        "id": task.id,
        "name": task.name,
        "goal": task.goal,
        "status": task.status.value if isinstance(task.status, TaskStatus) else task.status,
        "milestone": task.milestone,
        "assignee": task.assignee,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "steps": [
            {
                "id": s.id,
                "name": s.name,
                "acceptance": s.acceptance,
                "status": s.status.value if isinstance(s.status, StepStatus) else s.status,
            }
            for s in task.steps
        ],
        "current_step": current_step.id if current_step else None,
        "progress": task.progress,
    }


# =============================================================================
# Context Get
# =============================================================================


def handle_context_get(args: Dict[str, Any]) -> DaemonResponse:
    """获取 group 的完整上下文"""
    group_id = str(args.get("group_id") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")

    storage = _get_storage(group_id)
    if storage is None:
        return _error("group_not_found", f"group not found: {group_id}")

    context = storage.load_context()
    tasks = storage.list_tasks()
    presence = storage.load_presence()

    # Decay TTL and save
    context, archived_notes, archived_refs = storage.decay_ttl(context)
    if archived_notes or archived_refs:
        storage.save_context(context)

    # Build tasks summary
    done_count = sum(1 for t in tasks if t.status == TaskStatus.DONE)
    active_count = sum(1 for t in tasks if t.status == TaskStatus.ACTIVE)
    planned_count = sum(1 for t in tasks if t.status == TaskStatus.PLANNED)

    # Find active task
    active_task = None
    for t in tasks:
        if t.status == TaskStatus.ACTIVE:
            active_task = _task_to_dict(t)
            break

    result = {
        "version": storage.compute_version(),
        "vision": context.vision,
        "sketch": context.sketch,
        "milestones": [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "status": m.status.value if isinstance(m.status, MilestoneStatus) else m.status,
                "started": m.started,
                "completed": m.completed,
                "outcomes": m.outcomes,
            }
            for m in context.milestones
        ],
        "notes": [
            {"id": n.id, "content": n.content, "ttl": n.ttl, "expiring": n.expiring}
            for n in context.notes
        ],
        "references": [
            {"id": r.id, "url": r.url, "note": r.note, "ttl": r.ttl, "expiring": r.expiring}
            for r in context.references
        ],
        "tasks_summary": {
            "total": len(tasks),
            "done": done_count,
            "active": active_count,
            "planned": planned_count,
        },
        "active_task": active_task,
        "presence": {
            "agents": [
                {"id": a.id, "status": a.status, "updated_at": a.updated_at}
                for a in presence.agents
            ]
        },
    }

    return DaemonResponse(ok=True, result=result)


# =============================================================================
# Context Sync (batch operations)
# =============================================================================


def handle_context_sync(args: Dict[str, Any]) -> DaemonResponse:
    """批量同步上下文操作"""
    group_id = str(args.get("group_id") or "").strip()
    ops = args.get("ops") or []
    dry_run = bool(args.get("dry_run", False))

    if not group_id:
        return _error("missing_group_id", "missing group_id")
    if not isinstance(ops, list):
        return _error("invalid_ops", "ops must be a list")

    storage = _get_storage(group_id)
    if storage is None:
        return _error("group_not_found", f"group not found: {group_id}")

    context = storage.load_context()
    presence = storage.load_presence()
    tasks_by_id: Dict[str, Task] = {t.id: t for t in storage.list_tasks()}

    changes: List[Dict[str, Any]] = []
    context_dirty = False
    presence_dirty = False
    dirty_task_ids: set = set()

    def _mark_change(idx: int, op_name: str, detail: str) -> None:
        changes.append({"index": idx, "op": op_name, "detail": detail})

    try:
        for idx, item in enumerate(ops):
            if not isinstance(item, dict):
                raise ValueError(f"op[{idx}] must be a dict")

            op_name = str(item.get("op") or "")

            if op_name == "vision.update":
                vision = str(item.get("vision") or "")
                context.vision = vision
                context_dirty = True
                _mark_change(idx, op_name, "Set vision")

            elif op_name == "sketch.update":
                sketch = str(item.get("sketch") or "")
                context.sketch = sketch
                context_dirty = True
                _mark_change(idx, op_name, "Set sketch")

            elif op_name == "milestone.create":
                name = str(item.get("name") or "")
                description = str(item.get("description") or "")
                status_str = str(item.get("status") or "pending")
                try:
                    status = MilestoneStatus(status_str)
                except ValueError:
                    raise ValueError(f"Invalid milestone status: {status_str}")

                milestone_id = storage.generate_milestone_id(context)
                started = None
                if status == MilestoneStatus.ACTIVE:
                    started = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                milestone = Milestone(
                    id=milestone_id,
                    name=name,
                    description=description,
                    status=status,
                    started=started,
                    updated_at=_utc_now_iso(),
                )
                context.milestones.append(milestone)
                context_dirty = True
                _mark_change(idx, op_name, f"Created {milestone_id}")

            elif op_name == "milestone.update":
                milestone_id = str(item.get("milestone_id") or "")
                milestone = storage.get_milestone(context, milestone_id)
                if milestone is None:
                    raise ValueError(f"Milestone not found: {milestone_id}")

                if "name" in item:
                    milestone.name = str(item["name"])
                if "description" in item:
                    milestone.description = str(item["description"])
                if "status" in item:
                    try:
                        new_status = MilestoneStatus(str(item["status"]))
                    except ValueError:
                        raise ValueError(f"Invalid status: {item['status']}")
                    if new_status == MilestoneStatus.ACTIVE and milestone.status != MilestoneStatus.ACTIVE:
                        if milestone.started is None:
                            milestone.started = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    milestone.status = new_status

                milestone.updated_at = _utc_now_iso()
                context_dirty = True
                _mark_change(idx, op_name, f"Updated {milestone_id}")

            elif op_name == "milestone.complete":
                milestone_id = str(item.get("milestone_id") or "")
                outcomes = str(item.get("outcomes") or "")
                milestone = storage.get_milestone(context, milestone_id)
                if milestone is None:
                    raise ValueError(f"Milestone not found: {milestone_id}")

                milestone.status = MilestoneStatus.DONE
                milestone.completed = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                milestone.outcomes = outcomes
                milestone.updated_at = _utc_now_iso()
                context_dirty = True
                _mark_change(idx, op_name, f"Completed {milestone_id}")

            elif op_name == "milestone.remove":
                milestone_id = str(item.get("milestone_id") or "")
                before = len(context.milestones)
                context.milestones = [m for m in context.milestones if m.id != milestone_id]
                if len(context.milestones) == before:
                    raise ValueError(f"Milestone not found: {milestone_id}")
                context_dirty = True
                _mark_change(idx, op_name, f"Removed {milestone_id}")

            elif op_name == "task.create":
                name = str(item.get("name") or "")
                goal = str(item.get("goal") or "")
                steps_raw = item.get("steps") or []
                milestone_id = item.get("milestone_id") or item.get("milestone")
                assignee = item.get("assignee")

                task_id = storage.generate_task_id()
                task_steps = []
                for i, s in enumerate(steps_raw, start=1):
                    if isinstance(s, dict):
                        task_steps.append(Step(
                            id=f"S{i}",
                            name=str(s.get("name") or ""),
                            acceptance=str(s.get("acceptance") or ""),
                            status=StepStatus.PENDING,
                        ))

                now = _utc_now_iso()
                task = Task(
                    id=task_id,
                    name=name,
                    goal=goal,
                    status=TaskStatus.PLANNED,
                    milestone=str(milestone_id) if milestone_id else None,
                    assignee=str(assignee) if assignee else None,
                    created_at=now,
                    updated_at=now,
                    steps=task_steps,
                )
                tasks_by_id[task_id] = task
                dirty_task_ids.add(task_id)
                _mark_change(idx, op_name, f"Created {task_id}")

            elif op_name == "task.update":
                task_id = str(item.get("task_id") or "")
                task = tasks_by_id.get(task_id)
                if task is None:
                    raise ValueError(f"Task not found: {task_id}")

                if "status" in item:
                    try:
                        task.status = TaskStatus(str(item["status"]))
                    except ValueError:
                        raise ValueError(f"Invalid task status: {item['status']}")
                if "name" in item:
                    task.name = str(item["name"])
                if "goal" in item:
                    task.goal = str(item["goal"])
                if "assignee" in item:
                    task.assignee = str(item["assignee"]) if item["assignee"] else None
                if "milestone_id" in item or "milestone" in item:
                    mid = item.get("milestone_id") or item.get("milestone")
                    task.milestone = str(mid) if mid else None

                # Step update
                if "step_id" in item and "step_status" in item:
                    step_id = str(item["step_id"])
                    step_status_str = str(item["step_status"])
                    found = False
                    for step in task.steps:
                        if step.id == step_id:
                            try:
                                step.status = StepStatus(step_status_str)
                            except ValueError:
                                raise ValueError(f"Invalid step status: {step_status_str}")
                            found = True
                            break
                    if not found:
                        raise ValueError(f"Step not found: {step_id}")

                task.updated_at = _utc_now_iso()
                dirty_task_ids.add(task_id)
                _mark_change(idx, op_name, f"Updated {task_id}")

            elif op_name == "task.delete":
                task_id = str(item.get("task_id") or "")
                if task_id not in tasks_by_id:
                    raise ValueError(f"Task not found: {task_id}")
                del tasks_by_id[task_id]
                if not dry_run:
                    storage.delete_task(task_id)
                _mark_change(idx, op_name, f"Deleted {task_id}")

            elif op_name == "note.add":
                content = str(item.get("content") or "")
                ttl = int(item.get("ttl") or 30)
                if ttl < 10 or ttl > 100:
                    raise ValueError(f"ttl must be between 10 and 100, got: {ttl}")

                note_id = storage.generate_note_id(context)
                note = Note(id=note_id, content=content, ttl=ttl)
                context.notes.append(note)
                context_dirty = True
                _mark_change(idx, op_name, f"Added {note_id}")

            elif op_name == "note.update":
                note_id = str(item.get("note_id") or "")
                note = storage.get_note_by_id(context, note_id)
                if note is None:
                    raise ValueError(f"Note not found: {note_id}")

                if "content" in item:
                    note.content = str(item["content"])
                if "ttl" in item:
                    ttl = int(item["ttl"])
                    if ttl < 0 or ttl > 100:
                        raise ValueError(f"ttl must be between 0 and 100, got: {ttl}")
                    note.ttl = ttl

                context_dirty = True
                _mark_change(idx, op_name, f"Updated {note_id}")

            elif op_name == "note.remove":
                note_id = str(item.get("note_id") or "")
                before = len(context.notes)
                context.notes = [n for n in context.notes if n.id != note_id]
                if len(context.notes) == before:
                    raise ValueError(f"Note not found: {note_id}")
                context_dirty = True
                _mark_change(idx, op_name, f"Removed {note_id}")

            elif op_name == "reference.add":
                url = str(item.get("url") or "")
                note_text = str(item.get("note") or "")
                ttl = int(item.get("ttl") or 30)
                if ttl < 10 or ttl > 100:
                    raise ValueError(f"ttl must be between 10 and 100, got: {ttl}")

                ref_id = storage.generate_reference_id(context)
                ref = Reference(id=ref_id, url=url, note=note_text, ttl=ttl)
                context.references.append(ref)
                context_dirty = True
                _mark_change(idx, op_name, f"Added {ref_id}")

            elif op_name == "reference.update":
                ref_id = str(item.get("reference_id") or "")
                ref = storage.get_reference_by_id(context, ref_id)
                if ref is None:
                    raise ValueError(f"Reference not found: {ref_id}")

                if "url" in item:
                    ref.url = str(item["url"])
                if "note" in item:
                    ref.note = str(item["note"])
                if "ttl" in item:
                    ttl = int(item["ttl"])
                    if ttl < 0 or ttl > 100:
                        raise ValueError(f"ttl must be between 0 and 100, got: {ttl}")
                    ref.ttl = ttl

                context_dirty = True
                _mark_change(idx, op_name, f"Updated {ref_id}")

            elif op_name == "reference.remove":
                ref_id = str(item.get("reference_id") or "")
                before = len(context.references)
                context.references = [r for r in context.references if r.id != ref_id]
                if len(context.references) == before:
                    raise ValueError(f"Reference not found: {ref_id}")
                context_dirty = True
                _mark_change(idx, op_name, f"Removed {ref_id}")

            elif op_name == "presence.update":
                agent_id = str(item.get("agent_id") or "")
                status = str(item.get("status") or "")
                if not dry_run:
                    storage.update_agent_presence(agent_id, status)
                presence_dirty = True
                _mark_change(idx, op_name, f"Updated presence for {agent_id}")

            elif op_name == "presence.clear":
                agent_id = str(item.get("agent_id") or "")
                if not dry_run:
                    storage.clear_agent_status(agent_id)
                presence_dirty = True
                _mark_change(idx, op_name, f"Cleared presence for {agent_id}")

            else:
                raise ValueError(f"Unknown operation: {op_name}")

        if not dry_run:
            if context_dirty:
                storage.save_context(context)
            for task_id in sorted(dirty_task_ids):
                if task_id in tasks_by_id:
                    storage.save_task(tasks_by_id[task_id])

        result = {
            "success": True,
            "dry_run": dry_run,
            "changes": changes,
            "version": storage.compute_version(),
        }
        return DaemonResponse(ok=True, result=result)

    except ValueError as e:
        return _error("context_sync_error", str(e))
    except Exception as e:
        return _error("context_sync_error", f"unexpected error: {e}")


# =============================================================================
# Task List
# =============================================================================


def handle_task_list(args: Dict[str, Any]) -> DaemonResponse:
    """列出任务或获取单个任务"""
    group_id = str(args.get("group_id") or "").strip()
    task_id = args.get("task_id")

    if not group_id:
        return _error("missing_group_id", "missing group_id")

    storage = _get_storage(group_id)
    if storage is None:
        return _error("group_not_found", f"group not found: {group_id}")

    if task_id:
        task = storage.load_task(str(task_id))
        if task is None:
            return _error("task_not_found", f"Task not found: {task_id}")
        return DaemonResponse(ok=True, result={"task": _task_to_dict(task)})

    tasks = storage.list_tasks()
    return DaemonResponse(ok=True, result={"tasks": [_task_to_dict(t) for t in tasks]})


# =============================================================================
# Presence Get
# =============================================================================


def handle_presence_get(args: Dict[str, Any]) -> DaemonResponse:
    """获取在线状态"""
    group_id = str(args.get("group_id") or "").strip()

    if not group_id:
        return _error("missing_group_id", "missing group_id")

    storage = _get_storage(group_id)
    if storage is None:
        return _error("group_not_found", f"group not found: {group_id}")

    presence = storage.load_presence()
    return DaemonResponse(ok=True, result={
        "agents": [
            {"id": a.id, "status": a.status, "updated_at": a.updated_at}
            for a in presence.agents
        ],
        "heartbeat_timeout_seconds": presence.heartbeat_timeout_seconds,
    })
