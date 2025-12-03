# -*- coding: utf-8 -*-
"""
Blueprint Task Manager - CRUD operations for tasks.

This module handles all task file operations:
- Loading and saving tasks from docs/por/
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
        docs/por/
            POR.md              # Strategic Plan of Record (north star)
            scope.yaml          # Task list and metadata (auto-managed)
            T001-task-name/
                task.yaml       # Task definition
            T002-another-task/
                task.yaml
    
    Error Handling:
        - Parse errors are captured in self.parse_errors (dict: task_id -> error_msg)
        - Errors are NOT printed to stderr to avoid screen flooding
        - TUI/IM can query parse_errors for display
    """

    def __init__(self, root: Path):
        """
        Initialize TaskManager.

        Args:
            root: Project root directory (contains docs/por/)
        """
        self.root = Path(root)
        self.blueprint_dir = self.root / "docs" / "por"
        # Error tracking: task_id -> error message (cleared on successful load)
        self.parse_errors: Dict[str, str] = {}
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Ensure blueprint directory exists."""
        self.blueprint_dir.mkdir(parents=True, exist_ok=True)
    
    def refresh(self) -> None:
        """
        Refresh task state by re-scanning the blueprint directory.
        
        This clears existing parse_errors and reloads all tasks,
        capturing any new parse errors in the process.
        """
        # Clear stale errors before re-scan
        self.parse_errors.clear()
        # Re-sync scope (scans directory, loads tasks, captures errors)
        self._sync_scope()
        # Also try to load each task to catch validation errors
        scope = self.get_scope()
        for task_id in scope.tasks:
            self.get_task(task_id)  # This captures parse errors
    
    def get_parse_errors(self) -> Dict[str, str]:
        """Get current parse errors for display in TUI/IM."""
        return self.parse_errors.copy()
    
    def clear_error(self, task_id: str) -> None:
        """Clear error for a specific task (called when task is successfully loaded)."""
        self.parse_errors.pop(task_id, None)

    # =========================================================================
    # File I/O Utilities
    # =========================================================================

    def _read_yaml(self, path: Path, task_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Read YAML file with robust error handling.
        
        Features:
        - Handles multi-document YAML (takes first document only)
        - Stores errors in self.parse_errors for TUI/IM display
        - No stderr output to avoid screen flooding
        
        Args:
            path: Path to YAML file
            task_id: Optional task ID for error tracking
        """
        if not path.exists():
            return {}
        try:
            raw_text = path.read_text(encoding='utf-8')
            
            if yaml:
                # Use safe_load_all to handle multi-document YAML gracefully
                # Only use the first document (common agent mistake: appending with ---)
                try:
                    docs = list(yaml.safe_load_all(raw_text))
                    content = docs[0] if docs else {}
                except yaml.YAMLError:
                    # Fallback: try safe_load for better error messages
                    content = yaml.safe_load(raw_text)
            else:
                # Fallback to JSON-like parsing
                content = json.loads(raw_text)
            
            # Clear error on successful parse
            if task_id:
                self.clear_error(task_id)
            return content if isinstance(content, dict) else {}
            
        except Exception as e:
            # Store error for TUI/IM display (no stderr print to avoid flood)
            if task_id:
                # Extract concise, actionable error message
                err_str = str(e)
                # Extract line/column info if available
                if 'line' in err_str and 'column' in err_str:
                    # Keep YAML error context
                    if len(err_str) > 150:
                        err_str = err_str[:150] + "..."
                else:
                    if len(err_str) > 100:
                        err_str = err_str[:100] + "..."
                self.parse_errors[task_id] = f"YAML: {err_str}"
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
        Tracks duplicate task IDs in parse_errors (no stderr print).
        """
        scope = self.get_scope()
        existing_ids = set(scope.tasks)
        found_ids = set()
        id_to_dirs: Dict[str, List[str]] = {}  # Track multiple dirs per ID

        # Scan for task directories
        for item in self.blueprint_dir.iterdir():
            if item.is_dir() and item.name.startswith('T'):
                # Extract task ID from directory name (T001-slug -> T001)
                match = re.match(r'^(T\d{3})', item.name)
                if match:
                    task_id = match.group(1)
                    found_ids.add(task_id)
                    if task_id not in id_to_dirs:
                        id_to_dirs[task_id] = []
                    id_to_dirs[task_id].append(item.name)

        # Track duplicate task IDs as errors (no stderr print)
        for tid, dirs in id_to_dirs.items():
            if len(dirs) > 1:
                self.parse_errors[tid] = f"Duplicate: {', '.join(dirs)}"

        # Add new tasks
        for tid in found_ids - existing_ids:
            scope.add_task(tid)

        # Remove deleted tasks
        for tid in existing_ids - found_ids:
            scope.remove_task(tid)
            # Also clear any error for removed tasks
            self.clear_error(tid)

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

    def _normalize_task_data(self, data: Dict[str, Any], source: str = "") -> Dict[str, Any]:
        """
        Normalize and auto-fix common task data issues.
        
        This provides resilience against agent format variations:
        - Status values: 'pending' -> 'planned', unknown -> 'planned'
        - Step IDs: 'S01' -> 'S1', 'step1' -> 'S1', 's1' -> 'S1'
        - Step status: unknown -> 'pending'
        
        Note: Warnings are silently handled (no stderr print) to avoid screen flood.
        
        Args:
            data: Raw task data from YAML
            source: Source file path for logging
            
        Returns:
            Normalized data dict
        """
        # === Task Status Normalization ===
        if 'status' in data:
            raw_status = str(data['status']).lower().strip()
            # Map common variations to valid values
            status_map = {
                'planned': 'planned',
                'active': 'active', 
                'complete': 'complete',
                'completed': 'complete',
                'done': 'complete',
                'pending': 'planned',      # Common mistake: 'pending' for task
                'in_progress': 'active',   # Common mistake: step status used for task
                'in-progress': 'active',
                'inprogress': 'active',
                'draft': 'planned',
                'todo': 'planned',
                'wip': 'active',
                'inactive': 'planned',     # Another common variation
                'paused': 'planned',
                'blocked': 'active',       # Blocked is still active work
                'started': 'active',
            }
            if raw_status in status_map:
                data['status'] = status_map[raw_status]
            else:
                # Unknown status -> fallback to 'planned' (silent)
                data['status'] = 'planned'
        
        # === Steps Normalization ===
        if 'steps' in data and isinstance(data['steps'], list):
            normalized_steps = []
            for i, step in enumerate(data['steps']):
                if not isinstance(step, dict):
                    continue
                    
                # Step ID normalization
                if 'id' in step:
                    raw_id = str(step['id']).strip()
                    step['id'] = self._normalize_step_id(raw_id, i + 1)
                else:
                    # No ID -> generate one
                    step['id'] = f"S{i + 1}"
                
                # Step Status normalization
                if 'status' in step:
                    raw_step_status = str(step['status']).lower().strip()
                    step_status_map = {
                        'pending': 'pending',
                        'in_progress': 'in_progress',
                        'in-progress': 'in_progress',
                        'inprogress': 'in_progress',
                        'active': 'in_progress',
                        'wip': 'in_progress',
                        'complete': 'complete',
                        'completed': 'complete',
                        'done': 'complete',
                    }
                    if raw_step_status in step_status_map:
                        step['status'] = step_status_map[raw_step_status]
                    else:
                        # Unknown step status -> fallback to 'pending' (silent)
                        step['status'] = 'pending'
                else:
                    # Missing status -> default to pending
                    step['status'] = 'pending'
                
                # Ensure required fields
                if 'name' not in step or not step['name']:
                    step['name'] = f"Step {i + 1}"
                if 'done' not in step:
                    step['done'] = ""
                    
                normalized_steps.append(step)
            data['steps'] = normalized_steps
        
        return data
    
    def _normalize_step_id(self, raw_id: str, fallback_num: int) -> str:
        """
        Normalize step ID to standard format (S1, S2, S1.1, etc.)
        
        Handles variations:
        - S1, S01, S001 -> S1
        - s1, s01 -> S1
        - Step1, step1, Step-1 -> S1
        - 1, 01 -> S1
        - S1.1, S1.2 -> S1.1, S1.2 (preserved)
        
        Args:
            raw_id: Raw step ID string
            fallback_num: Fallback number if parsing fails
            
        Returns:
            Normalized step ID (S1, S2, S1.1, etc.)
        """
        import re
        
        # Already valid format
        if re.match(r'^S\d+(\.\d+)?$', raw_id):
            # Normalize leading zeros: S01 -> S1
            match = re.match(r'^S0*(\d+)(\.(\d+))?$', raw_id)
            if match:
                main = int(match.group(1))
                sub = match.group(3)
                if sub:
                    return f"S{main}.{int(sub)}"
                return f"S{main}"
            return raw_id
        
        # Lowercase s: s1, s01
        if re.match(r'^s\d+(\.\d+)?$', raw_id, re.IGNORECASE):
            return self._normalize_step_id(raw_id.upper(), fallback_num)
        
        # "Step" prefix: Step1, step-1, Step 1
        match = re.match(r'^step[-\s]?0*(\d+)$', raw_id, re.IGNORECASE)
        if match:
            return f"S{int(match.group(1))}"
        
        # Just a number: 1, 01, 001
        match = re.match(r'^0*(\d+)$', raw_id)
        if match:
            return f"S{int(match.group(1))}"
        
        # Fallback
        return f"S{fallback_num}"

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
        # Pass task_id for error tracking
        data = self._read_yaml(task_path, task_id=task_id)
        if not data:
            # If no data but file exists, error was already recorded in _read_yaml
            return None

        # === Detect non-standard formats and record as errors ===
        # Check for 'phases' instead of 'steps' (common mistake)
        has_phases = 'phases' in data and isinstance(data.get('phases'), list) and len(data['phases']) > 0
        has_steps = 'steps' in data and isinstance(data.get('steps'), list) and len(data['steps']) > 0
        
        if has_phases and not has_steps:
            # Task uses 'phases' instead of 'steps' - this is a format error
            self.parse_errors[task_id] = "Format: uses 'phases' instead of 'steps'. Rename 'phases' to 'steps' and ensure each step has id/name/status."
            return None
        
        # Check for completely missing steps (and no phases either)
        if not has_steps and not has_phases:
            # No steps defined at all - record as warning but still parse
            # (task without steps is technically valid but unusual)
            pass

        try:
            # Normalize data before validation
            data = self._normalize_task_data(data, str(task_path))
            task = TaskDefinition(**data)
            # Clear any previous error on successful load
            self.clear_error(task_id)
            return task
        except Exception as e:
            # Store validation error (don't print to stderr)
            err_str = str(e)
            if len(err_str) > 200:
                err_str = err_str[:200] + "..."
            self.parse_errors[task_id] = f"Validation: {err_str}"
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
            return False, f"Task {marker.task_id} not found. Create task.yaml first in docs/por/{marker.task_id}-slug/"

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
            return False, f"Task {task_id} not found. Create task.yaml first in docs/por/{task_id}-slug/"

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
            Dict with summary statistics, task list, and any parse errors
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
        
        # Include parse errors for TUI/IM display
        errors = self.get_parse_errors()

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
            ],
            'parse_errors': errors,  # Dict of task_id -> error_msg
            'error_count': len(errors),
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
            return "━━━ Tasks ━━━\nNo tasks defined"

        lines = [
            "━━━ Tasks ━━━",
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
