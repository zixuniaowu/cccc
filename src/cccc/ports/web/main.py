from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Optional

import uvicorn

from ...daemon.server import call_daemon


def _ensure_windows_selector_event_loop_policy() -> None:
    """Avoid Proactor accept instability on Windows under long-lived connections."""
    if os.name != "nt":
        return
    try:
        policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if policy_cls is None:
            return
        current = asyncio.get_event_loop_policy()
        if not isinstance(current, policy_cls):
            asyncio.set_event_loop_policy(policy_cls())
    except Exception:
        pass


def _check_daemon_running() -> bool:
    """Check if daemon is running (don't start it)."""
    resp = call_daemon({"op": "ping"})
    return resp.get("ok", False)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cccc web", description="cccc web port (FastAPI)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8848, help="Bind port (default: 8848)")
    parser.add_argument(
        "--mode",
        default=str(os.environ.get("CCCC_WEB_MODE") or "normal"),
        choices=["normal", "exhibit"],
        help="Web mode: normal (read/write) or exhibit (read-only) (default: normal)",
    )
    parser.add_argument("--exhibit", action="store_true", help="Shortcut for: --mode exhibit")
    parser.add_argument("--reload", action="store_true", help="Enable autoreload (dev)")
    parser.add_argument("--log-level", default="info", help="Uvicorn log level (default: info)")
    args = parser.parse_args(argv)
    if bool(getattr(args, "exhibit", False)):
        args.mode = "exhibit"

    os.environ["CCCC_WEB_MODE"] = str(args.mode or "normal")

    # Check daemon is running (don't auto-start)
    if not _check_daemon_running():
        print("error: daemon is not running. Start it with: cccc daemon start", file=sys.stderr)
        print("  or use: cccc (to start both daemon and web together)", file=sys.stderr)
        return 1

    _ensure_windows_selector_event_loop_policy()

    try:
        uvicorn.run(
            "cccc.ports.web.app:create_app",
            factory=True,
            host=str(args.host),
            port=int(args.port),
            log_level=str(args.log_level),
            loop="cccc.util.uvicorn_loop:create_safe_event_loop",
            reload=bool(args.reload),
        )
    except (KeyboardInterrupt, SystemExit):
        pass
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
