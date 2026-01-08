from __future__ import annotations

from . import headless

try:
    from . import pty
except Exception:
    # Windows (and some Python builds) lack POSIX PTY dependencies (pty/termios/fcntl).
    from . import pty_stub as pty  # type: ignore

__all__ = ["headless", "pty"]
