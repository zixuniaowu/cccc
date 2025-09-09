#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Tuple
import hashlib, json, time

PEERS = ("peerA", "peerB")

def ensure_mailbox(home: Path) -> Dict[str, Path]:
    base = home/"mailbox"
    paths = {}
    for p in PEERS:
        d = base/p
        d.mkdir(parents=True, exist_ok=True)
        # legacy files
        for fname in ("to_user.md", "to_peer.md", "patch.diff", "inbox.md"):
            f = d/fname
            if not f.exists():
                f.write_text("", encoding="utf-8")
        # new pull-based inbox/processed directories
        (d/"inbox").mkdir(exist_ok=True)
        (d/"processed").mkdir(exist_ok=True)
        paths[p] = d
    # write a tiny .gitignore to keep repo clean
    gi = base/".gitignore"
    if not gi.exists():
        gi.write_text("*\n!/.gitignore\n", encoding="utf-8")
    return paths

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

class MailboxIndex:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.idx_path = state_dir/"mailbox_seen.json"
        self.idx: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self):
        if self.idx_path.exists():
            try:
                self.idx = json.loads(self.idx_path.read_text(encoding="utf-8"))
            except Exception:
                self.idx = {}

    def save(self):
        try:
            self.state_dir.mkdir(exist_ok=True)
            self.idx_path.write_text(json.dumps(self.idx, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def key_for(self, peer: str, fname: str) -> str:
        return f"{peer}:{fname}"

    def seen_hash(self, peer: str, fname: str) -> str:
        return (self.idx.get(self.key_for(peer, fname)) or {}).get("sha", "")

    def update_hash(self, peer: str, fname: str, sha: str):
        self.idx[self.key_for(peer, fname)] = {"sha": sha, "ts": time.time()}

def _smart_decode(raw: bytes) -> str:
    """Decode bytes to str with simple BOM/heuristic detection.
    Order:
      - UTF-8 with BOM (utf-8-sig)
      - UTF-16 LE/BE (BOM)
      - UTF-8 (strict)
      - UTF-16 (heuristic via NUL ratio), try LE then BE (strict, then ignore)
      - GB18030 (common superset for CJK)
      - Latin-1 (last resort)
    Rationale: GB18030 can decode almost any byte stream, so we must try UTF-16
    heuristics before GB18030 to avoid mojibake when sources write UTF-16.
    """
    # BOM-based
    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw.decode("utf-8-sig", errors="strict")
        if raw.startswith(b"\xff\xfe"):
            return raw.decode("utf-16-le", errors="strict")
        if raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16-be", errors="strict")
    except Exception:
        pass
    # UTF-8 strict
    try:
        return raw.decode("utf-8", errors="strict")
    except Exception:
        pass
    # Heuristic for UTF-16 without BOM: many NULs → try LE then BE
    try:
        nul_count = raw.count(b"\x00")
        if nul_count > max(4, len(raw)//8):
            try:
                return raw.decode("utf-16-le", errors="strict")
            except Exception:
                try:
                    return raw.decode("utf-16-be", errors="strict")
                except Exception:
                    # Last resort for UTF-16-ish data
                    try:
                        return raw.decode("utf-16-le", errors="ignore")
                    except Exception:
                        return raw.decode("utf-16-be", errors="ignore")
    except Exception:
        pass
    # Try GB18030 (covers GBK/GB2312)
    try:
        return raw.decode("gb18030", errors="strict")
    except Exception:
        pass
    # Fallback
    return raw.decode("latin1", errors="ignore")

def read_if_changed(path: Path, last_sha: str) -> Tuple[bool, str, str]:
    """Read mailbox file robustly and detect changes.
    Treat empty/whitespace-only as no event.
    """
    try:
        raw = path.read_bytes()
        text = _smart_decode(raw)
    except Exception:
        return False, "", last_sha
    text = text.strip()
    if not text:
        return False, "", last_sha
    sha = sha256_text(text)
    if sha != last_sha:
        return True, text, sha
    return False, "", last_sha

def scan_mailboxes(home: Path, idx: MailboxIndex) -> Dict[str, Dict[str, Any]]:
    """
    Return events per peer when mailbox files change and are non-empty.
    Example:
      { 'peerA': {'to_user': '...', 'to_peer': '...', 'patch': '...'}, 'peerB': {...} }
    """
    ensure_mailbox(home)
    base = home/"mailbox"
    events: Dict[str, Dict[str, Any]] = {p: {} for p in PEERS}
    for p in PEERS:
        d = base/p
        # to_user
        changed, text, sha = read_if_changed(d/"to_user.md", idx.seen_hash(p, "to_user.md"))
        if changed:
            events[p]["to_user"] = text
            idx.update_hash(p, "to_user.md", sha)
        # to_peer
        changed, text, sha = read_if_changed(d/"to_peer.md", idx.seen_hash(p, "to_peer.md"))
        if changed:
            events[p]["to_peer"] = text
            idx.update_hash(p, "to_peer.md", sha)
        # patch
        changed, text, sha = read_if_changed(d/"patch.diff", idx.seen_hash(p, "patch.diff"))
        if changed:
            events[p]["patch"] = text
            idx.update_hash(p, "patch.diff", sha)
    return events

def reset_mailbox(home: Path):
    """Clear mailbox files (to_user.md, to_peer.md, patch.diff) for both peers and
    reset the seen-index to avoid stale reads at startup.
    """
    base = home/"mailbox"
    ensure_mailbox(home)
    for p in PEERS:
        d = base/p
        for fname in ("to_user.md", "to_peer.md", "patch.diff", "inbox.md"):
            try:
                (d/fname).write_text("", encoding="utf-8")
            except Exception:
                pass
        # clear inbox directory but keep processed for audit
        try:
            inbox = d/"inbox"
            for f in inbox.iterdir():
                try: f.unlink()
                except Exception: pass
        except Exception:
            pass
    # Reset index state
    state_dir = home/"state"
    try:
        (state_dir/"mailbox_seen.json").unlink()
    except Exception:
        pass
