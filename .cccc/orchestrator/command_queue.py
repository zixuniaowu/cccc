# -*- coding: utf-8 -*-
"""
Command queue helpers (system copy, minimal surface):
- init_command_offsets: truncate command files and start from position 0
- append_command_result: write structured result back to commands.jsonl
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, Iterable

def init_command_offsets(commands_paths: Iterable[Path], scan_path: Path) -> Dict[str, int]:
    """Initialize command queue by truncating files and starting from position 0.

    Truncates all command files to ensure clean state on each orchestrator start.
    The scan_path parameter is kept for API compatibility but no longer used.
    """
    last_pos_map: Dict[str, int] = {}
    for p in commands_paths:
        key = str(p)
        try:
            # Truncate file to clear stale commands from previous sessions
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('', encoding='utf-8')
        except Exception:
            pass
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

