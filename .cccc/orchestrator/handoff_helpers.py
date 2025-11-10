# -*- coding: utf-8 -*-
"""
Handoff helpers copied from orchestrator_tmux.py (system copy, no behavioral changes):
- Plain-text extraction and inbox path helpers
- NUDGE composition and file headline extractors
- Inbox writer with per-peer sequence/locking
- Timestamp injection helpers

Note: Higher-level handoff orchestration (_send_handoff, keepalive scheduling, etc.)
remains in orchestrator_tmux.py and calls into these helpers.
"""
from __future__ import annotations
import os, re, json, time, shlex, tempfile
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore

def _plain_text_without_tags_and_mid(s: str) -> str:
    try:
        s2 = re.sub(r"\[\s*MID\s*:[^\]]+\]", " ", s, flags=re.I)
        s2 = re.sub(r"<[^>]+>", " ", s2)
        s2 = re.sub(r"\s+", " ", s2)
        return s2.strip()
    except Exception:
        return s

def _append_suffix_inside(payload: str, suffix: str) -> str:
    """Append short suffix to the end of the main body inside the outermost tag, if present.
    If no XML-like wrapper is present, append to the end. (Copied from orchestrator_tmux.py)"""
    if not suffix or not payload:
        return payload
    try:
        idx = payload.rfind("</")
        if idx >= 0:
            head = payload[:idx].rstrip()
            tail = payload[idx:]
            sep = "" if head.endswith(suffix) else (" " if not head.endswith(" ") else "")
            return head + sep + suffix + "\n" + tail
        sep = "" if payload.rstrip().endswith(suffix) else (" " if not payload.rstrip().endswith(" ") else "")
        return payload.rstrip() + sep + suffix
    except Exception:
        return payload

def _peer_folder_name(label: str) -> str:
    return "peerA" if label == "PeerA" else "peerB"

def _inbox_dir(home: Path, receiver_label: str) -> Path:
    return home/"mailbox"/_peer_folder_name(receiver_label)/"inbox"

def _processed_dir(home: Path, receiver_label: str) -> Path:
    return home/"mailbox"/_peer_folder_name(receiver_label)/"processed"

def _format_local_ts() -> str:
    from datetime import datetime, timedelta
    dt = datetime.now().astimezone()
    tzname = dt.tzname() or ""
    off = dt.utcoffset() or timedelta(0)
    total = int(off.total_seconds())
    sign = '+' if total >= 0 else '-'
    hh = abs(total)//3600; mm = (abs(total)%3600)//60
    return dt.strftime(f"%Y-%m-%d %H:%M:%S {tzname} (UTC{sign}{hh:02d}:{mm:02d})")

def _compose_nudge(inbox_path: str, *, ts: str, new_arrival: bool = False,
                   backlog_gt_zero: Optional[bool] = None,
                   seq: Optional[str] = None, preview: Optional[str] = None,
                   suffix: str = "") -> str:
    parts = ["[NUDGE]", f"[TS: {ts}]"]
    if new_arrival and seq:
        parts.append(f"[new arrival: {seq}]")
    trailing_bits: List[str] = []
    if seq:
        trailing_bits.append(f"trigger={seq}")
    if preview:
        trailing_bits.append(f"preview='{preview}'")
    action = None
    if new_arrival or backlog_gt_zero is True:
        # Construct absolute processed path (sibling to inbox, NOT inbox/processed/)
        processed_path = inbox_path.replace("/inbox", "/processed")
        action = f"open oldest first, process oldest→newest. Move processed files to {processed_path}."
    else:
        action = "continue your work; open oldest→newest."
    msg_core = " ".join([p for p in parts if p])
    trig_prev = "" if not trailing_bits else (" ".join(trailing_bits))
    mid_section = f" — {trig_prev}" if trig_prev else ""
    msg = f"{msg_core}{mid_section} — Inbox: {inbox_path} — {action}"
    if suffix and suffix.strip():
        msg = msg + " " + suffix.strip()
    return msg

