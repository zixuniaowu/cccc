#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Outbox consumer utility (shared by chat adapters)

Purpose
- Tail the single-source outbox stream at .cccc/state/outbox.jsonl
- Persist a compact seen-baseline to avoid resending history across restarts
- Dispatch new events to adapter callbacks with minimal ceremony

Design
- No external deps; JSONL tail via periodic reads (poll)
- Idempotent: stores last seen ids in .cccc/state/outbox-seen-<name>.json
- Scope: text events only (type in {'to_user','to_peer_summary'})

Usage
from pathlib import Path
from .outbox_consumer import OutboxConsumer
oc = OutboxConsumer(Path('.cccc'), seen_name='slack')
oc.loop(on_to_user=fn1, on_to_peer_summary=fn2)
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Callable, Optional, Set
import json, time, hashlib

Event = Dict[str, Any]

class OutboxConsumer:
    def __init__(self, home: Path, *, seen_name: str = 'generic', poll_seconds: float = 1.0, window: int = 2000,
                 reset_on_start: str = 'baseline'):
        self.home = home
        self.outbox = home/"state"/"outbox.jsonl"
        self.seen_path = home/"state"/f"outbox-seen-{seen_name}.json"
        self.poll_seconds = float(poll_seconds)
        self.window = int(window)
        self.reset_on_start = str(reset_on_start or 'baseline')
        self._seen: Set[str] = set()
        self._baseline_done = False
        self._load_seen()
        # If we already have a non-empty seen set from previous runs,
        # do not swallow the first window as baseline again on restart.
        if self.reset_on_start == 'baseline' and self._seen:
            self._baseline_done = True

    def _load_seen(self):
        if self.reset_on_start == 'clear':
            # Start fresh and deliver immediately; do not swallow a baseline window.
            try:
                if self.seen_path.exists():
                    self.seen_path.unlink()
            except Exception:
                pass
            self._seen = set()
            self._baseline_done = True
            return
        try:
            if self.seen_path.exists():
                obj = json.loads(self.seen_path.read_text(encoding='utf-8'))
                ids = obj.get('ids') or []
                self._seen = set(str(x) for x in ids)
        except Exception:
            self._seen = set()

    def _save_seen(self):
        try:
            self.seen_path.parent.mkdir(parents=True, exist_ok=True)
            ids = list(self._seen)[-5000:]
            self.seen_path.write_text(json.dumps({'ids': ids}, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass

    @staticmethod
    def _id_of(ev: Event) -> str:
        oid = str(ev.get('id') or ev.get('eid') or '').strip()
        if oid:
            return oid
        # Fallback to SHA1(text+peer+type)
        raw = json.dumps({'t': ev.get('type'), 'p': ev.get('peer') or ev.get('from'), 'x': ev.get('text') or ''}, ensure_ascii=False)
        return hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()

    def loop(self,
             on_to_user: Optional[Callable[[Event], None]] = None,
             on_to_peer_summary: Optional[Callable[[Event], None]] = None):
        """Poll outbox.jsonl and dispatch new events.
        - on_to_user(ev): ev {'type':'to_user','peer':'peerA|peerB','text':...}
        - on_to_peer_summary(ev): ev {'type':'to_peer_summary','from':'PeerA|PeerB','text':...}
        """
        while True:
            try:
                if not self.outbox.exists():
                    time.sleep(self.poll_seconds); continue
                # Read only a window from tail to bound CPU
                lines = self.outbox.read_text(encoding='utf-8').splitlines()[-self.window:]
                changed = False
                for ln in lines:
                    try:
                        ev = json.loads(ln)
                    except Exception:
                        continue
                    et = str(ev.get('type') or '').lower()
                    if et not in ('to_user','to_peer_summary'):
                        continue
                    oid = self._id_of(ev)
                    if oid and oid in self._seen:
                        continue
                    if not self._baseline_done:
                        # First pass (baseline): mark current tail as seen without dispatch,
                        # then switch to delivery mode on the next loop.
                        if oid:
                            self._seen.add(oid); changed = True
                        continue
                    if et == 'to_user' and on_to_user:
                        on_to_user(ev)
                    elif et == 'to_peer_summary' and on_to_peer_summary:
                        on_to_peer_summary(ev)
                    if oid:
                        self._seen.add(oid); changed = True
                if changed:
                    self._save_seen()
                self._baseline_done = True
            except Exception:
                # Swallow errors; bridges should not crash orchestrator
                pass
            time.sleep(self.poll_seconds)
