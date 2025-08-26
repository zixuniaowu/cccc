#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini Chat Adapter (interactive) for CCCC

Purpose:
- Provide a simple interactive CLI that cooperates with the CCCC orchestrator.
- Reads [SYSTEM]/[CONTEXT]/[INPUT] blocks from stdin and prints model replies.
- Immediately ACKs any [MID: <id>] markers via <SYSTEM_NOTES>ack: <id></SYSTEM_NOTES>.

Requirements:
- pip install google-generativeai
- export GEMINI_API_KEY=...

Usage:
  python .cccc/adapters/gemini_chat.py --model gemini-1.5-flash

This adapter prints a stable prompt "gemini> ", so set peerB.prompt_regex accordingly.
"""
import os, sys, re, argparse, time

PROMPT = "gemini> "
SECTION_RE = re.compile(r"^\[(SYSTEM|CONTEXT|INPUT)\]\s*$", re.I)
MID_RE = re.compile(r"\[MID:\s*([A-Za-z0-9\-\._:]+)\]")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"))
    args = ap.parse_args()

    try:
        import google.generativeai as genai
    except Exception as e:
        sys.stderr.write("[FATAL] Missing dependency. Run: pip install google-generativeai\n")
        sys.exit(1)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.stderr.write("[FATAL] GEMINI_API_KEY not set in environment.\n")
        sys.exit(1)

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(args.model)

    system_text = ""
    context_text = ""
    input_lines = []
    cur_section = None

    sys.stdout.write(PROMPT)
    sys.stdout.flush()

    while True:
        line = sys.stdin.readline()
        if not line:
            time.sleep(0.05)
            continue

        m = SECTION_RE.match(line.strip())
        if m:
            cur_section = m.group(1).upper()
            if cur_section == "INPUT":
                input_lines = []
            continue

        if cur_section == "SYSTEM":
            system_text += line
            continue
        if cur_section == "CONTEXT":
            context_text += line
            continue
        if cur_section == "INPUT":
            input_lines.append(line)
            # Empty line signals the orchestrator finished pasting one payload
            if line.strip() == "":
                payload = "".join(input_lines)
                # Quick ACK for any MID markers to help delivery queues
                for mid in MID_RE.findall(payload):
                    sys.stdout.write(f"\n<SYSTEM_NOTES>ack: {mid}</SYSTEM_NOTES>\n")
                    sys.stdout.flush()
                try:
                    prompt = (
                        ("# SYSTEM\n" + system_text.strip() + "\n\n") if system_text.strip() else ""
                        + ("# CONTEXT\n" + context_text.strip() + "\n\n" if context_text.strip() else "")
                        + "# INPUT\n" + payload.strip() + "\n"
                    )
                    resp = model.generate_content(prompt)
                    text = getattr(resp, "text", None) or (resp.candidates[0].content.parts[0].text if getattr(resp, "candidates", None) else "")
                    if not text:
                        text = "<TO_USER>Gemini responded but no text was returned.</TO_USER>\n"
                except Exception as e:
                    text = f"<TO_USER>Gemini error: {e}</TO_USER>\n"
                sys.stdout.write(text + "\n")
                sys.stdout.write(PROMPT)
                sys.stdout.flush()
                cur_section = None
            continue

        # Non‑section lines without active section: ignore but keep prompt responsive
        if line.strip() == "":
            sys.stdout.write(PROMPT)
            sys.stdout.flush()

if __name__ == "__main__":
    main()

