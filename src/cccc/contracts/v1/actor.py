from __future__ import annotations

from typing import Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...util.time import utc_now_iso


ActorRole = Literal["foreman", "peer"]
ActorSubmit = Literal["enter", "newline", "none"]


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
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    model_config = ConfigDict(extra="forbid")
