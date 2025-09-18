# -*- coding: utf-8 -*-
"""POR helper utilities (Markdown-based)."""
from __future__ import annotations

from pathlib import Path
from typing import Dict
import datetime as _dt

POR_FILENAME = "POR.md"
POR_TEMPLATE = """# POR Summary
- Objective: _fill in_
- Current Focus: _fill in_
- Key Constraints: _fill in_
- Acceptance Benchmarks: _fill in_

## Roadmap & Milestones
- _Describe upcoming milestones, checkpoints, dependencies._

## Active Tasks & Next Steps
- _List concrete next actions with owners or expectations._

## Risks & Mitigations
- _Enumerate key risks, weak signals, mitigation plans._

## Decisions, Alternatives & Rationale
- _Capture recent choices, rejected options, and why._

## Reflections & Open Questions
- _Record lessons learned, doubts, follow-up investigations._
"""


def por_path(home: Path) -> Path:
    return (home/"state")/POR_FILENAME


def ensure_por(home: Path) -> Path:
    path = por_path(home)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(POR_TEMPLATE.strip() + "\n", encoding="utf-8")
    return path


def read_por_text(home: Path) -> str:
    path = ensure_por(home)
    return path.read_text(encoding="utf-8")


def por_status_snapshot(home: Path) -> Dict[str, str]:
    path = ensure_por(home)
    try:
        stat = path.stat()
        updated = _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    except Exception:
        updated = "unknown"
    summary = ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        collected = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                collected.append(stripped)
            if len(collected) >= 4:
                break
        summary = " ".join(collected)[:200]
    except Exception:
        summary = ""
    return {
        "path": str(path),
        "updated_at": updated,
        "summary": summary,
    }
