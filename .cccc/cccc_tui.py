#!/usr/bin/env python3
import argparse, json, os, sys, time, uuid
from pathlib import Path
from typing import List, Dict, Any

try:
    from textual.app import App, ComposeResult
    from textual.widgets import Static, Input, Footer, Header, ListView, ListItem, Label
    from textual.containers import Vertical, Horizontal
    from textual.reactive import reactive
    from textual import events
except Exception as e:
    # Render a readable message in the pane and pause briefly so users can see it
    sys.stderr.write("\n[TUI] textual is required for the CCCC TUI.\n")
    sys.stderr.write("[TUI] Install:  pip install textual  (inside the same venv running cccc)\n\n")
    sys.stderr.flush()
    try:
        import time; time.sleep(6)
    except Exception:
        pass
    sys.exit(1)

ASCII_CCCC = (
" _____   _____   _____   _____\n"
"/  __ \\ /  __ \\ /  __ \\ /  __ \\n\n"
"| /  \\/ | /  \\/ | /  \\/ | /  \\n\n"
"| |     | |     | |     | |\n\n"
"| \\__/\\ | \\__/\\ | \\__/\\ | \\__/\\\n"
" \\____/  \\____/  \\____/  \\____/\n"
"    C C C C  Orchestrator\n"
)

CMD_HELP = [
    ("/a <text>", "Send to PeerA", "/a Hello"),
    ("/b <text>", "Send to PeerB", "/b Hello"),
    ("/both <text>", "Send to both peers", "/both Hi both"),
    ("/foreman now", "Run Foreman once now", "/foreman now"),
    ("/foreman on|off|status", "Control Foreman", "/foreman status"),
    ("/sys-refresh", "Re-inject full SYSTEM prompt", "/sys-refresh"),
    ("/reset", "Hard reset (TUI will ask confirm)", "/reset"),
    ("/c \"<prompt>\"", "One-shot Aux", "/c \"summarize repo\""),
    ("/verbose on|off", "Toggle timeline verbosity", "/verbose on"),
    ("/log", "Show internal event overlay", "/log"),
    ("/state", "Show health & counters", "/state"),
    ("/help", "Show command list", "/help"),
    ("/quit", "Quit orchestrator and tmux session", "/quit"),
    ("/exit", "Alias of /quit", "/exit"),
]

class Timeline(Static):
    verbose = reactive(True)
    def __init__(self, home: Path):
        super().__init__(id="timeline")
        self.home = home
        self.eids: set[str] = set()
        self.max_items = 200
        self.items: List[str] = []

    def on_mount(self):
        self.set_interval(1.0, self.refresh_data)
        self.update(self.render())

    def refresh_data(self):
        outbox = self.home/"state"/"outbox.jsonl"
        if not outbox.exists():
            return
        try:
            lines = outbox.read_text(encoding='utf-8', errors='replace').splitlines()[-1000:]
        except Exception:
            return
        for ln in lines:
            if not ln.strip():
                continue
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            eid = str(ev.get('eid') or '')
            if eid and eid in self.eids:
                continue
            t = str(ev.get('type') or '')
            show = False
            if t == 'to_user':
                show = True
            elif self.verbose and t in ('to_peer_summary',):
                show = True
            if not show:
                continue
            frm = str(ev.get('from') or ev.get('peer') or '?')
            to = str(ev.get('to') or ev.get('owner') or 'User')
            text = str(ev.get('text') or '')
            head = f"[{frm}→{to}] "
            body = text.strip().splitlines()[0][:160]
            item = head + body
            self.items.append(item)
            if eid:
                self.eids.add(eid)
            if len(self.items) > self.max_items:
                self.items = self.items[-self.max_items:]
        self.update(self.render())

    def render(self) -> str:
        title = ASCII_CCCC + "\n"
        return title + "\n".join(self.items[-self.max_items:])

class StatusCards(Static):
    def __init__(self, home: Path):
        super().__init__(id="status")
        self.home = home
        self._last_text = ""

    def on_mount(self):
        self.set_interval(2.0, self.refresh_data)
        self._last_text = self._render_text({})
        self.update(self._last_text)

    def refresh_data(self):
        data = {}
        try:
            s = (self.home/"state"/"status.json").read_text(encoding='utf-8')
            data = json.loads(s)
        except Exception:
            pass
        self._last_text = self._render_text(data)
        self.update(self._last_text)

    def _render_text(self, data: Dict[str,Any]) -> str:
        p = data.get('handoffs') or {}
        fore = data.get('foreman') or {}
        lines = [
            "— Status —",
            f"PeerA handoffs={p.get('handoffs_peerA','-')} next_self={p.get('next_self_peerA','-')}",
            f"PeerB handoffs={p.get('handoffs_peerB','-')} next_self={p.get('next_self_peerB','-')}",
            f"Foreman enabled={fore.get('enabled',False)} running={fore.get('running',False)} next={fore.get('next_due','-')} last_rc={fore.get('last_rc','-')}",
        ]
        return "\n".join(lines)

    def render(self) -> str:
        return self._last_text

