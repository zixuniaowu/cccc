from __future__ import annotations

import uuid
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field

from ...util.time import utc_now_iso


class Event(BaseModel):
    v: int = 1
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = Field(default_factory=utc_now_iso)
    kind: str
    group_id: str
    scope_key: str = ""
    by: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

