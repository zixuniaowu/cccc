"""System notification contracts.

系统通知与聊天消息分离，避免把系统噪音塞进用户对话。

通知类型：
- nudge: 提醒 actor 处理未读消息
- keepalive: 提醒 actor 继续工作（检测到 Next: 声明后）
- actor_idle: Actor 空闲通知（发给 foreman）
- silence_check: 群聊静默通知（发给 foreman）
- standup: 周期性 stand-up 提醒（发给 foreman）
- status_change: actor/group 状态变更
- error: 系统错误通知
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


NotifyKind = Literal[
    "nudge",           # 提醒处理未读消息
    "keepalive",       # 提醒继续工作（检测到 Next: 声明后）
    "actor_idle",      # Actor 空闲通知（发给 foreman）
    "silence_check",   # 群聊静默通知（发给 foreman）
    "standup",         # 周期性 stand-up 提醒（发给 foreman）
    "status_change",   # 状态变更通知
    "error",           # 错误通知
    "info",            # 一般信息
]

NotifyPriority = Literal["low", "normal", "high", "urgent"]


class SystemNotifyData(BaseModel):
    """系统通知数据结构"""

    # 通知类型
    kind: NotifyKind
    priority: NotifyPriority = "normal"

    # 通知内容
    title: str = ""
    message: str = ""

    # 目标
    target_actor_id: Optional[str] = None  # 目标 actor（None=广播）

    # 上下文
    context: Dict[str, Any] = Field(default_factory=dict)

    # 是否需要确认
    requires_ack: bool = False

    # 关联事件
    related_event_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class NotifyAckData(BaseModel):
    """通知确认数据"""

    notify_event_id: str  # 被确认的通知 event_id
    actor_id: str         # 确认者

    model_config = ConfigDict(extra="forbid")
