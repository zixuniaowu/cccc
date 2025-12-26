from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import uvicorn

from ...daemon.server import call_daemon


def _check_daemon_running() -> bool:
    """Check if daemon is running (don't start it)."""
    resp = call_daemon({"op": "ping"})
    return resp.get("ok", False)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cccc web", description="cccc web port (FastAPI)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8848, help="Bind port (default: 8848)")
    parser.add_argument("--reload", action="store_true", help="Enable autoreload (dev)")
    parser.add_argument("--log-level", default="info", help="Uvicorn log level (default: info)")
    args = parser.parse_args(argv)

    # Check daemon is running (don't auto-start)
    if not _check_daemon_running():
        print("error: daemon is not running. Start it with: cccc daemon start", file=sys.stderr)
        print("  or use: cccc (to start both daemon and web together)", file=sys.stderr)
        return 1

    try:
        uvicorn.run(
            "cccc.ports.web.app:create_app",
            factory=True,
            host=str(args.host),
            port=int(args.port),
            log_level=str(args.log_level),
            reload=bool(args.reload),
        )
    except (KeyboardInterrupt, SystemExit):
        pass
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
