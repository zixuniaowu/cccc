# -*- coding: utf-8 -*-
"""
Keepalive Mechanism - Unified for Single/Dual Peer Modes

Purpose: When a peer sends a Progress message and the conversation stalls,
         send ONE continuation prompt to maintain work rhythm.

Trigger: Peer sends message containing "Progress:" line (in to_peer or to_user)
Behavior: After delay_s (default 60s), if no new activity, send one keepalive
         Each new Progress REPLACES pending keepalive (resets timer)

Unified Design:
- Same logic for single-peer and dual-peer modes
- One Progress → One potential keepalive (after delay)
- New Progress cancels old pending and starts fresh
- Skip if inbox/inflight/queued not empty
"""
from __future__ import annotations
import re, time
from typing import Any, Dict, Optional

from common.config import is_single_peer_mode


def make(ctx: Dict[str, Any]):
    home = ctx['home']
    pending = ctx['pending']  # {label: {"due": timestamp, "next": hint}}
    enabled = ctx['enabled']
    delay_s = float(ctx['delay_s'])  # Keepalive delay (default: 60s from config)
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

    _initial_single_peer_mode = bool(ctx.get('single_peer_mode', False))

    def _is_single_peer() -> bool:
        try:
            return is_single_peer_mode(home)
        except Exception:
            return _initial_single_peer_mode

    EVENT_PROGRESS_RE = re.compile(r"(?mi)^\s*(?:[-*]\s*)?Progress\s*(?:\(|:)\s*")
    EVENT_NEXT_RE = re.compile(r"(?mi)^\s*(?:[-*]\s*)?Next\s*(?:\(|:)\s*(.+)$")

    def _extract_body_from_tags(payload: str) -> str:
        """Extract body from TO_PEER or TO_USER tags"""
        try:
            m = re.search(r"<\s*TO_PEER\s*>([\s\S]*?)<\s*/TO_PEER\s*>", payload or "", re.I)
            if m:
                return m.group(1)
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
        """
        Schedule keepalive when peer sends Progress message.
        
        Called from:
        - Dual-peer: send_handoff (for TO_PEER messages)
        - Single-peer: mailbox_pipeline (for to_user/to_peer events)
        
        Behavior:
        - Each Progress schedules ONE keepalive after delay_s
        - New Progress REPLACES any pending keepalive (restarts timer)
        - This means active peers continuously get keepalive feedback
        """
        if not enabled:
            return
        
        current_single_peer = _is_single_peer()
        
        # Validate sender (single-peer: only PeerA; dual-peer: PeerA or PeerB)
        if current_single_peer:
            if sender_label != "PeerA":
                return
        else:
            if sender_label not in ("PeerA", "PeerB"):
                return
        
        # Check for Progress marker in TO_PEER or TO_USER tags
        if "<TO_PEER>" not in (payload or "") and "<TO_USER>" not in (payload or ""):
            return
        if not _has_progress_event(payload):
            return
        
        nx = _extract_next(payload)
        
        # Schedule keepalive (replaces any existing pending for this sender)
        pending[sender_label] = {
            "due": time.time() + delay_s,
            "next": nx
        }
        
        try:
            log_ledger(home, {
                "from": "system",
                "kind": "keepalive-scheduled",
                "peer": sender_label,
                "delay_s": delay_s,
                "single_peer": current_single_peer
            })
        except Exception:
            pass

    def tick():
        """
        Check and send due keepalives.
        Called every main loop iteration (~2 seconds).
        """
        if not enabled:
            return
        
        now = time.time()
        current_single_peer = _is_single_peer()
        labels_to_check = ("PeerA",) if current_single_peer else ("PeerA", "PeerB")
        
        for label in labels_to_check:
            ent = pending.get(label)
            if not ent:
                continue
            if now < float(ent.get('due') or 0.0):
                continue
            
            # Skip conditions: peer has pending messages or work
            inbox_files = list_inbox_files(label)
            skip_reason = None
            if inbox_files:
                skip_reason = "inbox-not-empty"
            elif inflight.get(label):
                skip_reason = "inflight"
            elif queued.get(label):
                skip_reason = "queued"
            
            if skip_reason:
                if keepalive_debug:
                    try:
                        log_ledger(home, {
                            "from": "system",
                            "kind": "keepalive-skipped",
                            "peer": label,
                            "reason": skip_reason
                        })
                    except Exception:
                        pass
                pending[label] = None
                continue
            
            # Build keepalive message
            nxt = (ent.get('next') or '').strip()
            if nxt:
                msg = f"<FROM_SYSTEM>\nContinue: {nxt}\n</FROM_SYSTEM>\n"
            else:
                msg = "<FROM_SYSTEM>\nContinue.\n</FROM_SYSTEM>\n"
            
            try:
                inbox_path = inbox_dir(home, label).as_posix()
            except Exception:
                inbox_path = ".cccc/mailbox/peerX/inbox"
            
            ka_suffix = nudge_api.compose_nudge_suffix_for(
                label,
                profileA=profileA,
                profileB=profileB,
                aux_mode=ctx.get('aux_mode', 'off'),
                aux_actor=ctx.get('aux_actor', '')
            )
            ka_nudge = compose_nudge(inbox_path, ts=format_ts(), backlog_gt_zero=False, suffix=ka_suffix)
            _send(label, msg, nudge_text=ka_nudge)
            
            # Clear pending (one keepalive per Progress)
            pending[label] = None
            
            try:
                log_ledger(home, {
                    "from": "system",
                    "kind": "keepalive-sent",
                    "peer": label,
                    "single_peer": current_single_peer
                })
            except Exception:
                pass

    return type('KeepaliveAPI', (), {
        'bind_send': bind_send,
        'schedule_from_payload': schedule_from_payload,
        'tick': tick,
    })
