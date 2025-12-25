from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ...util.time import utc_now_iso


ActorRole = Literal["foreman", "peer"]
ActorSubmit = Literal["enter", "newline", "none"]
RunnerKind = Literal["pty", "headless"]
AgentRuntime = Literal["claude", "codex", "droid", "opencode", "custom"]


class Actor(BaseModel):
    v: int = 1
    id: str
    role: ActorRole
    title: str = ""
    command: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    default_scope_key: str = ""
    submit: ActorSubmit = "enter"
    enabled: bool = True
    runner: RunnerKind = "pty"  # "pty" for interactive, "headless" for MCP-driven
    runtime: AgentRuntime = "custom"  # Agent CLI runtime (claude, codex, droid, opencode, custom)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    model_config = ConfigDict(extra="forbid")


class HeadlessState(BaseModel):
    """Runtime state for a headless actor session."""
    v: int = 1
    group_id: str
    actor_id: str
    status: Literal["idle", "working", "waiting", "stopped"] = "idle"
    current_task_id: Optional[str] = None
    last_message_id: Optional[str] = None
    started_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    model_config = ConfigDict(extra="forbid")
