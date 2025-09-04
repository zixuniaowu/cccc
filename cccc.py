#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import os, sys, shutil, argparse, subprocess, json, atexit, signal, time

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

    # 轻量引导：首次启动时询问是否连接 Telegram（不强制，默认仅本地）。
    # - Token 仅从环境读取或临时输入（不写盘）
    # - 配置文件仅写非敏感项（dry_run/allow_chats/discover_allowlist）
    def _isatty() -> bool:
        try:
            return sys.stdin.isatty()
        except Exception:
            return False

    def _read_yaml(p: Path):
        if not p.exists():
            return {}
        try:
            import yaml  # type: ignore
            return yaml.safe_load(p.read_text(encoding='utf-8')) or {}
        except Exception:
            # naive fallback
            d = {}
            for line in p.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or ':' not in line:
                    continue
                k, v = line.split(':', 1)
                # strip inline comments
                if '#' in v:
                    v = v.split('#', 1)[0]
                d[k.strip()] = v.strip().strip('"\'')
            return d

    def _write_yaml(p: Path, obj):
        try:
            import yaml  # type: ignore
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(yaml.safe_dump(obj, allow_unicode=True, sort_keys=False), encoding='utf-8')
            return
        except Exception:
            p.parent.mkdir(parents=True, exist_ok=True)
            # minimal JSON as fallback
            p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

    BRIDGE_PROC = {"p": None}

    def _kill_stale_bridge():
        """Best-effort: terminate a previously spawned bridge (by pid file), and
        remove stale lock/pid files to avoid duplicate or wedged instances.
        """
        try:
            pid_path = home/"state"/"telegram-bridge.pid"
            if pid_path.exists():
                try:
                    pid = int(pid_path.read_text(encoding='utf-8').strip())
                except Exception:
                    pid = 0
                if pid > 0:
                    # Verify target process belongs to this repo (by cmdline path)
                    belongs=False
                    try:
                        cmdline_path=f'/proc/{pid}/cmdline'
                        from pathlib import Path as _P
                        repo_root=_P(__file__).resolve().parent
                        if _P(cmdline_path).exists():
                            cmd=_P(cmdline_path).read_bytes().decode('utf-8','ignore')
                            # Must contain this repo's telegram_bridge.py path
                            if str(repo_root/'.cccc'/'adapters'/'telegram_bridge.py') in cmd:
                                belongs=True
                    except Exception:
                        pass
                    if not belongs:
                        # Do not kill unknown process; treat as stale pid
                        pid=0
                    if pid>0:
                        try:
                            os.killpg(os.getpgid(pid), signal.SIGTERM)
                        except Exception:
                            try:
                                os.kill(pid, signal.SIGTERM)
                            except Exception:
                                pass
                        for _ in range(10):
                            try:
                                os.kill(pid, 0)
                                time.sleep(0.3)
                            except Exception:
                                break
                        else:
                            try:
                                os.killpg(os.getpgid(pid), signal.SIGKILL)
                            except Exception:
                                try:
                                    os.kill(pid, signal.SIGKILL)
                                except Exception:
                                    pass
            try:
                (home/"state"/"telegram-bridge.lock").unlink(missing_ok=True)
            except Exception:
                pass
            try:
                (home/"state"/"telegram-bridge.pid").unlink(missing_ok=True)
            except Exception:
                pass
        except Exception:
            pass

    def _spawn_telegram_bridge(env_extra: dict):
        bridge = home/"adapters"/"telegram_bridge.py"
        if not bridge.exists():
            print("[WARN] 未找到 Telegram 桥接脚本，跳过连接。")
            return None
        env = os.environ.copy(); env.update(env_extra or {})
        print("[TELEGRAM] 正在启动桥接（长轮询）…")
        # Kill stale instance before spawning
        _kill_stale_bridge()
        # Start new session so we can kill the whole process group on exit; set cwd to repo root
        repo_root = Path(__file__).resolve().parent
        p = subprocess.Popen([sys.executable, str(bridge)], env=env, cwd=str(repo_root), start_new_session=True)
        BRIDGE_PROC["p"] = p
        try:
            # Persist PID for diagnostics
            pid_path = home/"state"/"telegram-bridge.pid"
            pid_path.parent.mkdir(parents=True, exist_ok=True)
            pid_path.write_text(str(p.pid), encoding='utf-8')
        except Exception:
            pass
        return p

    def _cleanup_bridge():
        p = BRIDGE_PROC.get("p")
        if not p:
            return
        try:
            # Send SIGTERM to the process group (Linux/macOS)
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                p.terminate()
            # Wait up to 5s, then SIGKILL the group
            for _ in range(10):
                if p.poll() is not None:
                    break
                time.sleep(0.5)
            if p.poll() is None:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
        finally:
            BRIDGE_PROC["p"] = None
            # best-effort: remove stale pid file
            try:
                (home/"state"/"telegram-bridge.pid").unlink(missing_ok=True)  # type: ignore
            except Exception:
                pass

    # Ensure child cleanup on exit and signals
    def _install_shutdown_hooks():
        atexit.register(_cleanup_bridge)
        def _sig_handler(signum, frame):
            _cleanup_bridge()
            # Re-raise default behavior
            raise SystemExit(0)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sig, _sig_handler)
            except Exception:
                pass

    # 引导逻辑：仅在交互式终端且未设置 CCCC_NO_WIZARD 时触发
    if _isatty() and not os.environ.get('CCCC_NO_WIZARD'):
        try:
            print("\n[SETUP] 请选择运行方式：\n  1) 仅本地命令行（默认）\n  2) 本地 + 连接 Telegram（需要 Bot token）")
            choice = input("> 请输入 1 或 2（回车=1）：").strip() or "1"
        except Exception:
            choice = "1"
        if choice == "2":
            cfg_path = home/"settings"/"telegram.yaml"
            cfg = _read_yaml(cfg_path)
            if not cfg:
                cfg = {
                    "token_env": "TELEGRAM_BOT_TOKEN",
                    "allow_chats": [],
                    "dry_run": False,
                    "discover_allowlist": True,
                    "autoregister": "open",
                    "max_auto_subs": 3,
                }
            else:
                cfg["dry_run"] = False
                if not cfg.get("allow_chats"):
                    cfg["discover_allowlist"] = True
                if not cfg.get("autoregister"):
                    cfg["autoregister"] = "open"
                if not cfg.get("max_auto_subs"):
                    cfg["max_auto_subs"] = 3
            # Normalize allow_chats (handle strings like "[]" or "[123]")
            def _coerce_allowlist(val):
                def to_int(x):
                    try:
                        return int(str(x).strip())
                    except Exception:
                        return None
                if isinstance(val, (list, tuple, set)):
                    out = []
                    for x in val:
                        v = to_int(x)
                        if v is not None:
                            out.append(v)
                    return out
                if isinstance(val, str):
                    s = val.strip().strip('"\'')
                    if not s:
                        return []
                    if s.startswith('[') and s.endswith(']'):
                        try:
                            arr = json.loads(s)
                            return _coerce_allowlist(arr)
                        except Exception:
                            pass
                    s2 = s.strip('[]')
                    parts = [p for p in s2.replace(',', ' ').split() if p]
                    out = []
                    for p in parts:
                        v = to_int(p)
                        if v is not None:
                            out.append(v)
                    return out
                return []
            coerced = _coerce_allowlist(cfg.get("allow_chats"))
            if coerced:
                cfg["allow_chats"] = coerced
            else:
                cfg["allow_chats"] = []
                cfg["discover_allowlist"] = True
            # 提示 token（不落盘）
            token_env = str(cfg.get("token_env") or "TELEGRAM_BOT_TOKEN")
            token_val = os.environ.get(token_env, "")
            if not token_val:
                print(f"[SETUP] 未检测到环境变量 {token_env}。建议在终端中 export，而不是写入文件。")
                try:
                    token_val = input("请输入 Telegram Bot Token（仅用于本次进程，不会写盘）：").strip()
                except Exception:
                    token_val = ""
            # 允许用户直接录入 chat_id（可留空跳过）
            if not cfg.get("allow_chats"):
                try:
                    raw = input("可选：请输入 chat_id（可多个，用逗号/空格分隔；群聊通常为负数 -100...；回车跳过）：").strip()
                except Exception:
                    raw = ""
                ids = []
                if raw:
                    import re as _re
                    parts = [p for p in _re.split(r"[\s,]+", raw) if p]
                    for p in parts:
                        try:
                            ids.append(int(p))
                        except Exception:
                            pass
                    if ids:
                        cfg["allow_chats"] = ids
                        cfg["discover_allowlist"] = False
                        print(f"[SETUP] 已设置 allow_chats={ids}")
            # 写入非敏感配置（YAML 不可用则写 JSON）
            _write_yaml(cfg_path, cfg)
            # 可选：获取 chat_id
            if not cfg.get("allow_chats"):
                print("[SETUP] 已启用发现模式（discover_allowlist=true）。你可以：\n  • 直接在聊天里发送 /subscribe 自助订阅（若配置为 autoregister=open）；或\n  • 发送 /whoami 然后在 .cccc/state/bridge-telegram.log 查到 chat_id，填入 allow_chats 后重启桥接。")
            # 启动桥接（只在当前进程环境注入 token）
            if token_val:
                _spawn_telegram_bridge({token_env: token_val})
            else:
                print("[WARN] 未提供 Token，无法连接 Telegram；将以本地模式继续。")
    # Install shutdown hooks after potential spawn
    _install_shutdown_hooks()

    # Autostart Telegram bridge in non-interactive environments when configured
    try:
        cfg_path = home/"settings"/"telegram.yaml"
        cfg = _read_yaml(cfg_path)
        auto = True if not cfg else bool((cfg or {}).get('autostart', True))
        if auto and (BRIDGE_PROC.get('p') is None):
            token_env = str((cfg or {}).get('token_env') or 'TELEGRAM_BOT_TOKEN')
            token_val = os.environ.get(token_env, '')
            dry = bool((cfg or {}).get('dry_run', True))
            if dry or token_val:
                _spawn_telegram_bridge({token_env: token_val} if token_val else {})
    except Exception:
        pass
    try:
        from orchestrator_tmux import main as run
    except Exception as e:
        print(f"[FATAL] 导入 orchestrator 失败：{e}")
        raise
    run(home)

if __name__ == "__main__":
    main()
