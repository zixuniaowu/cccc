# -*- coding: utf-8 -*-
"""
tmux session/pane helpers and layout utilities
Copied verbatim from orchestrator_tmux.py (system copy, no logic change).
"""
from __future__ import annotations
import os, sys, json, time, shlex, shutil
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

# BEGIN copy from orchestrator_tmux.py

# Module-level tmux socket name for isolation between cccc instances
# When set, all tmux commands will use `-L <socket>` to create/use an independent tmux server
# This allows each cccc instance to inherit its own terminal's environment variables
_TMUX_SOCKET: Optional[str] = None

def set_socket(socket_name: Optional[str]):
    """Set the tmux socket name for this cccc instance.

    Each cccc instance should use a unique socket (typically the session name)
    to ensure environment variable isolation between instances.
    """
    global _TMUX_SOCKET
    _TMUX_SOCKET = socket_name

def get_socket() -> Optional[str]:
    """Get the current tmux socket name."""
    return _TMUX_SOCKET

def tmux(*args: str) -> Tuple[int,str,str]:
    import subprocess
    cmd = ["tmux"]
    if _TMUX_SOCKET:
        cmd.extend(["-L", _TMUX_SOCKET])
    cmd.extend(args)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate()
    return p.returncode, out, err

def tmux_session_exists(name: str) -> bool:
    code,_,_ = tmux("has-session","-t",name)
    return code == 0

def tmux_new_session(name: str) -> Tuple[str,str]:
    code,out,err = tmux("new-session","-d","-s",name)
    if code != 0:
        raise RuntimeError(f"tmux new-session failed: {err.strip()}")
    return out, err

def tmux_respawn_pane(pane: str, cmd: str) -> bool:
    """Respawn a pane with a new command. Returns True on success."""
    code, out, err = tmux("respawn-pane","-k","-t",pane,cmd)
    if code != 0:
        print(f"[TMUX] respawn-pane failed for pane={pane}: {err.strip() if err else 'unknown error'}")
        return False
    return True

def tmux_ensure_ledger_tail(session: str, ledger_path: Path):
    # historical helper; safe no-op when not used
    try:
        if not ledger_path.exists():
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.touch()
        tmux("new-window","-t",session,"-n","ledger")
        tmux("send-keys","-t",f"{session}:",f"tail -F {shlex.quote(str(ledger_path))}","C-m")
    except Exception:
        pass

def tmux_build_2x2(session: str) -> Dict[str,str]:
    # legacy fallback; retained for compatibility
    tmux("kill-pane","-a","-t",f"{session}:0.0")
    tmux("split-window","-h","-t",f"{session}:0.0")
    tmux("split-window","-v","-t",f"{session}:0.1")
    tmux("split-window","-v","-t",f"{session}:0.0")
    code,out,_ = tmux("list-panes","-t",session,"-F","#{pane_id}")
    ids = out.strip().splitlines() if code==0 else []
    return {"lt": ids[0] if len(ids)>0 else f"{session}:0.0",
            "lb": ids[2] if len(ids)>2 else f"{session}:0.0",
            "rt": ids[1] if len(ids)>1 else f"{session}:0.0",
            "rb": ids[3] if len(ids)>3 else f"{session}:0.0"}

