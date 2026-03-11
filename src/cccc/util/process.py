from __future__ import annotations

import os
import signal
import time
from typing import Union

SignalValue = Union[int, signal.Signals]
SOFT_TERMINATE_SIGNAL: SignalValue = getattr(signal, "SIGTERM", signal.SIGINT)
HARD_TERMINATE_SIGNAL: SignalValue = getattr(signal, "SIGKILL", SOFT_TERMINATE_SIGNAL)


def pid_is_alive(pid: int) -> bool:
    """跨平台判断进程是否仍然存活。"""
    if int(pid or 0) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def best_effort_signal_pid(pid: int, sig: SignalValue, *, include_group: bool = True) -> bool:
    """尽力向进程发送信号；在 POSIX 上优先发给进程组。"""
    target_pid = int(pid or 0)
    if target_pid <= 0:
        return False

    delivered = False
    if include_group and os.name != "nt":
        try:
            os.killpg(target_pid, sig)
            delivered = True
        except Exception:
            pass

    if delivered:
        return True

    try:
        os.kill(target_pid, sig)
        return True
    except Exception:
        return False


def terminate_pid(
    pid: int,
    *,
    timeout_s: float = 1.0,
    include_group: bool = True,
    force: bool = False,
) -> bool:
    """尽力终止进程，并在需要时执行强制终止。"""
    target_pid = int(pid or 0)
    if target_pid <= 0:
        return True
    if not pid_is_alive(target_pid):
        return True

    best_effort_signal_pid(target_pid, SOFT_TERMINATE_SIGNAL, include_group=include_group)
    deadline = time.time() + max(float(timeout_s or 0.0), 0.0)
    while time.time() < deadline:
        if not pid_is_alive(target_pid):
            return True
        time.sleep(0.05)

    if not force:
        return not pid_is_alive(target_pid)

    best_effort_signal_pid(target_pid, HARD_TERMINATE_SIGNAL, include_group=include_group)
    deadline = time.time() + max(float(timeout_s or 0.0), 0.0)
    while time.time() < deadline:
        if not pid_is_alive(target_pid):
            return True
        time.sleep(0.05)
    return not pid_is_alive(target_pid)
