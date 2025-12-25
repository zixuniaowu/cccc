from __future__ import annotations

import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ...util.time import utc_now_iso
from .actor import Actor, ActorRole, ActorSubmit
from .message import ChatMessageData, ChatReactionData
from .notify import NotifyAckData, SystemNotifyData


EventKind = Literal[
    "group.create",
    "group.update",
    "group.attach",
    "group.detach_scope",
    "group.set_active_scope",
    "group.start",
    "group.stop",
    "actor.add",
    "actor.update",
    "actor.set_role",
    "actor.start",
    "actor.stop",
    "actor.restart",
    "actor.remove",
    "chat.message",
    "chat.read",
    "chat.reaction",
    "system.notify",
    "system.notify_ack",
]


class GroupCreateData(BaseModel):
    title: str
    topic: str = ""

    model_config = ConfigDict(extra="forbid")


class GroupUpdatePatch(BaseModel):
    title: Optional[str] = None
    topic: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class GroupUpdateData(BaseModel):
    patch: GroupUpdatePatch

    model_config = ConfigDict(extra="forbid")


class GroupAttachData(BaseModel):
    url: str
    label: str = ""
    git_remote: str = ""

    model_config = ConfigDict(extra="forbid")


class GroupDetachScopeData(BaseModel):
    scope_key: str

    model_config = ConfigDict(extra="forbid")


class GroupSetActiveScopeData(BaseModel):
    path: str

    model_config = ConfigDict(extra="forbid")


class GroupStartData(BaseModel):
    started: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class GroupStopData(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActorAddData(BaseModel):
    actor: Actor

    model_config = ConfigDict(extra="forbid")


class ActorUpdatePatch(BaseModel):
    role: Optional[ActorRole] = None
    title: Optional[str] = None
    command: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    default_scope_key: Optional[str] = None
    submit: Optional[ActorSubmit] = None
    enabled: Optional[bool] = None

    model_config = ConfigDict(extra="forbid")


class ActorUpdateData(BaseModel):
    actor_id: str
    patch: ActorUpdatePatch

    model_config = ConfigDict(extra="forbid")


class ActorSetRoleData(BaseModel):
    actor_id: str
    role: ActorRole

    model_config = ConfigDict(extra="forbid")


class ActorLifecycleData(BaseModel):
    actor_id: str

    model_config = ConfigDict(extra="forbid")


class ChatReadData(BaseModel):
    """已读回执：actor 标记已读到某条消息"""

    actor_id: str  # 谁标记的
    event_id: str  # 已读到哪条消息（含之前所有）

    model_config = ConfigDict(extra="forbid")


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


_KIND_TO_MODEL = {
    "group.create": GroupCreateData,
    "group.update": GroupUpdateData,
    "group.attach": GroupAttachData,
    "group.detach_scope": GroupDetachScopeData,
    "group.set_active_scope": GroupSetActiveScopeData,
    "group.start": GroupStartData,
    "group.stop": GroupStopData,
    "actor.add": ActorAddData,
    "actor.update": ActorUpdateData,
    "actor.set_role": ActorSetRoleData,
    "actor.start": ActorLifecycleData,
    "actor.stop": ActorLifecycleData,
    "actor.restart": ActorLifecycleData,
    "actor.remove": ActorLifecycleData,
    "chat.message": ChatMessageData,
    "chat.read": ChatReadData,
    "chat.reaction": ChatReactionData,
    "system.notify": SystemNotifyData,
    "system.notify_ack": NotifyAckData,
}


def normalize_event_data(kind: str, data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        data = {} if data is None else {"value": data}
    model = _KIND_TO_MODEL.get(str(kind))
    if model is None:
        # Unknown event kind: keep the envelope stable, keep data as a dict.
        return dict(data)
    parsed = model.model_validate(data)
    payload = parsed.model_dump()
    if kind == "group.update":
        patch = payload.get("patch") if isinstance(payload, dict) else None
        if isinstance(patch, dict) and not any(patch.get(k) is not None for k in ("title", "topic")):
            raise ValueError("group.update patch must include title and/or topic")
    if kind == "actor.update":
        patch = payload.get("patch") if isinstance(payload, dict) else None
        if isinstance(patch, dict) and not patch:
            raise ValueError("actor.update patch cannot be empty")
    return payload
