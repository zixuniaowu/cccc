from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .group import Group
from ..util.fs import atomic_write_text


PREAMBLE_FILENAME = "CCCC_PREAMBLE.md"
HELP_FILENAME = "CCCC_HELP.md"
STANDUP_FILENAME = "CCCC_STANDUP.md"

_MAX_FILE_BYTES = 512 * 1024  # Safety limit for repo-managed markdown files.

DEFAULT_PREAMBLE_BODY = """Quick start (recommended):
- Call `cccc_bootstrap` to load PROJECT.md (if present) + Context + inbox + the CCCC help playbook.
- If PROJECT.md is missing, ask the user for goals/constraints/DoD and record a short DoD in Context.
- Read the returned help playbook (or call `cccc_help` anytime to refresh).

Intent: collaborate rigorously (if peers exist) and keep pushing the task forward with evidence-based steps.
Mechanics: use MCP for visible chat; keep commitments/decisions/progress in Context; keep the inbox clean.
"""

DEFAULT_STANDUP_TEMPLATE = """{{interval_minutes}} minutes have passed. Stand-up reminder (foreman only).

Alignment checkpoint:
- Direction: Re-check goals/constraints/DoD (PROJECT.md if present; otherwise user + Context). Are we drifting?
- Rigor: Which key points are evidence vs hypotheses? What needs investigation/verification next (including web search if allowed)?
- Coordination: Ask @peers for risks/alternatives/objections. Synthesize and update Context. If a major decision is unclear, ask the user.

Use your own words. Avoid rigid templates; keep it human and direct.
"""

def load_builtin_help_markdown() -> str:
    """Load the built-in CCCC help markdown bundled in the package."""
    try:
        import importlib.resources

        files = importlib.resources.files("cccc.resources")
        return (files / "cccc-help.md").read_text(encoding="utf-8")
    except Exception:
        try:
            p = Path(__file__).resolve().parents[1] / "resources" / "cccc-help.md"
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""


@dataclass(frozen=True)
class PromptFile:
    filename: str
    path: Optional[str]
    found: bool
    content: Optional[str]


def resolve_active_scope_root(group: Group) -> Optional[Path]:
    """Resolve the active scope root directory for a group.

    Returns None when the group has no attached scope or the scope URL is missing.
    """
    scopes = group.doc.get("scopes")
    if not isinstance(scopes, list) or not scopes:
        return None

    active_scope_key = str(group.doc.get("active_scope_key") or "").strip()
    if active_scope_key:
        for sc in scopes:
            if not isinstance(sc, dict):
                continue
            if str(sc.get("scope_key") or "").strip() != active_scope_key:
                continue
            url = str(sc.get("url") or "").strip()
            if url:
                return Path(url).expanduser().resolve()

    for sc in scopes:
        if not isinstance(sc, dict):
            continue
        url = str(sc.get("url") or "").strip()
        if url:
            return Path(url).expanduser().resolve()

    return None


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    if len(raw) > _MAX_FILE_BYTES:
        raw = raw[:_MAX_FILE_BYTES]
    return raw.decode("utf-8", errors="replace")


def read_repo_prompt_file(group: Group, filename: str) -> PromptFile:
    """Read a prompt override file from the group's active scope root.

    The file is optional. When missing, found=False and content=None.
    """
    root = resolve_active_scope_root(group)
    if root is None:
        return PromptFile(filename=filename, path=None, found=False, content=None)

    path = (root / filename).expanduser()
    if not path.exists() or not path.is_file():
        return PromptFile(filename=filename, path=str(path), found=False, content=None)

    try:
        content = _read_text_file(path)
    except Exception:
        return PromptFile(filename=filename, path=str(path), found=True, content=None)
    return PromptFile(filename=filename, path=str(path), found=True, content=content)


def delete_repo_prompt_file(group: Group, filename: str) -> PromptFile:
    """Delete a repo prompt file if present (reset to built-in defaults)."""
    root = resolve_active_scope_root(group)
    if root is None:
        return PromptFile(filename=filename, path=None, found=False, content=None)

    path = (root / filename).expanduser()
    if not path.exists():
        return PromptFile(filename=filename, path=str(path), found=False, content=None)

    if path.is_file():
        os.unlink(path)
    return PromptFile(filename=filename, path=str(path), found=False, content=None)


def write_repo_prompt_file(group: Group, filename: str, content: str) -> PromptFile:
    """Create or update a repo prompt file under the group's active scope root."""
    root = resolve_active_scope_root(group)
    if root is None:
        raise ValueError("group has no scope attached")

    path = (root / filename).expanduser()
    atomic_write_text(path, str(content or ""), encoding="utf-8")
    return PromptFile(filename=filename, path=str(path), found=True, content=_read_text_file(path))
