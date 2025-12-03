# -*- coding: utf-8 -*-
"""
Format status and help messages for IM display.
Shared between Telegram, Slack, and Discord bridges.
"""
from __future__ import annotations
import json
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
        f"  {p}task \u2192 all tasks with steps",
        f"  {p}task T001 \u2192 detail for T001",
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


def parse_task_command(text: str) -> Optional[str]:
    """
    Parse /task command to extract task_id.
    
    Examples:
        /task -> None
        /task T001 -> T001
        /task 1 -> T001
        
    Returns:
        task_id or None for summary view
    """
    import re
    text = text.strip()
    
    # Match /task or !task followed by optional ID
    match = re.match(r'^[/!]task(?:\s+(.+))?$', text, re.I)
    if not match:
        return None
    
    arg = match.group(1)
    if not arg:
        return None
    
    arg = arg.strip()
    
    # Already a task ID like T001, T002
    if re.match(r'^T\d+$', arg, re.I):
        return arg.upper()
    
    # Just a number like 1, 2
    if re.match(r'^\d+$', arg):
        return f"T{arg.zfill(3)}"
    
    return arg  # Return as-is, let TaskPanel handle validation
