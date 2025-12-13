# -*- coding: utf-8 -*-
"""
Format status and help messages for IM display.
Shared between Telegram, Slack, and Discord bridges.
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional


def format_help_for_im(prefix: str = '/') -> str:
    """
    Format help message for IM.

    Args:
        prefix: Command prefix ('/' for Telegram, '!' for Slack/Discord)

    Design principles:
    - Grouped by function
    - Visual hierarchy with section headers
    - Concise command descriptions
    - Easy to scan
    """
    p = prefix

    lines = [
        "\u2501\u2501\u2501 CCCC Commands \u2501\u2501\u2501",  # ━━━
        "",
        "\u25b8 Message Peers",  # ▸
        f"  a: or {p}a \u2192 send to PeerA",
        f"  b: or {p}b \u2192 send to PeerB",
        f"  both: or {p}both \u2192 send to both",
        "",
        "\u25b8 Control",
        f"  {p}pause \u2192 pause delivery",
        f"  {p}resume \u2192 resume delivery",
        f"  {p}restart [a|b|both] \u2192 restart CLI",
        "",
        "\u25b8 Agents",
        f"  {p}aux <prompt> \u2192 run Aux helper",
        f"  {p}foreman [on|off|now|status]",
        "",
        "\u25b8 Info",
        f"  {p}status \u2192 system status",
        f"  {p}context \u2192 context overview (now)",
        f"  {p}context tasks [T001|1] \u2192 tasks summary/detail",
        f"  {p}context milestones \u2192 milestones timeline",
        f"  {p}context sketch \u2192 vision + sketch",
        f"  {p}context notes \u2192 top notes",
        f"  {p}context refs \u2192 top references",
        f"  {p}context presence \u2192 team presence",
        f"  {p}verbose [on|off] \u2192 toggle summaries",
        "",
        "\u25b8 Account",
        f"  {p}subscribe \u2192 opt-in",
        f"  {p}unsubscribe \u2192 opt-out",
        f"  {p}whoami \u2192 show chat ID",
    ]

    return '\n'.join(lines)


def _load_yaml_safe(path: Path) -> Dict[str, Any]:
    """Load YAML file safely, with fallback to JSON."""
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {}


def format_status_for_im(state_dir: Path) -> str:
    """
    Format status for IM display.

    Reads from TWO sources:
    - settings/cli_profiles.yaml: CURRENT agent configuration (authoritative)
    - state/status.json: Runtime status (handoffs, paused, etc.)

    Design principles:
    - Health/running state first (most important)
    - Scannable with emojis for quick visual parsing
    - Actionable information only
    - Compact for mobile/IM viewing
    """
    home_dir = state_dir.parent  # .cccc/state -> .cccc
    settings_dir = home_dir / "settings"

    # Read CURRENT agent config from settings (authoritative source)
    cli_profiles = _load_yaml_safe(settings_dir / "cli_profiles.yaml")
    roles_cfg = cli_profiles.get('roles') or {}

    # Read runtime status from status.json
    st_path = state_dir / "status.json"
    try:
        st = json.loads(st_path.read_text(encoding='utf-8')) if st_path.exists() else {}
    except Exception:
        st = {}

    lines = []

    # === Header: Overall health ===
    paused = st.get('paused', False)

    if paused:
        health = "\u23f8 Paused"  # ⏸
    elif st:
        health = "\u25b6 Running"  # ▶
    else:
        health = "\u26a0 Offline"  # ⚠

    lines.append(f"\u2501\u2501\u2501 CCCC {health} \u2501\u2501\u2501")  # ━━━
    lines.append("")

    # === Peers: Read from settings (authoritative) ===
    peer_a_actor = (roles_cfg.get('peerA') or {}).get('actor') or 'unset'
    peer_b_actor = (roles_cfg.get('peerB') or {}).get('actor') or 'unset'
    aux_actor = (roles_cfg.get('aux') or {}).get('actor') or ''
    
    # Check if single peer mode
    is_single_peer = peer_b_actor in ('none', 'unset', '')

    # CLI running status from runtime (process actually running)
    setup = st.get('setup') or {}
    cli = setup.get('cli') or {}
    
    # Get running state from last_handoff timestamps or pane status
    peer_a_running = bool((cli.get('peerA') or {}).get('available', True))
    peer_b_running = bool((cli.get('peerB') or {}).get('available', True)) if not is_single_peer else False

    if is_single_peer:
        # Single peer mode display
        lines.append(f"\u25b8 Mode: Single Peer")
        a_icon = "\u25b6" if peer_a_running else "\u23f8"  # ▶ or ⏸
        lines.append(f"\u25b8 Peer: {peer_a_actor} {a_icon}")
    else:
        # Dual peer mode display
        lines.append(f"\u25b8 Mode: Dual Peer")
        a_icon = "\u25b6" if peer_a_running else "\u23f8"
        b_icon = "\u25b6" if peer_b_running else "\u23f8"
        lines.append(f"\u25b8 PeerA: {peer_a_actor} {a_icon}")
        lines.append(f"\u25b8 PeerB: {peer_b_actor} {b_icon}")

    lines.append("")

    # === Handoffs: Activity metrics ===
    reset = st.get('reset') or {}
    total = reset.get('handoffs_total', 0)
    h_a = reset.get('handoffs_peerA', 0)
    h_b = reset.get('handoffs_peerB', 0)

    if is_single_peer:
        lines.append(f"\u25b8 Handoffs: {total}")
    else:
        lines.append(f"\u25b8 Handoffs: {total} (A:{h_a} B:{h_b})")

    # === Inbox: Count files directly from mailbox (authoritative) ===
    mailbox_dir = home_dir / "mailbox"
    try:
        inbox_a = len(list((mailbox_dir / "peerA" / "inbox").glob("*"))) if (mailbox_dir / "peerA" / "inbox").exists() else 0
    except Exception:
        inbox_a = 0
    try:
        inbox_b = len(list((mailbox_dir / "peerB" / "inbox").glob("*"))) if (mailbox_dir / "peerB" / "inbox").exists() else 0
    except Exception:
        inbox_b = 0

    if is_single_peer:
        if inbox_a > 0:
            lines.append(f"\u25b8 Inbox: {inbox_a} pending")
        else:
            lines.append(f"\u25b8 Inbox: empty")
    else:
        lines.append(f"\u25b8 Inbox: A:{inbox_a} B:{inbox_b}")

    # === Aux: Optional helper (from settings) ===
    if aux_actor and aux_actor != 'none':
        aux_info = st.get('aux') or {}
        aux_state = 'running' if aux_info.get('running') else 'idle'
        lines.append(f"\u25b8 Aux: {aux_actor} ({aux_state})")

    # === Foreman: From settings ===
    foreman_cfg = _load_yaml_safe(settings_dir / "foreman.yaml")
    foreman_enabled = foreman_cfg.get('enabled', False)
    foreman_agent = foreman_cfg.get('agent', '')

    if foreman_enabled and foreman_agent:
        foreman_st = st.get('foreman') or {}
        f_running = foreman_st.get('running', False)
        f_next = foreman_st.get('next_due', '')
        if f_running:
            f_state = 'running'
        elif f_next:
            f_state = f'next {f_next}'
        else:
            f_state = 'idle'
        lines.append(f"\u25b8 Foreman: {foreman_agent} ({f_state})")

    lines.append("")

    # === Timestamp ===
    ts = st.get('ts', '')
    if ts:
        # Show just time portion
        time_part = ts.split(' ')[-1] if ' ' in ts else ts
        lines.append(f"\u23f1 Updated: {time_part}")  # ⏱

    return '\n'.join(lines)


def format_status_compact(state_dir: Path) -> str:
    """
    Ultra-compact single-line status for notifications/headers.
    """
    st_path = state_dir / "status.json"
    try:
        st = json.loads(st_path.read_text(encoding='utf-8')) if st_path.exists() else {}
    except Exception:
        return "status: unavailable"

    paused = st.get('paused', False)
    reset = st.get('reset') or {}
    total = reset.get('handoffs_total', 0)

    status = "\u23f8" if paused else "\u25b6"  # ⏸ or ▶
    return f"{status} handoffs:{total}"


def format_task_for_im(state_dir: Path, task_id: Optional[str] = None) -> str:
    """
    Format task/blueprint status for IM display.

    Uses TaskPanel for consistent WBS-style output.

    Args:
        state_dir: Path to .cccc/state
        task_id: Optional specific task ID for detail view
    """
    home_dir = state_dir.parent  # .cccc/state -> .cccc
    root_dir = home_dir.parent   # .cccc -> project root

    try:
        from tui_ptk.task_panel import TaskPanel
        panel = TaskPanel(root_dir)
        return panel.format_for_im(task_id)
    except ImportError:
        return "\u2501\u2501\u2501 Tasks \u2501\u2501\u2501\nModule not available"
    except Exception as e:
        return f"\u2501\u2501\u2501 Tasks \u2501\u2501\u2501\nError: {str(e)[:50]}"


def parse_context_command(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse /context or !context commands.

    Examples:
        /context -> (None, None)
        /context tasks -> ("tasks", None)
        /context tasks T001 -> ("tasks", "T001")
        !context milestones -> ("milestones", None)
    """
    text = text.strip()
    match = re.match(r'^[/!]context(?:\s+(.+))?$', text, re.I)
    if not match:
        return None, None
    rest = match.group(1)
    if not rest:
        return None, None
    parts = rest.strip().split(None, 1)
    sub = parts[0].lower() if parts else None
    arg = parts[1].strip() if len(parts) > 1 else None
    return sub, arg


