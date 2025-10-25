from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from textual.app import ComposeResult
from textual.widgets import Static, ListView, ListItem, Label


class SetupPanel(Static):
    def __init__(self, home: Path):
        super().__init__(id="setup")
        self.home = home
        self.list = ListView()

    def compose(self) -> ComposeResult:
        yield self.list

    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh_data)
        self.refresh_data()

    def refresh_data(self) -> None:
        status = self._read_status()
        setup = status.get("setup") or {}
        roles = (setup.get("roles") or {})
        cli = (setup.get("cli") or {})
        tg = (setup.get("telegram") or {})
        actors = setup.get("actors_available") or []

        items: List[ListItem] = []
        items.append(ListItem(Label("Setup Checklist (select to act; close with /setup)")))

        # Roles
        for role in ("peerA","peerB","aux"):
            cur = roles.get(role) or ""
            label = f"{role}: {cur or '(unset)'}"
            items.append(ListItem(Label(label)))
            if not cur:
                # Offer choices
                for a in actors:
                    items.append(self._action_item(f"  set {role}→{a}", {"type":"roles-set-actor","args":{"role":role,"actor":a}}))

        # CLI availability
        for role_key, entry in cli.items():
            ok = bool(entry.get("available"))
            cmd = entry.get("command") or ""
            items.append(ListItem(Label(f"CLI {role_key}: {'OK' if ok else 'MISSING'} {cmd}")))

        # Telegram
        configured = bool(tg.get("configured"))
        running = bool(tg.get("running"))
        autostart = bool(tg.get("autostart", True))
        items.append(ListItem(Label(f"Telegram: configured={'YES' if configured else 'NO'} running={'YES' if running else 'NO'} autostart={'ON' if autostart else 'OFF'}")))
        if not configured:
            items.append(self._action_item("  token unset", {"type":"token","args":{"action":"unset"}}))
            items.append(self._hint_item("  To set token: type /token set <value> in the composer"))
        else:
            items.append(self._action_item("  token unset", {"type":"token","args":{"action":"unset"}}))

        self.list.clear()
        for it in items:
            self.list.append(it)

    def _hint_item(self, text: str) -> ListItem:
        return ListItem(Label(text))

    def _action_item(self, text: str, command: Dict[str, Any]) -> ListItem:
        item = ListItem(Label(text))
        item.data = command  # type: ignore[attr-defined]
        return item

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        cmd = getattr(item, "data", None)
        if isinstance(cmd, dict):
            self._append_command(cmd)

    def _append_command(self, obj: Dict[str, Any]) -> None:
        path = self.home / "state" / "commands.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n"); f.flush()

    def _read_status(self) -> Dict[str, Any]:
        p = self.home / "state" / "status.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

