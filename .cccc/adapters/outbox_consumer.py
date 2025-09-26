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

# Debug switch for high-frequency logs (heartbeats, read snapshots)
DEBUG_OUTBOX = False

def _ledger_append_local(home: Path, entry: Dict[str, Any]):
    try:
        state = home/"state"; state.mkdir(parents=True, exist_ok=True)
        ent = {"ts": time.strftime('%Y-%m-%d %H:%M:%S'), **entry}
        with (state/"ledger.jsonl").open('a', encoding='utf-8') as f:
            f.write(json.dumps(ent, ensure_ascii=False) + "\n")
    except Exception:
        pass

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
        self._last_hb_ts = 0.0

    def _load_cursor(self):
        try:
            cur = json.loads(self.cursor_path.read_text(encoding='utf-8'))
            self._dev = cur.get('dev'); self._ino = cur.get('ino'); self._offset = int(cur.get('offset') or 0)
        except Exception:
            self._dev = None; self._ino = None; self._offset = 0
        # Diagnostic: cursor loaded (debug only)
        if DEBUG_OUTBOX:
            _ledger_append_local(self.home, {"kind":"bridge-consumer-cursor-load","offset": self._offset, "dev": self._dev, "ino": self._ino})

    def _save_cursor(self, dev: int, ino: int, offset: int):
        try:
            tmp = self.cursor_path.with_suffix('.tmp')
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({'dev': dev, 'ino': ino, 'offset': int(offset)}, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(tmp, self.cursor_path)
        except Exception:
            pass
        # Diagnostic: cursor saved (debug only)
        if DEBUG_OUTBOX:
            _ledger_append_local(self.home, {"kind":"bridge-consumer-cursor-save","offset": int(offset)})

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
                # Periodic heartbeat (≤1 per 2s)
                now = time.time()
                if DEBUG_OUTBOX and (now - self._last_hb_ts >= 2.0):
                    _ledger_append_local(self.home, {"kind":"bridge-consumer-heartbeat","offset": self._offset, "size": size, "dev": dev, "ino": ino})
                    self._last_hb_ts = now
                # Handle rotation/truncate or first run
                rotated = (self._dev is None) or (self._dev != dev or self._ino != ino or self._offset > size)
                if rotated:
                    # Initialize according to start_mode
                    reason = "first" if self._dev is None else ("inode-change" if (self._dev != dev or self._ino != ino) else "truncate")
                    if self.start_mode == 'from_start':
                        self._offset = 0
                    else:
                        if self._dev is None:
                            # First time seeing the file; if the file looks freshly created, read from start to avoid skipping the first event
                            try:
                                is_fresh = (now - os.stat(self.outbox).st_mtime) <= 5.0
                            except Exception:
                                is_fresh = True
                            if is_fresh:
                                self._offset = 0
                            else:
                                self._offset = size
                        else:
                            # Rotation/truncate: default to tail (do not replay history)
                            self._offset = size
                    self._dev, self._ino = dev, ino
                    self._save_cursor(dev, ino, self._offset)
                    if DEBUG_OUTBOX:
                        _ledger_append_local(self.home, {"kind":"bridge-consumer-rotated","reason": reason, "set_offset": self._offset, "size": size})
                # Read new bytes
                if size > self._offset:
                    prev_off = self._offset
                    with open(self.outbox, 'r', encoding='utf-8', errors='replace') as f:
                        f.seek(self._offset)
                        chunk = f.read()
                    if not chunk:
                        time.sleep(self.poll_seconds); first_run = False; continue
                    data = self._buf + chunk
                    lines = data.splitlines(keepends=True)
                    if DEBUG_OUTBOX:
                        _ledger_append_local(self.home, {
                            "kind": "bridge-consumer-read",
                            "offset_before": prev_off,
                            "size": size,
                            "chunk": len(chunk),
                            "data": len(data),
                            "lines": len(lines),
                            "buf_prev": len(self._buf),
                        })
                    # Keep last line if it has no newline
                    pending = ''
                    if lines and not lines[-1].endswith('\n'):
                        pending = lines.pop().rstrip('\r\n')
                    advanced = 0
                    for idx, ln in enumerate(lines):
                        pos_advance = len(ln)
                        text = ln.rstrip('\r\n')
                        ok_commit = True
                        try:
                            ev = json.loads(text)
                            et = str(ev.get('type') or '').lower()
                            evid = str(ev.get('id') or ev.get('eid') or '')
                            # Diagnostic: record dispatch attempt (debug only)
                            if DEBUG_OUTBOX:
                                _ledger_append_local(self.home, {"kind":"bridge-dispatch-attempt","type":et,"id":evid,"offset":self._offset, "idx": idx, "len": pos_advance})
                            if et == 'to_user' and on_to_user:
                                ok_commit = bool(on_to_user(ev))
                            elif et == 'to_peer_summary' and on_to_peer_summary:
                                ok_commit = bool(on_to_peer_summary(ev))
                            else:
                                if DEBUG_OUTBOX:
                                    _ledger_append_local(self.home, {"kind":"bridge-dispatch-skip","reason":"unknown-type","offset": self._offset, "idx": idx})
                                ok_commit = True  # ignore unknown types but advance
                        except Exception:
                            ok_commit = True  # malformed line: skip to avoid deadlock
                            _ledger_append_local(self.home, {"kind":"bridge-outbox-json-error","offset": self._offset, "snippet": text[:120]})
                        if ok_commit:
                            self._offset += pos_advance
                            advanced += pos_advance
                            if DEBUG_OUTBOX:
                                _ledger_append_local(self.home, {"kind":"bridge-dispatch-ok","offset": self._offset, "idx": idx})
                        else:
                            # Stop processing further lines; keep buffer for retry (debug only)
                            if DEBUG_OUTBOX:
                                _ledger_append_local(self.home, {"kind":"bridge-dispatch-retry","offset": self._offset, "idx": idx})
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
