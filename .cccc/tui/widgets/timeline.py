from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from textual.reactive import reactive
try:
    from textual.widgets import TextLog as _BaseLog
except Exception:
    from textual.widgets import Log as _BaseLog  # type: ignore


ASCII_CCCC = (
    " _____   _____   _____   _____\n"
    "/  __ \\ /  __ \\ /  __ \\ /  __ \\n\n"
    "| /  \\/ | /  \\/ | /  \\/ | /  \\n\n"
    "| |     | |     | |     | |\n\n"
    "| \\__/\\ | \\__/\\ | \\__/\\ | \\__/\\\n"
    " \\____/  \\____/  \\____/  \\____/\n"
    "    C C C C  Orchestrator\n"
)


class Timeline(_BaseLog):
    verbose = reactive(True)

    def __init__(self, home: Path):
        
        try:
            super().__init__(id="timeline", highlight=False, markup=False, wrap=True, auto_scroll=True)  # type: ignore
        except Exception:
            try:
                super().__init__(id="timeline")  # type: ignore
            except Exception:
                super().__init__()  # type: ignore
        self.home = home
        self.eids: set[str] = set()
        self._header_written = False
        self.max_lines = 1200
        self._line_count = 0
        # Provide a minimal list-like proxy so callers can do timeline.items.append("text")
        class _ItemsProxy:
            def __init__(self, owner: 'Timeline') -> None:
                self._owner = owner
            def append(self, text: str) -> None:
                try:
                    self._owner._log_write(str(text))
                except Exception:
                    pass
        self.items = _ItemsProxy(self)  # type: ignore[attr-defined]

    def _log_write(self, text: str) -> None:
        if hasattr(self, "write"):
            try:
                getattr(self, "write")(text)
            except Exception:
                if hasattr(self, "write_line"):
                    getattr(self, "write_line")(text)
        elif hasattr(self, "write_line"):
            getattr(self, "write_line")(text)
        else:
            try:
                from rich.text import Text  # type: ignore
                self.update(Text(str(text)))
            except Exception:
                pass
        self._line_count += 1
        if self._line_count > self.max_lines:
            try:
                self.clear()
            except Exception:
                pass
            self._line_count = 0
            self._header_written = False
            self._ensure_header()

    def on_mount(self) -> None:  # noqa: D401
        # Write header once and start polling outbox
        self._ensure_header()
        self.set_interval(2.0, self.refresh_data)

    def _ensure_header(self) -> None:
        if self._header_written:
            return
        for ln in ASCII_CCCC.rstrip("\n").splitlines():
            self._log_write(ln)
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
            self._log_write(head + body)
            if eid:
                self.eids.add(eid)
