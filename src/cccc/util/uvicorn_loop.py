from __future__ import annotations

import asyncio
import sys


def create_safe_event_loop() -> asyncio.AbstractEventLoop:
    """Loop factory passed directly to asyncio.Runner(loop_factory=...)."""
    policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if sys.platform == "win32" and policy_cls is not None and not isinstance(asyncio.get_event_loop_policy(), policy_cls):
        asyncio.set_event_loop_policy(policy_cls())
    return asyncio.new_event_loop()
