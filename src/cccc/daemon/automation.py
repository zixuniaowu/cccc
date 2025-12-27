"""Automation manager for CCCC daemon.

Automation levels:
1. Message-level: nudge (unread timeout)
2. Session-level: actor idle detection, keepalive, group silence detection, standup

All automation respects group state:
- active: All automation enabled
- idle: Automation disabled (task complete, waiting for new work)
- paused: Automation disabled (user paused)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..contracts.v1 import SystemNotifyData
from ..kernel.actors import list_actors, find_foreman
from ..kernel.group import Group, load_group, get_group_state
from ..kernel.inbox import unread_messages, iter_events
from ..kernel.ledger import append_event
from ..runners import pty as pty_runner
from ..runners import headless as headless_runner
from ..util.fs import atomic_write_json, read_json
from ..util.time import parse_utc_iso, utc_now_iso


@dataclass(frozen=True)
class AutomationConfig:
    """Automation configuration for a group."""
    # Level 1: Message-level
    nudge_after_seconds: int          # Nudge actor if unread message older than this
    
    # Level 2: Session-level
    actor_idle_timeout_seconds: int   # Notify foreman if actor idle for this long
    keepalive_delay_seconds: int      # Send keepalive after Next: declaration
    keepalive_max_per_actor: int      # Max consecutive keepalives per actor
    silence_timeout_seconds: int      # Check group if silent for this long
    standup_interval_seconds: int     # Periodic standup reminder for foreman (0 to disable)


def _cfg(group: Group) -> AutomationConfig:
    """Load automation config from group.yaml."""
    doc = group.doc.get("automation")
    d = doc if isinstance(doc, dict) else {}

    def _int(key: str, default: int) -> int:
        try:
            v = int(d.get(key) if key in d else default)
        except Exception:
            v = int(default)
        return max(0, v)

    return AutomationConfig(
        # Level 1
        nudge_after_seconds=_int("nudge_after_seconds", 300),
        # Level 2
        actor_idle_timeout_seconds=_int("actor_idle_timeout_seconds", 600),
        keepalive_delay_seconds=_int("keepalive_delay_seconds", 120),
        keepalive_max_per_actor=_int("keepalive_max_per_actor", 3),
        silence_timeout_seconds=_int("silence_timeout_seconds", 600),
        standup_interval_seconds=_int("standup_interval_seconds", 900),  # Default 15 minutes
    )


def _state_path(group: Group) -> Path:
    return group.path / "state" / "automation.json"


def _load_state(group: Group) -> Dict[str, Any]:
    doc = read_json(_state_path(group))
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("v", 3)  # Bumped version for new schema
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


def _get_last_group_activity(group: Group) -> Optional[datetime]:
    """Get timestamp of last activity in the group (any event)."""
    last_ts: Optional[datetime] = None
    for ev in iter_events(group.ledger_path):
        ts_str = str(ev.get("ts") or "")
        if ts_str:
            dt = parse_utc_iso(ts_str)
            if dt is not None:
                last_ts = dt
    return last_ts


def _get_last_actor_activity(group: Group, actor_id: str) -> Optional[datetime]:
    """Get timestamp of last activity by a specific actor."""
    last_ts: Optional[datetime] = None
    for ev in iter_events(group.ledger_path):
        by = str(ev.get("by") or "")
        if by == actor_id:
            ts_str = str(ev.get("ts") or "")
            if ts_str:
                dt = parse_utc_iso(ts_str)
                if dt is not None:
                    last_ts = dt
    return last_ts


def _actor_declared_next(group: Group, actor_id: str) -> Optional[Tuple[str, datetime]]:
    """Check if actor's last message contains 'Next:' declaration.
    
    Returns (next_text, timestamp) if found, None otherwise.
    """
    last_next: Optional[Tuple[str, datetime]] = None
    for ev in iter_events(group.ledger_path):
        if str(ev.get("kind") or "") != "chat.message":
            continue
        if str(ev.get("by") or "") != actor_id:
            continue
        data = ev.get("data")
        if not isinstance(data, dict):
            continue
        text = str(data.get("text") or "")
        # Look for "Next:" pattern
        if "Next:" in text or "next:" in text:
            ts_str = str(ev.get("ts") or "")
            dt = parse_utc_iso(ts_str)
            if dt is not None:
                # Extract the Next: content
                for line in text.split("\n"):
                    if line.strip().lower().startswith("next:"):
                        last_next = (line.strip(), dt)
                        break
    return last_next


class AutomationManager:
    """Manages automation for all groups.
    
    Automation levels:
    1. Message-level: nudge (unread timeout)
    2. Session-level: actor idle detection, keepalive, group silence detection
    
    Automation respects group state:
    - active: All automation enabled
    - idle: Automation disabled (task complete, waiting for new work)
    - paused: Automation disabled (user paused)
    """
    
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def tick(self, *, home: Path) -> None:
        """Called periodically by daemon to check all groups."""
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
            # Check group state - skip automation if not active
            state = get_group_state(group)
            if state != "active":
                continue
            try:
                self._tick_group(group)
            except Exception:
                continue

    def _tick_group(self, group: Group) -> None:
        """Run all automation checks for a group."""
        cfg = _cfg(group)
        now = datetime.now(timezone.utc)
        
        # Level 1: Message-level checks
        self._check_nudge(group, cfg, now)
        
        # Level 2: Session-level checks
        self._check_actor_idle(group, cfg, now)
        self._check_keepalive(group, cfg, now)
        self._check_silence(group, cfg, now)
        self._check_standup(group, cfg, now)

    def _check_nudge(self, group: Group, cfg: AutomationConfig, now: datetime) -> None:
        """Check for actors with old unread messages and send nudge."""
        if cfg.nudge_after_seconds <= 0:
            return

        to_nudge: List[Tuple[str, str]] = []  # (actor_id, oldest_event_ts)

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
                # Check if actor is running
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
            notify_data = SystemNotifyData(
                kind="nudge",
                priority="normal",
                title="Unread messages waiting",
                message=f"You have unread messages (oldest from {ev_ts}). Use cccc_inbox_list() to check.",
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

    def _check_actor_idle(self, group: Group, cfg: AutomationConfig, now: datetime) -> None:
        """Check for idle actors and notify foreman."""
        if cfg.actor_idle_timeout_seconds <= 0:
            return

        foreman = find_foreman(group)
        if foreman is None:
            return  # No foreman to notify
        foreman_id = str(foreman.get("id") or "")

        to_notify: List[Tuple[str, float]] = []  # (actor_id, idle_seconds)

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
                # Skip foreman itself
                if aid == foreman_id:
                    continue
                # Check if actor is running
                runner_kind = str(actor.get("runner") or "pty").strip()
                if runner_kind == "headless":
                    if not headless_runner.SUPERVISOR.actor_running(group.group_id, aid):
                        continue
                else:
                    if not pty_runner.SUPERVISOR.actor_running(group.group_id, aid):
                        continue

                # Get last activity
                last_activity = _get_last_actor_activity(group, aid)
                if last_activity is None:
                    continue  # No activity yet, skip
                
                idle_seconds = (now - last_activity).total_seconds()
                if idle_seconds < float(cfg.actor_idle_timeout_seconds):
                    continue

                st = _actor_state(state, aid)
                last_idle_notify = st.get("last_idle_notify_at")
                if last_idle_notify:
                    last_notify_dt = parse_utc_iso(str(last_idle_notify))
                    if last_notify_dt is not None:
                        # Don't notify again within the timeout period
                        if (now - last_notify_dt).total_seconds() < float(cfg.actor_idle_timeout_seconds):
                            continue
                
                st["last_idle_notify_at"] = utc_now_iso()
                to_notify.append((aid, idle_seconds))

            if to_notify:
                _save_state(group, state)

        for aid, idle_seconds in to_notify:
            notify_data = SystemNotifyData(
                kind="actor_idle",
                priority="normal",
                title=f"Actor {aid} may need attention",
                message=f"Actor {aid} has been quiet for {int(idle_seconds)}s. They might be stuck or waiting for input.",
                target_actor_id=foreman_id,
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

    def _check_keepalive(self, group: Group, cfg: AutomationConfig, now: datetime) -> None:
        """Check for actors that declared Next: and send keepalive if needed."""
        if cfg.keepalive_delay_seconds <= 0:
            return

        to_keepalive: List[Tuple[str, str]] = []  # (actor_id, next_text)

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
                # Check if actor is running
                runner_kind = str(actor.get("runner") or "pty").strip()
                if runner_kind == "headless":
                    if not headless_runner.SUPERVISOR.actor_running(group.group_id, aid):
                        continue
                else:
                    if not pty_runner.SUPERVISOR.actor_running(group.group_id, aid):
                        continue

                # Check for Next: declaration
                next_info = _actor_declared_next(group, aid)
                if next_info is None:
                    continue
                next_text, next_ts = next_info
                
                # Check if enough time has passed
                elapsed = (now - next_ts).total_seconds()
                if elapsed < float(cfg.keepalive_delay_seconds):
                    continue

                st = _actor_state(state, aid)
                
                # Check keepalive count
                keepalive_count = int(st.get("keepalive_count") or 0)
                last_keepalive_next = st.get("last_keepalive_next")
                
                # Reset count if this is a new Next: declaration
                if last_keepalive_next != next_text:
                    keepalive_count = 0
                    st["last_keepalive_next"] = next_text
                
                # Check max keepalives
                if keepalive_count >= cfg.keepalive_max_per_actor:
                    continue
                
                st["keepalive_count"] = keepalive_count + 1
                st["last_keepalive_at"] = utc_now_iso()
                to_keepalive.append((aid, next_text))

            if to_keepalive:
                _save_state(group, state)

        for aid, next_text in to_keepalive:
            notify_data = SystemNotifyData(
                kind="keepalive",
                priority="normal",
                title="Ready to continue?",
                message=f"You mentioned: '{next_text}'. Continue when ready.",
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

    def _check_silence(self, group: Group, cfg: AutomationConfig, now: datetime) -> None:
        """Check if group has been silent and notify foreman."""
        if cfg.silence_timeout_seconds <= 0:
            return

        foreman = find_foreman(group)
        if foreman is None:
            return  # No foreman to notify
        foreman_id = str(foreman.get("id") or "")

        last_activity = _get_last_group_activity(group)
        if last_activity is None:
            return  # No activity yet

        silence_seconds = (now - last_activity).total_seconds()
        if silence_seconds < float(cfg.silence_timeout_seconds):
            return

        with self._lock:
            state = _load_state(group)
            last_silence_notify = state.get("last_silence_notify_at")
            if last_silence_notify:
                last_notify_dt = parse_utc_iso(str(last_silence_notify))
                if last_notify_dt is not None:
                    # Don't notify again within the timeout period
                    if (now - last_notify_dt).total_seconds() < float(cfg.silence_timeout_seconds):
                        return
            
            state["last_silence_notify_at"] = utc_now_iso()
            _save_state(group, state)

        notify_data = SystemNotifyData(
            kind="silence_check",
            priority="normal",
            title="Group is quiet",
            message=f"No activity for {int(silence_seconds)}s. Check if work is complete or if anyone needs help.",
            target_actor_id=foreman_id,
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

    def _check_standup(self, group: Group, cfg: AutomationConfig, now: datetime) -> None:
        """Check if it's time for a periodic standup meeting.
        
        Standup is a team review mechanism where foreman gathers peers to:
        1. Update progress in context
        2. Reflect on approach - is it correct? any blind spots?
        3. Share ideas and concerns
        4. Collectively decide on adjustments
        """
        if cfg.standup_interval_seconds <= 0:
            return
        
        # Find foreman - standup is only sent to foreman
        foreman = find_foreman(group)
        if foreman is None:
            return
        foreman_id = str(foreman.get("id") or "").strip()
        if not foreman_id:
            return
        
        # Check if foreman is running
        runner_kind = str(foreman.get("runner") or "pty").strip()
        if runner_kind == "headless":
            if not headless_runner.SUPERVISOR.actor_running(group.group_id, foreman_id):
                return
        else:
            if not pty_runner.SUPERVISOR.actor_running(group.group_id, foreman_id):
                return

        with self._lock:
            state = _load_state(group)
            last_standup = state.get("last_standup_at")
            
            if last_standup:
                last_standup_dt = parse_utc_iso(str(last_standup))
                if last_standup_dt is not None:
                    elapsed = (now - last_standup_dt).total_seconds()
                    if elapsed < float(cfg.standup_interval_seconds):
                        return
            
            state["last_standup_at"] = utc_now_iso()
            _save_state(group, state)

        # Calculate minutes since last standup for the message
        interval_minutes = cfg.standup_interval_seconds // 60

        standup_message = f"""{interval_minutes} minutes have passed. Time for a team review.

