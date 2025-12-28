"""
Entry point for running IM bridge as a module.

Usage:
    python -m cccc.ports.im.bridge <group_id> [platform]
"""

from __future__ import annotations

import sys

from .bridge import start_bridge


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m cccc.ports.im.bridge <group_id> [platform]")
        print("  platform: telegram (default), slack, discord")
        return 1

    group_id = sys.argv[1]
    platform = sys.argv[2] if len(sys.argv) > 2 else "telegram"

    try:
        start_bridge(group_id, platform)
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1
    except Exception as e:
        print(f"[error] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
