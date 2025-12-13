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

from datetime import datetime, timezone
import re
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


def _clip_prefix_to_width(s: str, max_width: int) -> str:
    """Take a prefix that fits within max display width (no ellipsis)."""
    if max_width <= 0:
        return ""
    result = ""
    width = 0
    for char in s:
        char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if width + char_width > max_width:
            break
        result += char
        width += char_width
    return result


def _clip_suffix_to_width(s: str, max_width: int) -> str:
    """Take a suffix that fits within max display width (no ellipsis)."""
    if max_width <= 0:
        return ""
    result_rev = ""
    width = 0
    for char in reversed(s):
        char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if width + char_width > max_width:
            break
        result_rev += char
        width += char_width
    return "".join(reversed(result_rev))


def _ellipsize_middle_to_width(s: str, max_width: int, ellipsis: str = "…") -> str:
    """Ellipsize in the middle to preserve both start and end."""
    if _display_width(s) <= max_width:
        return s
    ell_w = _display_width(ellipsis)
    if max_width <= ell_w:
        return ellipsis if ell_w == max_width else ""
    remaining = max_width - ell_w
    left_w = remaining // 2
    right_w = remaining - left_w
    left = _clip_prefix_to_width(s, left_w)
    right = _clip_suffix_to_width(s, right_w)
    return f"{left}{ellipsis}{right}"


def _condense_status_for_header(status: str) -> str:
    """Condense verbose status strings for single-line header display."""
    s = " ".join(str(status or "").split())
    if not s:
        return ""

    # Replace long parenthetical lists with an ellipsis marker to reduce noise.
    def _condense_paren(match: re.Match[str]) -> str:
        inner = match.group(1) or ""
        inner_stripped = inner.strip()
        if len(inner_stripped) <= 24:
            return f"({inner_stripped})"
        if ("," in inner_stripped) or ("，" in inner_stripped) or (";" in inner_stripped):
            return "(…)"
        if len(inner_stripped) > 40:
            return "(…)"
        return f"({inner_stripped})"

    s = re.sub(r"\(([^)]*)\)", _condense_paren, s)
    return s


def _split_one_line_for_wrap(s: str, max_width: int) -> Tuple[str, int]:
    """Split a single line from the front of s for header wrapping.

    Returns (line, consumed_chars_in_s).
    """
    if not s or max_width <= 0:
        return "", 0

    prefix = _clip_prefix_to_width(s, max_width)
    if not prefix:
        return "", 0

    # Fits fully on this line.
    if len(prefix) == len(s):
        return prefix, len(prefix)

    prefix_rstrip = prefix.rstrip()
    last_space = prefix_rstrip.rfind(" ")
    if last_space != -1:
        candidate_line = prefix_rstrip[:last_space].rstrip()
        if candidate_line and (_display_width(candidate_line) >= int(max_width * 0.6)):
            return candidate_line, last_space + 1

    # Hard-wrap (keeps more signal when the next token is long).
    return prefix_rstrip, len(prefix)


def _wrap_text_to_lines(text: str, max_width: int) -> List[str]:
    """Wrap text into lines constrained by display width.

    - Prefers breaking on spaces.
    - Uses hard-wrapping when word-boundary wrap would waste lots of space.
    """
    text = " ".join(str(text or "").split()).strip()
    if not text:
        return []
    if max_width <= 0:
        return [""]

    remaining = text
    lines: List[str] = []
    while remaining:
        line, consumed = _split_one_line_for_wrap(remaining, max_width)
        if not line or consumed <= 0:
            break
        lines.append(line)
        remaining = remaining[consumed:].lstrip()
    return lines


def _wrap_text_max_lines(text: str, max_width: int, max_lines: int) -> List[str]:
    """Wrap text to max_lines and ellipsize the last line if truncated."""
    if max_lines <= 0:
        return []
    text = " ".join(str(text or "").split()).strip()
    if not text:
        return []
    if max_width <= 0:
        return [""]

    ellipsis = "…"
    ell_w = _display_width(ellipsis)

    remaining = text
    out: List[str] = []
    for i in range(max_lines):
        if not remaining:
            break

        is_last = (i == max_lines - 1)
        if not is_last:
            line, consumed = _split_one_line_for_wrap(remaining, max_width)
            if not line or consumed <= 0:
                break
            out.append(line)
            remaining = remaining[consumed:].lstrip()
            continue

        # Last allowed line: maximize information density by hard-clipping and adding ellipsis.
        if _display_width(remaining) <= max_width:
            out.append(remaining)
            break

        if max_width <= ell_w:
            out.append(_truncate_to_width(ellipsis, max_width, ellipsis=""))
            break

        prefix = _clip_prefix_to_width(remaining, max_width - ell_w).rstrip()
        out.append(prefix + ellipsis)
        break

    return out


