#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sync the development scaffold (.cccc in repo root) into the packaged resources
directory (cccc_scaffold/scaffold) prior to building a wheel.

Usage:
  python scripts/sync_scaffold.py

Notes:
- Single source of truth is the repository root .cccc/ directory.
- This script creates/overwrites cccc_scaffold/scaffold/ with a clean copy,
  excluding runtime/cache directories (state/logs/work/mailbox/__pycache__).
"""
from __future__ import annotations
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT/".cccc"
DST = ROOT/"cccc_scaffold"/"scaffold"

EXCLUDE_DIRS = {"state", "logs", "work", "mailbox", "__pycache__"}

def copy_tree(src: Path, dst: Path):
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    import os
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        # prune excluded directories in-place for os.walk
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        target_dir = dst/rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for fn in files:
            if fn.endswith('.pyc') or fn == '.DS_Store':
                continue
            sp = Path(root)/fn
            dp = target_dir/fn
            shutil.copy2(sp, dp)

if __name__ == "__main__":
    import os, sys
    if not SRC.exists():
        print(f"[FATAL] Source not found: {SRC}")
        sys.exit(2)
    (DST.parent).mkdir(parents=True, exist_ok=True)
    copy_tree(SRC, DST)
    print(f"[OK] Synced {SRC} -> {DST}")
