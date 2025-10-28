# -*- coding: utf-8 -*-
from __future__ import annotations
import os, time, json
from typing import Any, Dict
from pathlib import Path

def make(ctx: Dict[str, Any]):
    home: Path = ctx['home']
    state: Path = ctx['state']
    session: str = ctx['session']
    policies = ctx['policies']
    conversation_reset_policy = ctx['conversation_reset_policy']
    default_reset_mode = ctx['default_reset_mode']
    auto_reset_interval_cfg = ctx['auto_reset_interval_cfg']
    reset_interval_effective = ctx['reset_interval_effective']
    self_check_enabled = ctx['self_check_enabled']
    self_check_every = ctx['self_check_every']
    instr_counter_box = ctx['instr_counter_box']
    handoffs_peer = ctx['handoffs_peer']
    por_status_snapshot = ctx['por_status_snapshot']
    _aux_snapshot = ctx['_aux_snapshot']
    cli_profiles = ctx['cli_profiles']
    settings = ctx['settings']
    resolved_box = ctx['resolved_box']
    _bin_available = ctx['_bin_available']
    _actors_available = ctx.get('_actors_available', lambda: [])
    _inbox_dir = ctx['_inbox_dir']
    _processed_dir = ctx['_processed_dir']
    _load_foreman_conf = ctx['_load_foreman_conf']
    _foreman_load_state = ctx['_foreman_load_state']
    mbox_counts = ctx['mbox_counts']
    mbox_last = ctx['mbox_last']
    phase = ctx['phase']

    def write_status(paused: bool):
        pol_enabled = bool((policies.get("handoff_filter") or {}).get("enabled", True))
        # Effective filter strictly follows policy; no runtime console override.
        eff_filter = pol_enabled
        next_selfA = None; next_selfB = None
        if self_check_enabled and self_check_every > 0:
            try:
                a = int(handoffs_peer.get('PeerA',0)); b = int(handoffs_peer.get('PeerB',0))
                remA = self_check_every - (a % self_check_every)
                remB = self_check_every - (b % self_check_every)
                next_selfA = (remA if remA > 0 else self_check_every)
                next_selfB = (remB if remB > 0 else self_check_every)
            except Exception:
                pass
        fconf = {}
        fstate = {}
        try:
            fconf = _load_foreman_conf()
            fstate = _foreman_load_state()
        except Exception:
            pass
        f_enabled = bool((fconf or {}).get('enabled', False))
        f_cc = bool((fconf or {}).get('cc_user', True))
        f_next = (fstate or {}).get('next_due_ts')
        try:
            f_next_hhmm = time.strftime('%H:%M', time.localtime(float(f_next))) if f_next else None
        except Exception:
            f_next_hhmm = None
        f_running = bool((fstate or {}).get('running', False))
        f_last_end = (fstate or {}).get('last_end_ts')
        try:
            f_last_hhmm = time.strftime('%H:%M', time.localtime(float(f_last_end))) if f_last_end else None
        except Exception:
            f_last_hhmm = None
        f_last_rc = (fstate or {}).get('last_rc')
        def _pid_alive(pid: int) -> bool:
            try:
                if pid <= 0:
                    return False
                os.kill(pid, 0)
                return True
            except Exception:
                return False
        try:
            roles_block = { 'peerA': (resolved_box['v'].get('peerA') or {}).get('actor') or '',
                            'peerB': (resolved_box['v'].get('peerB') or {}).get('actor') or '',
                            'aux':   (resolved_box['v'].get('aux')   or {}).get('actor') or '' }
        except Exception:
            roles_block = { 'peerA':'', 'peerB':'', 'aux':'' }
        try:
            pa_cmd = (resolved_box['v'].get('peerA') or {}).get('command') or ''
            pb_cmd = (resolved_box['v'].get('peerB') or {}).get('command') or ''
            cli_block = {
                'peerA': { 'command': pa_cmd, 'available': _bin_available(pa_cmd) },
                'peerB': { 'command': pb_cmd, 'available': _bin_available(pb_cmd) },
            }
        except Exception:
            cli_block = {'peerA': {'command':'','available': False}, 'peerB': {'command':'','available': False}}
        try:
            tcfg = ctx['read_yaml'](settings/"telegram.yaml")
            token_env = str((tcfg or {}).get('token_env') or 'TELEGRAM_BOT_TOKEN')
            configured = bool((tcfg or {}).get('token')) or bool(os.environ.get(token_env))
            autostart = True if not tcfg else bool((tcfg or {}).get('autostart', True))
            pidf = state/"telegram-bridge.pid"
            pid = 0
            if pidf.exists():
                try: pid = int(pidf.read_text(encoding='utf-8').strip() or '0')
                except Exception: pid = 0
            running = _pid_alive(pid)
            telegram_block = { 'configured': configured, 'autostart': autostart, 'running': running }
        except Exception:
            telegram_block = { 'configured': False, 'autostart': True, 'running': False }
        try:
            actors_list = _actors_available()
        except Exception:
            actors_list = []
        setup = { 'roles': roles_block, 'cli': cli_block, 'telegram': telegram_block, 'actors_available': actors_list }

        payload = {
            "session": session,
            "paused": paused,
            "phase": phase,
            "require_ack": bool((cli_profiles.get("delivery", {}) or {}).get("require_ack", False)),
            "mailbox_counts": mbox_counts,
            "mailbox_last": mbox_last,
            "handoff_filter_enabled": eff_filter,
            "por": por_status_snapshot(home),
            "aux": _aux_snapshot(),
            "reset": {
                "policy": conversation_reset_policy,
                "default_mode": default_reset_mode,
                "interval_handoffs": auto_reset_interval_cfg if auto_reset_interval_cfg > 0 else None,
                "interval_effective": reset_interval_effective if reset_interval_effective > 0 else None,
                "self_check_every": self_check_every if self_check_enabled else None,
                "handoffs_total": int(instr_counter_box['v']),
                "handoffs_peerA": int(handoffs_peer.get('PeerA',0)),
                "handoffs_peerB": int(handoffs_peer.get('PeerB',0)),
                "next_self_peerA": next_selfA,
                "next_self_peerB": next_selfB,
            },
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "foreman": ({"enabled": True, "running": f_running, "next_due": f_next_hhmm, "last": f_last_hhmm, "last_rc": f_last_rc, "cc_user": f_cc} if f_enabled else {"enabled": False}),
            "setup": setup,
        }
        try:
            (state/"status.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def write_queue_and_locks():
        try:
            q_payload = {
                'peerA': len((ctx['queued'] or {}).get('PeerA') or []),
                'peerB': len((ctx['queued'] or {}).get('PeerB') or []),
                'inflight': {
                    'peerA': bool((ctx['inflight'] or {}).get('PeerA')),
                    'peerB': bool((ctx['inflight'] or {}).get('PeerB')),
                }
            }
            (state/"queue.json").write_text(json.dumps(q_payload, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass
        try:
            locks = []
            for nm in ('inbox-seq-peerA.lock','inbox-seq-peerB.lock'):
                if (state/nm).exists():
                    locks.append(nm)
            l_payload = {
                'inbox_seq_locks': locks,
                'inflight': {
                    'peerA': bool((ctx['inflight'] or {}).get('PeerA')),
                    'peerB': bool((ctx['inflight'] or {}).get('PeerB')),
                }
            }
            (state/"locks.json").write_text(json.dumps(l_payload, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    return type('STAPI', (), {'write_status': write_status, 'write_queue_and_locks': write_queue_and_locks})
