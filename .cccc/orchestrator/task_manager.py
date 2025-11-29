# -*- coding: utf-8 -*-
"""
Blueprint Task Manager - CRUD operations for tasks.

This module handles all task file operations:
- Loading and saving tasks from docs/por/blueprint/
- Processing progress markers from agent messages
- Maintaining scope.yaml (auto-managed)
- Generating task summaries for TUI/IM

Design principles:
- Single source of truth: Orchestrator manages all task files
- Agents send progress markers, Manager updates files
- Atomic file operations to prevent corruption
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from orchestrator.task_schema import (
    TaskDefinition, Step, Scope, ProgressMarker,
    TaskStatus, StepStatus,
    parse_progress_markers, generate_next_task_id, validate_task_id
)


class TaskManager:
    """
    Manages Blueprint tasks - file operations and state updates.

    Directory structure:
        docs/por/blueprint/
            scope.yaml          # Task list and metadata (auto-managed)
            T001-task-name/
                task.yaml       # Task definition
            T002-another-task/
                task.yaml
    """

    def __init__(self, root: Path):
        """
        Initialize TaskManager.

        Args:
            root: Project root directory (contains docs/por/blueprint/)
        """
        self.root = Path(root)
        self.blueprint_dir = self.root / "docs" / "por" / "blueprint"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Ensure blueprint directory exists."""
        self.blueprint_dir.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # File I/O Utilities
    # =========================================================================

    def _read_yaml(self, path: Path) -> Dict[str, Any]:
        """Read YAML file, return empty dict on error."""
        if not path.exists():
            return {}
        try:
            if yaml:
                content = yaml.safe_load(path.read_text(encoding='utf-8'))
                return content if isinstance(content, dict) else {}
            else:
                # Fallback to JSON-like parsing
                return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _write_yaml(self, path: Path, data: Dict[str, Any]) -> bool:
        """
        Write YAML file atomically.

        Returns:
            True on success, False on error
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.tmp')
            if yaml:
                content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
            else:
                content = json.dumps(data, indent=2, ensure_ascii=False)
            tmp.write_text(content, encoding='utf-8')
            tmp.replace(path)
            return True
        except Exception:
            return False

    # =========================================================================
    # Scope Management
    # =========================================================================

    def get_scope(self) -> Scope:
        """Load scope.yaml or create default."""
        path = self.blueprint_dir / "scope.yaml"
        data = self._read_yaml(path)
        if data:
            try:
                return Scope(**data)
            except Exception:
                pass
        return Scope()

    def save_scope(self, scope: Scope) -> bool:
        """Save scope.yaml."""
        scope.updated = datetime.now().isoformat()
        path = self.blueprint_dir / "scope.yaml"
        return self._write_yaml(path, scope.model_dump())

    def _sync_scope(self) -> Scope:
        """
        Synchronize scope.yaml with actual task directories.

        Scans blueprint directory and updates scope to match.
        """
        scope = self.get_scope()
        existing_ids = set(scope.tasks)
        found_ids = set()

        # Scan for task directories
        for item in self.blueprint_dir.iterdir():
            if item.is_dir() and item.name.startswith('T'):
                # Extract task ID from directory name (T001-slug -> T001)
                match = re.match(r'^(T\d{3})', item.name)
                if match:
                    task_id = match.group(1)
                    found_ids.add(task_id)

        # Add new tasks
        for tid in found_ids - existing_ids:
            scope.add_task(tid)

        # Remove deleted tasks
        for tid in existing_ids - found_ids:
            scope.remove_task(tid)

        self.save_scope(scope)
        return scope

    # =========================================================================
    # Task CRUD Operations
    # =========================================================================

    def _get_task_dir(self, task_id: str) -> Optional[Path]:
        """
        Find task directory by ID.

        Directory format: T001-slug-name
        """
        if not validate_task_id(task_id):
            return None

        for item in self.blueprint_dir.iterdir():
            if item.is_dir() and item.name.startswith(task_id):
                return item
        return None

    def _create_task_dir(self, task_id: str, name: str) -> Path:
        """
        Create task directory with slug from name.

        Args:
            task_id: Task ID (T001)
            name: Task name for slug generation

        Returns:
            Path to created directory
        """
        # Generate slug from name
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:30]
        dir_name = f"{task_id}-{slug}" if slug else task_id
        task_dir = self.blueprint_dir / dir_name
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    def get_task(self, task_id: str) -> Optional[TaskDefinition]:
        """
        Load task by ID.

        Args:
            task_id: Task ID (T001)

        Returns:
            TaskDefinition or None if not found
        """
        task_dir = self._get_task_dir(task_id)
        if not task_dir:
            return None

        task_path = task_dir / "task.yaml"
        data = self._read_yaml(task_path)
        if not data:
            return None

        try:
            return TaskDefinition(**data)
        except Exception:
            return None

    def save_task(self, task: TaskDefinition) -> bool:
        """
        Save task to disk.

        Creates directory if needed, updates scope.yaml.

        Args:
            task: TaskDefinition to save

        Returns:
            True on success
        """
        task_dir = self._get_task_dir(task.id)
        if not task_dir:
            task_dir = self._create_task_dir(task.id, task.name)

        task_path = task_dir / "task.yaml"
        success = self._write_yaml(task_path, task.model_dump())

        if success:
            # Update scope
            scope = self.get_scope()
            scope.add_task(task.id)
            self.save_scope(scope)

        return success

    def delete_task(self, task_id: str) -> bool:
        """
        Delete task and its directory.

        Args:
            task_id: Task ID to delete

        Returns:
            True on success
        """
        task_dir = self._get_task_dir(task_id)
        if not task_dir:
            return False

        try:
            import shutil
            shutil.rmtree(task_dir)

            # Update scope
            scope = self.get_scope()
            scope.remove_task(task_id)
            self.save_scope(scope)
            return True
        except Exception:
            return False

    def list_tasks(self) -> List[TaskDefinition]:
        """
        List all tasks in priority order (from scope).

        Returns:
            List of TaskDefinition objects
        """
        scope = self._sync_scope()
        tasks = []
        for task_id in scope.tasks:
            task = self.get_task(task_id)
            if task:
                tasks.append(task)
        return tasks

    def create_task(self, name: str, goal: str, steps: List[Dict[str, str]]) -> Optional[TaskDefinition]:
        """
        Create a new task.

        Args:
            name: Task name
            goal: User-visible outcome
            steps: List of step dicts with 'name' and 'done' keys

        Returns:
            Created TaskDefinition or None on error
        """
        # Generate next task ID
        scope = self.get_scope()
        task_id = generate_next_task_id(scope.tasks)

        # Build steps
        task_steps = []
        for i, s in enumerate(steps, 1):
            task_steps.append(Step(
                id=f"S{i}",
                name=s.get('name', f'Step {i}'),
                done=s.get('done', 'Completed'),
                status=StepStatus.PENDING
            ))

        # Create task
        task = TaskDefinition(
            id=task_id,
            name=name,
            goal=goal,
            status=TaskStatus.PLANNED,
            steps=task_steps
        )

        if self.save_task(task):
            return task
        return None

    # =========================================================================
    # Progress Marker Processing
    # =========================================================================

    def process_markers(self, text: str, peer: str) -> List[Tuple[ProgressMarker, bool, str]]:
        """
        Process all progress markers in a message.

        Args:
            text: Message text containing markers
            peer: Peer that sent the message (peerA, peerB)

        Returns:
            List of (marker, success, message) tuples
        """
        markers = parse_progress_markers(text)
        results = []

        for marker in markers:
            success, msg = self._apply_marker(marker, peer)
            results.append((marker, success, msg))

        return results

    def _apply_marker(self, marker: ProgressMarker, peer: str) -> Tuple[bool, str]:
        """
        Apply a single progress marker.

        Args:
            marker: Parsed ProgressMarker
            peer: Peer that sent the marker

        Returns:
            (success, message) tuple
        """
        task = self.get_task(marker.task_id)

        # Handle task-level markers
        if marker.is_task_level:
            if marker.action == 'start':
                return self._handle_task_start(marker.task_id, task)
            elif marker.action == 'promoted':
                return self._handle_quick_task_promotion(marker.task_id, peer)
            elif marker.action == 'done':
                return self._handle_task_done(marker.task_id, task)
            else:
                return False, f"Unknown task action: {marker.action}"

        # Handle step-level markers
        if not task:
            return False, f"Task {marker.task_id} not found"

        if marker.action == 'done':
            return self._handle_step_done(task, marker.step_id)
        elif marker.action == 'in_progress':
            return self._handle_step_in_progress(task, marker.step_id)
        elif marker.action == 'blocked':
            return self._handle_step_blocked(task, marker.step_id, marker.reason, peer)
        else:
            return False, f"Unknown step action: {marker.action}"

    def _handle_task_start(self, task_id: str, task: Optional[TaskDefinition]) -> Tuple[bool, str]:
        """Handle 'progress: T001 start' marker."""
        if not task:
            return False, f"Task {task_id} not found"

        if task.status == TaskStatus.ACTIVE:
            return True, f"Task {task_id} already active"

        task.status = TaskStatus.ACTIVE
        # Set first step to in_progress
        if task.steps and task.steps[0].status == StepStatus.PENDING:
            task.steps[0].status = StepStatus.IN_PROGRESS

        if self.save_task(task):
            return True, f"Task {task_id} started"
        return False, f"Failed to save task {task_id}"

    def _handle_task_done(self, task_id: str, task: Optional[TaskDefinition]) -> Tuple[bool, str]:
        """Handle 'progress: T001 done' marker - complete all remaining steps."""
        if not task:
            return False, f"Task {task_id} not found"

        # Complete all steps
        for step in task.steps:
            if step.status != StepStatus.COMPLETE:
                step.status = StepStatus.COMPLETE

        task.status = TaskStatus.COMPLETE

        if self.save_task(task):
            return True, f"Task {task_id} completed"
        return False, f"Failed to save task {task_id}"

    def _handle_quick_task_promotion(self, task_id: str, peer: str) -> Tuple[bool, str]:
        """
        Handle 'progress: T001 promoted' marker.

        This creates a placeholder task that the agent should then define.
        """
        # Create minimal task as placeholder
        scope = self.get_scope()
        if task_id in scope.tasks:
            return False, f"Task {task_id} already exists"

        # Use next ID if specified doesn't match
        actual_id = generate_next_task_id(scope.tasks)

        task = TaskDefinition(
            id=actual_id,
            name="Promoted Quick Task",
            goal="Task promoted from quick task - awaiting definition",
            status=TaskStatus.ACTIVE,
            steps=[
                Step(id="S1", name="Define task scope", done="Task definition complete", status=StepStatus.IN_PROGRESS),
                Step(id="S2", name="Execute task", done="Implementation complete", status=StepStatus.PENDING),
            ]
        )

        if self.save_task(task):
            return True, f"Quick task promoted to {actual_id}"
        return False, f"Failed to create promoted task"

    def _handle_step_done(self, task: TaskDefinition, step_id: str) -> Tuple[bool, str]:
        """Handle 'progress: T001.S1 done' marker."""
        step = task.get_step(step_id)
        if not step:
            return False, f"Step {step_id} not found in {task.id}"

        step.status = StepStatus.COMPLETE

        # Activate next step if any
        next_step = task.get_step(f"S{int(step_id[1:]) + 1}")
        if next_step and next_step.status == StepStatus.PENDING:
            next_step.status = StepStatus.IN_PROGRESS

        # Check if all steps complete
        if all(s.status == StepStatus.COMPLETE for s in task.steps):
            task.status = TaskStatus.COMPLETE

        if self.save_task(task):
            return True, f"Step {task.id}.{step_id} completed"
        return False, f"Failed to save task {task.id}"

    def _handle_step_in_progress(self, task: TaskDefinition, step_id: str) -> Tuple[bool, str]:
        """Handle 'progress: T001.S1 in_progress' marker."""
        step = task.get_step(step_id)
        if not step:
            return False, f"Step {step_id} not found in {task.id}"

        step.status = StepStatus.IN_PROGRESS

        # Ensure task is active
        if task.status == TaskStatus.PLANNED:
            task.status = TaskStatus.ACTIVE

        if self.save_task(task):
            return True, f"Step {task.id}.{step_id} in progress"
        return False, f"Failed to save task {task.id}"

    def _handle_step_blocked(self, task: TaskDefinition, step_id: str,
                            reason: Optional[str], peer: str) -> Tuple[bool, str]:
        """
        Handle 'progress: T001.S1 blocked: reason' marker.

        Note: blocked is notification only, no state change.
        The step remains in its current state.
        """
        step = task.get_step(step_id)
        if not step:
            return False, f"Step {step_id} not found in {task.id}"

        # Log blocked notification (no state change)
        reason_str = reason or "no reason given"
        return True, f"Step {task.id}.{step_id} blocked: {reason_str}"

    # =========================================================================
    # Summary Generation
    # =========================================================================

    def get_summary(self) -> Dict[str, Any]:
        """
        Get overall blueprint summary for TUI/IM.

        Returns:
            Dict with summary statistics and task list
        """
        tasks = self.list_tasks()

        # Calculate overall stats
        total = len(tasks)
        completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETE)
        active = sum(1 for t in tasks if t.status == TaskStatus.ACTIVE)
        planned = sum(1 for t in tasks if t.status == TaskStatus.PLANNED)

        # Find current task (first active)
        current_task = None
        current_step = None
        for t in tasks:
            if t.status == TaskStatus.ACTIVE:
                current_task = t.id
                current_step = t.current_step
                break

        # Overall step progress
        total_steps = sum(t.total_steps for t in tasks)
        completed_steps = sum(t.completed_steps for t in tasks)

        return {
            'total_tasks': total,
            'completed_tasks': completed,
            'active_tasks': active,
            'planned_tasks': planned,
            'current_task': current_task,
            'current_step': current_step,
            'total_steps': total_steps,
            'completed_steps': completed_steps,
            'progress_percent': int(completed_steps / total_steps * 100) if total_steps else 0,
            'tasks': [
                {
                    'id': t.id,
                    'name': t.name,
                    'status': t.status,
                    'progress': t.progress,
                    'current_step': t.current_step,
                }
                for t in tasks
            ]
        }

    def format_status_line(self) -> str:
        """
        Format single-line status for TUI status bar.

        Returns:
            String like "2/5 → T003 [S2]"
        """
        summary = self.get_summary()
        if summary['total_tasks'] == 0:
            return "No tasks"

        current = summary['current_task'] or '-'
        step = f"[{summary['current_step']}]" if summary['current_step'] else ''
        return f"{summary['completed_tasks']}/{summary['total_tasks']} → {current} {step}".strip()

    def format_task_list(self, expanded: bool = False) -> str:
        """
        Format task list for TUI panel display.

        Args:
            expanded: Whether to show expanded view

        Returns:
            Formatted string for display
        """
        tasks = self.list_tasks()
        if not tasks:
            return "No tasks defined"

        lines = []

        for task in tasks:
            # Status icon
            if task.status == TaskStatus.COMPLETE:
                icon = '✓'
            elif task.status == TaskStatus.ACTIVE:
                icon = '→'
            else:
                icon = '○'

            if expanded:
                # Full task details
                lines.append(f"{icon} {task.id} {task.name}")
                lines.append(f"    Goal: {task.goal}")
                lines.append(f"    Progress: {task.progress} ({task.progress_percent}%)")
                for step in task.steps:
                    step_icon = '✓' if step.status == StepStatus.COMPLETE else ('→' if step.status == StepStatus.IN_PROGRESS else '○')
                    lines.append(f"      {step_icon} {step.id}: {step.name}")
                lines.append('')
            else:
                # Compact view
                lines.append(f"{icon} {task.id} {task.name:<20} {task.progress}")

        return '\n'.join(lines)

    def format_for_im(self) -> str:
        """
        Format task status for IM display.

        Returns:
            IM-friendly formatted string
        """
        summary = self.get_summary()
        if summary['total_tasks'] == 0:
            return "━━━ Blueprint ━━━\nNo tasks defined"

        lines = [
            "━━━ Blueprint ━━━",
            f"▸ Progress: {summary['completed_tasks']}/{summary['total_tasks']} tasks ({summary['progress_percent']}%)",
        ]

        if summary['current_task']:
            lines.append(f"▸ Current: {summary['current_task']} [{summary['current_step'] or '-'}]")

        lines.append("")

        # Task list (compact)
        for t in summary['tasks'][:5]:  # Limit to 5 for IM
            if t['status'] == 'complete':
                icon = '✓'
            elif t['status'] == 'active':
                icon = '→'
            else:
                icon = '○'
            lines.append(f"{icon} {t['id']} {t['name'][:15]} {t['progress']}")

        if len(summary['tasks']) > 5:
            lines.append(f"... and {len(summary['tasks']) - 5} more")

        return '\n'.join(lines)


# Module-level singleton instance
_manager: Optional[TaskManager] = None


def get_task_manager(root: Optional[Path] = None) -> TaskManager:
    """
    Get or create TaskManager singleton.

    Args:
        root: Project root directory (required on first call)

    Returns:
        TaskManager instance
    """
    global _manager
    if _manager is None:
        if root is None:
            root = Path.cwd()
        _manager = TaskManager(root)
    return _manager
