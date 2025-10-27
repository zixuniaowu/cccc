# -*- coding: utf-8 -*-
from __future__ import annotations
import sys, time
from typing import Any, Dict


def make(ctx: Dict[str, Any]):
    home = ctx['home']
    state = ctx['state']
    paneA = ctx['paneA']
    paneB = ctx['paneB']
    profileA = ctx['profileA']
    profileB = ctx['profileB']
    delivery_conf = ctx['delivery_conf']
    send_handoff = ctx['send_handoff']
    maybe_prepend = ctx['maybe_prepend']
    send_raw_to_cli = ctx['send_raw_to_cli']
    run_aux_cli = ctx['run_aux_cli']
    send_aux_reminder = ctx['send_aux_reminder']
    request_por_refresh = ctx['request_por_refresh']
    weave_system = ctx['weave_system']
    foreman_scheduler = ctx['foreman_scheduler']
    write_status = ctx['write_status']
    write_queue_and_locks = ctx['write_queue_and_locks']
    policies = ctx['policies']
    state_box = ctx['state_box']

    def _send_user_to(peer_label: str, message: str):
        payload = f"<FROM_USER>\n{message}\n</FROM_USER>\n"
        send_handoff("User", peer_label, maybe_prepend(peer_label, payload))

    def _handle_foreman(line: str) -> bool:
        parts = line.strip().split()
        action = parts[1].lower() if len(parts) > 1 else 'status'
        try:
            result = foreman_scheduler.command(action, origin="console")
        except Exception as e:
            print(f"Foreman error: {e}")
            return True
        msg = result.get('message')
        if msg:
            print(msg)
        return True

    def handle(line: str) -> str:
        if line is None:
            return "continue"
        low = line.lower()
        if low == "q":
            return "break"
        if low in ("h", "/help"):
            print("[HELP]")
            print("  a: <text>    → PeerA    |  b: <text> → PeerB")
            print("  both:/u: <text>         → send to both A/B")
            print("  a! <cmd> / b! <cmd>     → passthrough to respective CLI (no wrapper)")
            print("  /focus [hint]           → ask PeerB to refresh POR.md (optional hint)")
            print("  /pause | /resume        → pause/resume A↔B handoff")
            print("  /sys-refresh            → re-inject full SYSTEM prompt")
            print("  /clear                  → reserved (no-op)")
            print("  /foreman on|off|status  → enable/disable/check Foreman (User Proxy)")
            print("  /c <prompt> | c: <prompt> → run configured Aux once (one-off helper)")
            print("  /review                 → request Aux review bundle")
            print("  /echo on|off|<empty>    → console echo on/off/show")
            print("  q                       → quit orchestrator")
            try:
                sys.stdout.write("> "); sys.stdout.flush()
            except Exception:
                pass
            return "continue"
        if low.startswith("c:") or low.startswith("/c"):
            if low.startswith("c:"):
                prompt_text = line[2:].strip()
            else:
                prompt_text = line[2:].lstrip(" :").strip()
            if not prompt_text:
                print("[AUX] Usage: c: <prompt>  or  /c <prompt>")
                return "continue"
            rc, out, err, cmd_line = run_aux_cli(prompt_text)
            print(f"[AUX] command: {cmd_line}")
            print(f"[AUX] exit={rc}")
            if out:
                print(out.rstrip())
            if err:
                print("[AUX][stderr]")
                print(err.rstrip())
            return "continue"
        if line == "/sys-refresh" or line == "/refresh":
            sysA = weave_system(home, "peerA"); sysB = weave_system(home, "peerB")
            send_handoff("System", "PeerA", f"<FROM_SYSTEM>\n{sysA}\n</FROM_SYSTEM>\n")
            send_handoff("System", "PeerB", f"<FROM_SYSTEM>\n{sysB}\n</FROM_SYSTEM>\n")
            print("[SYSTEM] Refreshed (mailbox delivery).")
            return "continue"
        if line.startswith("/focus"):
            tokens = line.split(maxsplit=1)
            hint = tokens[1].strip() if len(tokens) > 1 else ""
            request_por_refresh("focus-cli", hint=hint or None, force=True)
            print("[FOCUS] Requested POR refresh from PeerB.")
            return "continue"
        if line.startswith("/clear"):
            print("[CLEAR] acknowledged (noop)")
            return "continue"
        if line == "/review":
            send_aux_reminder("manual-review")
            return "continue"
        if line == "/pause":
            state_box['deliver_paused'] = True
            write_status(True)
            print("[PAUSE] Paused A↔B handoff (still collect <TO_USER>)")
            return "continue"
        if line == "/resume":
            state_box['deliver_paused'] = False
            write_status(False)
            print("[PAUSE] Resumed A↔B handoff")
            return "continue"
        if line == "/echo on":
            state_box['CONSOLE_ECHO'] = True
            print("[ECHO] Console echo ON (may interfere with input)")
            return "continue"
        if line == "/echo off":
            state_box['CONSOLE_ECHO'] = False
            print("[ECHO] Console echo OFF (recommended)")
            return "continue"
        if line == "/echo":
            print(f"[ECHO] Status: {'on' if state_box.get('CONSOLE_ECHO') else 'off'}")
            return "continue"
        if line == "/anti-on":
            state_box['handoff_filter_override'] = True
            write_status(state_box.get('deliver_paused', False))
            write_queue_and_locks()
            print("[ANTI] Low-signal filter override=on")
            return "continue"
        if line == "/anti-off":
            state_box['handoff_filter_override'] = False
            write_status(state_box.get('deliver_paused', False))
            print("[ANTI] Low-signal filter override=off")
            return "continue"
        if line == "/anti-status":
            pol_enabled = bool((policies.get("handoff_filter") or {}).get("enabled", True))
            override = state_box.get('handoff_filter_override')
            eff = override if override is not None else pol_enabled
            src = "override" if override is not None else "policy"
            print(f"[ANTI] Low-signal filter: {eff} (source={src})")
            return "continue"
        if line.startswith("u:") or line.startswith("both:"):
            msg = line.split(":",1)[1].strip()
            _send_user_to("PeerA", msg)
            _send_user_to("PeerB", msg)
            return "continue"
        if line.startswith("a!"):
            msg = line[2:].strip()
            if msg:
                send_raw_to_cli(home, 'PeerA', msg, paneA, paneB)
            return "continue"
        if line.startswith("b!"):
            msg = line[2:].strip()
            if msg:
                send_raw_to_cli(home, 'PeerB', msg, paneA, paneB)
            return "continue"
        if line.startswith("a:"):
            msg = line.split(":",1)[1].strip()
            _send_user_to("PeerA", msg)
            return "continue"
        if line.startswith("b:"):
            msg = line.split(":",1)[1].strip()
            _send_user_to("PeerB", msg)
            return "continue"
        if low.startswith('/foreman'):
            _handle_foreman(line)
            return "continue"
        # Default broadcast: send to both peers immediately
        _send_user_to("PeerA", line)
        _send_user_to("PeerB", line)
        return "continue"

    return type('ConsoleAPI', (), {'handle': handle})
