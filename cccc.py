#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import os, sys, shutil, argparse, subprocess, json, atexit, signal, time

def _bootstrap(src_root: Path, target: Path, *, force: bool = False, include_guides: bool = False):
    # Preferred: packaged resources (importlib.resources)
    src_cccc = None
    try:
        from importlib import resources as _res
        base = _res.files("cccc_scaffold").joinpath("scaffold")  # type: ignore
        if base.is_dir():
            src_cccc = Path(str(base))
    except Exception:
        src_cccc = None
    # Fallback: repo/source layout during development
    if src_cccc is None:
        candidates = [src_root/".cccc", Path(sys.prefix)/".cccc", Path(sys.exec_prefix)/".cccc"]
        src_cccc = next((p for p in candidates if p.exists()), None)
    if src_cccc is None:
        print("[FATAL] Missing scaffold resources; reinstall the package or run from source repo with .cccc/")
        raise SystemExit(1)
    target_cccc = target/".cccc"
    target.mkdir(parents=True, exist_ok=True)

    def copy_one(src: Path, dst: Path):
        if dst.exists() and not force:
            print(f"[SKIP] Exists: {dst}")
            return
        if src.is_dir():
            if dst.exists() and force:
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        print(f"[OK] Wrote: {dst}")

    # Copy .cccc scaffold (exclude state and dynamic files)
    for root, dirs, files in os.walk(src_cccc):
        rel = Path(root).relative_to(src_cccc)
        # Skip dynamic/state directories
        if any(str(rel).startswith(p) for p in ("state",)):
            continue
        for fn in files:
            # Skip caches and local state
            if fn.endswith(".pyc"):
                continue
            src = Path(root)/fn
            dst = target_cccc/rel/fn
            copy_one(src, dst)

    # Do not copy entry script; users run the installed `cccc` command from the package
    # Optional: copy reference guides into .cccc/guides/ (keep repo root clean)
    if include_guides:
        guides_dir = target_cccc/"guides"
        for top in ("CLAUDE.md", "AGENTS.md"):
            src = src_root/top
            if src.exists():
                copy_one(src, guides_dir/top)

    # Default Ephemeral: if target is not this product repo, append /.cccc/** to target .gitignore
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
                print(f"[OK] Appended to .gitignore: {line}")
    except Exception:
        pass

    print(f"\n[BOOTSTRAP] CCCC scaffold written to: {target}")


