from __future__ import annotations

import os
import shlex
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import subprocess


def _run_tmux(args: List[str], *, timeout_s: float = 3.0) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(
            ["tmux", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return int(p.returncode), (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", "tmux timeout"
    except Exception as e:
        return 1, "", str(e)


def session_name(group_id: str) -> str:
    return f"cccc-{group_id}"


def has_session(session: str) -> bool:
    code, _, _ = _run_tmux(["has-session", "-t", session])
    return code == 0


def ensure_session(session: str) -> None:
    if has_session(session):
        return
    code, _, err = _run_tmux(["new-session", "-d", "-s", session, "-n", "system"])
    if code != 0:
        raise RuntimeError(f"tmux new-session failed: {err.strip()}")


def _list_window_names(session: str) -> List[str]:
    code, out, _ = _run_tmux(["list-windows", "-t", session, "-F", "#{window_name}"])
    if code != 0:
        return []
    return [ln.strip() for ln in (out or "").splitlines() if ln.strip()]


def has_window(session: str, window: str) -> bool:
    return window in set(_list_window_names(session))


def kill_window(session: str, window: str) -> None:
    _run_tmux(["kill-window", "-t", f"{session}:{window}"])


def pane_target(session: str, window: str) -> str:
    return f"{session}:{window}.0"


def _pane_dead(pane: str) -> bool:
    code, out, _ = _run_tmux(["display-message", "-p", "-t", pane, "#{pane_dead}"])
    if code != 0:
        return False
    return (out or "").strip() in ("1", "yes", "on", "true")


def ensure_window(
    session: str,
    *,
    window: str,
    cwd: Path,
    command: List[str],
    env: Optional[Dict[str, str]] = None,
) -> str:
    ensure_session(session)

    win = window.strip()
    if not win:
        raise ValueError("missing window name")

    cwd_path = cwd.expanduser().resolve()
    if not cwd_path.exists():
        cwd_path = Path.cwd()

    if has_window(session, win):
        pane = pane_target(session, win)
        if _pane_dead(pane):
            kill_window(session, win)
        else:
            return pane

    code, _, err = _run_tmux(["new-window", "-t", session, "-n", win, "-c", str(cwd_path)])
    if code != 0:
        raise RuntimeError(f"tmux new-window failed: {err.strip()}")

    pane = pane_target(session, win)
    cmd = [c for c in (command or []) if isinstance(c, str) and c.strip()]
    if not cmd:
        return pane

    env_prefix = ""
    if env:
        parts = []
        for k, v in env.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if not isinstance(v, str):
                continue
            parts.append(f"{k.strip()}={shlex.quote(v)}")
        if parts:
            env_prefix = "env " + " ".join(parts) + " "

    line = env_prefix + " ".join(shlex.quote(x) for x in cmd)
    _run_tmux(["send-keys", "-t", pane, "-l", line])
    _run_tmux(["send-keys", "-t", pane, "Enter"])
    return pane


def paste_text(pane: str, text: str, *, post_keys: Optional[List[str]] = None) -> None:
    # Ensure pane is not in copy-mode
    try:
        code, out, _ = _run_tmux(["display-message", "-p", "-t", pane, "#{pane_in_mode}"])
        if code == 0 and (out or "").strip() in ("1", "on", "yes", "true"):
            _run_tmux(["send-keys", "-t", pane, "-X", "cancel"])
    except Exception:
        pass

    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
        f.write(text)
        fname = f.name

    buf = f"buf-{int(time.time()*1000)}"
    _run_tmux(["load-buffer", "-b", buf, fname])
    _run_tmux(["paste-buffer", "-p", "-t", pane, "-b", buf])
    time.sleep(0.15)

    keys = list(post_keys or [])
    for k in keys:
        if not isinstance(k, str) or not k.strip():
            continue
        _run_tmux(["send-keys", "-t", pane, k.strip()])

    _run_tmux(["delete-buffer", "-b", buf])
    try:
        os.unlink(fname)
    except Exception:
        pass

