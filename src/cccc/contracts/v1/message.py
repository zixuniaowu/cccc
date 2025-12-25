from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Reference(BaseModel):
    """引用：文件/commit/URL 等"""

    kind: Literal["file", "url", "commit", "text"] = "url"
    url: str = ""
    path: str = ""
    title: str = ""
    sha: str = ""
    bytes: int = 0

    model_config = ConfigDict(extra="allow")


class Attachment(BaseModel):
    """附件元信息（实际内容存储在 blobs 目录）"""

    kind: Literal["text", "image", "file"] = "file"
    path: str = ""
    title: str = ""
    mime_type: str = ""
    bytes: int = 0
    sha256: str = ""

    model_config = ConfigDict(extra="allow")


class ChatMessageData(BaseModel):
    """IM 风格的聊天消息"""

    # 核心内容
    text: str
    format: Literal["plain", "markdown"] = "plain"

    # IM 核心语义
    to: List[str] = Field(default_factory=list)  # @mention 收件人（空=广播）
    reply_to: Optional[str] = None  # 回复哪条消息（event_id）
    quote_text: Optional[str] = None  # 被引用消息的文本片段（便于展示）

    # 附件与引用
    refs: List[Dict[str, Any]] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)

    # 预留
    thread: str = ""  # 话题/线程 ID（后置）

    # 元数据
    client_id: Optional[str] = None  # 客户端去重 ID（幂等）

    model_config = ConfigDict(extra="forbid")


class ChatReactionData(BaseModel):
    """消息反应（emoji）"""

    event_id: str  # 对哪条消息
    actor_id: str  # 谁发的
    emoji: str  # 反应符号（✅/❌/👍/🤔）

    model_config = ConfigDict(extra="forbid")