def tmux_build_tui_layout(session: str, win_index: str = '0') -> Dict[str,str]:
    target = f"{session}:{win_index}"
    tmux("kill-pane","-a","-t",f"{target}.0")
    rc,_,err = tmux("split-window","-h","-t",f"{target}.0")
    if rc != 0:
        print(f"[TMUX] split horizontal failed: {err.strip()}")
    code,out,_ = tmux("list-panes","-t",target,"-F","#{pane_id} #{pane_left} #{pane_top}")
    panes=[]
    for ln in out.splitlines():
        try:
            pid, left, top = ln.strip().split()
            panes.append((pid, int(left), int(top)))
        except Exception:
            pass
    if not panes:
        return {'lt': f"{target}.0", 'rt': f"{target}.0", 'rb': f"{target}.0"}
    top_y = min(p[2] for p in panes)
    top_row = sorted([p for p in panes if p[2]==top_y], key=lambda x: x[1])
    if len(top_row) < 2:
        code2,out2,_ = tmux("list-panes","-t",target,"-F","#{pane_index} #{pane_id}")
        idx={}
        for ln in out2.splitlines():
            if not ln.strip():
                continue
            k,v = ln.split(" ",1); idx[int(k)] = v.strip()
        lt = idx.get(0) or f"{target}.0"; rt = idx.get(1) or lt
    else:
        lt = top_row[0][0]; rt = top_row[-1][0]
    rc,_,err = tmux("split-window","-v","-t",rt)
    if rc != 0:
        print(f"[TMUX] split rt vertical failed: {err.strip()}")
    tmux("select-pane","-t",lt)
    tmux("select-layout","-t",target,"main-vertical")
    try:
        code,w,_ = tmux("display-message","-p","-t",target,"#{window_width}")
        win_w = int(w.strip()) if code==0 and w.strip().isdigit() else shutil.get_terminal_size(fallback=(160,48)).columns
    except Exception:
        win_w = shutil.get_terminal_size(fallback=(160,48)).columns
    left_w = max(40, int(win_w * 0.50))
    tmux("set-option","-t",target,"main-pane-width",str(left_w))
    tmux("resize-pane","-t",lt,"-x",str(left_w))
    rc,_,err = tmux("split-window","-v","-t",lt)
    if rc != 0:
        print(f"[TMUX] split lt vertical failed: {err.strip()}")
    tmux("set-option","-t",target,"destroy-unattached","on")
    code,out,_ = tmux("list-panes","-t",target,"-F","#{pane_id} #{pane_left} #{pane_top}")
    panes=[]
    for ln in out.splitlines():
        try:
            pid, left, top = ln.strip().split()
            panes.append((pid, int(left), int(top)))
        except Exception:
            pass
    if not panes:
        raise RuntimeError(f"No panes found in tmux window {target}. Ensure window has panes before calling tmux_build_tui_layout.")
    try:
        coords = sorted({p[1] for p in panes})
        left_x = coords[0]
        right_x = coords[1] if len(coords) > 1 else coords[0]
    except Exception:
        left_x = min(p[1] for p in panes)
        right_x = max(p[1] for p in panes)
    left_column = sorted([p for p in panes if p[1] == left_x], key=lambda x: x[2])
    if len(left_column) < 2:
        left_column = sorted(panes, key=lambda x: (x[1], x[2]))[:2]
    lt_id = left_column[0][0]
    lb_id = left_column[-1][0] if len(left_column) > 1 else left_column[0][0]
    right_column = sorted([p for p in panes if p[0] not in (lt_id, lb_id)], key=lambda x: x[2])
    if len(right_column) < 2:
        right_column = sorted([p for p in panes if p[1] == right_x], key=lambda x: x[2])
    rt_id = right_column[0][0] if right_column else lt_id
    rb_id = right_column[-1][0] if len(right_column) > 1 else rt_id
    positions={'lt': lt_id,'lb': lb_id,'rt': rt_id,'rb': rb_id}

    try:
        code, h, _ = tmux("display-message", "-p", "-t", target, "#{window_height}")
        win_h = int(h.strip()) if code == 0 and h.strip().isdigit() else shutil.get_terminal_size(fallback=(160, 48)).lines
    except Exception:
        win_h = shutil.get_terminal_size(fallback=(160, 48)).lines
    # TUI-first layout: prioritize TUI window (needs ~38 lines for setup view)
    # log window: min 5 lines, max 8 lines, ensuring TUI gets at least 38 lines when possible
    desired_log = max(5, min(8, win_h - 38))
    try:
        code, out, _ = tmux("list-panes", "-t", target, "-F", "#{pane_id} #{pane_height}")
        current_log = None
        if code == 0:
            for ln in out.splitlines():
                parts = ln.strip().split()
                if len(parts) == 2 and parts[1].isdigit():
                    if parts[0] == positions['lb']:
                        current_log = int(parts[1])
                        break
        if current_log is not None:
            delta = current_log - desired_log
            if delta > 0:
                # log pane too large, shrink it (-D contracts downward), free space for TUI
                tmux("resize-pane", "-t", positions['lb'], "-D", str(delta))
            elif delta < 0:
                # log pane too small, expand it (-U extends upward), take space from TUI
                tmux("resize-pane", "-t", positions['lb'], "-U", str(-delta))
    except Exception:
        pass
    try:
        tmux("select-pane", "-t", positions['lt'])
    except Exception:
        pass
    return positions

def tmux_ensure_quadrants(session: str, ledger_path: Path):
    # legacy compatibility helper
    try:
        tmux_build_tui_layout(session, '0')
        tmux_ensure_ledger_tail(session, ledger_path)
    except Exception:
        pass

def tmux_paste(pane: str, text: str):
    tmux("send-keys","-t",pane,text)

def tmux_type(pane: str, text: str):
    tmux("send-keys","-t",pane,text)

def tmux_capture(pane: str, lines: int=800) -> str:
    code,out,_ = tmux("capture-pane","-t",pane,"-p","-S",f"-{int(lines)}")
    return out if code==0 else ""

def tmux_start_interactive(pane: str, cmd: str, *, stderr_log: Optional[str] = None, remain_on_exit: bool = False) -> bool:
    """
    Start an interactive command in a tmux pane

    Args:
        pane: Target pane identifier (e.g., "session:0.1")
        cmd: Command to execute
        stderr_log: Optional absolute path to redirect stderr (append mode)
        remain_on_exit: If True, pane remains visible after process exits

    Returns:
        True if command was started successfully, False otherwise
    """
    # Build command with stderr redirection if specified
    if stderr_log:
        from pathlib import Path
        # Ensure log directory exists before redirecting
        log_dir = Path(stderr_log).parent
        cmd_with_prep = f"mkdir -p {shlex.quote(str(log_dir))} && {cmd} 2>> {shlex.quote(stderr_log)}"
    else:
        cmd_with_prep = cmd

    # Respawn pane with command
    success = tmux_respawn_pane(pane, f"bash -lc {shlex.quote(cmd_with_prep)}")

    # Set remain-on-exit for this pane if requested
    # This keeps the pane visible after process exits, allowing inspection
    if remain_on_exit:
        tmux("set-option", "-p", "-t", pane, "remain-on-exit", "on")

    return success

def wait_for_ready(pane: str, profile: Dict[str,Any], *, timeout: float = 12.0, poke: bool = True) -> bool:
    # heuristic: look for a prompt growth; keep compatible semantics
    t0=time.time(); base = len(tmux_capture(pane, lines=200))
    while time.time()-t0 < timeout:
        cur=len(tmux_capture(pane, lines=200))
        if cur>base+3:
            return True
        time.sleep(0.2)
    return False

# END copy
