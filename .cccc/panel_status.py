#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CCCC Status Panel
- Renders a professional help + live status dashboard in a tmux pane.
- Reads .cccc/state/ledger.jsonl and .cccc/state/status.json periodically.

Usage:
  python panel_status.py --home .cccc --interval 1.0
"""
from __future__ import annotations
import sys, json, time, argparse
from pathlib import Path
from typing import Dict, Any, List


def read_jsonl(path: Path, limit: int = 2000) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return items[-limit:]
    return items[-limit:]


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def summarize_ledger(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "handoff": {"delivered": 0, "queued": 0, "failed": 0, "A2B": 0, "B2A": 0},
        "patch": {
            "commit": 0,
            "tests_ok": 0,
            "tests_fail": 0,
            "precheck_fail": 0,
            "apply_fail": 0,
            "reject": 0,
        },
        "to_user": 0,
        "notes": [],
    }
    for it in items:
        kind = it.get("kind")
        if kind == "handoff":
            st = (it.get("status") or "").lower()
            out["handoff"].setdefault(st, 0)
            out["handoff"][st] += 1
            frm = it.get("from")
            if frm == "PeerA":
                out["handoff"]["A2B"] += 1
            elif frm == "PeerB":
                out["handoff"]["B2A"] += 1
        elif kind == "patch-commit":
            out["patch"]["commit"] += 1
            if it.get("tests_ok"):
                out["patch"]["tests_ok"] += 1
            else:
                out["patch"]["tests_fail"] += 1
        elif kind == "patch-precheck-fail":
            out["patch"]["precheck_fail"] += 1
        elif kind == "patch-apply-fail":
            out["patch"]["apply_fail"] += 1
        elif kind == "patch-reject":
            out["patch"]["reject"] += 1
        elif kind in ("to_user", "to_user-normalized"):
            out["to_user"] += 1

    # Keep last few notes for footer
    out["notes"] = items[-8:]
    return out


def format_note_line(it: Dict[str, Any]) -> str:
    ts = it.get("ts", "--:--:--")
    kind = it.get("kind", "?")
    who = it.get("from") or it.get("peer") or "sys"
    if kind == "handoff":
        st = it.get("status")
        return f"{ts}  handoff {who:6s} ->  status={st}"
    if kind == "patch-commit":
        ok = "ok" if it.get("tests_ok") else "fail"
        return f"{ts}  patch   {who:6s} ->  commit ({ok})"
    if kind in ("patch-precheck-fail", "patch-apply-fail", "patch-reject"):
        return f"{ts}  patch   {who:6s} ->  {kind}"
    if kind in ("to_user", "to_user-normalized"):
        return f"{ts}  to_user {who:6s}"
    if kind in ("handshake", "handoff-skipped"):
        return f"{ts}  {kind}   {who:6s}"
    return f"{ts}  {kind}   {who:6s}"


def render(home: Path):
    state = home / "state"
    ledger = read_jsonl(state / "ledger.jsonl")
    status = read_json(state / "status.json")
    session = read_json(state / "session.json")

    stats = summarize_ledger(ledger)
    paused = status.get("paused")
    phase = status.get("phase", "-")
    leader = status.get("leader", "-")
    require_ack = bool(status.get("require_ack", False))
    mcounts = (status.get("mailbox_counts") or {})
    mlast = (status.get("mailbox_last") or {})
    anti = status.get("handoff_filter_enabled")

    lines: List[str] = []
    lines.append("CCCC Status Panel")
    lines.append("=================")
    lines.append("")
    lines.append("Controls")
    lines.append("- a: <text> → PeerA  | b: <text> → PeerB")
    lines.append("- both:/u: <text>     → both peers")
    lines.append("- a! <cmd>            → raw passthrough to PeerA (no wrapper)")
    lines.append("- b! <cmd>            → raw passthrough to PeerB (no wrapper)")
    lines.append("- /pause | /resume     : toggle handoff")
    lines.append("- /anti-on|/anti-off   : toggle low-signal filter override")
    lines.append("- /anti-status         : show filter state")
    lines.append("- /refresh             : re-inject SYSTEM")
    lines.append("- q                    : quit orchestrator")
    lines.append("")
    lines.append("Session")
    lines.append(f"- name   : {session.get('session', '-')}   phase: {phase}  leader: {leader}")
    lines.append(f"- paused : {paused}")
    lines.append(f"- panes  : lt={session.get('lt','?')} rt={session.get('rt','?')} lb={session.get('lb','?')} rb={session.get('rb','?')}")
    lines.append("")
    lines.append("Delivery")
    lines.append(f"- require_ack: {require_ack}")
    lines.append(f"- anti-loop filter: {anti}")
    if mcounts:
        lines.append("- mailbox events:")
        for who in ("peerA","peerB"):
            c = mcounts.get(who) or {}
            lines.append(f"  · {who}: to_user={c.get('to_user',0)} to_peer={c.get('to_peer',0)} patch={c.get('patch',0)}")
    if mlast:
        lines.append("- mailbox last:")
        for who in ("peerA","peerB"):
            c = mlast.get(who) or {}
            lines.append(f"  · {who}: to_user={c.get('to_user','-')} to_peer={c.get('to_peer','-')} patch={c.get('patch','-')}")
    lines.append("")
    lines.append("Handoff")
    lines.append(f"- total: delivered={stats['handoff'].get('delivered',0)} queued={stats['handoff'].get('queued',0)} failed={stats['handoff'].get('failed',0)}")
    lines.append(f"- flow : A→B={stats['handoff'].get('A2B',0)}  B→A={stats['handoff'].get('B2A',0)}")
    lines.append("")
    lines.append("Patches")
    lines.append(f"- commits: {stats['patch']['commit']}   tests: ok={stats['patch']['tests_ok']} fail={stats['patch']['tests_fail']}")
    lines.append(f"- rejects: precheck={stats['patch']['precheck_fail']} apply={stats['patch']['apply_fail']} reject={stats['patch']['reject']}")
    lines.append("")
    lines.append("Recent Events (ledger tail)")
    for it in stats["notes"]:
        lines.append("- " + format_note_line(it))
    lines.append("")
    lines.append("Copy-mode: Prefix-[ to enter; q or Esc to exit")

    out = "\n".join(lines)
    sys.stdout.write("\033[H\033[J")  # clear screen
    sys.stdout.write(out + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=".cccc")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()
    home = Path(args.home).resolve()
    # Simple loop
    while True:
        try:
            render(home)
        except KeyboardInterrupt:
            break
        except Exception:
            # Best-effort panel; avoid crashing the pane
            pass
        time.sleep(max(0.3, args.interval))


if __name__ == "__main__":
    main()
