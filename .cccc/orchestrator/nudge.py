# -*- coding: utf-8 -*-
from __future__ import annotations
import json, time, random
from pathlib import Path
from typing import Dict, Any, Optional
from .handoff_helpers import (
    _peer_folder_name,
    _inbox_dir,
    _processed_dir,
    _compose_nudge,
    _compose_detailed_nudge,
    _format_local_ts,
    _safe_headline,
)

NUDGE_RESEND_SECONDS = 90
NUDGE_JITTER_PCT = 0.0
NUDGE_DEBOUNCE_MS = 1500.0
NUDGE_PROGRESS_TIMEOUT_S = 45.0
NUDGE_KEEPALIVE = True
NUDGE_BACKOFF_BASE_MS = 1000.0
NUDGE_BACKOFF_MAX_MS = 60000.0
NUDGE_MAX_RETRIES = 1.0
PROCESSED_RETENTION = 200
paste_when_ready = None


def _nudge_state_path(home: Path, receiver_label: str) -> Path:
    peer = _peer_folder_name(receiver_label)
    return home/"state"/f"nudge.{peer}.json"


def _load_nudge_state(home: Path, receiver_label: str) -> Dict[str, Any]:
    p = _nudge_state_path(home, receiver_label)
    try:
        st = json.loads(p.read_text(encoding='utf-8'))
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _save_nudge_state(home: Path, receiver_label: str, st: Dict[str, Any]):
    p = _nudge_state_path(home, receiver_label); p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass


def _nudge_mark_progress(home: Path, receiver_label: str, *, seq: Optional[str] = None):
    st = _load_nudge_state(home, receiver_label)
    st['inflight'] = False
    st['retries'] = 0
    st['last_progress_ts'] = time.time()
    if seq:
        st['last_ack_seq'] = str(seq)
    _save_nudge_state(home, receiver_label, st)


def _maybe_send_nudge(home: Path, receiver_label: str, pane: str,
                     profile: Dict[str,Any], *, force: bool = False, suffix: str = "",
                     custom_text: Optional[str] = None) -> bool:
    st = _load_nudge_state(home, receiver_label)
    now = time.time()
    inflight = bool(st.get('inflight', False))
    last_sent = float(st.get('last_sent_ts') or 0.0)
    last_prog = float(st.get('last_progress_ts') or 0.0)
    retries = int(st.get('retries') or 0)
    try:
        inbox_files_now = [f for f in _inbox_dir(home, receiver_label).iterdir() if f.is_file()]
        inbox_count_now = len(inbox_files_now)
    except Exception:
        inbox_files_now = []
        inbox_count_now = 0
    last_inbox_count = int(st.get('last_inbox_count') or 0)
    try:
        if (not force) and inflight and (retries >= int(NUDGE_MAX_RETRIES)):
            if inbox_count_now > last_inbox_count:
                inflight = False
                st['inflight'] = False
                st['retries'] = 0
            else:
                return False
    except Exception:
        pass
    if (not force) and (now - last_prog) * 1000.0 < max(0.0, float(NUDGE_DEBOUNCE_MS)):
        return False
    if inflight and not force:
        if (now - last_prog) >= max(1.0, float(NUDGE_PROGRESS_TIMEOUT_S)):
            interval = min(float(NUDGE_BACKOFF_MAX_MS), float(NUDGE_BACKOFF_BASE_MS) * (2 ** max(0, retries))) / 1000.0
            min_legacy = max(1.0, float(NUDGE_RESEND_SECONDS))
            interval = max(interval, min_legacy)
            try:
                jpct = float(NUDGE_JITTER_PCT)
                if jpct and jpct > 0.0:
                    jig = 1.0 + random.uniform(-jpct, jpct)
                    interval = max(1.0, interval * jig)
            except Exception:
                pass
            if (now - last_sent) < interval:
                return False
            st['retries'] = retries + 1
        else:
            return False
    if custom_text and custom_text.strip():
        nmsg = custom_text.strip()
    else:
        try:
            inbox_path = _inbox_dir(home, receiver_label).as_posix()
        except Exception:
            inbox_path = ".cccc/mailbox/peerX/inbox"
        nmsg = _compose_nudge(inbox_path, ts=_format_local_ts(), backlog_gt_zero=True, suffix=suffix)
    if paste_when_ready is not None:
        paste_when_ready(pane, profile, nmsg, timeout=6.0, poke=False)
    st['inflight'] = True
    st['last_sent_ts'] = now
    st['last_inbox_count'] = inbox_count_now
    _save_nudge_state(home, receiver_label, st)
    return True


def _compose_nudge_suffix_for(peer_label: str,
                              *, profileA: Dict[str,Any], profileB: Dict[str,Any], aux_mode: str,
                              aux_actor: str = "") -> str:
    base = ((profileA.get('nudge_suffix') if peer_label == 'PeerA' else profileB.get('nudge_suffix')) or '').strip()
    aux_line = ""
    if aux_mode == "on" and str(aux_actor or '').strip():
        actor_name = str(aux_actor).strip()
        aux_line = f"Invoke aux {actor_name} for sub-tasks."
    combined = " ".join(filter(None, [aux_line, base]))
    return combined.strip()


