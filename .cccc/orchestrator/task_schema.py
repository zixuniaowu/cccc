# -*- coding: utf-8 -*-
"""
Blueprint Task Schema - Pydantic models for task structure.

This module defines the core data models for the Blueprint task system:
- Step: Individual task step with completion criteria
- TaskDefinition: Full task with steps and metadata
- Scope: Collection of all tasks in the blueprint

Design principles:
- Minimal schema (6 fields) for high reliability
- Computed properties (progress, current_step) instead of stored fields
- Agent sends progress markers, Orchestrator manages files
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

try:
    from pydantic import BaseModel, Field, field_validator, model_validator
except ImportError:
    # Fallback for environments without pydantic v2
    try:
        from pydantic import BaseModel, Field, validator as field_validator, root_validator
        model_validator = None  # Will use root_validator instead
    except ImportError:
        raise ImportError("pydantic is required for task schema")


class StepStatus(str, Enum):
    """Step execution status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


class TaskStatus(str, Enum):
    """Task lifecycle status.

    Simple 3-state model:
    - planned: Initial draft / planning stage
    - active: Work in progress
    - complete: All steps done

    PeerA creates tasks; both peers can update status and content.
    """
    PLANNED = "planned"           # Draft, initial planning
    ACTIVE = "active"             # Work in progress
    COMPLETE = "complete"         # All steps done


class Step(BaseModel):
    """
    Individual task step with completion criteria.

    Attributes:
        id: Step identifier (S1, S2, S1.1, etc.)
        name: Brief description of the step
        done: Completion criteria (e.g., "Tests pass", "API documented")
        status: Current execution status
    """
    # Flexible step ID - will be normalized to S1, S2 format
    id: str = Field(..., description="Step ID (S1, S2, S1.1, ...)")
    name: str = Field(..., min_length=1, max_length=200, description="Step description")
    done: str = Field(default="", max_length=500, description="Completion criteria")
    status: StepStatus = Field(default=StepStatus.PENDING, description="Step status")

    class Config:
        use_enum_values = True
        extra = 'allow'  # Allow extra fields (current_progress, remaining_work, etc.)