class TaskPanel:
    """
    Task Panel widget for CCCC TUI.

    Provides 2-level TUI architecture:
    - Level 0 (Header): Presence-first status bar - always visible
      Format: "A: T003 JWT → S2 │ B: T005 User → S1"
    - Level 2 (Dialog): Tabbed detail view - toggle with [T]
      Tabs: Sketch, Milestones, Tasks, Notes, Refs

    Design: Press [T] to go directly from Header to Tabbed Dialog.
    There is no intermediate Level 1 (expanded list) - it was removed for simpler UX.
    Note: Presence tab was removed - presence is shown in header (Decision: 2024-12 simplification).
    """

    # Tab order for Level 2 dialog
    # Order by conceptual hierarchy: Sketch > Milestones > Tasks > Notes > Refs
    # Note: Presence tab removed - presence is shown in header (Decision: 2024-12 simplification)
    TABS = ['sketch', 'milestones', 'tasks', 'notes', 'refs']
    TAB_LABELS = {
        'tasks': 'Tasks',
        'sketch': 'Sketch',
        'milestones': 'Milestones',
        'notes': 'Notes',
        'refs': 'Refs'
    }
    TAB_KEYS = {
        'tasks': 'T',
        'sketch': 'K',
        'milestones': 'M',
        'notes': 'N',
        'refs': 'R'
    }

    def __init__(self, root: Path, on_toggle: Optional[Callable[[], None]] = None):
        """
        Initialize Task Panel.

        Args:
            root: Project root directory (contains context/)
            on_toggle: Optional callback when Level 2 dialog is opened
        """
        self.root = root
        self.on_toggle = on_toggle
        self._manager = None
        # Level 2 tab state
        self.current_tab = 'tasks'
        # Current task index for Level 2 Tasks tab navigation
        self.detail_task_index = 0
        # Track actual line count for height calculation
        self._last_line_count = 10  # Default fallback

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

    def refresh(self) -> None:
        """Force refresh of task data."""
        if self._manager:
            self._manager.refresh()

    def get_line_count(self) -> int:
        """Get the line count for height calculation.

        Legacy method kept for compatibility. Returns default value.
        """
        return self._last_line_count

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
    # Level 0: Header Strip (Presence-First Design)
    # =========================================================================

    def get_header_text(self, width: int = 80) -> str:
        """
        Get Presence-only header text for status bar.

        Design rationale (from comprehensive_design.md Decision 6):
        - Header ONLY shows Presence - no sketch summary, no progress %
        - Presence is MOST valuable - directly shows what agents are doing
        - Progress % is misleading (not real project completion)
        - Single "current task" is ambiguous in multi-agent scenario
        - User quote: "如果空间不够还不如都留给两位agent的Presence信息"

        Format:
        - Full: "A: T003 JWT → S2 │ B: T005 User → S1"
        - No status: "A: T003 → S2 │ B: —"
        - Narrow: "A: T003 S2 │ B: T005 S1"

        Args:
            width: Available width for header

        Returns:
            Formatted presence-only header string
        """
        manager = self._get_manager()
        if not manager:
            return ""

        # Get presence data
        presence = manager.get_presence()

        if not presence:
            # No presence data - fallback to showing current task
            return self._get_legacy_header_text()

        # Format presence for each agent
        # New simple format: "A: <status text> │ B: <status text>"
        presence_parts = []
        for agent in presence:
            agent_id = agent.get('id', '?')
            # Use short label (A for peer-a, B for peer-b, etc.)
            short_id = self._get_short_agent_id(agent_id)
            status = agent.get('status', '')

            if status:
                # Truncate status to fit in header
                max_status_len = (width - 10) // 2  # Leave room for IDs and separator
                if len(status) > max_status_len:
                    status = status[:max_status_len - 2] + ".."
                presence_parts.append(f"{short_id}: {status}")
            else:
                presence_parts.append(f"{short_id}: —")

        return " │ ".join(presence_parts)

    def get_presence_lines(self, width: int = 80) -> List[str]:
        """Get 2-line presence summary for the runtime header."""
        manager = self._get_manager()
        if not manager:
            return ["A: —", "B: —"]

        presence = manager.get_presence() or []
        if not presence:
            legacy = self._get_legacy_header_text()
            if legacy:
                legacy = " ".join(str(legacy).split())
                return [_truncate_to_width(f"A: {legacy}", width), "B: —"]
            return ["A: —", "B: —"]

        def _parse_ts(ts: str) -> Optional[datetime]:
            if not ts:
                return None
            try:
                s = str(ts).replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                return None

        best: Dict[str, Tuple[datetime, str]] = {}
        for agent in presence:
            agent_id = str(agent.get("id", "") or "")
            short_id = self._get_short_agent_id(agent_id)
            if short_id not in ("A", "B"):
                continue

            status = str(agent.get("status", "") or "")
            status = _condense_status_for_header(status)
            status = status if status else "—"
            ts = _parse_ts(str(agent.get("updated_at", "") or "")) or datetime.fromtimestamp(0, tz=timezone.utc)

            prev = best.get(short_id)
            if prev is None or ts >= prev[0]:
                best[short_id] = (ts, status)

        lines: List[str] = []
        for short_id in ("A", "B"):
            status = best.get(short_id, (datetime.fromtimestamp(0, tz=timezone.utc), "—"))[1]
            prefix = f"{short_id}: "
            max_status_width = max(0, width - _display_width(prefix))
            status_trunc = _ellipsize_middle_to_width(status, max_status_width)
            lines.append(prefix + status_trunc)

        return lines

    def get_presence_header_lines(self, width: int = 80, lines_per_agent: int = 2) -> List[str]:
        """Get fixed number of presence lines for the runtime header (wrap + truncate).

        Returns: [A line1, A line2, B line1, B line2] when lines_per_agent=2.
        """
        manager = self._get_manager()
        if not manager:
            return ["A: —", "", "B: —", ""] if lines_per_agent == 2 else []

        presence = manager.get_presence() or []
        if not presence:
            legacy = self._get_legacy_header_text()
            legacy = " ".join(str(legacy or "").split())
            a_status = legacy if legacy else "—"
            b_status = "—"
        else:
            def _parse_ts(ts: str) -> Optional[datetime]:
                if not ts:
                    return None
                try:
                    s = str(ts).replace("Z", "+00:00")
                    dt = datetime.fromisoformat(s)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)
                except Exception:
                    return None

            best: Dict[str, Tuple[datetime, str]] = {}
            for agent in presence:
                agent_id = str(agent.get("id", "") or "")
                short_id = self._get_short_agent_id(agent_id)
                if short_id not in ("A", "B"):
                    continue

                status = str(agent.get("status", "") or "")
                status = _condense_status_for_header(status)
                status = status if status else "—"
                ts = _parse_ts(str(agent.get("updated_at", "") or "")) or datetime.fromtimestamp(0, tz=timezone.utc)

                prev = best.get(short_id)
                if prev is None or ts >= prev[0]:
                    best[short_id] = (ts, status)

            a_status = best.get("A", (datetime.fromtimestamp(0, tz=timezone.utc), "—"))[1]
            b_status = best.get("B", (datetime.fromtimestamp(0, tz=timezone.utc), "—"))[1]

        def _format_agent(short_id: str, status: str) -> List[str]:
            prefix = f"{short_id}: "
            prefix_w = _display_width(prefix)
            avail = max(0, width - prefix_w)
            wrapped = _wrap_text_max_lines(status, avail, max(1, lines_per_agent)) or ["—"]
            while len(wrapped) < max(1, lines_per_agent):
                wrapped.append("")
            lines_out = [prefix + wrapped[0]]
            indent = " " * prefix_w
            for cont in wrapped[1:lines_per_agent]:
                lines_out.append((indent + cont) if cont else "")
            while len(lines_out) < lines_per_agent:
                lines_out.append("")
            return lines_out[:lines_per_agent]

        lines = _format_agent("A", a_status) + _format_agent("B", b_status)
        return lines

    def _get_short_agent_id(self, agent_id: str) -> str:
        """Convert agent ID to short display name."""
        mapping = {
            'peer-a': 'A',
            'peer-b': 'B',
            'peera': 'A',
            'peerb': 'B',
            'a': 'A',
            'b': 'B',
        }
        return mapping.get(agent_id.lower(), agent_id[:3].upper())

    def _get_legacy_header_text(self) -> str:
        """
        Fallback header when no presence data available.
        Shows current task progress (legacy format).
        """
        summary = self._get_summary()

        if summary['total_tasks'] == 0:
            return ""

        # Current task info
        current_id = summary.get('current_task')
        current_step = summary.get('current_step')

        if current_id:
            manager = self._get_manager()
            task_name = ""
            if manager:
                task = manager.get_task(current_id)
                if task:
                    task_name = task.name[:20]

            if task_name:
                if current_step:
                    return f"→ {current_id}: {task_name} [{current_step}]"
                return f"→ {current_id}: {task_name}"
            return f"→ {current_id}"
        elif summary['completed_tasks'] == summary['total_tasks']:
            return "✓ All tasks complete"
        else:
            return ""

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
        Get unified Level 2 detail view content (without tab bar - tabs are in UI).
        
        Args:
            width: Available width for rendering
        
        Returns:
            Formatted string with current tab content
        """
        lines = []
        INNER = max(50, width - 4)
        
        # Content based on current tab (no tab bar - handled by TUI buttons)
        # Note: Presence tab removed - presence is shown in header
        if self.current_tab == 'milestones':
            content = self._render_milestones_content(INNER)
        elif self.current_tab == 'tasks':
            content = self._render_tasks_content(INNER)
        elif self.current_tab == 'sketch':
            content = self._render_sketch_content(INNER)
        elif self.current_tab == 'notes':
            content = self._render_notes_content(INNER)
        elif self.current_tab == 'refs':
            content = self._render_refs_content(INNER)
        else:
            content = "Unknown tab"
        
        lines.append(content)

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
            
            # Main line - no truncation, let it wrap naturally
            lines.append(f"  {icon} {m_id}: {name} {status_label}")
            
            # Additional info for done/active milestones
            if status == 'done':
                completed = m.get('completed', '')
                outcomes = m.get('outcomes', '')
                if completed:
                    lines.append(f"      Completed: {completed}")
                if outcomes:
                    # No truncation - let it wrap
                    lines.append(f"      Outcomes: {outcomes}")
            elif status == 'active':
                started = m.get('started', '')
                desc = m.get('description', '')
                if started:
                    lines.append(f"      Started: {started}")
                if desc:
                    # No truncation - let it wrap
                    lines.append(f"      {desc}")
            
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
        
        # Task header - no truncation
        lines.append(f"  {task.id}: {task.name}")
        lines.append("")
        lines.append(f"  Goal: {task.goal}")
        lines.append(f"  Status: {task.status}  │  Progress: {task.progress} ({task.progress_percent}%)")
        lines.append("")
        lines.append("  Steps:")
        
        # Steps - no truncation on step names
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
            
            # Let step name wrap naturally
            lines.append(f"    {icon} {step.id} {step.name} {label}")
        
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
            ttl = n.get('ttl', n.get('score', 0))
            content = n.get('content', '')
            n_id = n.get('id', '?')
            
            # Header with ttl
            lines.append(f"  {n_id} (ttl: {ttl})")
            # Content - let it wrap naturally
            lines.append(f"    {content}")
            lines.append("")  # Blank line between notes
        
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
            ttl = r.get('ttl', r.get('score', 0))
            url = r.get('url', '')
            note = r.get('note', '')
            r_id = r.get('id', '?')
            
            # Header with ttl
            lines.append(f"  {r_id} (ttl: {ttl})")
            # URL - let it wrap
            lines.append(f"    URL: {url}")
            # Note - let it wrap
            if note:
                lines.append(f"    Note: {note}")
            lines.append("")  # Blank line between refs
        
        return "\n".join(lines) if lines else "  No references."

    def _render_sketch_content(self, width: int) -> str:
        """
        Render sketch tab content.

        Shows:
        - Vision statement (if set)
        - Execution blueprint (markdown with light H2 enhancement)
        """
        manager = self._get_manager()
        if not manager:
            return "  No data available"

        lines = []

        # Vision section
        vision = manager.get_vision()
        if vision:
            lines.append("  ━━ Vision ━━")
            lines.append(f"  {vision}")
            lines.append("")

        # Sketch section
        sketch = manager.get_sketch()
        if sketch:
            lines.append("  ━━ Execution Blueprint ━━")
            lines.append("")

            # Light markdown enhancement for prompt_toolkit display
            # Transform ## Title → ━━ Title ━━
            for line in sketch.split('\n'):
                if line.startswith('## '):
                    section_title = line[3:].strip()
                    lines.append(f"  ── {section_title} ──")
                elif line.startswith('# '):
                    # Main title
                    main_title = line[2:].strip()
                    lines.append(f"  ━━ {main_title} ━━")
                elif line.startswith('- '):
                    # Bullet points - add indent
                    lines.append(f"    • {line[2:]}")
                elif line.startswith('**') and line.endswith('**'):
                    # Bold text - show with marker
                    bold_text = line[2:-2]
                    lines.append(f"  ▸ {bold_text}")
                else:
                    # Normal text
                    if line.strip():
                        lines.append(f"    {line}")
                    else:
                        lines.append("")
        else:
            lines.append("  No sketch defined yet.")
            lines.append("")
            lines.append("  Agents can create a sketch using update_sketch() MCP tool")
            lines.append("  or by editing context/context.yaml directly.")

        return "\n".join(lines)

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
    # IM-friendly output (for /context tasks in bridges)
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
