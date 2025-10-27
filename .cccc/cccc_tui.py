#!/usr/bin/env python3
"""
CCCC TUI Entry Point - Direct launch without fallback

Requires prompt_toolkit to be installed (automatically handled by cccc installation).
"""
import argparse
import sys
from pathlib import Path

# Direct import - let it crash if there are any issues
import importlib.util

pkg_dir = Path(__file__).with_name("tui_ptk")
pkg_init = pkg_dir / "__init__.py"

spec = importlib.util.spec_from_file_location(
    "cccc_tui_ptk_pkg",
    str(pkg_init),
    submodule_search_locations=[str(pkg_dir)]
)

if not spec or not spec.loader:
    raise ImportError("cannot build package spec for PTK TUI")

pkg = importlib.util.module_from_spec(spec)
sys.modules["cccc_tui_ptk_pkg"] = pkg
spec.loader.exec_module(pkg)

run_app = getattr(pkg, "run_app")


def main() -> None:
    """Main entry point - runs TUI directly"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    args = ap.parse_args()
    home = Path(args.home)

    # Run directly - any errors will be displayed to user
    run_app(home)


if __name__ == "__main__":
    main()
