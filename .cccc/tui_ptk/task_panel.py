# -*- coding: utf-8 -*-
"""
Blueprint Task Panel - WBS-style task visualization for TUI.

Provides clear visualization of:
- Total tasks planned
- Tasks completed
- Current progress
- Step-level details

Design principles:
- Single source of truth: context/tasks/T###.yaml files
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
            root: Project root directory (contains context/)
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

    def get_display_item_count(self) -> Tuple[int, int]:
        """
        Get count of display items (tasks + error entries) and error count.
        
        Returns:
            Tuple of (total_display_items, error_count)
            - total_display_items: tasks + error entries (for navigation/height)
            - error_count: number of parse errors (for error line display)
        """
        summary = self._get_summary()
        tasks = summary.get('tasks', [])
        parse_errors = summary.get('parse_errors', {})
        
        # Count items that will be displayed
        seen_ids = set(t.get('id', '???') for t in tasks)
        error_entries = sum(1 for task_id in parse_errors if task_id not in seen_ids)
        
        total_display_items = len(tasks) + error_entries
        return total_display_items, len(parse_errors)

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
        Get rich header text for status bar showing current task progress.
        
        Format: "Tasks 2/5 (50%) │ → T003: Task Name [S2/4]"
        """
        summary = self._get_summary()
        
        if summary['total_tasks'] == 0:
            return ""  # Empty when no tasks
        
        completed = summary['completed_tasks']
        total = summary['total_tasks']
        
        # Calculate step-level progress
        total_steps = summary.get('total_steps', 0)
        completed_steps = summary.get('completed_steps', 0)
        percent = int(completed_steps / total_steps * 100) if total_steps > 0 else 0
        
        # Base progress info
        base = f"📋 {completed}/{total} ({percent}%)"
        
        # Current task info with name
        current_id = summary.get('current_task')
        current_step = summary.get('current_step')
        
        if current_id:
            # Get current task name
            manager = self._get_manager()
            task_name = ""
            step_progress = ""
            if manager:
                task = manager.get_task(current_id)
                if task:
                    task_name = task.name[:25]  # Truncate long names
                    # Step progress
                    step_progress = f"[{task.completed_steps}/{task.total_steps}]"
            
            if task_name:
                current_str = f"→ {current_id}: {task_name} {step_progress}"
            else:
                current_str = f"→ {current_id}"
        elif completed == total and total > 0:
            current_str = "✓ All complete"
        else:
            # No active task - show first planned task if any
            tasks = summary.get('tasks', [])
            planned_task = None
            for t in tasks:
                if t.get('status') and str(t['status']).lower() == 'planned':
                    planned_task = t
                    break
            if planned_task:
                name = planned_task.get('name', '')[:20]
                current_str = f"⏳ {planned_task['id']}: {name}" if name else f"⏳ {planned_task['id']}"
            else:
                current_str = ""
        
        if current_str:
            return f"{base} │ {current_str}"
        return base

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
        parse_errors = summary.get('parse_errors', {})
        
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
        
        # Collect all displayable items (tasks + errors)
        display_items = []
        seen_ids = set()
        
        # Add valid tasks
        for t in tasks:
            task_id = t.get('id', '???')
            seen_ids.add(task_id)
            display_items.append({
                'type': 'task',
                'id': task_id,
                'name': t.get('name', 'Unnamed'),
                'status': t.get('status', 'planned'),
                'progress': t.get('progress', '0/0'),
            })
        
        # Add error entries for tasks that failed to parse
        for task_id, error_msg in parse_errors.items():
            if task_id not in seen_ids:
                # Extract short error description
                short_error = error_msg[:35] + "..." if len(error_msg) > 35 else error_msg
                display_items.append({
                    'type': 'error',
                    'id': task_id,
                    'name': f"⚠ {short_error}",
                    'status': 'error',
                    'progress': '-',
                    'error': error_msg,
                })
        
        # Sort by task ID
        display_items.sort(key=lambda x: x['id'])
        
        if not display_items:
            # No tasks message
            add_row("   No tasks defined. Agent will create tasks in context/tasks/")
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
            selected_idx = max(0, min(self.selected_index, len(display_items) - 1))
            self.selected_index = selected_idx
            
            # Task/Error rows
            for idx, item in enumerate(display_items):
                task_id = item['id']
                name = item['name']
                progress = item['progress']
                item_type = item.get('type', 'task')
                
                # Status icon
                if item_type == 'error':
                    icon = '!'  # Error indicator
                else:
                    icon = self._get_status_icon(item['status'])
                
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
        valid_task_count = len(tasks)
        error_count = len(parse_errors)
        
        if valid_task_count > 0 or error_count > 0:
            # Calculate stats (only from valid tasks)
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
            
            # Error warning line (if any)
            if error_count > 0:
                error_warn = f"   ⚠ {error_count} task(s) have YAML errors - check context/tasks/"
                add_row(error_warn)
            
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
        Clean, readable format with good spacing and proper text wrapping.
        
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
        
        # Task name (title shown in dialog frame) - wrap if needed
        task_name = task.name
        if _display_width(task_name) > width - 6:
            task_name = _truncate_to_width(task_name, width - 6)
        lines.append(f"  {task_name}")
        lines.append("")
        
        # Goal - wrap if too long using proper word wrapping
        lines.append("  Goal:")
        goal_text = task.goal
        goal_max_width = width - 6  # Account for indent
        
        # Word wrap helper function
        def wrap_text(text: str, max_width: int, indent: str = "    ") -> list:
            """Wrap text to max_width, handling both ASCII and wide characters."""
            wrapped_lines = []
            words = text.split()
            current_line = indent
            current_width = _display_width(indent)
            
            for word in words:
                word_width = _display_width(word)
                space_width = 1 if current_line != indent else 0
                
                if current_width + space_width + word_width <= max_width:
                    if current_line != indent:
                        current_line += " "
                        current_width += 1
                    current_line += word
                    current_width += word_width
                else:
                    if current_line != indent:
                        wrapped_lines.append(current_line)
                    current_line = indent + word
                    current_width = _display_width(indent) + word_width
            
            if current_line.strip():
                wrapped_lines.append(current_line)
            
            return wrapped_lines if wrapped_lines else [indent + text]
        
        goal_lines = wrap_text(goal_text, goal_max_width, "    ")
        lines.extend(goal_lines)
        lines.append("")
        
        # Status and progress
        lines.append(f"  Status: {task.status}    Progress: {task.progress} ({task.progress_percent}%)")
        lines.append("")
        
        # Steps - use fixed column widths for alignment
        lines.append("  Steps:")
        
        # Calculate available width for step name
        # Layout: indent(4) + icon(1) + space(1) + id(3) + space(2) + name + space(2) + [status](9)
        # Total fixed: 4 + 1 + 1 + 3 + 2 + 2 + 9 = 22
        step_name_width = max(25, width - 24)
        
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
            
            # Truncate step name if needed, pad for alignment
            step_name = step.name
            if _display_width(step_name) > step_name_width:
                step_name = _truncate_to_width(step_name, step_name_width)
            step_name_padded = _pad_to_width(step_name, step_name_width)
            
            # Format: "    ✓ S1  Step name padded           [done]"
            # Use fixed-width status label for alignment
            status_display = f"[{status_label}]"
            lines.append(f"    {icon} {step.id}  {step_name_padded}  {status_display}")
            
            # Show progress note for in-progress steps
            if status == 'in_progress' and hasattr(step, 'progress') and step.progress:
                progress_text = step.progress
                if _display_width(progress_text) > width - 12:
                    progress_text = _truncate_to_width(progress_text, width - 12)
                lines.append(f"         └─ {progress_text}")
            
            # Show outputs for completed steps (if available)
            if status == 'complete' and hasattr(step, 'get_outputs_list'):
                outputs = step.get_outputs_list()
                if outputs:
                    for out in outputs[:2]:  # Limit to 2 outputs per step
                        path = out.get('path', '')
                        note = out.get('note', '')
                        if path:
                            out_text = f"📄 {path}"
                            if note:
                                out_text += f" ({note[:20]})" if len(note) > 20 else f" ({note})"
                            if _display_width(out_text) > width - 12:
                                out_text = _truncate_to_width(out_text, width - 12)
                            lines.append(f"         └─ {out_text}")
        
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
        """Format summary for IM - comprehensive view with all steps."""
        summary = self._get_summary()
        parse_errors = summary.get('parse_errors', {})
        
        if summary['total_tasks'] == 0 and not parse_errors:
            return "📋 Blueprint Status\n\nNo tasks defined.\nCreate tasks in context/tasks/T001.yaml"
        
        lines = ["━━━ Blueprint Tasks ━━━", ""]
        
        # Get full task data for detailed view
        manager = self._get_manager()
        tasks_data = manager.list_tasks() if manager else []
        
        for t in summary['tasks']:
            task_id = t.get('id', '???')
            name = t.get('name', 'Unnamed')
            status = t.get('status', 'planned')
            icon = self._get_status_icon(status)
            progress = t.get('progress', '0/0')
            
            # Task header with full name
            lines.append(f"{icon} {task_id}: {name}")
            lines.append(f"   Status: {status} │ Progress: {progress}")
            
            # Get steps for this task
            task_obj = None
            if manager:
                task_obj = manager.get_task(task_id)
            
            if task_obj and task_obj.steps:
                lines.append("   Steps:")
                for step in task_obj.steps:
                    if step.status == 'complete':
                        s_icon = '✓'
                    elif step.status == 'in_progress':
                        s_icon = '→'
                    else:
                        s_icon = '○'
                    
                    suffix = " ← current" if step.status == 'in_progress' else ""
                    # Truncate step name if too long for IM
                    step_name = step.name[:40] + "..." if len(step.name) > 40 else step.name
                    lines.append(f"   {s_icon} {step.id} {step_name}{suffix}")
                    
                    # Show progress note for in-progress steps (summary view - brief)
                    if step.status == 'in_progress' and hasattr(step, 'progress') and step.progress:
                        progress_text = step.progress[:40] + "..." if len(step.progress) > 40 else step.progress
                        lines.append(f"       └─ {progress_text}")
                    
                    # Show outputs for completed steps (summary view - only path)
                    if step.status == 'complete' and hasattr(step, 'get_outputs_list'):
                        outputs = step.get_outputs_list()
                        if outputs:
                            out = outputs[0]  # Only show first output in summary
                            path = out.get('path', '')
                            if path:
                                lines.append(f"       └─ 📄 {path}")
            
            lines.append("")  # Blank line between tasks
        
        # Show parse errors (if any)
        if parse_errors:
            lines.append("━━━ Parse Errors ━━━")
            for task_id, error_msg in parse_errors.items():
                # Truncate long error messages
                short_error = error_msg[:60] + "..." if len(error_msg) > 60 else error_msg
                lines.append(f"⚠ {task_id}: {short_error}")
            lines.append("")
        
        # Overall summary
        completed = summary.get('completed_tasks', 0)
        total = summary.get('total_tasks', 0)
        total_steps = summary.get('total_steps', 0)
        completed_steps = summary.get('completed_steps', 0)
        step_pct = int(completed_steps / total_steps * 100) if total_steps > 0 else 0
        
        lines.append("━━━ Summary ━━━")
        lines.append(f"Tasks: {completed}/{total} │ Steps: {completed_steps}/{total_steps} │ {step_pct}%")
        
        if parse_errors:
            lines.append(f"⚠ {len(parse_errors)} task(s) with YAML errors")
        
        current = summary.get('current_task')
        step = summary.get('current_step')
        if current:
            step_info = f" {step}" if step else ""
            lines.append(f"Current: {current}{step_info}")
        
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
            
            # Show progress note for in-progress steps
            if step.status == 'in_progress' and hasattr(step, 'progress') and step.progress:
                progress_text = step.progress[:60] + "..." if len(step.progress) > 60 else step.progress
                lines.append(f"    └─ {progress_text}")
            
            # Show outputs for completed steps
            if step.status == 'complete' and hasattr(step, 'get_outputs_list'):
                outputs = step.get_outputs_list()
                for out in outputs[:2]:  # Limit to 2 outputs
                    path = out.get('path', '')
                    note = out.get('note', '')
                    if path:
                        out_text = f"📄 {path}"
                        if note:
                            out_text += f" ({note[:25]}...)" if len(note) > 25 else f" ({note})"
                        lines.append(f"    └─ {out_text}")
        
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
