# -*- coding: utf-8 -*-
from __future__ import annotations
import re, time
from typing import Any, Dict, Optional


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

    EVENT_PROGRESS_RE = re.compile(r"(?mi)^\s*(?:[-*]\s*)?Progress\s*(?:\(|:)\s*")
    EVENT_NEXT_RE     = re.compile(r"(?mi)^\s*(?:[-*]\s*)?Next\s*(?:\(|:)\s*(.+)$")

    def _has_progress_event(payload: str) -> bool:
        try:
            m = re.search(r"<\s*TO_PEER\s*>([\s\S]*?)<\s*/TO_PEER\s*>", payload or "", re.I)
            body = m.group(1) if m else (payload or "")
            return bool(EVENT_PROGRESS_RE.search(body))
        except Exception:
            return False

    def _extract_next(payload: str) -> str:
        try:
            m = re.search(r"<\s*TO_PEER\s*>([\s\S]*?)<\s*/TO_PEER\s*>", payload or "", re.I)
            body = m.group(1) if m else (payload or "")
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
        if sender_label not in ("PeerA","PeerB"):
            return
        if "<TO_PEER>" not in (payload or ""):
            return
        if not _has_progress_event(payload):
            return
        nx = _extract_next(payload)
        pending[sender_label] = {"due": time.time() + delay_s, "next": nx}
        try:
            log_ledger(home, {"from":"system","kind":"keepalive-scheduled","peer": sender_label, "delay_s": delay_s})
        except Exception:
            pass

    def tick():
        if not enabled:
            return
        now = time.time()
        for label in ("PeerA","PeerB"):
            ent = pending.get(label)
            if not ent:
                continue
            if now < float(ent.get('due') or 0.0):
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
            if nxt:
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
            pending[label] = None
            try:
                log_ledger(home, {"from":"system","kind":"keepalive-sent","peer":label})
            except Exception:
                pass

    return type('KeepaliveAPI', (), {
        'bind_send': bind_send,
        'schedule_from_payload': schedule_from_payload,
        'tick': tick,
    })
