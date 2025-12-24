from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from typing import Optional

import uvicorn

from ...daemon.server import call_daemon


def _ensure_daemon_running() -> bool:
    resp = call_daemon({"op": "ping"})
    if resp.get("ok"):
        return True
    try:
        subprocess.run(
            [sys.executable, "-m", "cccc.daemon_main", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=os.environ.copy(),
        )
    except Exception:
        return False
    for _ in range(30):
        time.sleep(0.05)
        resp = call_daemon({"op": "ping"})
        if resp.get("ok"):
            return True
    return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cccc web", description="cccc web port (FastAPI)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8848, help="Bind port (default: 8848)")
    parser.add_argument("--reload", action="store_true", help="Enable autoreload (dev)")
    parser.add_argument("--log-level", default="info", help="Uvicorn log level (default: info)")
    args = parser.parse_args(argv)

    if not _ensure_daemon_running():
        print("error: ccccd daemon failed to start", file=sys.stderr)
        return 1

    uvicorn.run(
        "cccc.ports.web.app:create_app",
        factory=True,
        host=str(args.host),
        port=int(args.port),
        log_level=str(args.log_level),
        reload=bool(args.reload),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
