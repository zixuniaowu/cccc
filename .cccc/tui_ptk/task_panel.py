# -*- coding: utf-8 -*-
"""
Blueprint Task Panel - TUI component for task visualization.

This module provides the Task Panel widget for CCCC TUI:
- Collapsed view: Single status line in footer
- Expanded view: Full task list with progress details
- Toggle via T key or mouse click

Design principles:
- High information density without clutter
- Real-time updates from TaskManager
- Minimal distraction from main timeline
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from prompt_toolkit.layout import (
    Window, FormattedTextControl, Dimension, HSplit, VSplit
)
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.mouse_events import MouseEventType


class TaskPanel:
    """
    Task Panel widget for CCCC TUI.

    Attributes:
        expanded: Whether panel is in expanded view
        root: Project root directory
        on_toggle: Callback when panel is toggled
    """

    def __init__(self, root: Path, on_toggle: Optional[Callable[[], None]] = None):
        """
        Initialize Task Panel.

        Args:
            root: Project root directory
            on_toggle: Optional callback when panel is toggled
        """
        self.root = root
        self.expanded = False
        self.on_toggle = on_toggle
        self._cached_summary: Optional[Dict[str, Any]] = None
        self._last_update: float = 0

        # Lazy-load task manager
        self._manager = None

    def _get_manager(self):
        """Lazy-load TaskManager."""
        if self._manager is None:
            try:
                from orchestrator.task_manager import TaskManager
                self._manager = TaskManager(self.root)
            except ImportError:
                pass
        return self._manager

    def _get_summary(self) -> Dict[str, Any]:
        """Get task summary from TaskManager."""
        manager = self._get_manager()
        if not manager:
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

        try:
            return manager.get_summary()
        except Exception:
            return self._cached_summary or {
                'total_tasks': 0,
                'completed_tasks': 0,
                'current_task': None,
                'current_step': None,
                'progress_percent': 0,
                'tasks': []
            }

    def toggle(self) -> None:
        """Toggle between expanded and collapsed view."""
        self.expanded = not self.expanded
        if self.on_toggle:
            self.on_toggle()

    def refresh(self) -> None:
        """Force refresh of task data."""
        self._cached_summary = self._get_summary()

    # =========================================================================
    # Collapsed View (Footer Status Line)
    # =========================================================================

    def get_status_line(self) -> FormattedText:
        """
        Get formatted status line for footer.

        Format: "📊 2/5 → T003 [S2]  [T]"

        Returns:
            FormattedText for status line
        """
        summary = self._get_summary()

        if summary['total_tasks'] == 0:
            return FormattedText([
                ('class:task-panel.icon', '📊 '),
                ('class:task-panel.empty', 'No tasks'),
                ('class:task-panel.hint', '  [T]'),
            ])

        # Progress indicator
        completed = summary['completed_tasks']
        total = summary['total_tasks']
        percent = summary['progress_percent']

        # Current task info
        current = summary.get('current_task') or '-'
        step = summary.get('current_step') or ''
        step_str = f"[{step}]" if step else ''

        return FormattedText([
            ('class:task-panel.icon', '📊 '),
            ('class:task-panel.progress', f'{completed}/{total}'),
            ('class:task-panel.arrow', ' → '),
            ('class:task-panel.current', current),
            ('class:task-panel.step', f' {step_str}' if step_str else ''),
            ('class:task-panel.hint', '       [T]'),
        ])

    def get_collapsed_window(self) -> Window:
        """
        Get collapsed panel as Window widget.

        Returns:
            Window displaying status line
        """
        def get_text():
            return self.get_status_line()

        def mouse_handler(mouse_event):
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                self.toggle()
            return None

        return Window(
            content=FormattedTextControl(
                get_text,
                focusable=False,
            ),
            height=1,
            dont_extend_height=True,
            style='class:task-panel.collapsed',
        )

    # =========================================================================
    # Expanded View (Full Task List)
    # =========================================================================

    def get_expanded_content(self) -> FormattedText:
        """
        Get formatted content for expanded view.

        Returns:
            FormattedText for expanded panel
        """
        summary = self._get_summary()

        if summary['total_tasks'] == 0:
            return FormattedText([
                ('class:task-panel.header', '├─ Blueprint '),
                ('class:task-panel.empty', '(no tasks)'),
                ('class:task-panel.header', ' ─────────────────────────────── [T] ─┤\n'),
                ('class:task-panel.hint', '│  Use /task to view blueprint commands'),
                ('class:task-panel.hint', '                                       │\n'),
                ('class:task-panel.header', '└──────────────────────────────────────────────────────────────────────┘'),
            ])

        # Build header
        completed = summary['completed_tasks']
        total = summary['total_tasks']
        percent = summary['progress_percent']

        parts: List[Tuple[str, str]] = [
            ('class:task-panel.header', f'├─ Blueprint ({completed}/{total} · {percent}%) '),
            ('class:task-panel.header', '─' * max(0, 45 - len(f'({completed}/{total} · {percent}%)'))),
            ('class:task-panel.hint', ' [T] '),
            ('class:task-panel.header', '─┤\n'),
        ]

        # Build task list (2-column layout for compact view)
        tasks = summary['tasks']
        left_tasks = tasks[:((len(tasks) + 1) // 2)]
        right_tasks = tasks[((len(tasks) + 1) // 2):]

        max_rows = max(len(left_tasks), len(right_tasks))
        for i in range(max_rows):
            parts.append(('class:task-panel.border', '│  '))

            # Left column
            if i < len(left_tasks):
                t = left_tasks[i]
                icon = self._get_task_icon(t['status'])
                parts.append(('class:task-panel.icon', f'{icon} '))
                parts.append(('class:task-panel.id', f"{t['id']} "))
                name = t['name'][:18].ljust(18)
                parts.append(('class:task-panel.name', f'{name} '))
                parts.append(('class:task-panel.progress', f"{t['progress']}"))
            else:
                parts.append(('', ' ' * 30))

            parts.append(('', '   '))

            # Right column
            if i < len(right_tasks):
                t = right_tasks[i]
                icon = self._get_task_icon(t['status'])
                parts.append(('class:task-panel.icon', f'{icon} '))
                parts.append(('class:task-panel.id', f"{t['id']} "))
                name = t['name'][:18].ljust(18)
                parts.append(('class:task-panel.name', f'{name} '))
                parts.append(('class:task-panel.progress', f"{t['progress']}"))
            else:
                parts.append(('', ' ' * 30))

            parts.append(('class:task-panel.border', ' │\n'))

        # Footer
        parts.append(('class:task-panel.header', '└' + '─' * 70 + '┘'))

        return FormattedText(parts)

    def _get_task_icon(self, status: str) -> str:
        """Get icon for task status."""
        if status == 'complete':
            return '✓'
        elif status == 'active':
            return '→'
        else:
            return '○'

    def get_expanded_window(self) -> Window:
        """
        Get expanded panel as Window widget.

        Returns:
            Window displaying full task list
        """
        def get_text():
            return self.get_expanded_content()

        def mouse_handler(mouse_event):
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                # Check if click is in header area (first line)
                self.toggle()
            return None

        return Window(
            content=FormattedTextControl(
                get_text,
                focusable=False,
            ),
            height=Dimension(min=3, max=12, preferred=8),
            dont_extend_height=True,
            style='class:task-panel.expanded',
        )

    # =========================================================================
    # Unified Widget
    # =========================================================================

    def get_widget(self) -> Window:
        """
        Get current panel widget based on expanded state.

        Returns:
            Window widget (collapsed or expanded)
        """
        if self.expanded:
            return self.get_expanded_window()
        return self.get_collapsed_window()


# Style definitions for Task Panel
TASK_PANEL_STYLES = {
    # Panel container styles
    'task-panel.collapsed': 'bg:#161b22',
    'task-panel.expanded': 'bg:#0d1117',

    # Header and borders
    'task-panel.header': '#30363d',
    'task-panel.border': '#30363d',

    # Status indicators
    'task-panel.icon': '#58a6ff',
    'task-panel.progress': '#3fb950',
    'task-panel.arrow': '#8b949e',
    'task-panel.current': '#ffa657',
    'task-panel.step': '#79c0ff',

    # Task list
    'task-panel.id': '#58a6ff bold',
    'task-panel.name': '#c9d1d9',
    'task-panel.complete': '#3fb950',
    'task-panel.active': '#ffa657',
    'task-panel.planned': '#8b949e',

    # Hints and empty state
    'task-panel.hint': '#6e7681',
    'task-panel.empty': '#8b949e italic',
}


def format_task_for_timeline(task_id: str, action: str, message: str) -> str:
    """
    Format task update message for timeline display.

    Args:
        task_id: Task ID (T001)
        action: Action performed (start, done, blocked, etc.)
        message: Result message

    Returns:
        Formatted string for timeline
    """
    icon = {
        'start': '▶',
        'done': '✓',
        'blocked': '⚠',
        'promoted': '↑',
        'in_progress': '→',
    }.get(action, '•')

    return f"[TASK] {icon} {task_id}: {message}"
