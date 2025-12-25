from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from ..contracts.v1 import SystemNotifyData
from ..kernel.actors import list_actors
from ..kernel.group import Group, load_group
from ..kernel.inbox import unread_messages
from ..kernel.ledger import append_event
from ..runners import pty as pty_runner
from ..runners import headless as headless_runner
from ..util.fs import atomic_write_json, read_json
from ..util.time import parse_utc_iso, utc_now_iso
from .delivery import inject_system_prompt


@dataclass(frozen=True)
class DeliveryConfig:
    nudge_after_seconds: int
    self_check_every_handoffs: int
    system_refresh_every_self_checks: int


def _cfg(group: Group) -> DeliveryConfig:
    doc = group.doc.get("delivery")
    d = doc if isinstance(doc, dict) else {}

    def _int(key: str, default: int) -> int:
        try:
            v = int(d.get(key) if key in d else default)
        except Exception:
            v = int(default)
        return max(0, v)

    return DeliveryConfig(
        nudge_after_seconds=_int("nudge_after_seconds", 300),
        self_check_every_handoffs=_int("self_check_every_handoffs", 6),
        system_refresh_every_self_checks=_int("system_refresh_every_self_checks", 3),
    )


def _state_path(group: Group) -> Path:
    return group.path / "state" / "automation.json"


def _load_state(group: Group) -> Dict[str, Any]:
    doc = read_json(_state_path(group))
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("v", 1)
    actors = doc.get("actors")
    if not isinstance(actors, dict):
        actors = {}
        doc["actors"] = actors
    return doc


def _save_state(group: Group, doc: Dict[str, Any]) -> None:
    doc["updated_at"] = utc_now_iso()
    atomic_write_json(_state_path(group), doc)


def _actor_state(doc: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    actors = doc.get("actors")
    if not isinstance(actors, dict):
        actors = {}
        doc["actors"] = actors
    st = actors.get(actor_id)
    if not isinstance(st, dict):
        st = {}
        actors[actor_id] = st
    return st


class AutomationManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def tick(self, *, home: Path) -> None:
        base = home / "groups"
        if not base.exists():
            return
        for p in base.glob("*/group.yaml"):
            gid = p.parent.name
            group = load_group(gid)
            if group is None:
                continue
            if not bool(group.doc.get("running", False)):
                continue
            try:
                self._tick_group(group)
            except Exception:
                continue

    def _tick_group(self, group: Group) -> None:
        cfg = _cfg(group)
        if cfg.nudge_after_seconds <= 0:
            return

        now = datetime.now(timezone.utc)
        to_nudge: list[Tuple[str, str]] = []  # (actor_id, oldest_event_ts)

        with self._lock:
            state = _load_state(group)
            for actor in list_actors(group):
                if not isinstance(actor, dict):
                    continue
                aid = str(actor.get("id") or "").strip()
                if not aid:
                    continue
                if not bool(actor.get("enabled", True)):
                    continue
                # 检查 actor 是否在运行（PTY 或 headless）
                runner_kind = str(actor.get("runner") or "pty").strip()
                if runner_kind == "headless":
                    if not headless_runner.SUPERVISOR.actor_running(group.group_id, aid):
                        continue
                else:
                    if not pty_runner.SUPERVISOR.actor_running(group.group_id, aid):
                        continue

                msgs = unread_messages(group, actor_id=aid, limit=1)
                if not msgs:
                    continue
                oldest = msgs[0]
                ev_id = str(oldest.get("id") or "").strip()
                ev_ts = str(oldest.get("ts") or "").strip()
                if not ev_id or not ev_ts:
                    continue

                dt = parse_utc_iso(ev_ts)
                if dt is None:
                    continue
                age_s = (now - dt).total_seconds()
                if age_s < float(cfg.nudge_after_seconds):
                    continue

                st = _actor_state(state, aid)
                if str(st.get("last_nudge_event_id") or "") == ev_id:
                    continue
                st["last_nudge_event_id"] = ev_id
                st["last_nudge_at"] = utc_now_iso()
                to_nudge.append((aid, ev_ts))

            if to_nudge:
                _save_state(group, state)

        for aid, ev_ts in to_nudge:
            # 写入 system.notify 事件到 ledger
            notify_data = SystemNotifyData(
                kind="nudge",
                priority="normal",
                title="Unread messages waiting",
                message=f"Oldest unread message from {ev_ts}. Use cccc_inbox_list to check.",
                target_actor_id=aid,
                requires_ack=False,
            )
            append_event(
                group.ledger_path,
                kind="system.notify",
                group_id=group.group_id,
                scope_key="",
                by="system",
                data=notify_data.model_dump(),
            )

    def on_delivered_message(self, group: Group, *, actor: Dict[str, Any], by: str) -> None:
        who = str(by or "").strip()
        if not who or who == "system":
            return
        aid = str(actor.get("id") or "").strip()
        if not aid:
            return
        cfg = _cfg(group)
        if cfg.self_check_every_handoffs <= 0:
            return

        send_self_check = False
        send_system_refresh = False
        with self._lock:
            state = _load_state(group)
            st = _actor_state(state, aid)
            handoffs = int(st.get("handoff_count") or 0) + 1
            st["handoff_count"] = handoffs
            if handoffs % int(cfg.self_check_every_handoffs) == 0:
                send_self_check = True
                self_checks = int(st.get("self_check_count") or 0) + 1
                st["self_check_count"] = self_checks
                if cfg.system_refresh_every_self_checks > 0 and self_checks % int(cfg.system_refresh_every_self_checks) == 0:
                    send_system_refresh = True
            _save_state(group, state)

        if send_self_check:
            # 写入 system.notify 事件到 ledger
            notify_data = SystemNotifyData(
                kind="self_check",
                priority="normal",
                title="Self-check requested",
                message="Reply in 3 bullets: (1) what changed, (2) next step, (3) blocker/decision.",
                target_actor_id=aid,
                requires_ack=False,
            )
            append_event(
                group.ledger_path,
                kind="system.notify",
                group_id=group.group_id,
                scope_key="",
                by="system",
                data=notify_data.model_dump(),
            )
        if send_system_refresh:
            # 写入 system.notify 事件到 ledger（通知即将刷新 SYSTEM）
            notify_data = SystemNotifyData(
                kind="system_refresh",
                priority="low",
                title="SYSTEM prompt refreshed",
                message="Your SYSTEM prompt has been updated.",
                target_actor_id=aid,
                requires_ack=False,
            )
            append_event(
                group.ledger_path,
                kind="system.notify",
                group_id=group.group_id,
                scope_key="",
                by="system",
                data=notify_data.model_dump(),
            )
            inject_system_prompt(group, actor=actor)
