# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any

def _read_json_safe(p: Path) -> Dict[str,Any]:
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}

def _write_json_safe(p: Path, obj: Dict[str,Any]):
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass

