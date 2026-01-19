from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Reference(BaseModel):
    """Reference to a file/commit/URL/text snippet."""

    kind: Literal["file", "url", "commit", "text"] = "url"
    url: str = ""
    path: str = ""
    title: str = ""
    sha: str = ""
    bytes: int = 0

    model_config = ConfigDict(extra="allow")


class Attachment(BaseModel):
    """Attachment metadata (payload stored under blobs/)."""

    kind: Literal["text", "image", "file"] = "file"
    path: str = ""
    title: str = ""
    mime_type: str = ""
    bytes: int = 0
    sha256: str = ""

    model_config = ConfigDict(extra="allow")


class ChatMessageData(BaseModel):
    """IM-style chat message."""

    # Core content
    text: str
    format: Literal["plain", "markdown"] = "plain"

    # Priority / workflow semantics
    priority: Literal["normal", "attention"] = "normal"

    # IM semantics
    to: List[str] = Field(default_factory=list)  # @mentions (empty = broadcast)
    reply_to: Optional[str] = None  # The replied-to message event_id
    quote_text: Optional[str] = None  # Quoted snippet for display

    # Cross-group provenance (for relays/forwarding)
    src_group_id: Optional[str] = None
    src_event_id: Optional[str] = None

    # Cross-group destination metadata (for "send to other group" source messages)
    dst_group_id: Optional[str] = None
    dst_to: Optional[List[str]] = None

    # Attachments and references
    refs: List[Dict[str, Any]] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)

    # Reserved
    thread: str = ""  # Topic/thread ID (future)

    # Metadata
    client_id: Optional[str] = None  # Client-generated idempotency key

    model_config = ConfigDict(extra="forbid")


class ChatReactionData(BaseModel):
    """Message reaction (emoji)."""

    event_id: str  # Target message event_id
    actor_id: str  # Actor who reacted
    emoji: str  # Emoji reaction (e.g., ✅/❌/👍/🤔)

    model_config = ConfigDict(extra="forbid")
