# -*- coding: utf-8 -*-
from __future__ import annotations
import sys, select

def sanitize_console(s: str) -> str:
    try:
        return s.encode("utf-8", "replace").decode("utf-8", "replace")
    except Exception:
        return s

def read_console_line(prompt: str) -> str:
    try:
        s = input(prompt)
    except Exception:
        s = sys.stdin.readline()
    return sanitize_console(s)

def read_console_line_timeout(prompt: str, timeout_sec: float) -> str:
    try:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        r, _, _ = select.select([sys.stdin], [], [], max(0.0, float(timeout_sec)))
        if r:
            line = sys.stdin.readline()
            return sanitize_console(line)
        return ""
    except Exception:
        try:
            return input(prompt)
        except Exception:
            return ""

