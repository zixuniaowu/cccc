#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from textual.app import App, ComposeResult

from .widgets.timeline import Timeline
from .widgets.status import StatusCards
from .widgets.composer import Composer


class CCCCApp(App):
    CSS_PATH = None

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

    def on_mount(self) -> None:  # load CSS from package directory and signal ready
        css = Path(__file__).with_name("styles.tcss")
        try:
            if css.exists():
                self.load_css(css)
        except Exception:
            pass
        # Signal to orchestrator that TUI is actually mounted
        try:
            ready = self.home/"state"/"tui.ready"
            ready.parent.mkdir(parents=True, exist_ok=True)
            ready.write_text(str(int(time.time())), encoding='utf-8')
        except Exception:
            pass
