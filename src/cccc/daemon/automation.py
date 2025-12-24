from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from ..kernel.actors import list_actors
from ..kernel.group import Group, load_group
from ..kernel.inbox import unread_messages
from ..runners import pty as pty_runner
from ..util.fs import atomic_write_json, read_json
from ..util.time import parse_utc_iso, utc_now_iso
from .delivery import inject_system_prompt, pty_submit_text


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
            msg = (
                f"[cccc] NUDGE: unread message waiting (oldest {ev_ts}). "
                f"Run: cccc inbox --actor-id {aid} --by {aid} --mark-read"
            )
            pty_submit_text(group, actor_id=aid, text=msg, file_fallback=False)

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
            text = (
                "[cccc] SELF-CHECK: reply in 3 bullets — (1) what changed, (2) next step, (3) blocker/decision. "
                f"Clear inbox if needed: cccc inbox --actor-id {aid} --by {aid} --mark-read"
            )
            pty_submit_text(group, actor_id=aid, text=text, file_fallback=False)
        if send_system_refresh:
            inject_system_prompt(group, actor=actor)
