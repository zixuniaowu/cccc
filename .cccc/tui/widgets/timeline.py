from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from textual.reactive import reactive
from textual.widgets import Static


ASCII_CCCC = (
    " _____   _____   _____   _____\n"
    "/  __ \\ /  __ \\ /  __ \\ /  __ \\n\n"
    "| /  \\/ | /  \\/ | /  \\/ | /  \\n\n"
    "| |     | |     | |     | |\n\n"
    "| \\__/\\ | \\__/\\ | \\__/\\ | \\__/\\\n"
    " \\____/  \\____/  \\____/  \\____/\n"
    "    C C C C  Orchestrator\n"
)


class Timeline(Static):
    verbose = reactive(True)

    def __init__(self, home: Path):
        super().__init__(id="timeline")
        self.home = home
        self.eids: set[str] = set()
        self.max_items = 200
        self.items: List[str] = []

    def on_mount(self) -> None:  # noqa: D401
        # Poll outbox at a modest cadence to keep CPU low
        self.set_interval(2.0, self.refresh_data)
        self.update(self.render())

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
            self.items.append(head + body)
            if eid:
                self.eids.add(eid)
            if len(self.items) > self.max_items:
                self.items = self.items[-self.max_items :]
        self.update(self.render())

    def render(self) -> str:  # textual converts to Rich renderable
        title = ASCII_CCCC + "\n"
        return title + "\n".join(self.items[-self.max_items :])

