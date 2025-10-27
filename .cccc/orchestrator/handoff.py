# -*- coding: utf-8 -*-
"""
Stateful handoff API (Context factory)
- Copied逻辑与 orchestrator_tmux.py 的 _send_handoff/_schedule_keepalive 等价，
  通过显式 ctx 注入外层依赖，保持行为不变。
"""
from __future__ import annotations
import re, time, hashlib
from types import SimpleNamespace
from typing import Any, Dict, Optional
from pathlib import Path

from .handoff_helpers import (
    _plain_text_without_tags_and_mid,
    _write_inbox_message,
    _append_suffix_inside,
    _compose_nudge,
)
from .logging_util import log_ledger

def make(ctx: Dict[str, Any]):
    home: Path = ctx['home']

    def send_handoff(sender_label: str, receiver_label: str, payload: str, require_mid: Optional[bool]=None, *, nudge_text: Optional[str]=None):
        # Backpressure: enqueue
        if ctx['inflight'][receiver_label] is not None:
            ctx['queued'][receiver_label].append({"sender": sender_label, "payload": payload})
            log_ledger(home, {"from": sender_label, "kind": "handoff-queued", "to": receiver_label, "chars": len(payload)})
            return
        # Drop empty body
        try:
            plain = _plain_text_without_tags_and_mid(payload)
            if not plain:
                log_ledger(home, {"from": sender_label, "kind": "handoff-drop", "to": receiver_label, "reason": "empty-body", "chars": len(payload)})
                return
        except Exception:
            pass
        # Schedule keepalive on progress lines
        try:
            if sender_label in ("PeerA","PeerB"):
                schedule_cb = ctx.get('schedule_keepalive')
                if schedule_cb:
                    schedule_cb(sender_label, payload)
        except Exception:
            pass
        # Inbound suffix
        def _suffix_for(receiver: str, sender: str) -> str:
            key = 'from_peer'
            if sender == 'User':
                key = 'from_user'
            elif sender == 'System':
                key = 'from_system'
            prof = ctx['profileA'] if receiver == 'PeerA' else ctx['profileB']
            cfg = (prof or {}).get('inbound_suffix', '')
            if isinstance(cfg, dict):
                return (cfg.get(key) or '').strip()
            if receiver == 'PeerA':
                return str(cfg).strip()
            if receiver == 'PeerB' and sender == 'User':
                return str(cfg).strip()
            return ''
        suf = _suffix_for(receiver_label, sender_label)
        if suf:
            payload = _append_suffix_inside(payload, suf)
        # Duplicate de-bounce
        h = hashlib.sha1(payload.encode('utf-8', errors='replace')).hexdigest()
        now = time.time()
        rs = [it for it in ctx['recent_sends'][receiver_label] if now - float(it.get('ts',0)) <= ctx['duplicate_window']]
        if any(it.get('hash') == h for it in rs):
            log_ledger(home, {"from": sender_label, "kind": "handoff-duplicate-drop", "to": receiver_label, "chars": len(payload)})
            return
        rs.append({"hash": h, "ts": now}); ctx['recent_sends'][receiver_label] = rs[-20:]
        # Inbox + NUDGE
        mid = ctx['new_mid']()
        text_with_mid = ctx['wrap_with_mid'](payload, mid)
        try:
            seq, _ = _write_inbox_message(home, receiver_label, text_with_mid, mid)
            if nudge_text and nudge_text.strip():
                if receiver_label == 'PeerA':
                    ctx['maybe_send_nudge'](home, 'PeerA', ctx['paneA'], ctx['profileA'], custom_text=nudge_text, force=True)
                else:
                    ctx['maybe_send_nudge'](home, 'PeerB', ctx['paneB'], ctx['profileB'], custom_text=nudge_text, force=True)
            else:
                ctx['send_nudge'](home, receiver_label, seq, mid, ctx['paneA'], ctx['paneB'], ctx['profileA'], ctx['profileB'], ctx['aux_mode'])
            try:
                ctx['last_nudge_ts'][receiver_label] = time.time()
            except Exception:
                pass
            status = "nudged"
        except Exception as e:
            status = f"failed:{e}"; seq = "000000"
        ctx['inflight'][receiver_label] = None
        log_ledger(home, {"from": sender_label, "kind": "handoff", "to": receiver_label, "status": status, "mid": mid, "seq": seq, "chars": len(payload)})
        print(f"[HANDOFF] {sender_label} → {receiver_label} ({len(payload)} chars, status={status}, seq={seq})")

        # Self-check cadence（复制原逻辑的关键路径，依赖 is_low_signal 与计数器）
        try:
            if ctx['self_check_enabled'] and (not ctx['in_self_check']['v']) and ctx['self_check_every'] > 0:
                pl = payload or ""; is_nudge = pl.strip().startswith("[NUDGE]")
                meaningful_sender = sender_label in ("User", "System", "PeerA", "PeerB")
                low_signal = False
                try:
                    low_signal = ctx['is_low_signal'](pl, ctx['policies'])
                except Exception:
                    low_signal = False
                if (not is_nudge) and meaningful_sender and (not low_signal):
                    ctx['handoffs_peer'][receiver_label] = int(ctx['handoffs_peer'].get(receiver_label,0)) + 1
                    cnt = ctx['handoffs_peer'][receiver_label]
                    if (cnt % ctx['self_check_every']) == 0:
                        sc_index = cnt // ctx['self_check_every']
                        ctx['self_checks_done'][receiver_label] = sc_index
                        ctx['in_self_check']['v'] = True
                        try:
                            msg = ctx['self_check_text'].rstrip()
                            ctx['send_handoff']('System', receiver_label, f"<FROM_SYSTEM>\n{msg}\n</FROM_SYSTEM>\n")
                            log_ledger(home, {"from": "system", "kind": "self-check", "every": ctx['self_check_every'], "count": cnt, "peer": receiver_label})
                            ctx['request_por_refresh']("self-check", force=False)
                        finally:
                            ctx['in_self_check']['v'] = False
        except Exception:
            pass

    return SimpleNamespace(send_handoff=send_handoff)
