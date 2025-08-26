#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minimal mock agent for CCCC smoke testing without external CLIs.

Behavior:
- Prints a lightweight prompt to appear idle for tmux delivery.
- On first [INPUT], emits a <TO_USER> summary and a tiny unified diff patch
  that appends a demo line to README.md. Also ACKs any MID markers found.
"""
import sys, re, time

ROLE_PROMPT = {
    "peera": "assistant> ",
    "peerb": ">>> ",
}

MID_RE = re.compile(r"\[MID:\s*([A-Za-z0-9\-\._:]+)\]")

def main():
    role = "peerb"
    for i, a in enumerate(sys.argv):
        if a in ("--role", "-r") and i+1 < len(sys.argv):
            role = sys.argv[i+1].lower()
    prompt = ROLE_PROMPT.get(role, "> ")

    sys.stdout.write(prompt)
    sys.stdout.flush()

    buf = []
    emitted = False
    while True:
        line = sys.stdin.readline()
        if not line:
            time.sleep(0.05)
            continue
        buf.append(line)
        # ACK any MID markers quickly
        for mid in MID_RE.findall(line):
            sys.stdout.write(f"\n<SYSTEM_NOTES>ack: {mid}</SYSTEM_NOTES>\n")
            sys.stdout.flush()
        # On first [INPUT], emit a minimal patch once
        if not emitted and line.strip() == "" and any("[INPUT]" in x for x in buf):
            sys.stdout.write(
                "\n<TO_USER>Mock agent: generating demo patch to verify preflight.</TO_USER>\n"
            )
            # Use a new unique filename each run to avoid conflicts
            ts = int(time.time())
            fname = f"docs/DEMO-{ts}.md"
            sys.stdout.write(
                "```diff\n"
                f"diff --git a/{fname} b/{fname}\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                f"+++ b/{fname}\n"
                "@@ -0,0 +1,3 @@\n"
                "+# CCCC Demo\n"
                "+\n"
                "+This file verifies the preflight → commit → ledger pipeline (mock agent).\n"
                "```\n"
            )
            sys.stdout.write(prompt)
            sys.stdout.flush()
            emitted = True

if __name__ == "__main__":
    main()
