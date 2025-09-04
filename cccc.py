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

    # 默认 Ephemeral：仅当目标仓库不是本产品仓库时，向目标仓库 .gitignore 追加忽略 /.cccc/**
    try:
        if (target/".git").exists() and target.resolve() != src_root.resolve():
            gi = target/".gitignore"
            line = "/.cccc/**"
            existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
            if line not in existing.splitlines():
                with gi.open("a", encoding="utf-8") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write("# Ignore CCCC runtime domain (Ephemeral mode)\n")
                    f.write(line+"\n")
                print(f"[OK] 追加 .gitignore: {line}")
    except Exception:
        pass

    print(f"\n[BOOTSTRAP] 已将 CCCC 脚手架写入：{target}")


def main():
    parser = argparse.ArgumentParser(description="CCCC Orchestrator & Bootstrap",
                                     epilog=(
        "Examples:\n"
        "  python3 cccc.py init --to .            # 在目标仓生成 .cccc 脚手架并追加 .gitignore\n"
        "  python3 cccc.py doctor                 # 检查 git/tmux/python 与 telegram 配置\n"
        "  python3 cccc.py token set              # 保存 Telegram token 到 .cccc/settings/telegram.yaml\n"
        "  python3 cccc.py bridge start           # 启动 Telegram 桥接（需已设置 token）\n"
        "  python3 cccc.py clean                  # 清理 .cccc/{mailbox,work,logs,state}/\n"
        "  python3 cccc.py run                    # 运行 orchestrator\n"
    ))
    sub = parser.add_subparsers(dest="cmd")
    p_init = sub.add_parser("init", help="将 .cccc 脚手架拷贝到目标仓库")
    p_init.add_argument("--to", default=".", help="目标仓库路径（默认当前目录）")
    p_init.add_argument("--force", action="store_true", help="覆盖已存在文件/目录")
    p_init.add_argument("--include-guides", action="store_true", help="将 CLAUDE.md/AGENTS.md 复制到 .cccc/guides/")

    p_up = sub.add_parser("upgrade", help="升级现有 .cccc（与 init 类似，默认不覆盖已存在文件）")
    p_up.add_argument("--to", default=".", help="目标仓库路径（默认当前目录）")
    p_up.add_argument("--force", action="store_true", help="覆盖已存在文件/目录")
    p_up.add_argument("--include-guides", action="store_true", help="将 CLAUDE.md/AGENTS.md 复制到 .cccc/guides/")

    # Utility subcommands (M2.1/M2.2)
    p_clean = sub.add_parser("clean", help="清理 .cccc/{mailbox,work,logs,state}/ 运行产物")

    p_doctor = sub.add_parser("doctor", help="环境诊断（git/tmux/python/telegram 配置）")

    p_token = sub.add_parser("token", help="管理 Telegram token（保存在 .cccc/settings/telegram.yaml，不进 git）")
    p_token.add_argument("action", choices=["set","unset","show"], help="操作：set/unset/show")
    p_token.add_argument("value", nargs="?", help="当 action=set 时可直接提供 token（为空则交互输入）")

    p_bridge = sub.add_parser("bridge", help="控制 Telegram 桥接")
    p_bridge.add_argument("action", choices=["start","stop","status"], help="启动/停止/查看状态")

    # Alias run
    sub.add_parser("run", help="运行 orchestrator")

    args, rest = parser.parse_known_args()
    repo_root = Path(__file__).resolve().parent
    if args.cmd in {"init", "upgrade"}:
        _bootstrap(repo_root, Path(args.to).resolve(), force=bool(args.force), include_guides=bool(getattr(args, 'include_guides', False)))
        return

    # 计算运行域（即使不存在也允许部分子命令工作）
    home = Path(os.environ.get("CCCC_HOME", ".cccc")).resolve()

    # 轻量 YAML 读写（供子命令使用）
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
            p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

    # ---- 早期分发：工具子命令（不应触发编排器或向导） ----
    def _which(bin_name: str) -> bool:
        try:
            subprocess.run(["bash","-lc",f"command -v {bin_name}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return True
        except Exception:
            return False

    def _cmd_clean():
        for d in (home/"mailbox", home/"work", home/"logs", home/"state"):
            try:
                if d.exists():
                    shutil.rmtree(d)
                    print(f"[CLEAN] 已清理 {d}")
            except Exception as e:
                print(f"[CLEAN] 无法清理 {d}: {e}")

    def _cmd_doctor():
        print("[DOCTOR] 开始诊断…")
        ok_git = _which("git")
        ok_tmux = _which("tmux")
        ok_py = True
        try:
            subprocess.run([sys.executable, "-V"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            ok_py = False
        print(f"- git:  {'OK' if ok_git else 'MISSING'}")
        print(f"- tmux: {'OK' if ok_tmux else 'MISSING'}")
        print(f"- python: {'OK' if ok_py else 'MISSING'} ({sys.executable})")
        print(f"- CCCC_HOME: {home} ({'EXISTS' if home.exists() else 'MISSING'})")
        cfg = _read_yaml(home/"settings"/"telegram.yaml") if home.exists() else {}
        tok = (cfg or {}).get('token') or os.environ.get(str((cfg or {}).get('token_env') or 'TELEGRAM_BOT_TOKEN') )
        print(f"- telegram config: {'FOUND' if cfg else 'NONE'}; token: {'SET' if tok else 'NOT SET'}")
        if not ok_tmux:
            print("建议：安装 tmux（例如 apt install tmux / brew install tmux）。")

    def _cmd_token(action: str, value: str|None):
        cfg_path = home/"settings"/"telegram.yaml"
        cfg = _read_yaml(cfg_path)
        if action == 'set':
            tok = value
            if not tok:
                try:
                    tok = input("请输入 Telegram Bot Token：").strip()
                except Exception:
                    tok = None
            if not tok:
                print("[TOKEN] 未设置 token。"); return
            cfg['token'] = tok
            _write_yaml(cfg_path, cfg)
            print(f"[TOKEN] 已保存到 {cfg_path}（文件默认被忽略，不进 git）")
        elif action == 'unset':
            if 'token' in cfg:
                cfg.pop('token', None)
                _write_yaml(cfg_path, cfg)
                print("[TOKEN] 已移除保存的 token。")
            else:
                print("[TOKEN] 未发现已保存的 token。")
        else:  # show
            tok = cfg.get('token') if cfg else None
            if tok:
                print("[TOKEN] 已保存：" + (tok[:4] + "…" + tok[-4:]) )
            else:
                print("[TOKEN] 未保存。可用 `cccc token set` 设置。")

    def _cmd_bridge(action: str):
        state = home/"state"; state.mkdir(parents=True, exist_ok=True)
        pid_path = state/"telegram-bridge.pid"
        def _start():
            bridge = home/"adapters"/"telegram_bridge.py"
            if not bridge.exists():
                print("[BRIDGE] 未找到脚本：", bridge); return
            cfg = _read_yaml(home/"settings"/"telegram.yaml")
            token_env = str((cfg or {}).get('token_env') or 'TELEGRAM_BOT_TOKEN')
            tok = os.environ.get(token_env, '') or str((cfg or {}).get('token') or '')
            if not tok:
                print("[BRIDGE] 未检测到 token。请先 `cccc token set` 或设置环境变量。"); return
            env = os.environ.copy(); env[token_env] = tok
            p = subprocess.Popen([sys.executable, str(bridge)], env=env, cwd=str(repo_root), start_new_session=True)
            pid_path.write_text(str(p.pid), encoding='utf-8')
            print("[BRIDGE] 已启动，pid=", p.pid)
        def _stop():
            try:
                if pid_path.exists():
                    pid = int(pid_path.read_text(encoding='utf-8').strip())
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGTERM)
                    except Exception:
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except Exception:
                            pass
                    print("[BRIDGE] 已发送停止信号。")
            except Exception as e:
                print("[BRIDGE] 停止失败：", e)
        def _status():
            if pid_path.exists():
                print(f"[BRIDGE] 运行中，pid={pid_path.read_text(encoding='utf-8').strip()}")
            else:
                print("[BRIDGE] 未运行。")
        if action == 'start': _start(); return
        if action == 'stop': _stop(); return
        if action == 'status': _status(); return

    if args.cmd == 'clean':
        _cmd_clean(); return
    if args.cmd == 'doctor':
        _cmd_doctor(); return
    if args.cmd == 'token':
        _cmd_token(args.action, getattr(args, 'value', None)); return
    if args.cmd == 'bridge':
        _cmd_bridge(args.action); return

    # 运行 orchestrator（保持原有行为）
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

    # 上面已定义 _read_yaml/_write_yaml，下面继续使用

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
        # 从配置 YAML 读取 token（若未在 env_extra 指定）
        cfg = _read_yaml(home/"settings"/"telegram.yaml")
        token_env = str((cfg or {}).get('token_env') or 'TELEGRAM_BOT_TOKEN')
        env = os.environ.copy(); env.update(env_extra or {})
        if not env.get(token_env):
            tok = str((cfg or {}).get('token') or '')
            if tok:
                env[token_env] = tok
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
            # 提示 token（默认保存到 .cccc/settings/telegram.yaml；不进 git）
            token_env = str(cfg.get("token_env") or "TELEGRAM_BOT_TOKEN")
            token_val = os.environ.get(token_env, "")
            if not token_val:
                print(f"[SETUP] 未检测到环境变量 {token_env}。你可以输入一次 Token，我们会保存到 .cccc/settings/telegram.yaml（该文件默认被忽略，不进 git）。")
                try:
                    token_val = input("请输入 Telegram Bot Token（仅用于本次进程，不会写盘）：").strip()
                except Exception:
                    token_val = ""
            # 默认保存 token 到配置（Ephemeral 模式下安全）
            if token_val:
                cfg['token'] = token_val
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
            # 写入配置（包含 token；该文件位于 .cccc/settings/telegram.yaml，不应被提交）
            _write_yaml(cfg_path, cfg)
            # 可选：获取 chat_id
            if not cfg.get("allow_chats"):
                print("[SETUP] 已启用发现模式（discover_allowlist=true）。你可以：\n  • 直接在聊天里发送 /subscribe 自助订阅（若配置为 autoregister=open）；或\n  • 发送 /whoami 然后在 .cccc/state/bridge-telegram.log 查到 chat_id，填入 allow_chats 后重启桥接。")
            # 启动桥接（优先使用当前进程环境的 token，否则读 YAML 中的 token）
            if token_val:
                _spawn_telegram_bridge({token_env: token_val})
            else:
                # 若 YAML 已保存 token，_spawn_telegram_bridge 会自动从 YAML 注入
                if cfg.get('token'):
                    _spawn_telegram_bridge({})
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
            token_val = os.environ.get(token_env, '') or str((cfg or {}).get('token') or '')
            dry = bool((cfg or {}).get('dry_run', True))
            if token_val:
                _spawn_telegram_bridge({token_env: token_val})
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
