#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import os, sys, shutil, argparse

def _bootstrap(src_root: Path, target: Path, *, force: bool = False, include_guides: bool = False):
    src_cccc = src_root/".cccc"
    if not src_cccc.exists():
        print(f"[FATAL] 源目录缺少 .cccc：{src_cccc}")
        raise SystemExit(1)
    target_cccc = target/".cccc"
    target.mkdir(parents=True, exist_ok=True)

    def copy_one(src: Path, dst: Path):
        if dst.exists() and not force:
            print(f"[SKIP] 已存在：{dst}")
            return
        if src.is_dir():
            if dst.exists() and force:
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        print(f"[OK] 写入：{dst}")

    # 复制 .cccc 整体（排除 state 与动态文件）
    for root, dirs, files in os.walk(src_cccc):
        rel = Path(root).relative_to(src_cccc)
        # 跳过动态/状态目录
        if any(str(rel).startswith(p) for p in ("state",)):
            continue
        for fn in files:
            # 跳过缓存与本地状态
            if fn.endswith(".pyc"):
                continue
            src = Path(root)/fn
            dst = target_cccc/rel/fn
            copy_one(src, dst)

    # 复制入口脚本
    copy_one(src_root/"cccc.py", target/"cccc.py")
    # 可选：复制参考系统提示词到 .cccc/guides/（避免污染仓库根目录）
    if include_guides:
        guides_dir = target_cccc/"guides"
        for top in ("CLAUDE.md", "AGENTS.md"):
            src = src_root/top
            if src.exists():
                copy_one(src, guides_dir/top)

    print(f"\n[BOOTSTRAP] 已将 CCCC 脚手架写入：{target}")


def main():
    parser = argparse.ArgumentParser(description="CCCC Orchestrator & Bootstrap")
    sub = parser.add_subparsers(dest="cmd")
    p_init = sub.add_parser("init", help="将 .cccc 脚手架拷贝到目标仓库")
    p_init.add_argument("--to", default=".", help="目标仓库路径（默认当前目录）")
    p_init.add_argument("--force", action="store_true", help="覆盖已存在文件/目录")
    p_init.add_argument("--include-guides", action="store_true", help="将 CLAUDE.md/AGENTS.md 复制到 .cccc/guides/")

    p_up = sub.add_parser("upgrade", help="升级现有 .cccc（与 init 类似，默认不覆盖已存在文件）")
    p_up.add_argument("--to", default=".", help="目标仓库路径（默认当前目录）")
    p_up.add_argument("--force", action="store_true", help="覆盖已存在文件/目录")
    p_up.add_argument("--include-guides", action="store_true", help="将 CLAUDE.md/AGENTS.md 复制到 .cccc/guides/")

    args, rest = parser.parse_known_args()
    repo_root = Path(__file__).resolve().parent
    if args.cmd in {"init", "upgrade"}:
        _bootstrap(repo_root, Path(args.to).resolve(), force=bool(args.force), include_guides=bool(getattr(args, 'include_guides', False)))
        return

    # 运行 orchestrator（保持原有行为）
    home = Path(os.environ.get("CCCC_HOME", ".cccc")).resolve()
    if not home.exists():
        print(f"[FATAL] 未找到 CCCC 目录：{home}\n你可以先运行 `python cccc.py init --to .` 拷贝脚手架。也可设置环境变量 CCCC_HOME。")
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
