"""
Daemon operation handlers.

拆分 daemon/server.py 中的操作处理逻辑，按功能分组：
- group_ops: group 相关操作
- actor_ops: actor 相关操作
- message_ops: 消息相关操作
- context_ops: context 相关操作
- runner_ops: runner 相关操作 (PTY + headless)
"""

from __future__ import annotations

