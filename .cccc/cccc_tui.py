#!/usr/bin/env python3
import argparse
import sys
import time
from pathlib import Path

try:
    import importlib.util
    pkg_dir = Path(__file__).with_name("tui_ptk")
    pkg_init = pkg_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "cccc_tui_ptk_pkg", str(pkg_init), submodule_search_locations=[str(pkg_dir)]
    )
    if not spec or not spec.loader:
        raise ImportError("cannot build package spec for PTK TUI")
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["cccc_tui_ptk_pkg"] = pkg
    spec.loader.exec_module(pkg)  # loads package and its relative imports can resolve
    run_app = getattr(pkg, "run_app")
except Exception as e:
    # Fallback to status panel so the left pane remains usable even without PTK
    sys.stderr.write("\n[TUI] PTK TUI unavailable. Falling back to status panel.\n")
    sys.stderr.write("[TUI] To enable full TUI: pip install prompt_toolkit>=3.0.52\n")
    sys.stderr.write(f"[TUI] Reason: {e}\n\n")
    def run_app(home: Path):  # type: ignore
        # Minimal fallback: run the panel_status loop in this process
        import subprocess
        panel = Path(__file__).with_name("panel_status.py")
        cmd = [sys.executable or "python3", str(panel), "--home", str(home), "--interval", "1.0"]
        subprocess.run(cmd)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    args = ap.parse_args()
    home = Path(args.home)
    try:
        run_app(home)
    except Exception as e:
        sys.stderr.write("\n[TUI] PTK TUI failed to start. Falling back to status panel.\n")
        sys.stderr.write(f"[TUI] Reason: {e}\n\n")
        import subprocess
        panel = Path(__file__).with_name("panel_status.py")
        cmd = [sys.executable or "python3", str(panel), "--home", str(home), "--interval", "1.0"]
        subprocess.run(cmd)


if __name__ == "__main__":
    main()
