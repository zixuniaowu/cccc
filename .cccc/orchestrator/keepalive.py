# -*- coding: utf-8 -*-
"""
Keepalive Mechanism - Unified for Single/Dual Peer Modes

Purpose: When a peer declares a Next step and the conversation stalls,
         send ONE continuation prompt to maintain work rhythm.

Trigger: Peer sends message containing "Next:" line (in to_peer or to_user)
Behavior: After delay_s (default 60s), if no new activity, send one keepalive
         Each new Next REPLACES pending keepalive (resets timer)

Unified Design:
- Same logic for single-peer and dual-peer modes
- One Next declaration → One potential keepalive (after delay)
- New Next cancels old pending and starts fresh
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
    profileA = ctx['profileA']; profileB = ctx['profileB']
    log_ledger = ctx['log_ledger']
    keepalive_debug = bool(ctx.get('keepalive_debug', False))
    # Keepalive sends message directly to pane, NOT via send_handoff (which writes to inbox)
    paste_when_ready = ctx.get('paste_when_ready')
    paneA = ctx.get('paneA', '')
    paneB = ctx.get('paneB', '')

    _initial_single_peer_mode = bool(ctx.get('single_peer_mode', False))

    def _is_single_peer() -> bool:
        try:
            return is_single_peer_mode(home)
        except Exception:
            return _initial_single_peer_mode

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

    def _extract_next(payload: str) -> str:
        """Extract the first Next: line content from payload. Returns empty string if none found."""
        try:
            body = _extract_body_from_tags(payload)
            mm = EVENT_NEXT_RE.findall(body)
            return (mm[0].strip() if mm else "")
        except Exception:
            return ""

    def _has_next_declaration(payload: str) -> bool:
        """Check if payload contains a Next: declaration (the keepalive trigger)."""
        return bool(_extract_next(payload))

    def schedule_from_payload(sender_label: str, payload: str):
        """
        Schedule keepalive when peer declares a Next step.
        
        Called from:
        - Dual-peer: send_handoff (for TO_PEER messages)
        - Single-peer: mailbox_pipeline (for to_user/to_peer events)
        
        Behavior:
        - Each Next declaration schedules ONE keepalive after delay_s
        - New Next REPLACES any pending keepalive (restarts timer)
        - This ensures peers who declare intent get reminded if they stall
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
        
        # Check for Next declaration in TO_PEER or TO_USER tags
        if "<TO_PEER>" not in (payload or "") and "<TO_USER>" not in (payload or ""):
            return
        if not _has_next_declaration(payload):
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
            
            # Build keepalive message (sent directly to pane, no inbox write)
            nxt = (ent.get('next') or '').strip()
            
            # Keepalive message: simple continuation prompt
            # NOTE: Do NOT append nudge/inbox instructions here because:
            # 1. Keepalive only fires when inbox is EMPTY (see skip conditions above)
            # 2. Peer declared Next step - it knows what to do
            # 3. We just need a gentle "continue" reminder
            if nxt:
                ka_msg = f"[KEEPALIVE] Continue: {nxt}"
            else:
                ka_msg = "[KEEPALIVE] Continue your work."
            
            # Send directly to pane (no inbox write)
            pane = paneA if label == 'PeerA' else paneB
            profile = profileA if label == 'PeerA' else profileB
            if paste_when_ready and pane:
                paste_when_ready(pane, profile, ka_msg, timeout=6.0, poke=False)
            
            # Clear pending (one keepalive per Next declaration)
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
        'schedule_from_payload': schedule_from_payload,
        'tick': tick,
    })
