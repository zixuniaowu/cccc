# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from typing import Any, Dict
from pathlib import Path
from .logging_util import log_ledger

def make(ctx: Dict[str, Any]):
    home: Path = ctx['home']

    ITEM_HEAD_RE = re.compile(r"(?mi)^\s*(?:[-*]\s*)?Item\s*\(\s*([^\)]+?)\s*\)\s*:\s*(.+)$")
    KEY_ALIASES = {
        'progress': { 'progress' },
        'evidence': { 'evidence' },
        'ask':      { 'ask' },
        'counter':  { 'counter' },
        'risk':     { 'risk' },
        'next':     { 'next' },
    }
    CANON_KEYS = {a: k for k, vv in KEY_ALIASES.items() for a in vv}
    EVENT_LINE_RE = re.compile(r"(?mi)^\s*(?:[-*]\s*)?([A-Za-z]+)\s*(?:\(([^)]*)\))?\s*:\s*(.*)$")

    def _parse_params(s: str) -> Dict[str,str]:
        out: Dict[str,str] = {}
        if not s:
            return out
        try:
            parts = re.split(r",(?=(?:[^\[]*\[[^\]]*\])*[^\]]*$)", s)
            for p in parts:
                if '=' in p:
                    k,v = p.split('=',1)
                    out[k.strip().lower()] = v.strip().strip()
        except Exception:
            pass
        return out

    def _extract_body(payload: str) -> str:
        m = re.search(r"<\s*TO_PEER\s*>([\s\S]*?)<\s*/TO_PEER\s*>", payload or "", re.I)
        return m.group(1) if m else (payload or "")

    def ledger_events_from_payload(sender_label: str, payload: str):
        try:
            body = _extract_body(payload)
            events = []
            cur_label = 'misc'
            for raw in (body or '').splitlines():
                m = ITEM_HEAD_RE.match(raw)
                if m:
                    cur_label = m.group(1).strip() or 'misc'
                    continue
                mm = EVENT_LINE_RE.match(raw)
                if not mm:
                    continue
                key_raw, param_s, text = mm.group(1).strip(), (mm.group(2) or '').strip(), (mm.group(3) or '').strip()
                key = CANON_KEYS.get(key_raw.lower())
                if key not in KEY_ALIASES:
                    continue
                params = _parse_params(param_s)
                tag = (params.get('tag') or cur_label or 'misc').strip()
                rec: Dict[str,Any] = {
                    'from': sender_label,
                    'kind': f"event-{key}",
                    'tag': tag,
                    'text': text,
                }
                for k in ('to','strength','sev','refs'):
                    if k == 'refs' and params.get(k):
                        inside = params.get(k)
                        if inside.startswith('[') and inside.endswith(']'):
                            inner = inside[1:-1]
                            rec['refs'] = [r.strip() for r in inner.split(',') if r.strip()]
                        continue
                    if params.get(k) is not None:
                        rec[k] = params.get(k)
                log_ledger(home, rec)
        except Exception:
            pass

    def has_trailing_insight_block(text: str) -> bool:
        INSIGHT_FENCE = "```insight"
        try:
            s = text.strip()
            open_pos = s.rfind(INSIGHT_FENCE)
            if open_pos < 0:
                return False
            close_pos = s.rfind("```")
            return close_pos > open_pos
        except Exception:
            return False

    def teach_intercept_missing_insight(peer_label: str, payload: str) -> bool:
        if has_trailing_insight_block(payload):
            return False
        peer_name = "peerA" if peer_label == "PeerA" else "peerB"
        tip = (
            "Missing trailing ```insight fenced block; please end each to_peer message with exactly one insight block (include a Next or a ≤10‑min micro‑experiment).\n"
            f"Overwrite .cccc/mailbox/{peer_name}/to_peer.md and resend (do NOT append).\n"
            "If exploring, use kind: note with a one‑line Next to indicate direction."
        )
        ctx['send_handoff']('System', peer_label, f"<FROM_SYSTEM>\n{tip}\n</FROM_SYSTEM>\n")
        try:
            log_ledger(home, {"from": "system", "kind": "teach-warn", "peer": peer_label, "reason": "missing-insight"})
        except Exception:
            pass
        return False

    return type('EVAPI', (), {
        'ledger_events_from_payload': ledger_events_from_payload,
        'has_trailing_insight_block': has_trailing_insight_block,
        'teach_intercept_missing_insight': teach_intercept_missing_insight,
    })