def _compose_detailed_nudge(seq: str, preview: str, inbox_path: str, *, suffix: str = "") -> str:
    return _compose_nudge(inbox_path, ts=_format_local_ts(), new_arrival=True, seq=seq, preview=preview or None, suffix=suffix)

def _safe_headline(path: Path, *, max_bytes: int = 4096, max_chars: int = 32) -> str:
    try:
        with open(path, "rb") as f:
            raw = f.read(max(512, int(max_bytes)))
        text = raw.decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in text.splitlines()]
        def is_wrapped(ln: str) -> bool:
            if not ln:
                return True
            if ln.startswith("<") and ln.endswith(">"):
                return True
            if ln.startswith("```"):
                return True
            if ln.startswith("[MID:") or ln.startswith("[TS:"):
                return True
            return False
        head = ""
        for ln in lines:
            if is_wrapped(ln):
                continue
            head = ln
            if head:
                break
        if not head:
            return "[unreadable-or-binary]"
        head = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", head)
        head = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", head)
        head = re.sub(r"\s+", " ", head).strip()
        if len(head) > max_chars:
            return head[:max_chars].rstrip() + " …"
        return head
    except Exception:
        return "[unreadable-or-binary]"

def _inject_ts_after_mid(payload: str) -> str:
    try:
        if "[TS:" in payload:
            return payload
        lines = payload.splitlines()
        for i, ln in enumerate(lines):
            if ln.strip().startswith("[MID:"):
                ts_line = f"[TS: {_format_local_ts()}]"
                lines.insert(i+1, ts_line)
                return "\n".join(lines)
        return f"[TS: {_format_local_ts()}]\n" + payload
    except Exception:
        return payload

def _write_inbox_message(home: Path, receiver_label: str, payload: str, mid: str) -> Tuple[str, Path]:
    inbox = _inbox_dir(home, receiver_label)
    processed = _processed_dir(home, receiver_label)
    state = home/"state"
    state.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True); processed.mkdir(parents=True, exist_ok=True)

    peer = _peer_folder_name(receiver_label)
    lock_path = state/f"inbox-seq-{peer}.lock"
    counter_path = state/f"inbox-seq-{peer}.txt"

    def _compute_next_seq() -> int:
        try:
            val = int(counter_path.read_text(encoding="utf-8").strip())
            return val + 1
        except Exception:
            pass
        def _max_seq_in(d: Path) -> int:
            mx = 0
            try:
                for f in d.iterdir():
                    name = f.name
                    if len(name) >= 6 and name[:6].isdigit():
                        mx = max(mx, int(name[:6]))
            except Exception:
                pass
            return mx
        current = max(_max_seq_in(inbox), _max_seq_in(processed))
        return current + 1

    if fcntl is not None:
        with open(lock_path, "w") as lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX)
            except Exception:
                pass
            seq_int = _compute_next_seq()
            seq = f"{seq_int:06d}"
            fpath = inbox/f"{seq}.{mid}.txt"
            fpath.write_text(_inject_ts_after_mid(payload), encoding='utf-8')
            try:
                counter_path.write_text(str(seq_int), encoding='utf-8')
            except Exception:
                pass
    else:
        lock_dir = state/f"inbox-seq-{peer}.lckdir"
        acquired = False
        for _ in range(50):
            try:
                lock_dir.mkdir(exist_ok=False); acquired = True; break
            except Exception:
                time.sleep(0.01)
        try:
            seq_int = _compute_next_seq()
            seq = f"{seq_int:06d}"
            fpath = inbox/f"{seq}.{mid}.txt"
            fpath.write_text(_inject_ts_after_mid(payload), encoding='utf-8')
            try:
                counter_path.write_text(str(seq_int), encoding='utf-8')
            except Exception:
                pass
        finally:
            if acquired:
                try: lock_dir.rmdir()
                except Exception: pass
    return seq, fpath
