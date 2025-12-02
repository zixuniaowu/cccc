# -*- coding: utf-8 -*-
"""
Blueprint Task Panel - WBS-style task visualization for TUI.

Provides clear visualization of:
- Total tasks planned
- Tasks completed
- Current progress
- Step-level details

Design principles:
- Single source of truth: task.yaml files
- WBS (Work Breakdown Structure) style display
- Consistent across TUI and IM interfaces
- Professional alignment and spacing
"""
from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _display_width(s: str) -> int:
    """Calculate display width of string, accounting for wide characters."""
    width = 0
    for char in s:
        # East Asian Width: W (Wide) and F (Fullwidth) take 2 columns
        if unicodedata.east_asian_width(char) in ('W', 'F'):
            width += 2
        else:
            width += 1
    return width


def _pad_to_width(s: str, target_width: int, fill: str = ' ') -> str:
    """Pad string to target display width."""
    current = _display_width(s)
    if current >= target_width:
        return s
    return s + fill * (target_width - current)


def _truncate_to_width(s: str, max_width: int, ellipsis: str = '…') -> str:
    """Truncate string to max display width."""
    if _display_width(s) <= max_width:
        return s
    
    result = ''
    width = 0
    ellipsis_width = _display_width(ellipsis)
    
    for char in s:
        char_width = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if width + char_width + ellipsis_width > max_width:
            return result + ellipsis
        result += char
        width += char_width
    
    return result


