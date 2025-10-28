# -*- coding: utf-8 -*-
from __future__ import annotations
import os, sys, subprocess, time
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
        if cfg.get('app_token'):
            env[at_env] = str(cfg.get('app_token'))
        # Spawn
        return _spawn_generic('slack', env)

    def ensure_discord_running() -> Optional[int]:
        state = home/"state"; _ensure_dir(state)
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
        return _spawn_generic('discord', env)

    return type('BridgeRuntime', (), {
        'ensure_telegram_running': ensure_telegram_running,
        'ensure_slack_running': ensure_slack_running,
        'ensure_discord_running': ensure_discord_running,
    })