def _send_nudge(home: Path, receiver_label: str, seq: str, mid: str,
                left_pane: str, right_pane: str,
                profileA: Dict[str,Any], profileB: Dict[str,Any],
                aux_mode: str = "off"):
    aux_actor_name = ""
    try:
        from common.config import load_profiles as _lp
        aux_actor_name = ((_lp(home).get('aux') or {}).get('actor') or '').strip()
    except Exception:
        aux_actor_name = ""
    combined_suffix = _compose_nudge_suffix_for(receiver_label, profileA=profileA, profileB=profileB, aux_mode=aux_mode, aux_actor=aux_actor_name)
    try:
        inbox = _inbox_dir(home, receiver_label)
        trigger_file = None
        for f in sorted(inbox.iterdir(), key=lambda p: p.name):
            if f.name.startswith(str(seq)):
                trigger_file = f; break
        preview = _safe_headline(trigger_file) if trigger_file else "[unreadable-or-binary]"
    except Exception:
        preview = "[unreadable-or-binary]"
    custom = _compose_detailed_nudge(seq, preview, inbox.as_posix() if 'inbox' in locals() else ".cccc/mailbox/peerX/inbox", suffix=combined_suffix)
    if receiver_label == 'PeerA':
        _maybe_send_nudge(home, 'PeerA', left_pane, profileA, custom_text=custom, force=True)
    else:
        _maybe_send_nudge(home, 'PeerB', right_pane, profileB, custom_text=custom, force=True)


def _archive_inbox_entry(home: Path, receiver_label: str, token: str):
    inbox = _inbox_dir(home, receiver_label)
    proc = _processed_dir(home, receiver_label)
    target: Optional[Path] = None
    seq_pat = None
    if token.isdigit():
        seq_pat = token
    else:
        import re as _re
        m = _re.search(r"(\d{6,})", token)
        if m:
            seq_pat = m.group(1)[:6]
    if seq_pat:
        for f in sorted(inbox.iterdir()):
            if f.name.startswith(seq_pat):
                target = f
                break
    if target is None:
        for f in sorted(inbox.iterdir()):
            if f".{token}." in f.name:
                target = f; break
    if target is None:
        return False
    try:
        proc.mkdir(parents=True, exist_ok=True)
        target.rename(proc/target.name)
    except Exception:
        return False
    try:
        # Filter out directories, only process files
        files = sorted([f for f in proc.iterdir() if f.is_file()], key=lambda p: p.name)
        if len(files) > PROCESSED_RETENTION:
            remove_n = len(files) - PROCESSED_RETENTION
            for f in files[:remove_n]:
                try: f.unlink()
                except Exception: pass
    except Exception:
        pass
    return True


def make(ctx: Dict[str, Any]):
    global paste_when_ready
    paste_when_ready = ctx.get('paste_when_ready')

    def configure(params: Dict[str, Any]):
        global NUDGE_RESEND_SECONDS, NUDGE_JITTER_PCT, NUDGE_DEBOUNCE_MS, NUDGE_PROGRESS_TIMEOUT_S
        global NUDGE_KEEPALIVE, NUDGE_BACKOFF_BASE_MS, NUDGE_BACKOFF_MAX_MS, NUDGE_MAX_RETRIES, PROCESSED_RETENTION
        for key, val in (params or {}).items():
            try:
                if key == 'NUDGE_RESEND_SECONDS':
                    NUDGE_RESEND_SECONDS = float(val)
                elif key == 'NUDGE_JITTER_PCT':
                    NUDGE_JITTER_PCT = float(val)
                elif key == 'NUDGE_DEBOUNCE_MS':
                    NUDGE_DEBOUNCE_MS = float(val)
                elif key == 'NUDGE_PROGRESS_TIMEOUT_S':
                    NUDGE_PROGRESS_TIMEOUT_S = float(val)
                elif key == 'NUDGE_KEEPALIVE':
                    NUDGE_KEEPALIVE = bool(val)
                elif key == 'NUDGE_BACKOFF_BASE_MS':
                    NUDGE_BACKOFF_BASE_MS = float(val)
                elif key == 'NUDGE_BACKOFF_MAX_MS':
                    NUDGE_BACKOFF_MAX_MS = float(val)
                elif key == 'NUDGE_MAX_RETRIES':
                    NUDGE_MAX_RETRIES = float(val)
                elif key == 'PROCESSED_RETENTION':
                    PROCESSED_RETENTION = int(val)
            except Exception:
                pass

    return type('NudgeAPI', (), {
        'configure': configure,
        'nudge_mark_progress': _nudge_mark_progress,
        'maybe_send_nudge': _maybe_send_nudge,
        'send_nudge': _send_nudge,
        'compose_nudge_suffix_for': _compose_nudge_suffix_for,
        'archive_inbox_entry': _archive_inbox_entry,
    })

