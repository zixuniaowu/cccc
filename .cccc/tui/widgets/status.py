from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from textual.widgets import Static


class StatusCards(Static):
    def __init__(self, home: Path):
        super().__init__(id="status")
        self.home = home
        self._last_text = ""

    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh_data)
        self._last_text = self._render_text({})
        self.update(self._last_text)

    def refresh_data(self) -> None:
        data: Dict[str, Any] = {}
        try:
            s = (self.home / "state" / "status.json").read_text(encoding="utf-8")
            data = json.loads(s)
        except Exception:
            pass
        self._last_text = self._render_text(data)
        self.update(self._last_text)

    def _render_text(self, data: Dict[str, Any]) -> str:
        p = data.get("handoffs") or {}
        fore = data.get("foreman") or {}
        lines = [
            "— Status —",
            f"PeerA handoffs={p.get('handoffs_peerA','-')} next_self={p.get('next_self_peerA','-')}",
            f"PeerB handoffs={p.get('handoffs_peerB','-')} next_self={p.get('next_self_peerB','-')}",
            f"Foreman enabled={fore.get('enabled',False)} running={fore.get('running',False)} next={fore.get('next_due','-')} last_rc={fore.get('last_rc','-')}",
        ]
        return "\n".join(lines)

    def render(self) -> str:
        return self._last_text

