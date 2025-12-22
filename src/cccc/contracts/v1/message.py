from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageData(BaseModel):
    text: str
    format: Literal["plain", "markdown"] = "plain"
    to: List[str] = Field(default_factory=list)
    thread: str = ""
    refs: List[Dict[str, Any]] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