Foreman, please initiate a stand-up with @peers:

1. ASK PEERS TO REFLECT (not just report):
   - Update your progress in context (use cccc_task_update)
   - Step back and think: Is our approach correct? Any blind spots?
   - Any better ideas or alternative approaches?
   - What concerns or risks do you see?

2. COLLECT & SYNTHESIZE:
   - Gather insights from all peers
   - Look for patterns, conflicts, or new opportunities
   - Peers are collaborators, not subordinates - value their perspectives

3. DECIDE TOGETHER:
   - Adjust direction if needed based on collective wisdom
   - Update vision/sketch to reflect new understanding
   - Reallocate work if better approaches emerge

Example: "@peers Stand-up time. Please: 1) Update your task progress, 2) Share any concerns about our current approach, 3) Suggest improvements if you see any.\""""

        notify_data = SystemNotifyData(
            kind="standup",
            priority="normal",
            title="Stand-up Meeting",
            message=standup_message,
            target_actor_id=foreman_id,
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

    def on_new_message(self, group: Group) -> None:
        """Called when a new message arrives.
        
        Note: Auto-transition from idle -> active is now handled in delivery.py
        at the point of actual PTY delivery. This ensures the transition only
        happens when a message is actually delivered, not just queued.
        """
        # State transition is handled in delivery.should_deliver_message()
        pass

