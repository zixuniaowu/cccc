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
    ("/clear", "Clear (reserved; no-op for now)", "/clear"),
    ("/pause | /resume", "Pause/resume A↔B handoff", "/pause"),
    ("/focus [hint]", "Ask PeerB to refresh POR.md", "/focus"),
    ("/review", "Request Aux review bundle", "/review"),
    ("/echo on|off|<empty>", "Console echo on/off/show", "/echo"),
    ("/c \"<prompt>\"", "One-shot Aux", "/c \"summarize repo\""),
    ("/verbose on|off", "Toggle timeline verbosity", "/verbose on"),
    ("/log", "Show internal event overlay", "/log"),
    ("/state", "Show health & counters", "/state"),
    ("/help", "Show command list", "/help"),
    ("/quit", "Quit orchestrator and tmux session", "/quit"),
    ("/exit", "Alias of /quit", "/exit"),
]


class Composer(Static):
    def __init__(self, home: Path, timeline, on_toggle_setup=None) -> None:  # noqa: ANN001
        super().__init__(id="composer")
        self.home = home
        self.timeline = timeline
        self.on_toggle_setup = on_toggle_setup
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
            elif cmd in ("/pause", "/resume"):
                payload = {"id": cid, "type": cmd[1:], "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/foreman":
                action = (arg or "status").strip()
                payload = {"id": cid, "type": "foreman", "args": {"action": action}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/sys-refresh":
                payload = {"id": cid, "type": "sys-refresh", "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/setup":
                if callable(self.on_toggle_setup):
                    self.on_toggle_setup()
                return
            elif cmd == "/clear":
                payload = {"id": cid, "type": "clear", "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/token":
                # /token set <value> | /token unset
                a = (arg or '').split()
                sub = a[0].lower() if a else ''
                if sub == 'set' and len(a) >= 2:
                    val = ' '.join(a[1:])
                    payload = {"id": cid, "type": "token", "args": {"action": "set", "value": val}, "source": "tui", "ts": time.time()}
                    self._append_command(payload)
                elif sub == 'unset':
                    payload = {"id": cid, "type": "token", "args": {"action": "unset"}, "source": "tui", "ts": time.time()}
                    self._append_command(payload)
                else:
                    self.timeline.items.append("[TUI] Usage: /token set <value> | /token unset")
            elif cmd == "/focus":
                payload = {"id": cid, "type": "focus", "args": {"hint": arg}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/review":
                payload = {"id": cid, "type": "review", "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/echo":
                v = arg.strip().lower()
                if v not in ("on", "off", ""):
                    v = ""
                payload = {"id": cid, "type": "echo", "args": {"value": v}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif cmd == "/help":
                # Open the command list instead of injecting timeline noise
                self.list.visible = True
                await self.input.focus()
                return
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
            # Prefix forms: a:/b:/both:/u:/a!/b!
            cid = uuid.uuid4().hex[:12]
            lower = text.lower()
            if lower.startswith("a! "):
                payload = {"id": cid, "type": "passthru", "args": {"peer": "A", "cmd": text[2:].strip()}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif lower.startswith("b! "):
                payload = {"id": cid, "type": "passthru", "args": {"peer": "B", "cmd": text[2:].strip()}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
            elif lower.startswith("a:"):
                payload = {"id": cid, "type": "a", "args": {"text": text.split(":", 1)[1].strip()}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
                self.timeline.items.append(f"[You→A] {text.split(':',1)[1].strip()}")
            elif lower.startswith("b:"):
                payload = {"id": cid, "type": "b", "args": {"text": text.split(":", 1)[1].strip()}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
                self.timeline.items.append(f"[You→B] {text.split(':',1)[1].strip()}")
            elif lower.startswith("both:") or lower.startswith("u:"):
                payload = {"id": cid, "type": "both", "args": {"text": text.split(":", 1)[1].strip()}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
                self.timeline.items.append(f"[You→BOTH] {text.split(':',1)[1].strip()}")
            else:
                payload = {"id": cid, "type": "both", "args": {"text": text}, "source": "tui", "ts": time.time()}
                self._append_command(payload)
                self.timeline.items.append(f"[You→BOTH] {text}")
        self.input.value = ""
