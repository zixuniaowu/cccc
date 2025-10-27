# -*- coding: utf-8 -*-
"""
Command queue helpers copied from orchestrator_tmux.py (system copy, minimal surface):
- init_command_offsets: restore or initialize tail offsets (skip historical lines)
- append_command_result: write structured result back to commands.jsonl
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, Iterable

def init_command_offsets(commands_paths: Iterable[Path], scan_path: Path) -> Dict[str, int]:
    last_pos_map: Dict[str, int] = {}
    loaded = False
    try:
        snap = json.loads(scan_path.read_text(encoding='utf-8'))
        _map = snap.get("last_pos_map") or {}
        for p in commands_paths:
            key = str(p)
            if key in _map:
                last_pos_map[key] = max(0, int(_map.get(key) or 0))
                loaded = True
    except Exception:
        pass
    if not loaded:
        for p in commands_paths:
            key = str(p)
            try:
                last_pos_map[key] = max(0, p.stat().st_size)
            except FileNotFoundError:
                last_pos_map[key] = 0
    return last_pos_map

def append_command_result(commands_path: Path, cmd_id: str, ok: bool, message: str, **extra):
    try:
        rec = {"id": cmd_id, "result": {"ok": bool(ok), "message": str(message)}}
        rec["result"].update(extra)
        with commands_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
    except Exception:
        pass

