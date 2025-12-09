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
    - Level 2 (Detail): Unified detail view with tabs (Milestones/Tasks/Notes/Refs)
    
    Level 1 Navigation Areas:
    - 'milestone': Current active milestone (top)
    - 'tasks': Task list (middle, default)
    - 'notes': Notes entry (bottom)
    - 'refs': References entry (bottom)
    """

    # Tab order for Level 2
    TABS = ['milestones', 'tasks', 'notes', 'refs']
    TAB_LABELS = {'milestones': 'Milestones', 'tasks': 'Tasks', 'notes': 'Notes', 'refs': 'Refs'}
    TAB_KEYS = {'milestones': 'M', 'tasks': 'T', 'notes': 'N', 'refs': 'R'}

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
        # Level 1 navigation area: 'milestone', 'tasks', 'notes', 'refs'
        self.selected_area = 'tasks'
        # Level 2 tab state
        self.current_tab = 'tasks'
        # Current task index for Level 2 Tasks tab navigation
        self.detail_task_index = 0

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
            self._manager.refresh()

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
        """
        Move selection down in Level 1.
        Navigation order: tasks (with internal index) -> notes -> refs -> (wrap to tasks)
        
        Returns:
            True if selection moved
        """
        task_count = self.get_task_count()
        
        if self.selected_area == 'tasks':
            if task_count > 0 and self.selected_index < task_count - 1:
                self.selected_index += 1
                return True
            # Move to notes
            self.selected_area = 'notes'
            return True
        elif self.selected_area == 'notes':
            self.selected_area = 'refs'
            return True
        elif self.selected_area == 'refs':
            # Wrap to tasks
            self.selected_area = 'tasks'
            self.selected_index = 0
            return True
        return False

    def select_prev(self) -> bool:
        """
        Move selection up in Level 1.
        Navigation order: refs -> notes -> tasks (with internal index)
        
        Returns:
            True if selection moved
        """
        task_count = self.get_task_count()
        
        if self.selected_area == 'tasks':
            if self.selected_index > 0:
                self.selected_index -= 1
                return True
            # Wrap to refs
            self.selected_area = 'refs'
            return True
        elif self.selected_area == 'notes':
            self.selected_area = 'tasks'
            self.selected_index = max(0, task_count - 1)
            return True
        elif self.selected_area == 'refs':
            self.selected_area = 'notes'
            return True
        return False

    def get_selected_task_id(self) -> Optional[str]:
        """Get the task ID of currently selected task (only if in tasks area)."""
        if self.selected_area != 'tasks':
            return None
        summary = self._get_summary()
        tasks = summary.get('tasks', [])
        if not tasks:
            return None
        idx = max(0, min(self.selected_index, len(tasks) - 1))
        self.selected_index = idx
        return tasks[idx].get('id')

    def get_selected_area(self) -> str:
        """Get currently selected area in Level 1."""
        return self.selected_area

    def reset_selection(self) -> None:
        """Reset selection to first task."""
        self.selected_index = 0
        self.selected_area = 'tasks'

    # =========================================================================
    # Level 2 Tab Navigation
    # =========================================================================

    def switch_tab(self, direction: int = 1) -> str:
        """
        Switch to next/previous tab.
        
        Args:
            direction: 1 for next (Tab), -1 for previous (Shift+Tab)
        
        Returns:
            New tab name
        """
        idx = self.TABS.index(self.current_tab)
        new_idx = (idx + direction) % len(self.TABS)
        self.current_tab = self.TABS[new_idx]
        return self.current_tab

    def set_tab(self, tab_name: str) -> bool:
        """
        Set current tab directly.
        
        Args:
            tab_name: One of 'milestones', 'tasks', 'notes', 'refs'
        
        Returns:
            True if tab was changed
        """
        if tab_name in self.TABS:
            self.current_tab = tab_name
            return True
        return False

    def get_tab_for_area(self, area: str) -> str:
        """Map Level 1 area to Level 2 tab."""
        mapping = {
            'milestone': 'milestones',
            'tasks': 'tasks',
            'notes': 'notes',
            'refs': 'refs',
        }
        return mapping.get(area, 'tasks')

    def next_task_in_detail(self) -> Optional[str]:
        """
        Move to next task in Level 2 Tasks tab.
        
        Returns:
            New task ID or None if at end
        """
        summary = self._get_summary()
        tasks = summary.get('tasks', [])
        if not tasks:
            return None
        if self.detail_task_index < len(tasks) - 1:
            self.detail_task_index += 1
            return tasks[self.detail_task_index].get('id')
        return None

    def prev_task_in_detail(self) -> Optional[str]:
        """
        Move to previous task in Level 2 Tasks tab.
        
        Returns:
            New task ID or None if at beginning
        """
        summary = self._get_summary()
        tasks = summary.get('tasks', [])
        if not tasks:
            return None
        if self.detail_task_index > 0:
            self.detail_task_index -= 1
            return tasks[self.detail_task_index].get('id')
        return None

    def get_current_detail_task_id(self) -> Optional[str]:
        """Get task ID for current position in Level 2 Tasks tab."""
        summary = self._get_summary()
        tasks = summary.get('tasks', [])
        if not tasks:
            return None
        idx = max(0, min(self.detail_task_index, len(tasks) - 1))
        self.detail_task_index = idx
        return tasks[idx].get('id')

    def set_detail_task_by_id(self, task_id: str) -> bool:
        """Set the detail task index by task ID."""
        summary = self._get_summary()
        tasks = summary.get('tasks', [])
        for i, t in enumerate(tasks):
            if t.get('id') == task_id:
                self.detail_task_index = i
                return True
        return False

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
        
        Layout:
        ┏━━ Blueprint Tasks ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ [T] ━━┓
        ┃ 🎯 M2: API Integration [active]                                      ┃  ← Milestone
        ┃ ─────────────────────────────────────────────────────────────────── ┃
        ┃   St   ID      Name                                        Progress  ┃
        ┃   ✓    T001    OAuth Implementation Complete                  4/4    ┃
        ┃ ▶ →    T002    Dashboard Analytics Feature                    2/5    ┃
        ┃   ○    T003    User Settings Panel                            0/3    ┃
        ┃                                                                      ┃
        ┃   Progress: 1/3 tasks │ 6/12 steps (50%)                            ┃
        ┃ ─────────────────────────────────────────────────────────────────── ┃
        ┃   📝 Notes (3)                                                       ┃  ← Entry
        ┃   📎 References (2)                                                  ┃  ← Entry
        ┃ ─────────────────────────────────────────────────────────────────── ┃
        ┃   ↑↓ select │ Enter detail │ T close                                 ┃
        ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
        """
        summary = self._get_summary()
        tasks = summary.get('tasks', [])
        parse_errors = summary.get('parse_errors', {})
        
        # Get context data
        manager = self._get_manager()
        active_milestone = manager.get_active_milestone() if manager else None
        notes = manager.get_notes() if manager else []
        refs = manager.get_references() if manager else []
        
        # Use provided width, ensure minimum
        INNER = max(58, width - 4)
        
        lines = []
        
        def add_row(content: str) -> None:
            """Add a row with proper padding and border."""
            content_width = _display_width(content)
            padding = INNER - content_width
            if padding > 0:
                content = content + ' ' * padding
            lines.append(f"┃{content}┃")
        
        def add_separator() -> None:
            """Add a thin separator line."""
            add_row(" " + "─" * (INNER - 2) + " ")
        
        # Top border with title
        title = "━━ Blueprint Tasks "
        toggle_hint = " [T] ━━"
        title_len = len(title) + len(toggle_hint)
        border_fill = INNER - title_len
        if border_fill < 0:
            border_fill = 0
        lines.append(f"┏{title}{'━' * border_fill}{toggle_hint}┓")
        
        # === Milestone Section ===
        if active_milestone:
            m_name = active_milestone.get('name', 'Unnamed')
            m_name_display = _truncate_to_width(m_name, INNER - 25)
            milestone_line = f" 🎯 {m_name_display} [active]"
            add_row(milestone_line)
            add_separator()
        
        # === Tasks Section ===
        # Collect all displayable items (tasks + errors)
        display_items = []
        seen_ids = set()
        
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
        
        for task_id, error_msg in parse_errors.items():
            if task_id not in seen_ids:
                short_error = error_msg[:35] + "..." if len(error_msg) > 35 else error_msg
                display_items.append({
                    'type': 'error',
                    'id': task_id,
                    'name': f"⚠ {short_error}",
                    'status': 'error',
                    'progress': '-',
                })
        
        display_items.sort(key=lambda x: x['id'])
        
        if not display_items:
            add_row("   No tasks defined. Agent will create tasks in context/tasks/")
        else:
            name_width = INNER - 32
            name_width = max(20, min(60, name_width))
            
            header = f"   St   ID      {'Name':<{name_width}}    Progress"
            add_row(header)
            sep = f"   ──   ────    {'─' * name_width}    ────────"
            add_row(sep)
            
            selected_idx = max(0, min(self.selected_index, len(display_items) - 1))
            self.selected_index = selected_idx
            
            for idx, item in enumerate(display_items):
                task_id = item['id']
                name = item['name']
                progress = item['progress']
                item_type = item.get('type', 'task')
                
                if item_type == 'error':
                    icon = '!'
                else:
                    icon = self._get_status_icon(item['status'])
                
                name_display = _truncate_to_width(name, name_width)
                name_padded = _pad_to_width(name_display, name_width)
                prog_display = f"{progress:>8}"
                
                is_selected = (idx == selected_idx) and (self.selected_area == 'tasks')
                sel = "▶" if is_selected else " "
                
                if is_selected:
                    row = f" {sel} {icon}    {task_id:<5}   {name_padded}    {prog_display}"
                else:
                    row = f"   {icon}    {task_id:<5}   {name_padded}    {prog_display}"
                
                add_row(row)
        
        add_row("")
        
        # Progress summary
        valid_task_count = len(tasks)
        error_count = len(parse_errors)
        
        if valid_task_count > 0 or error_count > 0:
            completed = summary.get('completed_tasks', 0)
            total = summary.get('total_tasks', 0)
            total_steps = summary.get('total_steps', 0)
            completed_steps = summary.get('completed_steps', 0)
            step_pct = int(completed_steps / total_steps * 100) if total_steps > 0 else 0
            current = summary.get('current_task', '')
            
            progress_info = f"   Progress: {completed}/{total} tasks │ {completed_steps}/{total_steps} steps ({step_pct}%)"
            if current:
                progress_info += f" │ {current}"
            add_row(progress_info)
            
            if error_count > 0:
                add_row(f"   ⚠ {error_count} task(s) have YAML errors")
        
        # === Notes/Refs Entry Section ===
        add_separator()
        
        # Notes entry
        notes_sel = "▶" if self.selected_area == 'notes' else " "
        notes_count = len(notes)
        add_row(f" {notes_sel} 📝 Notes ({notes_count})")
        
        # References entry  
        refs_sel = "▶" if self.selected_area == 'refs' else " "
        refs_count = len(refs)
        add_row(f" {refs_sel} 📎 References ({refs_count})")
        
        # Help line
        add_separator()
        add_row("   ↑↓ select │ Enter detail │ T close")
        
        # Bottom border
        lines.append(f"┗{'━' * INNER}┛")
        
        return "\n".join(lines)

    def _get_status_display(self, status: str) -> Tuple[str, str]:
        """Get icon and short label for status."""
        mapping = {
            'done': ('✓', 'done'),
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
    # Level 2: Unified Detail View (with Tab switching)
    # =========================================================================

    def get_detail_view(self, width: int = 80) -> str:
        """
        Get unified Level 2 detail view with tab bar.
        
        Args:
            width: Available width for rendering
        
        Returns:
            Formatted string with tab bar and current tab content
        """
        lines = []
        INNER = max(50, width - 4)
        
        def add_row(content: str) -> None:
            content_width = _display_width(content)
            padding = INNER - content_width
            if padding > 0:
                content = content + ' ' * padding
            lines.append(content)
        
        # Tab bar
        tab_bar = self._render_tab_bar(INNER)
        lines.append(tab_bar)
        lines.append("─" * INNER)
        
        # Content based on current tab
        if self.current_tab == 'milestones':
            content = self._render_milestones_content(INNER)
        elif self.current_tab == 'tasks':
            content = self._render_tasks_content(INNER)
        elif self.current_tab == 'notes':
            content = self._render_notes_content(INNER)
        elif self.current_tab == 'refs':
            content = self._render_refs_content(INNER)
        else:
            content = "Unknown tab"
        
        lines.append(content)
        
        # Footer with navigation hints
        lines.append("─" * INNER)
        if self.current_tab == 'tasks':
            lines.append("Tab: switch │ ← → prev/next task │ Esc: close")
        else:
            lines.append("Tab: switch │ Esc: close")
        
        return "\n".join(lines)

    def _render_tab_bar(self, width: int) -> str:
        """Render the tab bar with current tab highlighted."""
        parts = []
        for tab in self.TABS:
            key = self.TAB_KEYS[tab]
            label = self.TAB_LABELS[tab]
            if tab == self.current_tab:
                parts.append(f"[{key}]{label}")
            else:
                parts.append(f" {key} {label} ")
        return "  ".join(parts)

    def _render_milestones_content(self, width: int) -> str:
        """Render milestones tab content."""
        manager = self._get_manager()
        if not manager:
            return "  No data available"
        
        milestones = manager.get_milestones_for_display()
        if not milestones:
            return "  No milestones defined yet."
        
        lines = []
        for m in milestones:
            status = m.get('status', 'pending')
            name = m.get('name', 'Unnamed')
            m_id = m.get('id', '?')
            
            if status == 'done':
                icon = '✓'
                status_label = '[done]'
            elif status == 'active':
                icon = '→'
                status_label = '[active]'
            else:
                icon = '○'
                status_label = '[pending]'
            
            # Main line
            name_display = _truncate_to_width(name, width - 25)
            lines.append(f"  {icon} {m_id}: {name_display} {status_label}")
            
            # Additional info for done/active milestones
            if status == 'done':
                completed = m.get('completed', '')
                outcomes = m.get('outcomes', '')
                if completed:
                    lines.append(f"      Completed: {completed}")
                if outcomes:
                    outcomes_display = _truncate_to_width(outcomes, width - 16)
                    lines.append(f"      Outcomes: {outcomes_display}")
            elif status == 'active':
                started = m.get('started', '')
                desc = m.get('description', '')
                if started:
                    lines.append(f"      Started: {started}")
                if desc:
                    desc_display = _truncate_to_width(desc, width - 8)
                    lines.append(f"      {desc_display}")
            
            lines.append("")  # Blank line between milestones
        
        return "\n".join(lines) if lines else "  No milestones."

    def _render_tasks_content(self, width: int) -> str:
        """Render tasks tab content (current task detail with prev/next)."""
        task_id = self.get_current_detail_task_id()
        if not task_id:
            return "  No tasks defined yet."
        
        manager = self._get_manager()
        if not manager:
            return "  Task manager not available"
        
        task = manager.get_task(task_id)
        if not task:
            return f"  Task {task_id} not found"
        
        lines = []
        
        # Task header
        lines.append(f"  {task.id}: {_truncate_to_width(task.name, width - 10)}")
        lines.append("")
        lines.append(f"  Goal: {_truncate_to_width(task.goal, width - 10)}")
        lines.append(f"  Status: {task.status}  │  Progress: {task.progress} ({task.progress_percent}%)")
        lines.append("")
        lines.append("  Steps:")
        
        # Steps
        for step in task.steps:
            status = str(step.status)
            if status == 'done':
                icon = '✓'
                label = '[done]'
            elif status == 'in_progress':
                icon = '→'
                label = '[working]'
            else:
                icon = '○'
                label = '[pending]'
            
            step_name = _truncate_to_width(step.name, width - 25)
            lines.append(f"    {icon} {step.id} {step_name} {label}")
        
        lines.append("")
        
        # Navigation indicator
        summary = self._get_summary()
        tasks = summary.get('tasks', [])
        total = len(tasks)
        current_idx = self.detail_task_index + 1
        lines.append(f"  Task {current_idx} of {total}")
        
        return "\n".join(lines)

    def _render_notes_content(self, width: int) -> str:
        """Render notes tab content."""
        manager = self._get_manager()
        if not manager:
            return "  No data available"
        
        notes = manager.get_notes()
        if not notes:
            return "  No notes yet."
        
        lines = []
        for n in notes:
            score = n.get('score', 0)
            content = n.get('content', '')
            n_id = n.get('id', '?')
            
            # Truncate content for display
            content_display = _truncate_to_width(content, width - 15)
            lines.append(f"  [{score:>3}] {n_id}: {content_display}")
        
        return "\n".join(lines) if lines else "  No notes."

    def _render_refs_content(self, width: int) -> str:
        """Render references tab content."""
        manager = self._get_manager()
        if not manager:
            return "  No data available"
        
        refs = manager.get_references()
        if not refs:
            return "  No references yet."
        
        lines = []
        for r in refs:
            score = r.get('score', 0)
            url = r.get('url', '')
            note = r.get('note', '')
            r_id = r.get('id', '?')
            
            # URL or path
            url_display = _truncate_to_width(url, width - 15)
            lines.append(f"  [{score:>3}] {r_id}: {url_display}")
            if note:
                note_display = _truncate_to_width(note, width - 10)
                lines.append(f"         {note_display}")
        
        return "\n".join(lines) if lines else "  No references."

    # =========================================================================
    # Task Detail Formatters (for timeline output and IM)
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
            if status == 'done':
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
            if status == 'done':
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
            if status == 'done' and hasattr(step, 'get_outputs_list'):
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
                    if step.status == 'done':
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
                    if step.status == 'done' and hasattr(step, 'get_outputs_list'):
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
            if step.status == 'done':
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
            if step.status == 'done' and hasattr(step, 'get_outputs_list'):
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