def format_context_for_im(state_dir: Path, sub: Optional[str] = None, arg: Optional[str] = None) -> str:
    """
    Unified context formatter for IM.

    Subcommands:
      - tasks [T001|1]
      - milestones
      - sketch
      - presence
      - (none): now summary
    """
    home_dir = state_dir.parent  # .cccc/state -> .cccc
    root_dir = home_dir.parent   # .cccc -> project root

    try:
        from orchestrator.task_manager import TaskManager
        tm = TaskManager(root_dir)
    except Exception:
        tm = None

    def _short(s: Any, max_len: int = 180) -> str:
        t = " ".join(str(s or "").split())
        if not t:
            return ""
        return (t[: max_len - 1] + "…") if len(t) > max_len else t

    if tm and hasattr(tm, "is_ready") and not tm.is_ready():
        return "\n".join([
            "━━━ Context ━━━",
            "Context not initialized (missing context/).",
            "Create context/context.yaml and context/tasks/ to enable /context.",
        ])

    if sub in (None, "", "now"):
        lines = ["━━━ Context ━━━"]
        if tm:
            try:
                vision = tm.get_vision()
            except Exception:
                vision = None
            if vision:
                lines.append(f"Vision: {_short(vision, 140)}")

            try:
                ms = tm.get_active_milestone()
            except Exception:
                ms = None
            if ms:
                mid = str(ms.get("id", "") or "").strip()
                name = str(ms.get("name", "") or "").strip()
                ms_line = f"{mid}: {name}".strip(": ").strip() if (mid or name) else ""
                lines.append(f"Milestone: → {ms_line}" if ms_line else "Milestone: → (active)")
            else:
                lines.append("Milestone: (none)")
            try:
                summary = tm.get_summary()
                active = []
                for t in (summary.get("tasks") or []):
                    if str(t.get("status", "")).lower() == "active":
                        active.append(t)
                if active:
                    lines.append(f"Active tasks: {len(active)}")
                    for t in active[:4]:
                        tid = str(t.get("id", "") or "").strip()
                        name = str(t.get("name", "") or "").strip()
                        lines.append(f"  → {tid}: {_short(name, 80)}".rstrip(": "))
                    if len(active) > 4:
                        lines.append(f"  … and {len(active) - 4} more")
                else:
                    lines.append("Active tasks: (none)")
                parse_errors = summary.get("parse_errors", {}) or {}
                if parse_errors:
                    ids = ", ".join(list(parse_errors.keys())[:6])
                    suffix = "…" if len(parse_errors) > 6 else ""
                    lines.append(f"⚠ Task YAML errors: {ids}{suffix}")
            except Exception:
                lines.append("Active tasks: (unavailable)")
        else:
            lines.append("Context not available.")
        lines.append("")
        lines.append("Tips: context sketch | milestones | tasks | notes | refs | presence")
        return "\n".join(lines)

    if sub in ("tasks", "task"):
        task_id = None
        if arg:
            a = arg.strip()
            if re.match(r'^T\\d+$', a, re.I):
                task_id = a.upper()
            elif re.match(r'^\\d+$', a):
                task_id = f"T{a.zfill(3)}"
            else:
                task_id = a.upper()
        return format_task_for_im(state_dir, task_id)

    if sub in ("milestones", "milestone"):
        if not tm:
            return "━━━ Milestones ━━━\nContext not available."
        try:
            all_ms = (tm.load_context() or {}).get("milestones", []) or []
        except Exception:
            all_ms = []

        active = [m for m in all_ms if str(m.get("status", "")).lower() == "active"]
        pending = [m for m in all_ms if str(m.get("status", "")).lower() not in ("done", "active")]
        done = [m for m in all_ms if str(m.get("status", "")).lower() == "done"]
        recent_done = done[-3:] if done else []

        if not (active or pending or recent_done):
            return "━━━ Milestones ━━━\nNo milestones defined."
        lines = ["━━━ Milestones ━━━"]
        def _fmt(m: Dict[str, Any]) -> None:
            st = str(m.get("status", "pending")).lower()
            icon = "✓" if st == "done" else ("→" if st == "active" else "○")
            name = str(m.get("name", "") or "").strip()
            mid = str(m.get("id", "") or "").strip()
            lines.append(f"{icon} {mid}: {name}".strip(": "))

        for m in active:
            _fmt(m)
        for m in pending:
            _fmt(m)
        if recent_done:
            lines.append("")
            lines.append("Recent done:")
            for m in recent_done:
                _fmt(m)
        return "\n".join(lines)

    if sub == "sketch":
        if not tm:
            return "━━━ Sketch ━━━\nContext not available."
        try:
            return tm.format_sketch_for_im()
        except Exception as e:
            return f"━━━ Sketch ━━━\nError: {str(e)[:80]}"

    if sub in ("notes", "note"):
        if not tm:
            return "━━━ Notes ━━━\nContext not available."
        try:
            notes = tm.get_notes()
        except Exception:
            notes = []
        if not notes:
            return "━━━ Notes ━━━\nNo notes yet."
        lines = ["━━━ Notes ━━━"]
        shown = notes[:5]
        for n in shown:
            nid = str(n.get("id", "") or "").strip()
            ttl = n.get("ttl", n.get("score", 0))
            content = _short(n.get("content", ""), 200)
            header = f"{nid} (ttl:{ttl})".strip()
            lines.append(header)
            lines.append(f"  {content}" if content else "  —")
        if len(notes) > 5:
            lines.append(f"… and {len(notes) - 5} more")
        return "\n".join(lines)

    if sub in ("refs", "ref", "reference", "references"):
        if not tm:
            return "━━━ Refs ━━━\nContext not available."
        try:
            refs = tm.get_references()
        except Exception:
            refs = []
        if not refs:
            return "━━━ Refs ━━━\nNo references yet."
        lines = ["━━━ Refs ━━━"]
        shown = refs[:5]
        for r in shown:
            rid = str(r.get("id", "") or "").strip()
            ttl = r.get("ttl", r.get("score", 0))
            url = _short(r.get("url", ""), 180)
            note = _short(r.get("note", ""), 140)
            header = f"{rid} (ttl:{ttl})".strip()
            lines.append(header)
            if url:
                lines.append(f"  {url}")
            if note:
                lines.append(f"  {note}")
        if len(refs) > 5:
            lines.append(f"… and {len(refs) - 5} more")
        return "\n".join(lines)

    if sub == "presence":
        if not tm:
            return "━━━ Presence ━━━\nContext not available."
        try:
            return tm.format_presence_for_im()
        except Exception as e:
            return f"━━━ Presence ━━━\nError: {str(e)[:80]}"

    return "Usage: context [sketch|milestones|tasks|notes|refs|presence] ..."