class Composer(Static):
    def __init__(self, home: Path, timeline: Timeline):
        super().__init__(id="composer")
        self.home = home
        self.timeline = timeline
        self.input = Input(placeholder="Type / for commands…")
        self.list = ListView(*[ListItem(Label(f"{cmd:20} — {desc}")) for cmd,desc,_ in CMD_HELP])
        self.list.visible = False
        self.await_reset_confirm = False

    def compose(self) -> ComposeResult:
        yield self.input
        yield self.list

    async def on_input_changed(self, event: Input.Changed) -> None:
        txt = event.value
        self.list.visible = txt.strip().startswith('/')

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.index
        _,_,example = CMD_HELP[idx]
        self.input.value = example
        self.list.visible = False
        await self.input.focus()

    def _append_command(self, obj: Dict[str,Any]):
        path = self.home/"state"/"commands.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n"); f.flush()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if text.startswith('/'):  # command path
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ''
            cid = uuid.uuid4().hex[:12]
            # route commands
            if cmd in ('/a','/b','/both'):
                route = 'a' if cmd=='/a' else ('b' if cmd=='/b' else 'both')
                payload = {"id": cid, "type": route, "args": {"text": arg}, "source":"tui","ts": time.time()}
                self._append_command(payload)
                self.timeline.items.append(f"[You→{route.upper()}] {arg}")
            elif cmd == '/foreman':
                action = (arg or 'status').strip()
                payload = {"id": cid, "type": "foreman", "args": {"action": action}, "source":"tui","ts": time.time()}
                self._append_command(payload)
            elif cmd == '/sys-refresh':
                payload = {"id": cid, "type": "sys-refresh", "source":"tui","ts": time.time()}
                self._append_command(payload)
            elif cmd == '/reset':
                # two-step confirm
                if not self.await_reset_confirm:
                    self.timeline.items.append("[TUI] Confirm reset: type '/reset confirm' to proceed within 60s")
                    self.await_reset_confirm = True
                    return
                ok = (arg.strip().lower() == 'confirm')
                if not ok:
                    self.timeline.items.append("[TUI] Reset aborted (need '/reset confirm')")
                    self.await_reset_confirm = False
                    return
                payload = {"id": cid, "type": "reset", "args": {"confirm": True}, "source":"tui","ts": time.time()}
                self._append_command(payload)
                self.await_reset_confirm = False
            elif cmd == '/c':
                payload = {"id": cid, "type": "c", "args": {"prompt": arg}, "source":"tui","ts": time.time()}
                self._append_command(payload)
            elif cmd == '/verbose':
                self.timeline.verbose = (arg.strip().lower() != 'off')
            elif cmd in ('/log','/state','/help'):
                # For MVP: inject a brief line; future: open overlay panels
                self.timeline.items.append(f"[TUI] {cmd[1:]} not implemented as overlay yet")
            elif cmd in ('/quit','/exit'):
                payload = {"id": cid, "type": "quit", "source":"tui","ts": time.time()}
                self._append_command(payload)
                self.timeline.items.append("[TUI] Quit requested…")
            else:
                self.timeline.items.append(f"[TUI] Unknown command: {cmd}")
        else:
            # plain text defaults to both
            cid = uuid.uuid4().hex[:12]
            payload = {"id": cid, "type": "both", "args": {"text": text}, "source":"tui","ts": time.time()}
            self._append_command(payload)
            self.timeline.items.append(f"[You→BOTH] {text}")
        self.input.value = ""

class CCCCApp(App):
    CSS = """
    #timeline {height: 70%; border: round $accent}
    #composer {height: 15%;}
    #status {height: 15%; border: tall $secondary}
    """
    BINDINGS = []
    def __init__(self, home: Path):
        super().__init__()
        self.home = home
        self.timeline = Timeline(home)
        self.status = StatusCards(home)
        self.composer = Composer(home, self.timeline)

    def compose(self) -> ComposeResult:
        yield self.timeline
        yield self.composer
        yield self.status

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--home', required=True)
    args = ap.parse_args()
    home = Path(args.home)
    # Write a ready marker so the orchestrator can confirm startup
    try:
        ready = home/"state"/"tui.ready"
        ready.parent.mkdir(parents=True, exist_ok=True)
        ready.write_text(str(int(time.time())), encoding='utf-8')
    except Exception:
        pass
    app = CCCCApp(home)
    app.run()

if __name__ == '__main__':
    main()
