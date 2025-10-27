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
try:
    # Local import (script runs from .cccc). Used for a reliable POR fallback.
    from por_manager import por_status_snapshot as _por_snap  # type: ignore
except Exception:
    _por_snap = None  # type: ignore


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
        # patch/diff events removed in current runtime
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
    # legacy patch/diff events no longer emitted
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
    por = status.get("por") or {}
    # Always show POR path/updated; fall back to computing when status lacks it
    if not por and _por_snap is not None:
        try:
            por = _por_snap(home)
        except Exception:
            por = {}
    coach = status.get("aux") or {}

    lines: List[str] = []
    # Header (fixed height; avoid runaway growth)
    lines.append("CCCC Panel  |  type h or /help in terminal")
    lines.append("============================================================")
    # Compact status
    lines.append(f"Session: {session.get('session','-')}  Phase: {phase}  Leader: {leader}  Paused: {paused}")
    lines.append(f"Delivery: require_ack={require_ack}  filter={anti}")
    if mcounts:
        ca = mcounts.get('peerA') or {}; cb = mcounts.get('peerB') or {}
        lines.append(f"Mailbox: A tu={ca.get('to_user',0)} tp={ca.get('to_peer',0)}  |  B tu={cb.get('to_user',0)} tp={cb.get('to_peer',0)}")
    # Foreman (User Proxy) minimal line
    fman = status.get('foreman') or {}
    if isinstance(fman, dict) and fman.get('enabled'):
        if fman.get('running'):
            lines.append(f"Foreman: RUNNING  next @ {fman.get('next_due') or '-'}  cc: {'ON' if fman.get('cc_user') else 'OFF'}")
        else:
            last = fman.get('last') or '-'
            rc = fman.get('last_rc')
            rc_s = f" rc={rc}" if rc is not None else ""
            lines.append(f"Foreman: ON  last @ {last}{rc_s}  next @ {fman.get('next_due') or '-'}  cc: {'ON' if fman.get('cc_user') else 'OFF'}")
    # Show POR path and updated time even without summary
    lines.append(f"POR path={(por.get('path') if isinstance(por, dict) else '-') or '-'}  updated={(por.get('updated_at') if isinstance(por, dict) else '-') or '-'}")
    summary = (por.get('summary') if isinstance(por, dict) else '') or ''
    if summary:
        lines.append(f"POR summary: {summary[:160]}")
    # Reset cadence / remaining rounds (if present)
    rst = status.get("reset") or {}
    k = rst.get("next_self_check_in")
    pol = rst.get("policy") or "compact"
    if k is not None:
        ks = str(k)
        lines.append(f"Next: self-check in {ks}  |  reset policy: {pol}")
    if coach:
        lines.append(f"Aux mode={coach.get('mode','off')} command={(coach.get('command') or '-')}")
        if coach.get('last_reason'):
            lines.append(f"Aux last={coach.get('last_reason')}")
    lines.append(f"Handoff: delivered={stats['handoff'].get('delivered',0)} queued={stats['handoff'].get('queued',0)} failed={stats['handoff'].get('failed',0)}  Flow A→B={stats['handoff'].get('A2B',0)} B→A={stats['handoff'].get('B2A',0)}")
    # Recent (limited items)
    lines.append("Recent:")
    for it in stats["notes"][-6:]:
        lines.append("- " + format_note_line(it))
    # Footer hint
    lines.append("------------------------------------------------------------")
    lines.append("In terminal: a:/b:/both:/u: send; h or /help for help; q to quit.")

    out = "\n".join(lines)
    sys.stdout.write("\033[H\033[J")  # clear screen
    sys.stdout.write(out + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=".cccc")
    ap.add_argument("--interval", type=float, default=2.0)
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
