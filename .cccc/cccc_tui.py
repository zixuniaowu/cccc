#!/usr/bin/env python3
import argparse
import sys
import time
from pathlib import Path

try:
    from importlib.machinery import SourceFileLoader
    APP_PATH = Path(__file__).with_name("tui").joinpath("app.py")
    _mod = SourceFileLoader("cccc_tui_app", str(APP_PATH)).load_module()  # type: ignore
    _AppClass = getattr(_mod, "CCCCApp")
except Exception:
    sys.stderr.write("\n[TUI] Textual is required for the CCCC TUI.\n")
    sys.stderr.write("[TUI] Install:  pip install textual  (inside the same venv running cccc)\n\n")
    sys.stderr.flush()
    time.sleep(6)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    args = ap.parse_args()
    home = Path(args.home)
    # Ready marker (orchestrator waits for this)
    ready = home / "state" / "tui.ready"
    try:
        ready.parent.mkdir(parents=True, exist_ok=True)
        ready.write_text(str(int(time.time())), encoding="utf-8")
    except Exception:
        pass
    app = _AppClass(home)
    app.run()


if __name__ == "__main__":
    main()