class TaskPanel:
    """
    Task Panel widget for CCCC TUI.
    
    Provides three display levels:
    - Level 0 (Header): Compact status bar line - always visible
    - Level 1 (Expanded): Full task list with progress - toggle with [T]
    - Level 2 (Detail): Single task with all steps - via /task command
    """

    def __init__(self, root: Path, on_toggle: Optional[Callable[[], None]] = None):
        """
        Initialize Task Panel.

        Args:
            root: Project root directory (contains docs/por/)
            on_toggle: Optional callback when panel is toggled
        """
        self.root = root
        self.expanded = False
        self.on_toggle = on_toggle
        self._manager = None
        # Navigation state for Level 1
        self.selected_index = 0
        self._cached_task_count = 0

    def _get_manager(self):
        """Lazy-load TaskManager with proper path setup."""
        if self._manager is None:
            try:
                import sys
                cccc_dir = self.root / '.cccc'
                if cccc_dir.exists() and str(cccc_dir) not in sys.path:
                    sys.path.insert(0, str(cccc_dir))
                
                from orchestrator.task_manager import TaskManager
                self._manager = TaskManager(self.root)
            except ImportError as e:
                # Common issue: pydantic not installed in current venv
                if 'pydantic' in str(e):
                    import sys
                    print("[TaskPanel] pydantic not found. Install with: pip install pydantic>=2.0", file=sys.stderr)
            except Exception:
                pass
        return self._manager

    def _get_summary(self) -> Dict[str, Any]:
        """Get task summary from TaskManager."""
        manager = self._get_manager()
        if not manager:
            return self._empty_summary()

        try:
            return manager.get_summary()
        except Exception:
            return self._empty_summary()

    def _empty_summary(self) -> Dict[str, Any]:
        """Return empty summary structure."""
        return {
            'total_tasks': 0,
            'completed_tasks': 0,
            'active_tasks': 0,
            'planned_tasks': 0,
            'current_task': None,
            'current_step': None,
            'total_steps': 0,
            'completed_steps': 0,
            'progress_percent': 0,
            'tasks': []
        }

    def toggle(self) -> None:
        """Toggle between expanded and collapsed view."""
        self.expanded = not self.expanded
        # Reset selection when closing panel
        if not self.expanded:
            self.selected_index = 0
        if self.on_toggle:
            self.on_toggle()

    def refresh(self) -> None:
        """Force refresh of task data."""
        if self._manager:
            self._manager._sync_scope()

    # =========================================================================
    # Navigation Methods (for Level 1 keyboard navigation)
    # =========================================================================

    def get_task_count(self) -> int:
        """Get number of tasks for navigation bounds."""
        summary = self._get_summary()
        return len(summary.get('tasks', []))

    def select_next(self) -> bool:
        """Move selection to next task. Returns True if moved."""
        count = self.get_task_count()
        if count == 0:
            return False
        if self.selected_index < count - 1:
            self.selected_index += 1
            return True
        return False

    def select_prev(self) -> bool:
        """Move selection to previous task. Returns True if moved."""
        count = self.get_task_count()
        if count == 0:
            return False
        if self.selected_index > 0:
            self.selected_index -= 1
            return True
        return False

    def get_selected_task_id(self) -> Optional[str]:
        """Get the task ID of currently selected task."""
        summary = self._get_summary()
        tasks = summary.get('tasks', [])
        if not tasks:
            return None
        # Clamp index to valid range
        idx = max(0, min(self.selected_index, len(tasks) - 1))
        self.selected_index = idx  # Update in case it was out of bounds
        return tasks[idx].get('id')

    def reset_selection(self) -> None:
        """Reset selection to first task."""
        self.selected_index = 0

    # =========================================================================
    # Level 0: Header Strip (Always Visible in Status Bar)
    # =========================================================================

    def get_header_text(self) -> str:
        """
        Get compact header text for status bar.
        
        Format: "📋 2/5 │ 50% │ → T003 [S2]"
        """
        summary = self._get_summary()
        
        if summary['total_tasks'] == 0:
            return "📋 No tasks"
        
        completed = summary['completed_tasks']
        total = summary['total_tasks']
        
        # Calculate step-level progress
        total_steps = summary.get('total_steps', 0)
        completed_steps = summary.get('completed_steps', 0)
        percent = int(completed_steps / total_steps * 100) if total_steps > 0 else 0
        
        # Current task info
        current = summary.get('current_task')
        step = summary.get('current_step')
        
        if current:
            current_str = f"→ {current}"
            if step:
                current_str += f" [{step}]"
        elif completed == total:
            current_str = "✓ Done"
        else:
            current_str = "○ Idle"
        
        return f"📋 {completed}/{total} │ {percent}% │ {current_str}"

    # =========================================================================
    # Level 1: Expanded WBS View (Toggle with [T])
    # =========================================================================

    def get_expanded_text(self, width: int = 80) -> str:
        """
        Get expanded task list with dynamic width.
        
        Args:
            width: Panel width (auto-detected from terminal)
        
        Layout (clean, professional):
        ┏━━ Blueprint Tasks ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ [T] ━━┓
        ┃                                                                      ┃
        ┃   St   ID      Name                                        Progress  ┃
        ┃   ──   ────    ──────────────────────────────────────────   ────────  ┃
        ┃   ✓    T001    OAuth Implementation Complete                  4/4    ┃
        ┃ ▶ →    T002    Dashboard Analytics Feature                    2/5    ┃  ← selected
        ┃   ○    T003    User Settings Panel                            0/3    ┃
        ┃                                                                      ┃
        ┃   Progress: 1/3 tasks │ 6/12 steps (50%) │ Current: T002            ┃
        ┃   ↑↓ select │ Enter detail │ Click task │ Esc close                  ┃
        ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
        """
        summary = self._get_summary()
        tasks = summary.get('tasks', [])
        
        # Use provided width, ensure minimum
        # INNER is the content width inside the borders (excluding the two ┃ chars)
        INNER = max(58, width - 4)  # -4 for borders and margin
        
        lines = []
        
        def add_row(content: str, highlight: bool = False) -> None:
            """Add a row with proper padding and border."""
            content_width = _display_width(content)
            padding = INNER - content_width
            if padding > 0:
                content = content + ' ' * padding
            lines.append(f"┃{content}┃")
        
        # Top border with title (must match INNER width)
        title = "━━ Blueprint Tasks "
        toggle_hint = " [T] ━━"
        title_len = len(title) + len(toggle_hint)
        border_fill = INNER - title_len
        if border_fill < 0:
            border_fill = 0
        lines.append(f"┏{title}{'━' * border_fill}{toggle_hint}┓")
        
        # Empty line
        add_row("")
        
        if not tasks:
            # No tasks message
            add_row("   No tasks defined. Agent will create tasks in docs/por/")
            add_row("")
        else:
            # Calculate dynamic name width based on panel width
            # Layout: 3(indent) + 2(St) + 3(space) + 5(ID) + 4(space) + NAME + 4(space) + 8(progress) + 3(margin)
            name_width = INNER - 32
            name_width = max(20, min(60, name_width))
            
            # Column headers
            header = f"   St   ID      {'Name':<{name_width}}    Progress"
            add_row(header)
            sep = f"   ──   ────    {'─' * name_width}    ────────"
            add_row(sep)
            
            # Clamp selected_index to valid range
            selected_idx = max(0, min(self.selected_index, len(tasks) - 1))
            self.selected_index = selected_idx
            
            # Task rows
            for idx, t in enumerate(tasks):
                status = t.get('status', 'planned')
                task_id = t.get('id', '???')
                name = t.get('name', 'Unnamed')
                progress = t.get('progress', '0/0')
                
                # Status icon
                icon = self._get_status_icon(status)
                
                # Truncate and pad name
                name_display = _truncate_to_width(name, name_width)
                name_padded = _pad_to_width(name_display, name_width)
                
                # Format progress (right-aligned, 8 chars)
                prog_display = f"{progress:>8}"
                
                # Selection indicator
                is_selected = (idx == selected_idx)
                sel = "▶" if is_selected else " "
                
                # Build row
                if is_selected:
                    # Highlight selected row with visual indicator
                    row = f" {sel} {icon}    {task_id:<5}   {name_padded}    {prog_display}"
                else:
                    row = f"   {icon}    {task_id:<5}   {name_padded}    {prog_display}"
                
                add_row(row, highlight=is_selected)
        
        # Empty line
        add_row("")
        
        # Summary and help section
        if tasks:
            # Calculate stats
            completed = summary.get('completed_tasks', 0)
            total = summary.get('total_tasks', 0)
            total_steps = summary.get('total_steps', 0)
            completed_steps = summary.get('completed_steps', 0)
            step_pct = int(completed_steps / total_steps * 100) if total_steps > 0 else 0
            current = summary.get('current_task', '')
            
            # Progress line
            progress_info = f"   Progress: {completed}/{total} tasks │ {completed_steps}/{total_steps} steps ({step_pct}%)"
            if current:
                progress_info += f" │ Current: {current}"
            add_row(progress_info)
            
            # Help line
            add_row("   ↑↓/click select │ Enter → detail │ Esc/T close")
        
        # Bottom border (must match INNER width)
        lines.append(f"┗{'━' * INNER}┛")
        
        return "\n".join(lines)

    def _get_status_display(self, status: str) -> Tuple[str, str]:
        """Get icon and short label for status."""
        mapping = {
            'complete': ('✓', 'done'),
            'active': ('→', 'work'),
            'in_progress': ('→', 'work'),
            'pending_review': ('⏳', 'rev'),
            'planned': ('○', 'plan'),
        }
        return mapping.get(status, ('○', 'plan'))

    def _get_status_icon(self, status: str) -> str:
        """Get icon for task status."""
        icon, _ = self._get_status_display(status)
        return icon

    def _get_step_name(self, task: Dict, step_id: str) -> str:
        """Get step name from task data."""
        manager = self._get_manager()
        if not manager:
            return ""
        
        try:
            task_obj = manager.get_task(task.get('id', ''))
            if task_obj:
                step = task_obj.get_step(step_id)
                if step:
                    return step.name
        except Exception:
            pass
        return ""

    # =========================================================================
    # Level 2: Task Detail (via /task T003 command)
    # =========================================================================

    def get_task_detail(self, task_id: str) -> str:
        """
        Get detailed view of a specific task with all steps.
        
        Layout:
        ╭─ T003: Dashboard Feature ─────────────────────────╮
        │                                                   │
        │  Goal: Implement main dashboard with analytics    │
        │  Status: active  │  Progress: 2/5 (40%)           │
        │                                                   │
        │  Steps:                                           │
        │  ✓ S1  Design wireframes              [done]      │
        │  ✓ S2  Create database schema         [done]      │
        │  → S3  Build API endpoints            [working]   │
        │  ○ S4  Frontend components            [pending]   │
        │  ○ S5  Integration testing            [pending]   │
        │                                                   │
        ╰───────────────────────────────────────────────────╯
        """
        manager = self._get_manager()
        if not manager:
            return "Task manager not available"
        
        task = manager.get_task(task_id)
        if not task:
            return f"Task {task_id} not found"
        
        WIDTH = 53  # Inner content width
        lines = []
        
        def add_row(content: str) -> None:
            """Add a row with proper padding and border."""
            content_width = _display_width(content)
            padding = WIDTH - content_width
            if padding > 0:
                content = content + ' ' * padding
            lines.append(f"│{content}│")
        
        # Top border with task ID and name
        title_text = f" {task.id}: {_truncate_to_width(task.name, 38)} "
        border_len = WIDTH - _display_width(title_text)
        lines.append(f"╭─{title_text}{'─' * border_len}╮")
        
        # Empty line
        add_row("")
        
        # Goal
        goal_text = _truncate_to_width(task.goal, WIDTH - 10)
        add_row(f"  Goal: {goal_text}")
        
        # Status and progress
        status_line = f"  Status: {task.status}  │  Progress: {task.progress} ({task.progress_percent}%)"
        add_row(status_line)
        
        # Empty line
        add_row("")
        
        # Steps header
        add_row("  Steps:")
        
        # Step list
        for step in task.steps:
            status = str(step.status)
            
            # Icon based on status
            if status == 'complete':
                icon = '✓'
                status_label = '[done]'
            elif status == 'in_progress':
                icon = '→'
                status_label = '[working]'
            elif status == 'blocked':
                icon = '!'
                status_label = '[blocked]'
            else:
                icon = '○'
                status_label = '[pending]'
            
            # Name max width: total - 2(indent) - 2(icon+space) - 3(S#) - 2(spaces) - 9(label) = 35
            name_max = 35
            step_name = _truncate_to_width(step.name, name_max)
            step_name_padded = _pad_to_width(step_name, name_max)
            
            # Format: "  ✓ S1 Step name padded here           [done]"
            step_line = f"  {icon} {step.id} {step_name_padded} {status_label}"
            add_row(step_line)
        
        # Empty line
        add_row("")
        
        # Bottom border
        lines.append(f"╰{'─' * WIDTH}╯")
        
        return "\n".join(lines)

    def get_task_detail_plain(self, task_id: str, width: int = 80) -> str:
        """
        Get task detail without box borders (for use in Dialog).
        Clean, readable format with good spacing.
        
        Args:
            task_id: Task ID to show
            width: Available content width for formatting
        """
        manager = self._get_manager()
        if not manager:
            return "Task manager not available"
        
        task = manager.get_task(task_id)
        if not task:
            return f"Task {task_id} not found"
        
        lines = []
        
        # Task name (title shown in dialog frame)
        lines.append(f"  {task.name}")
        lines.append("")
        
        # Goal - wrap if too long
        lines.append("  Goal:")
        goal_text = task.goal
        goal_width = width - 6  # Account for indent
        if len(goal_text) > goal_width:
            # Simple word wrap
            words = goal_text.split()
            current_line = "    "
            for word in words:
                if len(current_line) + len(word) + 1 > width - 2:
                    lines.append(current_line)
                    current_line = "    " + word
                else:
                    current_line += (" " if len(current_line) > 4 else "") + word
            if current_line.strip():
                lines.append(current_line)
        else:
            lines.append(f"    {goal_text}")
        lines.append("")
        
        # Status and progress
        lines.append(f"  Status: {task.status}    Progress: {task.progress} ({task.progress_percent}%)")
        lines.append("")
        
        # Steps - use dynamic width
        lines.append("  Steps:")
        # Calculate step name width: width - indent(4) - icon(2) - id(4) - label(12) - margins(6)
        step_name_width = max(30, width - 28)
        
        for step in task.steps:
            status = str(step.status)
            
            # Icon based on status
            if status == 'complete':
                icon = '✓'
                status_label = 'done'
            elif status == 'in_progress':
                icon = '→'
                status_label = 'working'
            elif status == 'blocked':
                icon = '!'
                status_label = 'blocked'
            else:
                icon = '○'
                status_label = 'pending'
            
            # Format step line with dynamic width
            name_display = step.name[:step_name_width]
            lines.append(f"    {icon} {step.id}  {name_display:<{step_name_width}}  [{status_label}]")
        
        lines.append("")
        lines.append("  Press Esc to close")
        
        return "\n".join(lines)

    # =========================================================================
    # IM-friendly output (for /task command in bridges)
    # =========================================================================

    def format_for_im(self, task_id: Optional[str] = None) -> str:
        """
        Format task status for IM display.
        
        Args:
            task_id: If provided, show detail for specific task
            
        Returns:
            IM-friendly formatted string
        """
        if task_id:
            return self._format_task_detail_im(task_id)
        return self._format_summary_im()

    def _format_summary_im(self) -> str:
        """Format summary for IM."""
        summary = self._get_summary()
        
        if summary['total_tasks'] == 0:
            return "📋 Blueprint Status\n\nNo tasks defined.\nCreate tasks in docs/por/T001-name/task.yaml"
        
        lines = ["📋 Blueprint Status", ""]
        
        # Task list
        for t in summary['tasks']:
            icon = self._get_status_icon(t.get('status', 'planned'))
            task_id = t.get('id', '???')
            name = t.get('name', 'Unnamed')[:18]
            progress = t.get('progress', '0/0')
            
            current_step = t.get('current_step', '')
            if t.get('status') == 'active' and current_step:
                line = f"{icon} {task_id} {name:<18} {progress}  ← {current_step}"
            else:
                line = f"{icon} {task_id} {name:<18} {progress}"
            
            lines.append(line)
        
        lines.append("")
        
        # Summary
        completed = summary.get('completed_tasks', 0)
        total = summary.get('total_tasks', 0)
        total_steps = summary.get('total_steps', 0)
        completed_steps = summary.get('completed_steps', 0)
        step_pct = int(completed_steps / total_steps * 100) if total_steps > 0 else 0
        
        lines.append(f"Progress: {completed}/{total} tasks │ {completed_steps}/{total_steps} steps │ {step_pct}%")
        
        current = summary.get('current_task')
        step = summary.get('current_step')
        if current:
            # Get step name
            step_name = ""
            for t in summary['tasks']:
                if t.get('id') == current:
                    step_name = self._get_step_name(t, step) if step else ""
                    break
            
            if step_name:
                lines.append(f"Current: {current} {step} - {step_name}")
            elif step:
                lines.append(f"Current: {current} {step}")
            else:
                lines.append(f"Current: {current}")
        
        return "\n".join(lines)

    def _format_task_detail_im(self, task_id: str) -> str:
        """Format task detail for IM."""
        manager = self._get_manager()
        if not manager:
            return "Task manager not available"
        
        task = manager.get_task(task_id)
        if not task:
            return f"Task {task_id} not found"
        
        lines = [
            f"📋 {task.id}: {task.name}",
            "",
            f"Goal: {task.goal}",
            f"Status: {task.status} │ Progress: {task.progress} ({task.progress_percent}%)",
            "",
            "Steps:"
        ]
        
        for step in task.steps:
            if step.status == 'complete':
                icon = '✓'
            elif step.status == 'in_progress':
                icon = '→'
            else:
                icon = '○'
            
            suffix = " ← current" if step.status == 'in_progress' else ""
            lines.append(f"{icon} {step.id} {step.name}{suffix}")
        
        return "\n".join(lines)


# Convenience function for use outside TUI
def get_task_status(root: Path, task_id: Optional[str] = None) -> str:
    """
    Get task status as formatted string.
    
    Args:
        root: Project root directory
        task_id: Optional specific task ID
        
    Returns:
        Formatted task status string
    """
    panel = TaskPanel(root)
    return panel.format_for_im(task_id)