def main():
    parser = argparse.ArgumentParser(description="CCCC Orchestrator & Bootstrap",
                                     epilog=(
        "Examples:\n"
        "  cccc init              # Create .cccc scaffold in current repo and append /.cccc/** to .gitignore\n"
        "  cccc doctor            # Check git/tmux/python and Telegram token presence\n"
        "  cccc token set         # Save Telegram token to .cccc/settings/telegram.yaml (gitignored)\n"
        "  cccc bridge start      # Start Telegram bridge (requires token)\n"
        "  cccc clean             # Purge .cccc/{mailbox,work,logs,state}/\n"
        "  cccc run               # Run orchestrator\n"
    ))
    sub = parser.add_subparsers(dest="cmd")
    p_init = sub.add_parser("init", help="Copy .cccc scaffold into target repo")
    p_init.add_argument("--to", default=".", help="Target repository path (default: current dir)")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing files/directories")
    p_init.add_argument("--include-guides", action="store_true", help="Also copy CLAUDE.md/AGENTS.md into .cccc/guides/")

    p_up = sub.add_parser("upgrade", help="Upgrade existing .cccc (like init; by default non-overwriting)")
    p_up.add_argument("--to", default=".", help="Target repository path (default: current dir)")
    p_up.add_argument("--force", action="store_true", help="Overwrite existing files/directories")
    p_up.add_argument("--include-guides", action="store_true", help="Also copy CLAUDE.md/AGENTS.md into .cccc/guides/")

    # Utility subcommands (M2.1/M2.2)
    p_clean = sub.add_parser("clean", help="Purge .cccc/{mailbox,work,logs,state}/ runtime artifacts")

    p_doctor = sub.add_parser("doctor", help="Environment check (git/tmux/python/telegram)")

    p_token = sub.add_parser("token", help="Manage Telegram token (stored in .cccc/settings/telegram.yaml; gitignored)")
    p_token.add_argument("action", choices=["set","unset","show"], help="Action: set/unset/show")
    p_token.add_argument("value", nargs="?", help="When action=set, token value (empty = prompt)")

    p_bridge = sub.add_parser("bridge", help="Control Telegram bridge")
    p_bridge.add_argument("action", choices=["start","stop","status","restart","logs"], help="Start/stop/show status/restart/show logs")
    p_bridge.add_argument("-n","--lines", type=int, default=120, help="Tail this many lines for logs action")
    p_bridge.add_argument("-f","--follow", action="store_true", help="Follow logs (stream)")

    # Utility: show versions
    sub.add_parser("version", help="Show package and scaffold versions")
    # Alias run
    sub.add_parser("run", help="Run orchestrator")

    args, rest = parser.parse_known_args()
    repo_root = Path(__file__).resolve().parent
    if args.cmd in {"init", "upgrade"}:
        _bootstrap(repo_root, Path(args.to).resolve(), force=bool(args.force), include_guides=bool(getattr(args, 'include_guides', False)))
        return

    # Resolve runtime home (allow utility subcommands to run even if missing)
    home = Path(os.environ.get("CCCC_HOME", ".cccc")).resolve()

    # Lightweight YAML read/write helpers (for subcommands)
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

    # ---- Early dispatch: utility subcommands (must not trigger orchestrator/wizard) ----
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
                    print(f"[CLEAN] Purged {d}")
            except Exception as e:
                print(f"[CLEAN] Failed to purge {d}: {e}")

    def _cmd_doctor():
        print("[DOCTOR] Starting checks…")
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
        def _resolve_token(c):
            src = None
            val = None
            if c and c.get('token'):
                val = str(c.get('token'))
                src = 'config'
            else:
                tenv = (c or {}).get('token_env')
                if tenv:
                    v = os.environ.get(str(tenv), '')
                    if v:
                        val = v
                        src = f"env:{tenv}"
            return val, src
        tok, src = _resolve_token(cfg)
        print(f"- telegram config: {'FOUND' if cfg else 'NONE'}; token: {'SET' if tok else 'NOT SET'}" + (f" (source={src})" if src else ""))
        # Optional hint when env is present but config also has token (config wins by design)
        try:
            tenv = (cfg or {}).get('token_env')
            if tenv and os.environ.get(str(tenv)) and (cfg or {}).get('token'):
                print(f"  hint: env {tenv} is set but ignored (config token takes precedence)")
        except Exception:
            pass
        if not ok_tmux:
            print("Hint: install tmux (e.g., apt install tmux / brew install tmux).")

    def _cmd_token(action: str, value: str|None):
        cfg_path = home/"settings"/"telegram.yaml"
        cfg = _read_yaml(cfg_path)
        if action == 'set':
            tok = value
            if not tok:
                try:
                    tok = input("Enter Telegram Bot Token: ").strip()
                except Exception:
                    tok = None
            if not tok:
                print("[TOKEN] No token set."); return
            cfg['token'] = tok
            _write_yaml(cfg_path, cfg)
            print(f"[TOKEN] Saved to {cfg_path} (file is gitignored)")
        elif action == 'unset':
            if 'token' in cfg:
                cfg.pop('token', None)
                _write_yaml(cfg_path, cfg)
                print("[TOKEN] Token removed.")
            else:
                print("[TOKEN] No saved token.")
        else:  # show
            tok = cfg.get('token') if cfg else None
            if tok:
                print("[TOKEN] Saved: " + (tok[:4] + "…" + tok[-4:]) )
            else:
                print("[TOKEN] Not saved. Use `cccc token set`.")

    def _cmd_bridge(action: str):
        state = home/"state"; state.mkdir(parents=True, exist_ok=True)
        pid_path = state/"telegram-bridge.pid"
        def _alive(pid: int) -> bool:
            try:
                os.kill(pid, 0)
                return True
            except Exception:
                return False
        def _start():
            bridge = home/"adapters"/"telegram_bridge.py"
            if not bridge.exists():
                print("[BRIDGE] Script not found:", bridge); return
            cfg = _read_yaml(home/"settings"/"telegram.yaml")
            token_env = str((cfg or {}).get('token_env') or 'TELEGRAM_BOT_TOKEN')
            # Resolve token: prefer config token; fall back to env only if token_env explicitly configured
            tok = None; src = None
            if (cfg or {}).get('token'):
                tok = str(cfg.get('token')); src = 'config'
            else:
                tenv = (cfg or {}).get('token_env')
                if tenv:
                    v = os.environ.get(str(tenv), '')
                    if v:
                        tok = v; src = f"env:{tenv}"
            if not tok:
                print("[BRIDGE] Token not found. Run `cccc token set` to save it locally, or set `token_env` in telegram.yaml and provide that env var."); return
            env = os.environ.copy(); env[token_env] = tok
            # Run from project root so .cccc resolves correctly inside bridge
            p = subprocess.Popen([sys.executable, str(bridge)], env=env, cwd=str(Path.cwd()), start_new_session=True)
            pid_path.write_text(str(p.pid), encoding='utf-8')
            print(f"[BRIDGE] Started, pid={p.pid} (token_source={src})")
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
                    print("[BRIDGE] Stop signal sent.")
            except Exception as e:
                print("[BRIDGE] Stop failed:", e)
        def _status():
            if pid_path.exists():
                try:
                    pid = int(pid_path.read_text(encoding='utf-8').strip())
                except Exception:
                    pid = 0
                if pid and _alive(pid):
                    print(f"[BRIDGE] Running, pid={pid}")
                else:
                    print("[BRIDGE] Not running (stale pid file).")
            else:
                print("[BRIDGE] Not running.")
        def _logs(lines: int = 120, follow: bool = False):
            lg = home/"state"/"bridge-telegram.log"
            if not lg.exists():
                print("[BRIDGE] Log file not found:", lg)
                return
            try:
                if follow:
                    # Simple follow implementation
                    print(f"[BRIDGE] Tailing {lg} (Ctrl-C to stop)…")
                    with open(lg, 'r', encoding='utf-8') as f:
                        # jump to last lines
                        try:
                            from collections import deque
                            dq = deque(f, maxlen=lines)
                            for ln in dq:
                                print(ln.rstrip())
                        except Exception:
                            pass
                        while True:
                            ln = f.readline()
                            if not ln:
                                time.sleep(0.5); continue
                            print(ln.rstrip())
                else:
                    # print last N lines
                    try:
                        from collections import deque
                        with open(lg, 'r', encoding='utf-8') as f:
                            dq = deque(f, maxlen=lines)
                            for ln in dq:
                                print(ln.rstrip())
                    except Exception as e:
                        print(f"[BRIDGE] Failed to read logs: {e}")
            except KeyboardInterrupt:
                pass
        if action == 'start': _start(); return
        if action == 'stop': _stop(); return
        if action == 'status': _status(); return
        if action == 'restart': _stop(); time.sleep(0.5); _start(); return
        if action == 'logs': _logs(lines=int(getattr(args,'lines',120) or 120), follow=bool(getattr(args,'follow', False))); return

    if args.cmd == 'clean':
        _cmd_clean(); return
    if args.cmd == 'doctor':
        _cmd_doctor(); return
    if args.cmd == 'token':
        _cmd_token(args.action, getattr(args, 'value', None)); return
    if args.cmd == 'bridge':
        _cmd_bridge(args.action); return
    if args.cmd == 'version':
        # Package version
        pkg_ver = "unknown"
        try:
            try:
                import importlib.metadata as md  # py3.8+
            except Exception:
                import importlib_metadata as md  # type: ignore
            for name in ("cccc-pair","cccc_pair","cccc"):
                try:
                    pkg_ver = md.version(name); break
                except Exception:
                    continue
        except Exception:
            pass
        # Scaffold info
        scaffold_path = home
        exists = scaffold_path.exists()
        file_count = 0
        if exists:
            try:
                for root, dirs, files in os.walk(scaffold_path):
                    if any(x in root for x in ("state","logs","work","mailbox","__pycache__")):
                        continue
                    file_count += len(files)
            except Exception:
                pass
        print(f"cccc package: {pkg_ver}")
        print(f"scaffold path: {scaffold_path} (exists={exists}, files~{file_count})")
        return

    # Run orchestrator (original behavior)
    if not home.exists():
        print(f"[FATAL] CCCC directory not found: {home}\nRun `cccc init` in your target repository to copy the scaffold, or set CCCC_HOME.")
        raise SystemExit(1)
    sys.path.insert(0, str(home))

    # Lightweight wizard: ask once about Telegram connection (optional; local by default).
    # - Token can come from env or a one-time prompt (we save to YAML, gitignored)
    # - Config writes only non-sensitive fields
    def _isatty() -> bool:
        try:
            return sys.stdin.isatty()
        except Exception:
            return False

    # _read_yaml/_write_yaml defined above; reuse here

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
                        if _P(cmdline_path).exists():
                            cmd=_P(cmdline_path).read_bytes().decode('utf-8','ignore')
                            # Must contain this repo's telegram_bridge.py path (under current project's .cccc)
                            if str((home/'adapters'/'telegram_bridge.py').resolve()) in cmd:
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
            print("[WARN] Telegram bridge script not found; skipping.")
            return None
        # Read token from YAML config (if not provided in env_extra)
        cfg = _read_yaml(home/"settings"/"telegram.yaml")
        token_env = str((cfg or {}).get('token_env') or 'TELEGRAM_BOT_TOKEN')
        env = os.environ.copy(); env.update(env_extra or {})
        src = None
        if env.get(token_env):
            src = f"env:{token_env}"
        else:
            if (cfg or {}).get('token'):
                env[token_env] = str(cfg.get('token'))
                src = 'config'
            else:
                tenv = (cfg or {}).get('token_env')
                if tenv:
                    v = os.environ.get(str(tenv), '')
                    if v:
                        env[token_env] = v
                        src = f"env:{tenv}"
        print(f"[TELEGRAM] Starting bridge (long-polling)… (token_source={src or 'none'})")
        # Kill stale instance before spawning
        _kill_stale_bridge()
        # Start new session so we can kill the whole process group on exit; set cwd to project root
        project_root = home.parent
        p = subprocess.Popen([sys.executable, str(bridge)], env=env, cwd=str(project_root), start_new_session=True)
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

    # Wizard logic: only in interactive TTY and when CCCC_NO_WIZARD is not set
    if _isatty() and not os.environ.get('CCCC_NO_WIZARD'):
        try:
            print("\n[SETUP] Choose run mode:\n  1) Local CLI only (default)\n  2) Local + connect Telegram (requires Bot token)")
            choice = input("> Enter 1 or 2 (Enter=1): ").strip() or "1"
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
            # Prompt for token (saved to .cccc/settings/telegram.yaml; gitignored)
            token_env = str(cfg.get("token_env") or "TELEGRAM_BOT_TOKEN")
            token_val = os.environ.get(token_env, "")
            if not token_val:
                saved = str(cfg.get('token') or '')
                if saved:
                    # Offer a minimal choice: use saved or paste new (no extra skip here to avoid duplicate mode selection)
                    masked = (saved[:4] + "…" + saved[-4:]) if len(saved) >= 8 else "(saved)"
                    print(f"[SETUP] Saved Bot Token detected (masked: {masked}).")
                    print("  1) Use saved token (default)\n  2) Paste a new token and save")
                    try:
                        ch = input("> Choose 1/2 (Enter=1): ").strip() or "1"
                    except Exception:
                        ch = "1"
                    if ch == "2":
                        try:
                            token_val = input("Paste new Telegram Bot Token: ").strip()
                        except Exception:
                            token_val = ""
                        if token_val:
                            cfg['token'] = token_val
                            _write_yaml(cfg_path, cfg)
                            print("[SETUP] New token saved.")
                        else:
                            token_val = saved
                            print("[SETUP] Empty input; continue with saved token.")
                    else:
                        token_val = saved
                        print("[SETUP] Using saved token.")
                else:
                    print(f"[SETUP] Env var {token_env} not found. Paste your Bot Token to save it to .cccc/settings/telegram.yaml (local).")
                    try:
                        token_val = input("Paste Telegram Bot Token: ").strip()
                    except Exception:
                        token_val = ""
                    if token_val:
                        cfg['token'] = token_val
                        _write_yaml(cfg_path, cfg)
                        print("[SETUP] Token saved.")
            # Persist early if env provided a token but config lacks one (optional convenience)
            if token_val and not cfg.get('token'):
                cfg['token'] = token_val
                _write_yaml(cfg_path, cfg)
            # Allow user to input chat_id(s) (optional; Enter to skip)
            if not cfg.get("allow_chats"):
                try:
                    raw = input("Optional: enter chat_id (comma/space separated; groups are negative -100...; Enter to skip): ").strip()
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
                        print(f"[SETUP] allow_chats set to {ids}")
            # Write config (including any changes) to .cccc/settings/telegram.yaml (should not be committed)
            _write_yaml(cfg_path, cfg)
            # Optional: onboarding for chat_id
            if not cfg.get("allow_chats"):
                print("[SETUP] Discovery mode enabled (discover_allowlist=true). You can either:\n  • Send /subscribe in chat (if autoregister=open), or\n  • Send /whoami and find chat_id in .cccc/state/bridge-telegram.log, then add it to allow_chats and restart.")
            # Start bridge (prefer env token; else read token from YAML)
            if token_val:
                _spawn_telegram_bridge({token_env: token_val})
            else:
                # If YAML has a token, _spawn_telegram_bridge will inject it automatically
                if cfg.get('token'):
                    _spawn_telegram_bridge({})
                else:
                    print("[WARN] No token provided; continue in local mode without Telegram.")
    # Install shutdown hooks after potential spawn
    _install_shutdown_hooks()

    # Autostart Telegram bridge in non-interactive environments when configured
    try:
        cfg_path = home/"settings"/"telegram.yaml"
        cfg = _read_yaml(cfg_path)
        auto = True if not cfg else bool((cfg or {}).get('autostart', True))
        if auto and (BRIDGE_PROC.get('p') is None):
            token_env = str((cfg or {}).get('token_env') or 'TELEGRAM_BOT_TOKEN')
            # Prefer config token; only fall back to env when token_env explicitly configured
            token_val = None
            if (cfg or {}).get('token'):
                token_val = str(cfg.get('token'))
            else:
                tenv = (cfg or {}).get('token_env')
                if tenv:
                    v = os.environ.get(str(tenv), '')
                    if v:
                        token_val = v
            dry = bool((cfg or {}).get('dry_run', True))
            if token_val:
                _spawn_telegram_bridge({token_env: token_val})
    except Exception:
        pass

    try:
        from orchestrator_tmux import main as run
    except Exception as e:
        print(f"[FATAL] Failed to import orchestrator: {e}")
        raise
    run(home)

if __name__ == "__main__":
    main()
