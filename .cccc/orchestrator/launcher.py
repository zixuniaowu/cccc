# -*- coding: utf-8 -*-
from __future__ import annotations
import os, sys, shlex, shutil, time, json
from pathlib import Path
from typing import Any, Dict, Tuple


def make(ctx: Dict[str, Any]):
    home: Path = ctx['home']
    state: Path = ctx['state']
    paneA = ctx['paneA']; paneB = ctx['paneB']
    tmux = ctx['tmux']; tmux_start_interactive = ctx['tmux_start_interactive']
    profileA = ctx['profileA']; profileB = ctx['profileB']
    outbox_write = ctx['outbox_write']
    inbox_dir = ctx['inbox_dir']; processed_dir = ctx['processed_dir']
    ensure_mailbox = ctx['ensure_mailbox']; log_ledger = ctx['log_ledger']
    processed_retention = ctx['processed_retention']; inbox_policy_default = ctx['inbox_policy']
    read_console_line_timeout = ctx.get('read_console_line_timeout')
    cli_profiles = ctx['cli_profiles']; mb_pull_enabled = ctx['mb_pull_enabled']
    wait_for_ready = ctx['wait_for_ready']
    commands_path: Path = ctx['commands_path']
    settings_confirmed_ready = ctx['settings_confirmed_ready']
    load_profiles = ctx['load_profiles']

    # Auto-launch removed: orchestrator relies on a single launch command written by TUI

    def _first_bin(cmd: str) -> str:
        try:
            return shlex.split(cmd or '')[0] if cmd else ''
        except Exception:
            return (cmd or '').split(' ')[0]

    def _bin_available(cmd: str) -> bool:
        prog = _first_bin(cmd)
        if not prog:
            return False
        return shutil.which(prog) is not None

    def _normalize_absbin(cmd: str) -> str:
        try:
            prog = _first_bin(cmd)
            if not prog:
                return cmd
            ab = shutil.which(prog)
            if not ab:
                return cmd
            parts = shlex.split(cmd)
            parts[0] = ab
            return " ".join(shlex.quote(x) for x in parts)
        except Exception:
            return cmd

    def _wrap_cwd(cmd: str, cwd: str | None) -> str:
        if cwd and cwd not in (".", ""):
            return f"cd {cwd} && {cmd}"
        return cmd

    def _is_debug() -> bool:
        try:
            import os
            return str(os.environ.get('CCCC_LOG_LEVEL','')).lower() == 'debug'
        except Exception:
            return False

    def _dump_panes():
        try:
            code, out, _err = tmux('list-panes', '-F', '#{pane_id} #{pane_current_command}')
            if code == 0 and out.strip() and _is_debug():
                print('[DEBUG] pane commands:\n' + out.strip())
        except Exception:
            pass

    def _startup_handle_inbox(label: str, policy_override: str | None):
        try:
            ensure_mailbox(home)
        except Exception:
            pass
        inbox = inbox_dir(home, label)
        proc = processed_dir(home, label)
        try:
            files = sorted([f for f in inbox.iterdir() if f.is_file()], key=lambda p: p.name)
        except FileNotFoundError:
            files = []
        if not files:
            return 0
        policy = (policy_override or inbox_policy_default or "resume").strip().lower()
        if policy == "discard":
            moved = 0
            for f in files:
                try:
                    proc.mkdir(parents=True, exist_ok=True)
                    f.rename(proc/f.name); moved += 1
                except Exception:
                    pass
            log_ledger(home, {"from":"system","kind":"startup-inbox-discard","peer":label,"moved":moved})
            return moved
        log_ledger(home, {"from":"system","kind":"startup-inbox-resume","peer":label,"pending":len(files)})
        return len(files)

    def _prompt_inbox_policy():
        try:
            ensure_mailbox(home)
            def _count(label: str) -> int:
                try:
                    ib = inbox_dir(home, label)
                    return len([f for f in ib.iterdir() if f.is_file()])
                except Exception:
                    return 0
            cntA = _count("PeerA"); cntB = _count("PeerB")
            if (cntA > 0 or cntB > 0):
                chosen_policy = (inbox_policy_default or "resume").strip().lower()
                try:
                    is_interactive = sys.stdin.isatty()
                except Exception:
                    is_interactive = False
                t_conf = (cli_profiles.get("delivery", {}) or {})
                timeout_s = float(t_conf.get("inbox_startup_prompt_timeout_seconds", 30))
                timeout_nonint = float(t_conf.get("inbox_startup_prompt_noninteractive_timeout_seconds", 0))
                eff_timeout = timeout_s if is_interactive else timeout_nonint
                print("\n[INBOX] Residual inbox detected:")
                print(f"  - PeerA: {cntA} @ {str(inbox_dir(home,'PeerA'))}")
                print(f"  - PeerB: {cntB} @ {str(inbox_dir(home,'PeerB'))}")
                print(f"  Policy for this session: [r] resume  [d] discard; default: {chosen_policy}")
                if eff_timeout > 0:
                    print(f"  Will apply default policy {chosen_policy} after {int(eff_timeout)}s of inactivity.")
                if read_console_line_timeout and eff_timeout > 0:
                    ans = read_console_line_timeout(
                        "> Choose r/d and Enter (or Enter to use default): ", eff_timeout
                    ).strip().lower()
                else:
                    ans = ""
                if ans in ("r","resume"):
                    chosen_policy = "resume"
                elif ans in ("d","discard"):
                    chosen_policy = "discard"
                print(f"[INBOX] Using policy: {chosen_policy}")
                _startup_handle_inbox("PeerA", chosen_policy)
                _startup_handle_inbox("PeerB", chosen_policy)
        except Exception as e:
            try:
                log_ledger(home, {"from":"system","kind":"startup-inbox-check-error","error":str(e)[:200]})
            except Exception:
                pass

    def initial_setup(resolved: Dict[str, Any], config_deferred: bool, start_mode: str) -> Tuple[bool, Dict[str, Any]]:
        # Orchestrator does not auto-enqueue launch/resume; TUI writes the single launch command.
        if start_mode in ("has_doc", "ai_bootstrap"):
            print("[LAUNCH] Waiting for launch command from TUI (after settings.confirmed).")
        return False, resolved

    def tick(resolved: Dict[str, Any], config_deferred: bool) -> Tuple[bool, Dict[str, Any]]:
        # No-op: orchestrator does not auto-launch; rely on queue commands.
        return False, resolved

    return type('LauncherAPI', (), {
        'initial_setup': initial_setup,
        'tick': tick,
    })
