#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from textual.app import App, ComposeResult

from .widgets.timeline import Timeline
from .widgets.status import StatusCards
from .widgets.composer import Composer
from .widgets.setup import SetupPanel


class CCCCApp(App):
    CSS_PATH = None

    def __init__(self, home: Path):
        super().__init__()
        self.home = home
        self.timeline = Timeline(home)
        self.status = StatusCards(home)
        self.setup = SetupPanel(home)
        self.setup.visible = False
        self.composer = Composer(home, self.timeline, on_toggle_setup=self._toggle_setup)

    def compose(self) -> ComposeResult:
        yield self.timeline
        yield self.setup
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

    def _toggle_setup(self) -> None:
        try:
            self.setup.visible = not self.setup.visible
        except Exception:
            pass
