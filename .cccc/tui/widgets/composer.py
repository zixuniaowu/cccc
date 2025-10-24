from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from textual.app import ComposeResult
from textual.widgets import Input, ListItem, ListView, Label, Static


CMD_HELP: List[Tuple[str, str, str]] = [
    ("/a <text>", "Send to PeerA", "/a Hello"),
    ("/b <text>", "Send to PeerB", "/b Hello"),
    ("/both <text>", "Send to both peers", "/both Hi both"),
    ("/foreman now", "Run Foreman once now", "/foreman now"),
    ("/foreman on|off|status", "Control Foreman", "/foreman status"),
    ("/sys-refresh", "Re-inject full SYSTEM prompt", "/sys-refresh"),
    ("/reset", "Hard reset (no confirm)", "/reset"),
    ("/c \"<prompt>\"", "One-shot Aux", "/c \"summarize repo\""),
    ("/verbose on|off", "Toggle timeline verbosity", "/verbose on"),
    ("/log", "Show internal event overlay", "/log"),
    ("/state", "Show health & counters", "/state"),
    ("/help", "Show command list", "/help"),
    ("/quit", "Quit orchestrator and tmux session", "/quit"),
    ("/exit", "Alias of /quit", "/exit"),
]


class Composer(Static):
    def __init__(self, home: Path, timeline) -> None:  # noqa: ANN001
        super().__init__(id="composer")
        self.home = home
        self.timeline = timeline
        self.input = Input(placeholder="Type / for commands…")
        self.list = ListView(*[ListItem(Label(f"{cmd:20} — {desc}")) for cmd, desc, _ in CMD_HELP])
        self.list.visible = False

    def compose(self) -> ComposeResult:
        yield self.input
        yield self.list

    async def on_input_changed(self, event: Input.Changed) -> None:  # noqa: D401
        txt = event.value
        self.list.visible = txt.strip().startswith("/")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:  # noqa: D401
        idx = event.index
        _, _, example = CMD_HELP[idx]
        self.input.value = example
        self.list.visible = False
        await self.input.focus()

    def _append_command(self, obj: Dict[str, Any]) -> None:
        path = self.home / "state" / "commands.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n"); f.flush()

    async def on_input_submitted(self, event: Input.Submitted) -> None:  # noqa: D401
        text = event.value.strip()
        if not text:
            return
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
            cid = uuid.uuid4().hex[:12]
            if cmd in ("/a", "/b", "/both"):
                route = "a" if cmd == "/a" else ("b" if cmd == "/b" else "both")
                payload = {"id": cid, "type": route, "args": {"text": arg}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
                self.timeline.items.append(f"[You→{route.upper()}] {arg}")
            elif cmd == "/foreman":
                action = (arg or "status").strip()
                payload = {"id": cid, "type": "foreman", "args": {"action": action}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/sys-refresh":
                payload = {"id": cid, "type": "sys-refresh", "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/reset":
                payload = {"id": cid, "type": "reset", "args": {"confirm": True}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/c":
                payload = {"id": cid, "type": "c", "args": {"prompt": arg}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/verbose":
                self.timeline.verbose = (arg.strip().lower() != "off")
            elif cmd in ("/log", "/state", "/help"):
                self.timeline.items.append(f"[TUI] {cmd[1:]} not implemented as overlay yet")
            elif cmd in ("/quit", "/exit"):
                payload = {"id": cid, "type": "quit", "source": "tui", "ts": time.time()}
                self._append_command(payload)
                self.timeline.items.append("[TUI] Quit requested…")
            else:
                self.timeline.items.append(f"[TUI] Unknown command: {cmd}")
        else:
            cid = uuid.uuid4().hex[:12]
            payload = {"id": cid, "type": "both", "args": {"text": text}, "source": "tui", "ts": time.time()}
            self._append_command(payload)
            self.timeline.items.append(f"[You→BOTH] {text}")
        self.input.value = ""