class TaskDefinition(BaseModel):
    """
    Full task definition with steps and metadata.

    Attributes:
        id: Task identifier (T001, T002, etc.)
        name: Task name
        goal: User-visible outcome description
        status: Task lifecycle status
        steps: List of steps

    Computed Properties:
        current_step: ID of first non-complete step
        progress: Progress string (e.g., "2/5")
        progress_percent: Progress as percentage
    """
    id: str = Field(..., pattern=r"^T\d{3}$", description="Task ID (T001, T002, ...)")
    name: str = Field(..., min_length=1, max_length=200, description="Task name")
    goal: str = Field(..., min_length=1, max_length=1000, description="User-visible outcome")
    status: TaskStatus = Field(default=TaskStatus.PLANNED, description="Task status")
    steps: List[Step] = Field(default_factory=list, description="Task steps")

    class Config:
        use_enum_values = True
        extra = 'allow'  # Allow extra fields (priority, milestones, etc.)

    @property
    def current_step(self) -> Optional[str]:
        """Get the ID of the first non-complete step."""
        for step in self.steps:
            if step.status != StepStatus.COMPLETE:
                return step.id
        return None

    @property
    def progress(self) -> str:
        """Get progress as 'completed/total' string."""
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETE)
        return f"{completed}/{len(self.steps)}"

    @property
    def progress_percent(self) -> int:
        """Get progress as percentage (0-100)."""
        if not self.steps:
            return 0
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETE)
        return int(completed / len(self.steps) * 100)

    @property
    def completed_steps(self) -> int:
        """Get count of completed steps."""
        return sum(1 for s in self.steps if s.status == StepStatus.COMPLETE)

    @property
    def total_steps(self) -> int:
        """Get total number of steps."""
        return len(self.steps)

    def get_step(self, step_id: str) -> Optional[Step]:
        """Get step by ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None


class Scope(BaseModel):
    """
    Blueprint scope - collection of all tasks.

    Attributes:
        version: Schema version for compatibility
        updated: Last update timestamp
        tasks: List of task IDs in priority order
    """
    version: str = Field(default="1.0", description="Schema version")
    updated: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Last update")
    tasks: List[str] = Field(default_factory=list, description="Task IDs in priority order")

    def add_task(self, task_id: str) -> None:
        """Add a task ID to the scope."""
        if task_id not in self.tasks:
            self.tasks.append(task_id)

    def remove_task(self, task_id: str) -> None:
        """Remove a task ID from the scope."""
        if task_id in self.tasks:
            self.tasks.remove(task_id)


# Progress Marker Parsing
class ProgressMarker(BaseModel):
    """
    Parsed progress marker from agent message.

    Format: progress: <target> <action>[: <reason>]
    Examples:
        progress: T001 start
        progress: T001.S1 done
        progress: T001.S2 blocked: waiting for API
        progress: T001 promoted
    """
    target: str = Field(..., description="Task or step target (T001 or T001.S1)")
    action: str = Field(..., description="Action (start, done, blocked, promoted)")
    reason: Optional[str] = Field(default=None, description="Optional reason for blocked")

    @property
    def task_id(self) -> str:
        """Extract task ID from target."""
        if '.' in self.target:
            return self.target.split('.')[0]
        return self.target

    @property
    def step_id(self) -> Optional[str]:
        """Extract step ID from target, if present."""
        if '.' in self.target:
            return self.target.split('.')[1]
        return None

    @property
    def is_task_level(self) -> bool:
        """Check if this marker targets a task (not a step)."""
        return '.' not in self.target


# Marker pattern: progress: <target> <action>[: <reason>]
# Target format: T### (task) or T###.S# (step)
# Examples: T001, T001.S1, T001.S2, T012.S10
MARKER_PATTERN = re.compile(
    r'progress:\s*(T\d{3}(?:\.S\d+)?)\s+(\w+)(?::\s*(.+?))?(?:\n|$)',
    re.IGNORECASE | re.MULTILINE
)


def parse_progress_markers(text: str) -> List[ProgressMarker]:
    """
    Parse all progress markers from a message.

    Args:
        text: Message text to parse

    Returns:
        List of ProgressMarker objects, in order of appearance

    Examples:
        >>> parse_progress_markers("progress: T001 start")
        [ProgressMarker(target='T001', action='start', reason=None)]

        >>> parse_progress_markers("progress: T001.S1 done\\nprogress: T001.S2 in_progress")
        [ProgressMarker(target='T001.S1', action='done', ...), ...]
    """
    markers = []
    for match in MARKER_PATTERN.finditer(text):
        target, action, reason = match.groups()
        # Normalize target format (ensure uppercase)
        target = target.upper()
        # Normalize action
        action = action.lower()
        # Strip reason whitespace
        reason = reason.strip() if reason else None

        markers.append(ProgressMarker(
            target=target,
            action=action,
            reason=reason
        ))

    return markers


def validate_task_id(task_id: str) -> bool:
    """Validate task ID format (T001-T999)."""
    return bool(re.match(r'^T\d{3}$', task_id))


def validate_step_id(step_id: str) -> bool:
    """Validate step ID format (S1-S99)."""
    return bool(re.match(r'^S\d{1,2}$', step_id))


def generate_next_task_id(existing_ids: List[str]) -> str:
    """
    Generate the next task ID based on existing IDs.

    Args:
        existing_ids: List of existing task IDs

    Returns:
        Next available task ID (T001, T002, etc.)
    """
    if not existing_ids:
        return "T001"

    # Extract numeric parts and find max
    max_num = 0
    for tid in existing_ids:
        match = re.match(r'^T(\d{3})$', tid)
        if match:
            max_num = max(max_num, int(match.group(1)))

    return f"T{max_num + 1:03d}"
