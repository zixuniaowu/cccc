#!/usr/bin/env python3
import argparse
import sys
import time
from pathlib import Path

try:
    import importlib.util
    pkg_dir = Path(__file__).with_name("tui")
    pkg_init = pkg_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "cccc_tui_pkg", str(pkg_init), submodule_search_locations=[str(pkg_dir)]
    )
    if not spec or not spec.loader:
        raise ImportError("cannot build package spec for TUI")
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["cccc_tui_pkg"] = pkg
    spec.loader.exec_module(pkg)  # loads package and its relative imports can resolve
    run_app = getattr(pkg, "run_app")
except Exception as e:
    sys.stderr.write("\n[TUI] Failed to load the CCCC TUI package. Textual must be installed.\n")
    sys.stderr.write("[TUI] Install:  pip install textual  (inside the same venv running cccc)\n")
    sys.stderr.write(f"[TUI] Reason: {e}\n\n")
    sys.stderr.flush()
    time.sleep(6)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    args = ap.parse_args()
    home = Path(args.home)
    run_app(home)


if __name__ == "__main__":
    main()
