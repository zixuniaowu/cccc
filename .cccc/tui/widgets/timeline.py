from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from textual.reactive import reactive
from textual.widgets import TextLog


ASCII_CCCC = (
    " _____   _____   _____   _____\n"
    "/  __ \\ /  __ \\ /  __ \\ /  __ \\n\n"
    "| /  \\/ | /  \\/ | /  \\/ | /  \\n\n"
    "| |     | |     | |     | |\n\n"
    "| \\__/\\ | \\__/\\ | \\__/\\ | \\__/\\\n"
    " \\____/  \\____/  \\____/  \\____/\n"
    "    C C C C  Orchestrator\n"
)


class Timeline(TextLog):
    verbose = reactive(True)

    def __init__(self, home: Path):
        super().__init__(id="timeline", highlight=False, markup=False, wrap=True, auto_scroll=True)
        self.home = home
        self.eids: set[str] = set()
        self._header_written = False
        self.max_lines = 1200

    def on_mount(self) -> None:  # noqa: D401
        # Write header once and start polling outbox
        self._ensure_header()
        self.set_interval(2.0, self.refresh_data)

    def _ensure_header(self) -> None:
        if self._header_written:
            return
        for ln in ASCII_CCCC.rstrip("\n").splitlines():
            self.write(ln)
        self._header_written = True

    def refresh_data(self) -> None:
        outbox = self.home / "state" / "outbox.jsonl"
        if not outbox.exists():
            return
        try:
            lines = outbox.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]
        except Exception:
            return
        for ln in lines:
            if not ln.strip():
                continue
            try:
                ev: Dict[str, Any] = json.loads(ln)
            except Exception:
                continue
            eid = str(ev.get("eid") or "")
            if eid and eid in self.eids:
                continue
            etype = str(ev.get("type") or "")
            show = (etype == "to_user") or (self.verbose and etype in ("to_peer_summary",))
            if not show:
                continue
            frm = str(ev.get("from") or ev.get("peer") or "?")
            to = str(ev.get("to") or ev.get("owner") or "User")
            text = str(ev.get("text") or "")
            head = f"[{frm}→{to}] "
            body = text.strip().splitlines()[0][:160]
            self.write(head + body)
            if eid:
                self.eids.add(eid)
        # Trim backlog if needed
        try:
            if self.document and len(self.document.lines) > self.max_lines:
                excess = len(self.document.lines) - self.max_lines
                self.clear()
                self._header_written = False
                self._ensure_header()
        except Exception:
            pass
