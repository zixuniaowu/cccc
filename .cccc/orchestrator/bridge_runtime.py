# -*- coding: utf-8 -*-
from __future__ import annotations
import os, sys, subprocess, time, json
from pathlib import Path
from typing import Any, Dict, Optional


def make(ctx: Dict[str, Any]):
    home: Path = ctx['home']
    log_ledger = ctx.get('log_ledger', lambda *_: None)
    read_yaml = ctx.get('read_yaml', lambda p: {})

    def _pid_alive(pid: int) -> bool:
        try:
            if pid <= 0:
                return False
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def _ensure_dir(p: Path):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # Persisted warnings for TUI/logs to consume without spamming every tick
    WARNINGS_PATH = None  # set in make() body once 'home' is known
    def _load_warnings() -> Dict[str, Any]:
        try:
            if WARNINGS_PATH and WARNINGS_PATH.exists():
                return json.loads(WARNINGS_PATH.read_text(encoding='utf-8')) or {}
        except Exception:
            pass
        return {}
    def _save_warnings(data: Dict[str, Any]):
        try:
            if WARNINGS_PATH:
                WARNINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
                tmp = WARNINGS_PATH.with_suffix('.tmp')
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                tmp.replace(WARNINGS_PATH)
        except Exception:
            pass
    def _warn_once(adapter: str, code: str, message: str, cooldown_s: float = 120.0):
        """Record a warning and print/log it with basic cooldown to avoid spam."""
        now = time.time()
        data = _load_warnings()
        ent = data.get(adapter) or {}
        next_at = float(ent.get('next_at') or 0)
        if now >= next_at or ent.get('code') != code or ent.get('message') != message:
            # print to orchestrator stdout (tailed by log pane)
            print(f"[BRIDGE] {adapter}: {message}")
            try:
                log_ledger(home, {"from": "system", "kind": "bridge-warning", "adapter": adapter, "code": code, "message": message[:300]})
            except Exception:
                pass
            ent = {
                'code': code,
                'message': message,
                'last_ts': now,
                'next_at': now + float(max(30.0, cooldown_s)),
            }
            data[adapter] = ent
            _save_warnings(data)
    def _clear_warning(adapter: str, code_prefix: str = ""):
        data = _load_warnings()
        ent = data.get(adapter)
        if not ent:
            return
        if code_prefix and not str(ent.get('code','')).startswith(code_prefix):
            return
        try:
            del data[adapter]
            _save_warnings(data)
        except Exception:
            pass

    def ensure_telegram_running() -> Optional[int]:
        """Autostart Telegram bridge when configured and not running.
        Reads .cccc/settings/telegram.yaml; respects autostart (default True).
        """
        state = home/"state"
        _ensure_dir(state)
        pidf = state/"telegram-bridge.pid"
        # If pid file exists and process is alive, nothing to do
        try:
            if pidf.exists():
                try:
                    pid = int(pidf.read_text(encoding='utf-8').strip() or '0')
                except Exception:
                    pid = 0
                if _pid_alive(pid):
                    return pid
        except Exception:
            pass

        # Check autostart + token availability
        cfg = read_yaml(home/"settings"/"telegram.yaml") or {}
        autostart = True if not cfg else bool(cfg.get('autostart', True))
        if not autostart:
            return None
        token_env = str(cfg.get('token_env') or 'TELEGRAM_BOT_TOKEN')
        token_val: Optional[str] = None
        if cfg.get('token'):
            token_val = str(cfg.get('token'))
        else:
            tenv = str(cfg.get('token_env') or token_env)
            if os.environ.get(tenv):
                token_val = os.environ.get(tenv)
        if not token_val:
            # Not configured yet
            return None

        # Spawn bridge
        script = home/"adapters"/"bridge_telegram.py"
        if not script.exists():
            try:
                log_ledger(home, {"from":"system","kind":"bridge-start-skip","adapter":"telegram","reason":"script-missing"})
            except Exception:
                pass
            return None
        env = os.environ.copy()
        env[token_env] = token_val
        project_root = home.parent
        try:
            p = subprocess.Popen([sys.executable, str(script)], env=env, cwd=str(project_root), start_new_session=True)
            try:
                pidf.write_text(str(p.pid), encoding='utf-8')
            except Exception:
                pass
            try:
                log_ledger(home, {"from":"system","kind":"bridge-start","adapter":"telegram","pid":p.pid})
            except Exception:
                pass
            return p.pid
        except Exception as e:
            try:
                log_ledger(home, {"from":"system","kind":"bridge-start-error","adapter":"telegram","error":str(e)[:200]})
            except Exception:
                pass
            return None

    def _spawn_generic(adapter: str, env: dict) -> Optional[int]:
        state = home/"state"; _ensure_dir(state)
        script = home/"adapters"/f"bridge_{adapter}.py"
        if not script.exists():
            try:
                log_ledger(home, {"from":"system","kind":"bridge-start-skip","adapter":adapter,"reason":"script-missing"})
            except Exception:
                pass
            return None
        project_root = home.parent
        try:
            p = subprocess.Popen([sys.executable, str(script)], env=env, cwd=str(project_root), start_new_session=True)
            try:
                (state/f"bridge-{adapter}.pid").write_text(str(p.pid), encoding='utf-8')
            except Exception:
                pass
            try:
                log_ledger(home, {"from":"system","kind":"bridge-start","adapter":adapter,"pid":p.pid})
            except Exception:
                pass
            return p.pid
        except Exception as e:
            try:
                log_ledger(home, {"from":"system","kind":"bridge-start-error","adapter":adapter,"error":str(e)[:200]})
            except Exception:
                pass
            return None

    def ensure_slack_running() -> Optional[int]:
        state = home/"state"; _ensure_dir(state)
        # Clear stale missing_dep warning first, regardless of PID state
        try:
            import slack_sdk  # type: ignore
            _clear_warning('slack', 'missing_dep:')
        except Exception:
            pass
        pidf = state/"bridge-slack.pid"
        try:
            if pidf.exists():
                pid = int(pidf.read_text(encoding='utf-8').strip() or '0')
                if _pid_alive(pid):
                    return pid
        except Exception:
            pass
        cfg = read_yaml(home/"settings"/"slack.yaml") or {}
        autostart = bool(cfg.get('autostart', False))
        if not autostart:
            return None
        # Require at least bot token (outbound-only ok)
        env = os.environ.copy()
        bt_env = str(cfg.get('bot_token_env') or 'SLACK_BOT_TOKEN')
        at_env = str(cfg.get('app_token_env') or 'SLACK_APP_TOKEN')
        if cfg.get('bot_token'):
            env[bt_env] = str(cfg.get('bot_token'))
        elif env.get(bt_env, ''):
            pass
        else:
            return None
        # Dependency check (only when configured to run)
        try:
            import slack_sdk  # type: ignore
            _clear_warning('slack', 'missing_dep:')
        except Exception as e:
            _warn_once('slack', 'missing_dep:slack_sdk',
                       f"slack_sdk import error: {e}. Fix with: {sys.executable} -m pip install -U slack_sdk")
            return None
        if cfg.get('app_token'):
            env[at_env] = str(cfg.get('app_token'))
        # Spawn
        return _spawn_generic('slack', env)

    def ensure_discord_running() -> Optional[int]:
        state = home/"state"; _ensure_dir(state)
        # Clear stale missing_dep warning first
        try:
            import discord  # type: ignore
            _clear_warning('discord', 'missing_dep:')
        except Exception:
            pass
        pidf = state/"bridge-discord.pid"
        try:
            if pidf.exists():
                pid = int(pidf.read_text(encoding='utf-8').strip() or '0')
                if _pid_alive(pid):
                    return pid
        except Exception:
            pass
        cfg = read_yaml(home/"settings"/"discord.yaml") or {}
        autostart = bool(cfg.get('autostart', False))
        if not autostart:
            return None
        env = os.environ.copy()
        be = str(cfg.get('bot_token_env') or 'DISCORD_BOT_TOKEN')
        if cfg.get('bot_token'):
            env[be] = str(cfg.get('bot_token'))
        elif env.get(be, ''):
            pass
        else:
            return None
        # Dependency check (only when configured to run)
        try:
            import discord  # type: ignore
            _clear_warning('discord', 'missing_dep:')
        except Exception as e:
            _warn_once('discord', 'missing_dep:discord.py',
                       f"discord.py import error: {e}. Fix with: {sys.executable} -m pip install -U discord.py (if legacy 'discord' exists: pip uninstall -y discord)")
            return None
        return _spawn_generic('discord', env)

    # set warnings path now that 'home' is known
    WARNINGS_PATH = home/"state"/"bridge-warnings.json"

    return type('BridgeRuntime', (), {
        'ensure_telegram_running': ensure_telegram_running,
        'ensure_slack_running': ensure_slack_running,
        'ensure_discord_running': ensure_discord_running,
    })
