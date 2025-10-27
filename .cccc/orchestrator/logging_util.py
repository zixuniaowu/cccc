# -*- coding: utf-8 -*-
"""
Logging and outbox helpers copied verbatim from orchestrator_tmux.py.
"""
from __future__ import annotations
import json, time, hashlib
from pathlib import Path
from typing import Dict, Any

OUTBOX_DEBUG = False  # will be shadowed by caller if needed

def log_ledger(home: Path, entry: Dict[str,Any]):
    state = home/"state"; state.mkdir(exist_ok=True)
    entry={"ts":time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
    with (state/"ledger.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps(entry,ensure_ascii=False)+"\n")

def outbox_write(home: Path, event: Dict[str,Any]) -> Dict[str,Any]:
    state = home/"state"; state.mkdir(exist_ok=True)
    ev = dict(event)
    try:
        if 'ts' not in ev:
            ev['ts'] = time.strftime('%Y-%m-%d %H:%M:%S')
        base = (ev.get('type') or '') + '|' + (ev.get('peer') or ev.get('from') or '') + '|' + (ev.get('text') or '')
        hid = hashlib.sha1(base.encode('utf-8','ignore')).hexdigest()[:12]
        ev.setdefault('id', hid)
    except Exception:
        ev.setdefault('id', str(int(time.time())))
        ev.setdefault('ts', time.strftime('%Y-%m-%d %H:%M:%S'))
    with (state/"outbox.jsonl").open('a', encoding='utf-8') as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    if OUTBOX_DEBUG:
        try:
            log_ledger(home, {"kind":"bridge-outbox-enqueued","type": ev.get('type'), "id": ev.get('id'), "chars": len(str(ev.get('text') or ''))})
        except Exception:
            pass
    return ev

