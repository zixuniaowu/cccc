#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minimal file-bridge adapter:
- Watches .cccc/mailbox/<peer>/inbox.md for new payloads
- Injects payloads into the CLI child via a PTY (pexpect), submitting with a final Enter
- Forwards child stdout to this adapter's stdout (so the tmux pane shows CLI output)
- Does NOT parse child stdout; mailbox remains authoritative for outputs
"""
from __future__ import annotations
import os, sys, time, argparse, io
from pathlib import Path

def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding='utf-8')
    except Exception:
        return ""

def write_text(p: Path, s: str):
    try:
        p.write_text(s, encoding='utf-8')
    except Exception:
        pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--home', required=True)
    ap.add_argument('--peer', required=True, choices=['peerA','peerB'])
    ap.add_argument('--cmd', required=True, help='CLI command to run')
    ap.add_argument('--inbox', required=False, help='Path to inbox.md (optional; derived from home+peer if omitted)')
    ap.add_argument('--newline', default='\r', help='Submission newline (\r is Enter)')
    ap.add_argument('--prompt-regex', default='', help='Optional prompt regex to detect readiness')
    args = ap.parse_args()

    home = Path(args.home)
    peer = args.peer
    inbox = Path(args.inbox) if args.inbox else home/"mailbox"/peer/"inbox.md"
    inbox.parent.mkdir(parents=True, exist_ok=True)

    # Truncate inbox on start
    write_text(inbox, "")
    # File log for diagnostics
    log_path = home/"state"/f"bridge-{peer}.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    def log(msg: str):
        try:
            with log_path.open('a', encoding='utf-8') as f:
                f.write(time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + msg + '\n')
        except Exception:
            pass

    # Spawn CLI under PTY
    print(f"[bridge] adapter ready peer={peer} home={home} inbox={inbox}")
    print(f"[bridge] starting child: {args.cmd}")
    log(f"adapter ready peer={peer} home={home} inbox={inbox}")
    log(f"starting child: {args.cmd}")
    sys.stdout.flush()
    # Lazy import pexpect (print/log friendly message if missing)
    try:
        import pexpect  # type: ignore
    except Exception as e:
        msg = f"pexpect import failed: {e}. Please 'pip install pexpect' in the same Python env."
        print("[bridge] " + msg)
        log(msg)
        time.sleep(2)
        return
    # Run the CLI under a shell so complex commands/flags work
    try:
        # Ensure sane terminal env for the child
        env = os.environ.copy()
        if not env.get('TERM'):
            env['TERM'] = 'xterm-256color'
        child = pexpect.spawn('/bin/bash', ['-lc', args.cmd], encoding='utf-8', echo=False, timeout=None, env=env)
        # Give the child a reasonable window size to reduce TUI quirks
        try:
            child.setwinsize(40, 120)
        except Exception:
            pass
        # Nudge once to coax a prompt
        try:
            child.write('\r')
        except Exception:
            pass
    except Exception as e:
        msg = f"spawn failed: {e}"
        print("[bridge] " + msg)
        log(msg)
        time.sleep(2)
        return

    last_mtime = 0.0
    # Regex patterns to clean DSR queries/replies from printed output
    # Also synthesize replies back to the child PTY so CLIs that probe cursor position don't hang.
    import re, shutil, signal
    DSR_QUERY_RE = re.compile(r"\x1b\[(\?|)6n")
    DSR_REPLY_RE = re.compile(r"\x1b\[\d{1,3};\d{1,3}R")
    # SGR (Select Graphic Rendition) – normalize low-contrast styles to improve readability in tmux themes
    SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
    # TUI control sequences that cause screen wipes or alternate-screen switching — strip or soften them
    ALT_TOGGLE_RE = re.compile(r"\x1b\[\?(?:1047|1049)[hl]")  # enter/exit alt screen
    ED_RE  = re.compile(r"\x1b\[(?:[0-3]?)[J]")              # erase in display
    EL_RE  = re.compile(r"\x1b\[(?:[0-2]?)[K]")              # erase in line
    CUP_RE = re.compile(r"\x1b\[\d{0,3};\d{0,3}H")          # cursor position → replace with newline
    STBM_RE= re.compile(r"\x1b\[\d{0,3};\d{0,3}r")          # set scrolling region

    def _normalize_contrast(s: str) -> str:
        def _repl(m: re.Match) -> str:
            params = m.group(1)
            if params is None:
                return m.group(0)
            try:
                codes = [int(p) for p in params.split(';') if p != '']
            except Exception:
                return m.group(0)
            out = []
            for c in codes:
                if c == 2:  # dim
                    continue  # drop dim to avoid unreadable text
                if c == 90:  # bright black → white
                    out.append(37)
                else:
                    out.append(c)
            if not out:
                return "\x1b[m"  # reset only
            return "\x1b[" + ";".join(str(x) for x in out) + "m"
        try:
            return SGR_RE.sub(_repl, s)
        except Exception:
            return s

    # Keep child PTY window size aligned with our pane to avoid wrapping/garbling
    def apply_winsize():
        try:
            size = shutil.get_terminal_size(fallback=(120, 40))
            # shutil returns (columns, rows); pexpect expects (rows, columns)
            child.setwinsize(size.lines, size.columns)
        except Exception:
            pass

    try:
        apply_winsize()
    except Exception:
        pass

    def _on_winch(signum, frame):
        apply_winsize()
    try:
        signal.signal(signal.SIGWINCH, _on_winch)
    except Exception:
        pass

    # Prompt detection removed for simplicity to avoid timing issues across CLIs

    while True:
        try:
            # Pump child stdout to pane (non-blocking)
            try:
                data = child.read_nonblocking(4096, timeout=0)
                if data:
                    # If the child queries cursor position (DSR), synthesize a reply so it doesn't block.
                    try:
                        for m in DSR_QUERY_RE.finditer(data):
                            try:
                                # Reply with a safe default position (row=1, col=1). If DEC private query used ("?"), mirror it.
                                priv = m.group(1) or ""
                                child.write(f"\x1b[{priv}1;1R")
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # Clean DSR queries and replies from what we print to the pane (cosmetic only)
                    try:
                        clean = DSR_QUERY_RE.sub("", data)
                        clean = DSR_REPLY_RE.sub("", clean)
                        clean = _normalize_contrast(clean)
                        # Do not rewrite carriage returns here; let the CLI control the screen
                        sys.stdout.write(clean)
                        sys.stdout.flush()
                    except Exception:
                        pass
            except Exception:
                pass
            # Poll inbox for new payload
            try:
                st = inbox.stat()
                mt = st.st_mtime
            except Exception:
                mt = last_mtime
            if mt != last_mtime:
                last_mtime = mt
                payload = read_text(inbox).strip()
                if payload:
                    log("inbox changed; injecting payload")
                    # Submit payload (peer-specific minimal strategy)
                    try:
                        pl = payload.rstrip("\r\n")
                        if peer == 'peerB':
                            # Codex CLI: newline-terminated send is most reliable
                            child.sendline(pl)
                            time.sleep(0.06)
                        else:
                            # Claude Code: write + single Enter (CR)
                            child.write(pl)
                            try:
                                child.sendcontrol('m')
                            except Exception:
                                try: child.send('\r')
                                except Exception: pass
                            time.sleep(0.06)
                    except Exception as e:
                        print(f"[bridge] inject failed: {e}")
                        log(f"inject failed: {e}")
                    log("payload submitted; clearing inbox")
                    # Clear inbox to avoid duplicate sends
                    write_text(inbox, "")
            # Check child alive
            if not child.isalive():
                print("[bridge] child exited; restarting in 2s …", file=sys.stderr)
                log("child exited; restarting")
                time.sleep(2)
                child = pexpect.spawn('/bin/bash', ['-lc', args.cmd], encoding='utf-8', echo=False, timeout=None, env=env)
                try:
                    child.setwinsize(40, 120)
                except Exception:
                    pass
                try:
                    child.write('\r')
                except Exception:
                    pass
            time.sleep(0.2)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[bridge] error: {e}", file=sys.stderr)
            log(f"error: {e}")
            time.sleep(0.5)

if __name__ == '__main__':
    main()
