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
- With MCP: Agent uses ccontext tools; Without MCP: Agent edits files directly
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
    DONE = "done"


class TaskStatus(str, Enum):
    """Task lifecycle status.

    Simple 3-state model:
    - planned: Initial draft / planning stage
    - active: Work in progress
    - done: All steps done

    PeerA creates tasks; both peers can update status and content.
    """
    PLANNED = "planned"           # Draft, initial planning
    ACTIVE = "active"             # Work in progress
    DONE = "done"                 # All steps done


class StepOutput(BaseModel):
    """
    Output/deliverable from a completed step.
    
    Attributes:
        path: Relative path to output file
        note: Brief description of the output
    """
    path: str = Field(..., description="Relative path to output file")
    note: str = Field(default="", description="Brief description")
    
    class Config:
        extra = 'allow'


class Step(BaseModel):
    """
    Individual task step with acceptance criteria.

    Attributes:
        id: Step identifier (S1, S2, S1.1, etc.)
        name: Brief description of the step
        acceptance: Acceptance criteria (e.g., "Tests pass", "API documented")
        status: Current execution status
        outputs: Optional list of deliverables (for done steps)
        progress: Optional progress note (for in-progress steps)
    """
    # Flexible step ID - will be normalized to S1, S2 format
    id: str = Field(..., description="Step ID (S1, S2, S1.1, ...)")
    name: str = Field(..., min_length=1, max_length=200, description="Step description")
    acceptance: str = Field(default="", max_length=500, description="Acceptance criteria")
    status: StepStatus = Field(default=StepStatus.PENDING, description="Step status")
    # Optional: deliverables when step is complete
    outputs: Optional[List[Union[StepOutput, Dict[str, str]]]] = Field(default=None, description="Step outputs/deliverables")
    # Optional: progress note for in-progress steps
    progress: Optional[str] = Field(default=None, description="Current progress note")

    class Config:
        use_enum_values = True
        extra = 'allow'  # Allow extra fields (current_progress, remaining_work, etc.)
    
    def get_outputs_list(self) -> List[Dict[str, str]]:
        """Get outputs as list of dicts (normalized)."""
        if not self.outputs:
            return []
        result = []
        for out in self.outputs:
            if isinstance(out, StepOutput):
                result.append({"path": out.path, "note": out.note})
            elif isinstance(out, dict):
                result.append({"path": out.get("path", ""), "note": out.get("note", "")})
        return result


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
        """Get the ID of the first non-done step."""
        for step in self.steps:
            if step.status != StepStatus.DONE:
                return step.id
        return None

    @property
    def progress(self) -> str:
        """Get progress as 'done/total' string."""
        done_count = sum(1 for s in self.steps if s.status == StepStatus.DONE)
        return f"{done_count}/{len(self.steps)}"

    @property
    def progress_percent(self) -> int:
        """Get progress as percentage (0-100)."""
        if not self.steps:
            return 0
        done_count = sum(1 for s in self.steps if s.status == StepStatus.DONE)
        return int(done_count / len(self.steps) * 100)

    @property
    def completed_steps(self) -> int:
        """Get count of done steps."""
        return sum(1 for s in self.steps if s.status == StepStatus.DONE)

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
