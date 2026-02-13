"""
Entry point for running the news agent as a module.

Usage:
    python -m cccc.ports.news <group_id> [interests] [schedule]

Arguments:
    group_id    CCCC group ID to broadcast news to
    interests   Comma-separated interest keywords (default: AI,科技,编程,股市,美股,A股)
    schedule    Comma-separated hours for briefings in 24h format (default: 8,11,14,17,20)
"""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m cccc.ports.news <group_id> [interests] [schedule]")
        print("  interests: comma-separated keywords (default: AI,科技,编程,股市,美股,A股)")
        print("  schedule:  comma-separated hours in 24h format (default: 8,11,14,17,20)")
        return 1

    group_id = sys.argv[1]
    interests = sys.argv[2] if len(sys.argv) > 2 else "AI,科技,编程,股市,美股,A股"
    schedule = sys.argv[3] if len(sys.argv) > 3 else "8,11,14,17,20"

    from .agent import start_agent

    try:
        start_agent(group_id, interests=interests, schedule=schedule)
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1
    except Exception as e:
        print(f"[news] error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
