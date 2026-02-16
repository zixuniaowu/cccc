"""
Entry point for running the news agent as a module.

Usage:
    python -m cccc.ports.news <group_id> [interests] [schedule] [mode]
    python -m cccc.ports.news <group_id> [interests] [schedule] --mode=<mode>

Arguments:
    group_id    CCCC group ID to broadcast news to
    interests   Comma-separated interest keywords
    schedule    Comma-separated hours for briefings in 24h format
    mode        news | market | ai_long | horror | all
"""
from __future__ import annotations

import os
import sys


def _normalize_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    if m in ("horror", "horror_story", "story", "ghost"):
        return "horror"
    if m in ("news", "market", "ai_long", "horror", "all"):
        return m
    return "news"


def _default_interests(mode: str) -> str:
    m = _normalize_mode(mode)
    if m == "market":
        return "股市,美股,A股,港股,宏观,财报"
    if m == "ai_long":
        return "CCCC,框架,多Agent,协作,消息总线,语音播报"
    if m == "horror":
        return "深夜,公寓,都市传说,悬疑,心理惊悚"
    if m == "all":
        return "AI,科技,编程,股市,美股,A股"
    return "AI,科技,编程"


def _default_schedule(mode: str) -> str:
    m = _normalize_mode(mode)
    if m == "market":
        return "9,12,15,18,22"
    if m == "ai_long":
        return "10,16,21"
    if m == "horror":
        return "21,23,1"
    return "8,11,14,17,20"


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m cccc.ports.news <group_id> [interests] [schedule] [mode]")
        print("  mode:      news | market | ai_long | horror | all")
        return 1

    mode = _normalize_mode(os.environ.get("NEWS_AGENT_MODE", "news"))
    args: list[str] = []
    i = 1
    while i < len(sys.argv):
        a = str(sys.argv[i] or "").strip()
        if a.startswith("--mode="):
            mode = _normalize_mode(a.split("=", 1)[1])
        elif a == "--mode" and i + 1 < len(sys.argv):
            mode = _normalize_mode(sys.argv[i + 1])
            i += 1
        else:
            args.append(a)
        i += 1

    if not args:
        print("Usage: python -m cccc.ports.news <group_id> [interests] [schedule] [mode]")
        return 1

    group_id = args[0]
    interests = args[1] if len(args) > 1 else _default_interests(mode)
    schedule = args[2] if len(args) > 2 else _default_schedule(mode)
    if len(args) > 3:
        mode = _normalize_mode(args[3])

    from .agent import start_agent

    try:
        start_agent(group_id, interests=interests, schedule=schedule, mode=mode)
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1
    except Exception as e:
        print(f"[news] error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
