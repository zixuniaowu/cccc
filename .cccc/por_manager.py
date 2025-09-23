# -*- coding: utf-8 -*-
"""POR helper utilities (Markdown-based).

This module defines the canonical POR file location and ensures it exists.
It now points to docs/por/POR.md (business domain), not .cccc/state/.
When creating a new POR, we prefer rendering from the repository template
at .cccc/settings/templates/por.md.j2; if missing, we fall back to a
minimal built-in skeleton. No external templating dependency is used.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict
import datetime as _dt
from datetime import timezone as _tz
import hashlib as _hash

# Keep ASCII-only content and comments.

POR_FILENAME = "POR.md"

# Built-in minimal skeleton (used only when the template is missing).
POR_TEMPLATE = """# POR - Strategic Board
- North Star: <one line>; Guardrails: <quality/safety/cost/latency 2-3 items>
- Non-Goals / Boundaries: <1-3 lines>

## Deliverables (top-level)
- <deliverable A> - path/interface/format - owner
- <deliverable B> - path/interface/format - owner

## Bets & Assumptions
- Bet 1: <one intent line> | Probe: <cmd/script> | Evidence: <one line> | Window: <date/threshold>
- Bet 2: <...>

## Roadmap (Now/Next/Later)
- Now (<= 2 weeks): <3-5 lines of intent + criteria>
- Next (<= 6 weeks): <...>
- Later (> 6 weeks): <...>

## Decision & Pivot Log (recent 5)
- YYYY-MM-DD | context | choice/pivot | evidence | impact/rollback | default

## Risk Radar & Mitigations (up/down/flat)
- R1: signal/impact/minimal counter (up)

## Portfolio Health (in-progress / at-risk only)
| ID | Title | Owner | Stage | Latest evidence (one line) | SUBPOR |
|----|-------|-------|-------|----------------------------|--------|

## Operating Principles (short)
- Falsify before expand; one decidable next step; stop when wrong; Done = evidence.

## Maintenance & Change Log (append-only, one line each)
- YYYY-MM-DD HH:MM | who | reason | evidence

<!-- Generated: fallback skeleton (por_manager). Consider generating via .cccc/por_subpor.py for the full template. -->
"""


def por_path(home: Path) -> Path:
    """Return the canonical POR path under docs/por/.

    This moves POR to the business domain (docs/), keeping .cccc/ for
    orchestrator internals only.
    """
    return (Path.cwd()/"docs"/"por")/POR_FILENAME


def _render_from_template(template_path: Path) -> str:
    """Very small placeholder renderer: replaces {{name}} with values.

    We intentionally avoid external deps (no Jinja). Only a few vars are
    provided and the template should remain simple.
    """
    raw = template_path.read_text(encoding="utf-8")
    sha1 = _hash.sha1(raw.encode("utf-8", errors="replace")).hexdigest()
    subs = {
        "generated_on": _dt.datetime.now(_tz.utc).isoformat(timespec="seconds"),
        "template_sha1": sha1,
        "tool": "por_manager.ensure_por",
        "tool_version": "0.1.1",
        }
    out = raw
    for k, v in subs.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def ensure_por(home: Path) -> Path:
    """Ensure docs/por/POR.md exists.

    Preference order:
      1) Render from .cccc/settings/templates/por.md.j2 when present.
      2) Fall back to a built-in minimal skeleton.
    The function never overwrites an existing POR.md.
    """
    path = por_path(home)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tpl = (Path.cwd()/".cccc"/"settings"/"templates"/"por.md.j2")
    try:
        if tpl.exists():
            text = _render_from_template(tpl)
        else:
            text = POR_TEMPLATE.strip() + "\n"
        # Prepend a small provenance header for traceability (ASCII only)
        _ts = _dt.datetime.now(_tz.utc).isoformat(timespec='seconds')
        header = (f"<!-- Generated on {_ts} by por_manager; template={'present' if tpl.exists() else 'builtin'} -->\n\n")
        path.write_text(header + text, encoding="utf-8")
    except Exception:
        # Last resort: try writing the fallback skeleton
        try:
            path.write_text(POR_TEMPLATE.strip() + "\n", encoding="utf-8")
        except Exception:
            pass
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


# --- Aux section management ---
AUX_SECTION_TITLE = "## Aux Delegations - Meta-Review/Revise (strategic)"

def ensure_aux_section(home: Path) -> bool:
    """Ensure the POR file contains the Aux delegations section.

    Returns True if the file was modified (section appended), False otherwise.
    This function is idempotent and only appends when the exact heading is missing.
    """
    path = ensure_por(home)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    if AUX_SECTION_TITLE in text:
        return False
    lines = [
        "",
        AUX_SECTION_TITLE,
        "Strategic only: list meta-review/revise items offloaded to Aux.",
        "Keep each item compact: what (one line), why (one line), optional acceptance.",
        "Tactical Aux subtasks now live in each SUBPOR under 'Aux (tactical)'; do not list them here.",
        "After integrating Aux results, either remove the item or mark it done.",
        "- [ ] <meta-review — why — acceptance(optional)>",
        "- [ ] <revise — why — acceptance(optional)>",
        "",
    ]
    try:
        path.write_text(text.rstrip("\n") + "\n" + "\n".join(lines), encoding="utf-8")
        return True
    except Exception:
        return False
