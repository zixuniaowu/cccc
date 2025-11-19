# -*- coding: utf-8 -*-
"""
Auto-compact: Automatic context compression for long-running peer sessions

Monitors peer activity and opportunistically sends compact commands when:
- Peer is idle (no inflight, no queued, silent for threshold duration)
- Sufficient time has passed since last compact (min_interval_seconds)
- Sufficient work has been done since last compact (min_messages_since_last)
- Actor CLI supports compact command (configured in agents.yaml)
"""
from __future__ import annotations
import time
from typing import Dict, Any, Callable
from pathlib import Path


def make(ctx: Dict[str, Any]):
    """
    Factory function to create auto-compact API

    Context requirements:
    - home: Path
    - actors: Dict[str, Any] - from agents.yaml
    - delivery_conf: Dict[str, Any] - delivery config from cli_profiles.yaml
    - get_peer_actor: Callable[[str], str] - returns actor name for peer
    - inflight: Dict[str, Any] - inflight messages per peer
    - queued: Dict[str, list] - queued messages per peer
    - send_raw_to_cli: Callable - function to send raw command to CLI
    - paneA: str - tmux pane ID for PeerA
    - paneB: str - tmux pane ID for PeerB
    - log_ledger: Callable - logging function
    """
    home: Path = ctx['home']
    actors: Dict[str, Any] = ctx['actors']
    delivery_conf: Dict[str, Any] = ctx['delivery_conf']
    get_peer_actor: Callable[[str], str] = ctx['get_peer_actor']
    inflight: Dict[str, Any] = ctx['inflight']
    queued: Dict[str, list] = ctx['queued']
    send_raw_to_cli: Callable = ctx['send_raw_to_cli']
    paneA: str = ctx['paneA']
    paneB: str = ctx['paneB']
    log_ledger: Callable = ctx['log_ledger']

    # Auto-compact state (persistent across calls)
    last_compact_ts = {"PeerA": 0.0, "PeerB": 0.0}
    messages_since_compact = {"PeerA": 0, "PeerB": 0}
    last_activity_ts = {"PeerA": 0.0, "PeerB": 0.0}  # Track last handoff delivery per peer
    last_check_ts = 0.0

    # Load auto-compact configuration
    auto_compact_cfg = delivery_conf.get('auto_compact') if isinstance(delivery_conf.get('auto_compact'), dict) else {}

    # Configuration defaults
    ENABLED = bool(auto_compact_cfg.get('enabled', True))
    MIN_INTERVAL = int(auto_compact_cfg.get('min_interval_seconds', 900))
    MIN_MESSAGES = int(auto_compact_cfg.get('min_messages_since_last', 5))
    IDLE_THRESHOLD = int(auto_compact_cfg.get('idle_threshold_seconds', 180))
    CHECK_INTERVAL = int(auto_compact_cfg.get('check_interval_seconds', 60))

    def _get_actor_compact_config(actor_name: str) -> Dict[str, Any]:
        """Get compact configuration for an actor from agents.yaml"""
        try:
            actor_cfg = actors.get(actor_name, {})
            compact_cfg = actor_cfg.get('compact', {})
            # Default to disabled if not explicitly configured
            return {
                'enabled': bool(compact_cfg.get('enabled', False)),
                'command': str(compact_cfg.get('command', '/compact'))
            }
        except Exception:
            return {'enabled': False, 'command': '/compact'}

    def _is_peer_idle(peer: str) -> bool:
        """Check if peer is idle (no inflight, no queued, sufficient silence)"""
        try:
            now = time.time()
            # Check inflight
            if inflight.get(peer) is not None:
                return False
            # Check queued
            if len(queued.get(peer, [])) > 0:
                return False
            # Check silence duration
            last_ts = last_activity_ts.get(peer, 0.0)
            if last_ts == 0.0:
                # No activity yet, not idle
                return False
            if (now - last_ts) < IDLE_THRESHOLD:
                return False
            return True
        except Exception:
            return False

    def _should_auto_compact(peer: str) -> bool:
        """
        Comprehensive check: should we attempt auto-compact for this peer?

        Returns True only if ALL conditions are met:
        1. Auto-compact globally enabled
        2. Actor supports compact
        3. Minimum time interval since last compact
        4. Minimum message count since last compact (work threshold)
        5. Peer is currently idle
        """
        try:
            # Collect diagnostic info upfront
            now = time.time()
            actor = get_peer_actor(peer)
            msg_count = messages_since_compact.get(peer, 0)
            time_since_last = now - last_compact_ts.get(peer, 0)
            last_act = last_activity_ts.get(peer, 0.0)
            silence_duration = now - last_act if last_act > 0 else 0
            has_inflight = inflight.get(peer) is not None
            has_queued = len(queued.get(peer, [])) > 0

            # Check 1: Globally enabled?
            if not ENABLED:
                log_ledger(home, {
                    "from": "system",
                    "kind": "auto-compact-skip",
                    "peer": peer,
                    "reason": "disabled",
                    "actor": actor or "unknown",
                    "messages": msg_count,
                    "time_since_last": int(time_since_last)
                })
                return False

            # Check 2: Actor exists and supports compact?
            if not actor:
                log_ledger(home, {
                    "from": "system",
                    "kind": "auto-compact-skip",
                    "peer": peer,
                    "reason": "no-actor",
                    "messages": msg_count,
                    "time_since_last": int(time_since_last)
                })
                return False

            compact_conf = _get_actor_compact_config(actor)
            if not compact_conf.get('enabled', False):
                log_ledger(home, {
                    "from": "system",
                    "kind": "auto-compact-skip",
                    "peer": peer,
                    "reason": "actor-compact-disabled",
                    "actor": actor,
                    "messages": msg_count,
                    "time_since_last": int(time_since_last)
                })
                return False

            # Check 3: Minimum time interval?
            if time_since_last < MIN_INTERVAL:
                log_ledger(home, {
                    "from": "system",
                    "kind": "auto-compact-skip",
                    "peer": peer,
                    "reason": "time-interval",
                    "actor": actor,
                    "time_since_last": int(time_since_last),
                    "min_interval": MIN_INTERVAL,
                    "messages": msg_count
                })
                return False

            # Check 4: Minimum message count?
            if msg_count < MIN_MESSAGES:
                log_ledger(home, {
                    "from": "system",
                    "kind": "auto-compact-skip",
                    "peer": peer,
                    "reason": "insufficient-messages",
                    "actor": actor,
                    "messages": msg_count,
                    "min_messages": MIN_MESSAGES,
                    "time_since_last": int(time_since_last)
                })
                return False

            # Check 5: Peer idle?
            is_idle = _is_peer_idle(peer)
            if not is_idle:
                log_ledger(home, {
                    "from": "system",
                    "kind": "auto-compact-skip",
                    "peer": peer,
                    "reason": "not-idle",
                    "actor": actor,
                    "has_inflight": has_inflight,
                    "has_queued": has_queued,
                    "silence_seconds": int(silence_duration),
                    "idle_threshold": IDLE_THRESHOLD,
                    "messages": msg_count,
                    "time_since_last": int(time_since_last)
                })
                return False

            # All checks passed!
            log_ledger(home, {
                "from": "system",
                "kind": "auto-compact-check-passed",
                "peer": peer,
                "actor": actor,
                "messages": msg_count,
                "time_since_last": int(time_since_last),
                "silence_seconds": int(silence_duration)
            })
            return True
        except Exception as e:
            log_ledger(home, {
                "from": "system",
                "kind": "auto-compact-error",
                "peer": peer,
                "error": str(e)
            })
            return False

    def _try_auto_compact(peer: str):
        """Attempt to send compact command to peer"""
        try:
            actor = get_peer_actor(peer)
            if not actor:
                return

            compact_conf = _get_actor_compact_config(actor)
            compact_cmd = compact_conf.get('command', '/compact')

            # Determine tmux pane
            pane = paneA if peer == 'PeerA' else paneB

            # Send compact command (includes first Enter)
            send_raw_to_cli(home, peer, compact_cmd, paneA, paneB)

            # Wait 1s and send second Enter to handle potential confirmation prompts
            # (harmless for CLIs that don't require confirmation)
            time.sleep(1.0)
            from .tmux_layout import tmux
            tmux("send-keys", "-t", pane, "Enter")

            # Update state
            now = time.time()
            msgs_count = messages_since_compact[peer]
            prev_compact_ts = last_compact_ts.get(peer, 0)
            last_compact_ts[peer] = now
            messages_since_compact[peer] = 0

            # Log
            log_ledger(home, {
                "from": "system",
                "kind": "auto-compact",
                "peer": peer,
                "actor": actor,
                "trigger": "idle-detected",
                "messages_since_last": msgs_count,
                "time_since_last": int(now - prev_compact_ts) if prev_compact_ts > 0 else 0
            })
        except Exception as e:
            # Silent failure - auto-compact is best-effort
            try:
                log_ledger(home, {
                    "from": "system",
                    "kind": "auto-compact-error",
                    "peer": peer,
                    "error": str(e)[:200]
                })
            except Exception:
                pass

    def tick():
        """
        Main loop check: evaluate auto-compact for all peers
        Call this periodically from main orchestrator loop
        """
        nonlocal last_check_ts
        try:
            now = time.time()
            # Rate limit checks to CHECK_INTERVAL
            if (now - last_check_ts) < CHECK_INTERVAL:
                return

            last_check_ts = now

            # Check each peer
            for peer in ["PeerA", "PeerB"]:
                if _should_auto_compact(peer):
                    _try_auto_compact(peer)
        except Exception:
            pass  # Silent failure

    def on_handoff_delivered(peer: str):
        """
        Increment message counter and update activity timestamp when a handoff is successfully delivered
        Call this from handoff delivery logic
        """
        try:
            messages_since_compact[peer] = messages_since_compact.get(peer, 0) + 1
            last_activity_ts[peer] = time.time()
        except Exception:
            pass

    def get_status(peer: str) -> Dict[str, Any]:
        """Get current auto-compact status for a peer (for debugging/monitoring)"""
        try:
            now = time.time()
            actor = get_peer_actor(peer)
            compact_conf = _get_actor_compact_config(actor) if actor else {'enabled': False, 'command': ''}

            return {
                "enabled": ENABLED and compact_conf.get('enabled', False),
                "actor": actor or "unknown",
                "compact_command": compact_conf.get('command', ''),
                "last_compact_ago": int(now - last_compact_ts.get(peer, 0)),
                "messages_since_last": messages_since_compact.get(peer, 0),
                "is_idle": _is_peer_idle(peer),
                "should_compact": _should_auto_compact(peer),
                "config": {
                    "min_interval": MIN_INTERVAL,
                    "min_messages": MIN_MESSAGES,
                    "idle_threshold": IDLE_THRESHOLD,
                    "check_interval": CHECK_INTERVAL
                }
            }
        except Exception:
            return {"enabled": False, "error": "failed to get status"}

    # Return API
    return type('AutoCompactAPI', (), {
        'tick': tick,
        'on_handoff_delivered': on_handoff_delivered,
        'get_status': get_status,
    })
