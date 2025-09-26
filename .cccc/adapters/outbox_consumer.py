#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Outbox consumer utility (shared by chat adapters) — cursor-based tail (exactly-once).

Purpose
- Tail the single-source outbox stream at .cccc/state/outbox.jsonl
- Persist a file cursor (device, inode, offset) to resume exactly once across restarts
- Dispatch only newly appended events to adapter callbacks

Design
- No external deps; JSONL tail via periodic reads (poll)
- Cursor file: .cccc/state/outbox-cursor-<name>.json {dev, ino, offset}
- Scope: text events only (type in {'to_user','to_peer_summary'})
- Callback should return bool: True = delivered → commit; False = not delivered → retry later

Usage
from pathlib import Path
from .outbox_consumer import OutboxConsumer
oc = OutboxConsumer(Path('.cccc'), seen_name='slack', start_mode='tail', replay_last=0)
oc.loop(on_to_user=fn1, on_to_peer_summary=fn2)
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Callable, Optional
import json, time, os

Event = Dict[str, Any]

class OutboxConsumer:
    def __init__(self, home: Path, *, seen_name: str = 'generic', poll_seconds: float = 1.0,
                 start_mode: str = 'tail', replay_last: int = 0):
        self.home = home
        self.outbox = home/"state"/"outbox.jsonl"
        self.cursor_path = home/"state"/f"outbox-cursor-{seen_name}.json"
        self.poll_seconds = float(poll_seconds)
        self.start_mode = (start_mode or 'tail').strip().lower()
        self.replay_last = int(replay_last or 0)
        self._buf = ""
        self._offset = 0
        self._dev = None
        self._ino = None
        self._load_cursor()

    def _load_cursor(self):
        try:
            cur = json.loads(self.cursor_path.read_text(encoding='utf-8'))
            self._dev = cur.get('dev'); self._ino = cur.get('ino'); self._offset = int(cur.get('offset') or 0)
        except Exception:
            self._dev = None; self._ino = None; self._offset = 0

    def _save_cursor(self, dev: int, ino: int, offset: int):
        try:
            tmp = self.cursor_path.with_suffix('.tmp')
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({'dev': dev, 'ino': ino, 'offset': int(offset)}, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(tmp, self.cursor_path)
        except Exception:
            pass

    def loop(self,
             on_to_user: Optional[Callable[[Event], bool]] = None,
             on_to_peer_summary: Optional[Callable[[Event], bool]] = None):
        """Poll outbox.jsonl and dispatch new events.
        Callback returns True → commit cursor; False → retry later.
        """
        first_run = True
        while True:
            try:
                if not self.outbox.exists():
                    time.sleep(self.poll_seconds); continue
                st = os.stat(self.outbox)
                dev, ino, size = st.st_dev, st.st_ino, st.st_size
                # Handle rotation/truncate or first run
                rotated = (self._dev is None) or (self._dev != dev or self._ino != ino or self._offset > size)
                if rotated:
                    # Initialize according to start_mode
                    if self.start_mode == 'from_start':
                        self._offset = 0
                    else:
                        self._offset = size
                        # Optional small replay
                        if first_run and self.replay_last > 0:
                            try:
                                lines = self.outbox.read_text(encoding='utf-8').splitlines()
                                tail = lines[-self.replay_last:]
                                for ln in tail:
                                    ev = json.loads(ln)
                                    et = str(ev.get('type') or '').lower()
                                    if et == 'to_user' and on_to_user:
                                        on_to_user(ev)
                                    elif et == 'to_peer_summary' and on_to_peer_summary:
                                        on_to_peer_summary(ev)
                            except Exception:
                                pass
                    self._dev, self._ino = dev, ino
                    self._save_cursor(dev, ino, self._offset)
                # Read new bytes
                if size > self._offset:
                    with open(self.outbox, 'r', encoding='utf-8', errors='replace') as f:
                        f.seek(self._offset)
                        chunk = f.read()
                    if not chunk:
                        time.sleep(self.poll_seconds); first_run = False; continue
                    data = self._buf + chunk
                    lines = data.splitlines(keepends=True)
                    # Keep last line if it has no newline
                    pending = ''
                    if lines and not lines[-1].endswith('\n'):
                        pending = lines.pop().rstrip('\r\n')
                    advanced = 0
                    for ln in lines:
                        pos_advance = len(ln)
                        text = ln.rstrip('\r\n')
                        ok_commit = True
                        try:
                            ev = json.loads(text)
                            et = str(ev.get('type') or '').lower()
                            if et == 'to_user' and on_to_user:
                                ok_commit = bool(on_to_user(ev))
                            elif et == 'to_peer_summary' and on_to_peer_summary:
                                ok_commit = bool(on_to_peer_summary(ev))
                            else:
                                ok_commit = True  # ignore unknown types but advance
                        except Exception:
                            ok_commit = True  # malformed line: skip to avoid deadlock
                        if ok_commit:
                            self._offset += pos_advance
                            advanced += pos_advance
                        else:
                            # Stop processing further lines; keep buffer for retry
                            break
                    # Persist cursor if advanced
                    if advanced:
                        self._save_cursor(dev, ino, self._offset)
                    self._buf = pending
                first_run = False
            except Exception:
                # Swallow errors; bridges should not crash orchestrator
                pass
            time.sleep(self.poll_seconds)
