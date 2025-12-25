"""
Context storage for CCCC groups.

基于 ccontext 的设计，为每个 group 提供上下文管理：
- Vision/Sketch: 项目愿景和静态蓝图
- Milestones: 里程碑（2-6 个粗粒度阶段）
- Tasks: 任务（带 3-7 个步骤的可交付工作项）
- Notes: 笔记（带 TTL 的临时记录）
- References: 引用（带 TTL 的文件/URL 引用）
- Presence: 在线状态（agent 当前在做什么）

存储位置: ~/.cccc/groups/<group_id>/context/
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .group import Group


# =============================================================================
# Enums
# =============================================================================


class MilestoneStatus(str, Enum):
    DONE = "done"
    ACTIVE = "active"
    PENDING = "pending"


class TaskStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    DONE = "done"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


# =============================================================================
# Data Classes
# =============================================================================


class Milestone:
    def __init__(
        self,
        id: str,
        name: str,
        description: str = "",
        status: MilestoneStatus = MilestoneStatus.PENDING,
        started: Optional[str] = None,
        completed: Optional[str] = None,
        outcomes: Optional[str] = None,
        updated_at: Optional[str] = None,
    ):
        self.id = id
        self.name = name
        self.description = description
        self.status = status
        self.started = started
        self.completed = completed
        self.outcomes = outcomes
        self.updated_at = updated_at


class Note:
    def __init__(self, id: str, content: str, ttl: int = 30):
        self.id = id
        self.content = content
        self.ttl = max(0, min(100, ttl))

    @property
    def expiring(self) -> bool:
        return self.ttl <= 3


class Reference:
    def __init__(self, id: str, url: str, note: str, ttl: int = 30):
        self.id = id
        self.url = url
        self.note = note
        self.ttl = max(0, min(100, ttl))

    @property
    def expiring(self) -> bool:
        return self.ttl <= 3


class Step:
    def __init__(
        self,
        id: str,
        name: str,
        acceptance: str = "",
        status: StepStatus = StepStatus.PENDING,
    ):
        self.id = id
        self.name = name
        self.acceptance = acceptance
        self.status = status


class Task:
    def __init__(
        self,
        id: str,
        name: str,
        goal: str = "",
        status: TaskStatus = TaskStatus.PLANNED,
        milestone: Optional[str] = None,
        assignee: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        steps: Optional[List[Step]] = None,
    ):
        self.id = id
        self.name = name
        self.goal = goal
        self.status = status
        self.milestone = milestone
        self.assignee = assignee
        self.created_at = created_at or _utc_now_iso()
        self.updated_at = updated_at
        self.steps = steps or []

    @property
    def current_step(self) -> Optional[Step]:
        for step in self.steps:
            if step.status != StepStatus.DONE:
                return step
        return None

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        done_count = sum(1 for s in self.steps if s.status == StepStatus.DONE)
        return done_count / len(self.steps)


class AgentPresence:
    def __init__(self, id: str, status: str = "", updated_at: Optional[str] = None):
        self.id = id
        self.status = status
        self.updated_at = updated_at or _utc_now_iso()


class Context:
    def __init__(
        self,
        vision: Optional[str] = None,
        sketch: Optional[str] = None,
        milestones: Optional[List[Milestone]] = None,
        notes: Optional[List[Note]] = None,
        references: Optional[List[Reference]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ):
        self.vision = vision
        self.sketch = sketch
        self.milestones = milestones or []
        self.notes = notes or []
        self.references = references or []
        self.meta = meta or {}


class PresenceData:
    def __init__(
        self,
        agents: Optional[List[AgentPresence]] = None,
        heartbeat_timeout_seconds: int = 300,
    ):
        self.agents = agents or []
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds


# =============================================================================
# Helpers
# =============================================================================


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ttl(value: Any, default: int = 30) -> int:
    try:
        ttl_int = int(value)
    except (TypeError, ValueError):
        ttl_int = default
    return max(0, min(100, ttl_int))


# =============================================================================
# Context Storage
# =============================================================================


# Archive thresholds
ARCHIVE_TTL_THRESHOLD = 0
ARCHIVE_DONE_DAYS = 7
MAX_DONE_TASKS = 10
MAX_DONE_MILESTONES_RETURNED = 3


class ContextStorage:
    """Context storage for a single group."""

    def __init__(self, group: Group):
        self.group = group
        self.context_dir = group.path / "context"
        self.tasks_dir = self.context_dir / "tasks"
        self.archive_dir = self.context_dir / "archive"
        self.archive_tasks_dir = self.archive_dir / "tasks"

    def _ensure_dirs(self) -> None:
        self.context_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.archive_tasks_dir.mkdir(parents=True, exist_ok=True)

    def _context_path(self) -> Path:
        return self.context_dir / "context.yaml"

    def _task_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.yaml"

    def _archive_task_path(self, task_id: str) -> Path:
        return self.archive_tasks_dir / f"{task_id}.yaml"

    def _presence_path(self) -> Path:
        return self.context_dir / "presence.yaml"

    # =========================================================================
    # Version Computation
    # =========================================================================

    def compute_version(self) -> str:
        """Compute a version hash for the current context state."""

        def _jsonable(obj: Any) -> Any:
            if obj is None or isinstance(obj, (str, int, float, bool)):
                return obj
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, list):
                return [_jsonable(x) for x in obj]
            if isinstance(obj, dict):
                return {str(k): _jsonable(v) for k, v in obj.items()}
            return str(obj)

        def _strip_ttl(obj: Any) -> Any:
            if not isinstance(obj, dict):
                return obj
            out = dict(obj)
            for key in ("notes", "references"):
                items = out.get(key)
                if isinstance(items, list):
                    new_items: List[Any] = []
                    for it in items:
                        if isinstance(it, dict):
                            it2 = dict(it)
                            it2.pop("ttl", None)
                            new_items.append(it2)
                        else:
                            new_items.append(it)
                    out[key] = new_items
            return out

        h = hashlib.sha256()

        ctx_path = self._context_path()
        if ctx_path.exists():
            try:
                data = yaml.safe_load(ctx_path.read_text(encoding="utf-8"))
            except Exception:
                data = None
            payload = json.dumps(_jsonable(_strip_ttl(data)), sort_keys=True).encode()
            h.update(payload)

        if self.tasks_dir.exists():
            for task_file in sorted(self.tasks_dir.glob("T*.yaml")):
                h.update(task_file.name.encode("utf-8"))
                try:
                    data = yaml.safe_load(task_file.read_text(encoding="utf-8"))
                except Exception:
                    data = None
                payload = json.dumps(_jsonable(data), sort_keys=True).encode()
                h.update(payload)

        return h.hexdigest()[:12]

    # =========================================================================
    # Context Operations
    # =========================================================================

    def _default_meta(self) -> Dict[str, Any]:
        return {
            "contract": {
                "vision": "One-sentence north star; update rarely.",
                "sketch": "Static blueprint only (architecture/strategy). No TODO/progress/tasks.",
                "milestones": "Coarse phase timeline (2-6). Exactly one active.",
                "tasks": "Deliverable work items with 3-7 steps.",
                "linking": "Each task should set milestone: Mx to form Vision→Milestones→Tasks tree.",
            }
        }

    def load_context(self) -> Context:
        path = self._context_path()
        if not path.exists():
            return Context(meta=self._default_meta())

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if data is None:
                return Context(meta=self._default_meta())

            vision = data.get("vision")
            sketch = data.get("sketch")
            meta = data.get("meta")
            if not isinstance(meta, dict) or not meta:
                meta = self._default_meta()

            milestones = []
            for m in data.get("milestones", []):
                milestones.append(
                    Milestone(
                        id=m.get("id", ""),
                        name=m.get("name", ""),
                        description=m.get("description", ""),
                        status=MilestoneStatus(m.get("status", "pending")),
                        started=m.get("started"),
                        completed=m.get("completed"),
                        outcomes=m.get("outcomes"),
                        updated_at=m.get("updated_at"),
                    )
                )

            notes = []
            for n in data.get("notes", []):
                if not isinstance(n, dict):
                    continue
                notes.append(
                    Note(
                        id=n.get("id", ""),
                        content=n.get("content", ""),
                        ttl=_parse_ttl(n.get("ttl", 30)),
                    )
                )

            refs = []
            for r in data.get("references", []):
                if not isinstance(r, dict):
                    continue
                refs.append(
                    Reference(
                        id=r.get("id", ""),
                        url=r.get("url", ""),
                        note=r.get("note", ""),
                        ttl=_parse_ttl(r.get("ttl", 30)),
                    )
                )

            return Context(
                vision=vision,
                sketch=sketch,
                milestones=milestones,
                notes=notes,
                references=refs,
                meta=meta,
            )
        except Exception:
            return Context(meta=self._default_meta())

    def save_context(self, context: Context) -> None:
        self._ensure_dirs()

        data: Dict[str, Any] = {}
        if context.vision is not None:
            data["vision"] = context.vision
        if context.sketch is not None:
            data["sketch"] = context.sketch

        meta = context.meta if isinstance(context.meta, dict) else {}
        if not meta:
            meta = self._default_meta()
        data["meta"] = meta

        data["milestones"] = [
            {
                k: v
                for k, v in {
                    "id": m.id,
                    "name": m.name,
                    "description": m.description,
                    "status": m.status.value if isinstance(m.status, MilestoneStatus) else m.status,
                    "started": m.started,
                    "completed": m.completed,
                    "outcomes": m.outcomes,
                    "updated_at": m.updated_at,
                }.items()
                if v is not None
            }
            for m in context.milestones
        ]

        data["notes"] = [{"id": n.id, "content": n.content, "ttl": n.ttl} for n in context.notes]
        data["references"] = [
            {"id": r.id, "url": r.url, "note": r.note, "ttl": r.ttl} for r in context.references
        ]

        path = self._context_path()
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )

    # =========================================================================
    # Milestone Operations
    # =========================================================================

    def generate_milestone_id(self, context: Context) -> str:
        max_num = 0
        for m in context.milestones:
            match = re.match(r"M(\d+)", m.id)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return f"M{max_num + 1}"

    def get_milestone(self, context: Context, milestone_id: str) -> Optional[Milestone]:
        for m in context.milestones:
            if m.id == milestone_id:
                return m
        return None

    # =========================================================================
    # Note Operations
    # =========================================================================

    def generate_note_id(self, context: Context) -> str:
        max_num = 0
        for note in context.notes:
            match = re.match(r"N(\d+)", note.id)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return f"N{max_num + 1:03d}"

    def get_note_by_id(self, context: Context, note_id: str) -> Optional[Note]:
        for n in context.notes:
            if n.id == note_id:
                return n
        return None

    # =========================================================================
    # Reference Operations
    # =========================================================================

    def generate_reference_id(self, context: Context) -> str:
        max_num = 0
        for ref in context.references:
            match = re.match(r"R(\d+)", ref.id)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return f"R{max_num + 1:03d}"

    def get_reference_by_id(self, context: Context, ref_id: str) -> Optional[Reference]:
        for r in context.references:
            if r.id == ref_id:
                return r
        return None

    # =========================================================================
    # Task Operations
    # =========================================================================

    def load_task(self, task_id: str, include_archived: bool = False) -> Optional[Task]:
        path = self._task_path(task_id)
        if path.exists():
            return self._parse_task(path)
        if include_archived:
            archive_path = self._archive_task_path(task_id)
            if archive_path.exists():
                return self._parse_task(archive_path)
        return None

    def _parse_task(self, path: Path) -> Optional[Task]:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if data is None:
                return None

            steps = []
            for s in data.get("steps", []):
                steps.append(
                    Step(
                        id=s.get("id", ""),
                        name=s.get("name", ""),
                        acceptance=s.get("acceptance", ""),
                        status=StepStatus(s.get("status", "pending")),
                    )
                )

            return Task(
                id=data.get("id", ""),
                name=data.get("name", ""),
                goal=data.get("goal", ""),
                status=TaskStatus(data.get("status", "planned")),
                milestone=data.get("milestone") or data.get("milestone_id"),
                assignee=data.get("assignee"),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at"),
                steps=steps,
            )
        except Exception:
            return None

    def save_task(self, task: Task) -> None:
        self._ensure_dirs()

        data = {
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
        }
        data = {k: v for k, v in data.items() if v is not None}

        path = self._task_path(task.id)
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )

    def delete_task(self, task_id: str) -> bool:
        path = self._task_path(task_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_tasks(self, include_archived: bool = False) -> List[Task]:
        tasks = []
        if self.tasks_dir.exists():
            for path in self.tasks_dir.glob("T*.yaml"):
                task = self._parse_task(path)
                if task:
                    tasks.append(task)
        if include_archived and self.archive_tasks_dir.exists():
            for path in self.archive_tasks_dir.glob("T*.yaml"):
                task = self._parse_task(path)
                if task:
                    tasks.append(task)
        tasks.sort(key=lambda t: t.id)
        return tasks

    def generate_task_id(self) -> str:
        max_num = 0
        for dir_path in [self.tasks_dir, self.archive_tasks_dir]:
            if dir_path.exists():
                for path in dir_path.glob("T*.yaml"):
                    match = re.match(r"T(\d+)", path.stem)
                    if match:
                        max_num = max(max_num, int(match.group(1)))
        return f"T{max_num + 1:03d}"

    # =========================================================================
    # TTL Decay
    # =========================================================================

    def decay_ttl(self, context: Context) -> Tuple[Context, List[Note], List[Reference]]:
        """Decay ttl for notes and references. Returns archived items."""
        archived_notes = []
        archived_refs = []
        remaining_notes = []
        remaining_refs = []

        for note in context.notes:
            if note.ttl <= ARCHIVE_TTL_THRESHOLD:
                archived_notes.append(note)
                continue
            note.ttl = max(0, note.ttl - 1)
            remaining_notes.append(note)

        for ref in context.references:
            if ref.ttl <= ARCHIVE_TTL_THRESHOLD:
                archived_refs.append(ref)
                continue
            ref.ttl = max(0, ref.ttl - 1)
            remaining_refs.append(ref)

        context.notes = remaining_notes
        context.references = remaining_refs
        return context, archived_notes, archived_refs

    # =========================================================================
    # Presence Operations
    # =========================================================================

    def load_presence(self) -> PresenceData:
        path = self._presence_path()
        if not path.exists():
            return PresenceData()

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if data is None:
                return PresenceData()

            agents = []
            for a in data.get("agents", []):
                agents.append(
                    AgentPresence(
                        id=a.get("id", ""),
                        status=a.get("status", ""),
                        updated_at=a.get("updated_at", ""),
                    )
                )

            return PresenceData(
                agents=agents,
                heartbeat_timeout_seconds=data.get("heartbeat_timeout_seconds", 300),
            )
        except Exception:
            return PresenceData()

    def save_presence(self, presence: PresenceData) -> None:
        self._ensure_dirs()

        data = {
            "agents": [
                {k: v for k, v in {"id": a.id, "status": a.status, "updated_at": a.updated_at}.items() if v}
                for a in presence.agents
            ],
            "heartbeat_timeout_seconds": presence.heartbeat_timeout_seconds,
        }

        path = self._presence_path()
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )

    def _canonicalize_agent_id(self, agent_id: str) -> str:
        s = str(agent_id or "").strip()
        if not s:
            return ""
        s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"-\1", s)
        s = s.replace("_", "-").replace(" ", "-")
        s = re.sub(r"-{2,}", "-", s)
        s = s.strip("-")
        return s.lower()

    def update_agent_presence(self, agent_id: str, status: str) -> AgentPresence:
        canonical_id = self._canonicalize_agent_id(agent_id)
        if not canonical_id:
            raise ValueError("agent_id must be a non-empty string")

        status_norm = re.sub(r"\s+", " ", str(status or "")).strip()
        presence = self.load_presence()

        agent = None
        for a in presence.agents:
            if a.id == canonical_id:
                agent = a
                break

        if agent is None:
            agent = AgentPresence(id=canonical_id)
            presence.agents.append(agent)

        agent.status = status_norm
        agent.updated_at = _utc_now_iso()

        self.save_presence(presence)
        return agent

    def clear_agent_status(self, agent_id: str) -> AgentPresence:
        return self.update_agent_presence(agent_id=agent_id, status="")
