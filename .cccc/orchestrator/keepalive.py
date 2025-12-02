# -*- coding: utf-8 -*-
from __future__ import annotations
import re, time
from typing import Any, Dict, Optional

from common.config import is_single_peer_mode


def make(ctx: Dict[str, Any]):
    home = ctx['home']
    pending = ctx['pending']
    enabled = ctx['enabled']
    delay_s = float(ctx['delay_s'])
    inflight = ctx['inflight']
    queued = ctx['queued']
    list_inbox_files = ctx['list_inbox_files']
    inbox_dir = ctx['inbox_dir']
    compose_nudge = ctx['compose_nudge']
    format_ts = ctx['format_ts']
    profileA = ctx['profileA']; profileB = ctx['profileB']
    nudge_api = ctx['nudge_api']
    log_ledger = ctx['log_ledger']
    keepalive_debug = bool(ctx.get('keepalive_debug', False))
    send_box = {'fn': ctx.get('send_handoff')}

    # Single-peer mode parameters (initial value, but re-checked dynamically)
    _initial_single_peer_mode = bool(ctx.get('single_peer_mode', False))
    single_peer_delay_s = float(ctx.get('single_peer_delay_s', 240.0))  # 4 minutes
    single_peer_max_nudges = int(ctx.get('single_peer_max_nudges', 3))

    def _is_single_peer() -> bool:
        """Dynamically check single-peer mode (config may have changed via TUI)"""
        try:
            return is_single_peer_mode(home)
        except Exception:
            return _initial_single_peer_mode

    # Track nudge count per peer for single-peer mode
    nudge_counts: Dict[str, int] = {}

    EVENT_PROGRESS_RE = re.compile(r"(?mi)^\s*(?:[-*]\s*)?Progress\s*(?:\(|:)\s*")
    EVENT_NEXT_RE     = re.compile(r"(?mi)^\s*(?:[-*]\s*)?Next\s*(?:\(|:)\s*(.+)$")

    def _extract_body_from_tags(payload: str) -> str:
        """Extract body from TO_PEER or TO_USER tags"""
        try:
            # Try TO_PEER first
            m = re.search(r"<\s*TO_PEER\s*>([\s\S]*?)<\s*/TO_PEER\s*>", payload or "", re.I)
            if m:
                return m.group(1)
            # Fall back to TO_USER (for single-peer mode)
            m = re.search(r"<\s*TO_USER\s*>([\s\S]*?)<\s*/TO_USER\s*>", payload or "", re.I)
            if m:
                return m.group(1)
            return payload or ""
        except Exception:
            return payload or ""

    def _has_progress_event(payload: str) -> bool:
        try:
            body = _extract_body_from_tags(payload)
            return bool(EVENT_PROGRESS_RE.search(body))
        except Exception:
            return False

    def _extract_next(payload: str) -> str:
        try:
            body = _extract_body_from_tags(payload)
            mm = EVENT_NEXT_RE.findall(body)
            return (mm[0].strip() if mm else "")
        except Exception:
            return ""

    def bind_send(send_fn):
        send_box['fn'] = send_fn

    def _send(label: str, text: str, *, nudge_text: Optional[str] = None):
        fn = send_box.get('fn')
        if not fn:
            return
        fn('System', label, text, nudge_text=nudge_text)

    def schedule_from_payload(sender_label: str, payload: str):
        if not enabled:
            return
        # In single-peer mode, only PeerA is active
        current_single_peer = _is_single_peer()
        if current_single_peer:
            if sender_label != "PeerA":
                return
        else:
            if sender_label not in ("PeerA","PeerB"):
                return
        
        # Check for relevant tags (TO_PEER or TO_USER both indicate agent activity)
        # Guard conditions (inbox empty, no inflight, no queued) prevent false positives
        if "<TO_PEER>" not in (payload or "") and "<TO_USER>" not in (payload or ""):
            return
        if not _has_progress_event(payload):
            return
        nx = _extract_next(payload)
        # Use single-peer delay when in single-peer mode
        effective_delay = single_peer_delay_s if current_single_peer else delay_s
        pending[sender_label] = {"due": time.time() + effective_delay, "next": nx}
        # Reset nudge count on new Progress
        nudge_counts[sender_label] = 0
        try:
            log_ledger(home, {"from":"system","kind":"keepalive-scheduled","peer": sender_label, "delay_s": effective_delay, "single_peer": current_single_peer})
        except Exception:
            pass

    def tick():
        if not enabled:
            return
        now = time.time()
        # In single-peer mode, only check PeerA
        current_single_peer = _is_single_peer()
        labels_to_check = ("PeerA",) if current_single_peer else ("PeerA","PeerB")
        for label in labels_to_check:
            ent = pending.get(label)
            if not ent:
                continue
            if now < float(ent.get('due') or 0.0):
                continue

            # Single-peer mode: check max nudges
            if current_single_peer:
                current_count = nudge_counts.get(label, 0)
                if current_count >= single_peer_max_nudges:
                    if keepalive_debug:
                        try:
                            log_ledger(home, {"from":"system","kind":"keepalive-exhausted","peer":label,"count":current_count,"max":single_peer_max_nudges})
                        except Exception:
                            pass
                    pending[label] = None
                    continue

            inbox_files = list_inbox_files(label)
            reason = None
            if inbox_files:
                reason = "inbox-not-empty"
            elif inflight.get(label):
                reason = "inflight"
            elif queued.get(label):
                reason = "queued"
            if reason:
                if keepalive_debug:
                    try:
                        log_ledger(home, {"from":"system","kind":"keepalive-skipped","peer":label,"reason":reason})
                    except Exception:
                        pass
                pending[label] = None
                continue

            nxt = (ent.get('next') or '').strip()
            # Single-peer mode: more detailed continuation message
            if current_single_peer:
                msg = """<FROM_SYSTEM>
Continue with your current task.

If task is complete, summarize results in to_user.md.
If blocked or need input, ask in to_user.md.
Otherwise, continue working and log progress in to_peer.md.
</FROM_SYSTEM>
"""
            elif nxt:
                msg = f"<FROM_SYSTEM>\nOK. Continue: {nxt}\n</FROM_SYSTEM>\n"
            else:
                msg = "<FROM_SYSTEM>\nOK. Continue.\n</FROM_SYSTEM>\n"
            try:
                inbox_path = inbox_dir(home, label).as_posix()
            except Exception:
                inbox_path = ".cccc/mailbox/peerX/inbox"
            # Dynamically read aux_mode and aux_actor from ctx to get latest values after config refresh
            ka_suffix = nudge_api.compose_nudge_suffix_for(label, profileA=profileA, profileB=profileB, aux_mode=ctx.get('aux_mode', 'off'), aux_actor=ctx.get('aux_actor', ''))
            ka_nudge = compose_nudge(inbox_path, ts=format_ts(), backlog_gt_zero=False, suffix=ka_suffix)
            _send(label, msg, nudge_text=ka_nudge)

            # Increment nudge count for single-peer mode
            if current_single_peer:
                nudge_counts[label] = nudge_counts.get(label, 0) + 1
                # Re-schedule if not exhausted
                if nudge_counts[label] < single_peer_max_nudges:
                    pending[label] = {"due": time.time() + single_peer_delay_s, "next": nxt}
                else:
                    pending[label] = None
            else:
                pending[label] = None

            try:
                log_ledger(home, {"from":"system","kind":"keepalive-sent","peer":label,"single_peer":current_single_peer,"nudge_count":nudge_counts.get(label,0)})
            except Exception:
                pass

    return type('KeepaliveAPI', (), {
        'bind_send': bind_send,
        'schedule_from_payload': schedule_from_payload,
        'tick': tick,
    })
