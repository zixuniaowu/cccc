#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import os, sys

def main():
    home = Path(os.environ.get("CCCC_HOME", ".cccc")).resolve()
    if not home.exists():
        print(f"[FATAL] 未找到 CCCC 目录：{home}\n请将 .cccc/ 放到项目根目录，或设置环境变量 CCCC_HOME。")
        raise SystemExit(1)
    sys.path.insert(0, str(home))
    try:
        from orchestrator_tmux import main as run
    except Exception as e:
        print(f"[FATAL] 导入 orchestrator 失败：{e}")
        raise
    run(home)

if __name__ == "__main__":
    main()
