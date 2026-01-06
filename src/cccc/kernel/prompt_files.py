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

DEFAULT_PREAMBLE_BODY = """Collaboration baseline (high ROI):
- If DoD is unclear: ask + record a short DoD in Context (notes/tasks).
- If you claim done/fixed: update tasks/milestones + 1-line evidence (tests/files/logs).
- If you agree with someone: say what you checked; otherwise raise 1 concrete risk/question.

Start:
- cccc_bootstrap(...) (recommended).
- Read/clear inbox via cccc_inbox_list(...) + cccc_inbox_mark_read(event_id=...) / cccc_inbox_mark_all_read(...).
- Follow PROJECT.md (via cccc_project_info).
- Use MCP for CCCC control-plane actions; use shell only for repo work.

Tone: talk like a real teammate (human, direct, lightly emotional is OK). Avoid bureaucratic/corporate phrasing.
"""

DEFAULT_STANDUP_TEMPLATE = """{{interval_minutes}} minutes have passed. Time for a team review.

Foreman, please initiate a stand-up with @peers:

1. ASK PEERS TO REFLECT (not just report):
   - Update your progress in context (use cccc_task_update)
   - Step back and think: Is our approach correct? Any blind spots?
   - Any better ideas or alternative approaches?
   - What concerns or risks do you see?

2. COLLECT & SYNTHESIZE:
   - Gather insights from all peers
   - Look for patterns, conflicts, or new opportunities
   - Peers are collaborators, not subordinates - value their perspectives

3. DECIDE TOGETHER:
   - Adjust direction if needed based on collective wisdom
   - Update vision/sketch to reflect new understanding
   - Reallocate work if better approaches emerge

Example: "@peers Stand-up time. Please: 1) Update your task progress, 2) Share any concerns about our current approach, 3) Suggest improvements if you see any."
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
