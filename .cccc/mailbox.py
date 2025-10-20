#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Tuple
import hashlib, json, time

PEERS = ("peerA", "peerB")
FOREMAN = "foreman"

# Sentinel marker (single-line) written after a message is queued from mailbox
SENTINEL_PREFIX = "<!-- MAILBOX:SENT v1"

def is_sentinel_text(text: str) -> bool:
    """Return True if the whole file content is a mailbox SENT sentinel.
    Expect a single-line HTML comment like:
      <!-- MAILBOX:SENT v1 ts=... eid=... sha=... route=... -->
    We purposefully require the fixed prefix to avoid false positives.
    """
    if not text:
        return False
    s = text.strip()
    return s.startswith(SENTINEL_PREFIX) and s.endswith("-->") and "\n" not in s

def compose_sentinel(*, ts: str, eid: str, sha8: str, route: str) -> str:
    """Compose a single-line sentinel comment stored in mailbox files after queueing."""
    # Keep ASCII-only where possible; route may include unicode arrow which is fine.
    # Example: <!-- MAILBOX:SENT v1 ts=2025-10-17T06:15:22Z eid=a1b2c3d4 sha=7c45dead route=PeerB→PeerA -->
    parts = [
        f"ts={ts}",
        f"eid={eid}",
        f"sha={sha8}",
        f"route={route}",
    ]
    return f"{SENTINEL_PREFIX} " + " ".join(parts) + " -->"

def ensure_mailbox(home: Path) -> Dict[str, Path]:
    base = home/"mailbox"
    paths = {}
    for p in PEERS:
        d = base/p
        d.mkdir(parents=True, exist_ok=True)
        # message files (runtime contract)
        for fname in ("to_user.md", "to_peer.md", "inbox.md"):
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
    # Ensure foreman mailbox (single to_peer.md sink)
    fdir = base/FOREMAN
    try:
        fdir.mkdir(parents=True, exist_ok=True)
        fp = fdir/"to_peer.md"
        if not fp.exists():
            fp.write_text("", encoding="utf-8")
    except Exception:
        pass
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

def _ledger_append(path_state: Path, entry: Dict[str, Any]):
    """Append a JSONL entry to ledger; tolerate failures silently."""
    try:
        state = path_state
        state.mkdir(exist_ok=True)
        p = state/"ledger.jsonl"
        ent = {"ts": time.strftime('%Y-%m-%d %H:%M:%S'), **entry}
        with p.open('a', encoding='utf-8') as f:
            f.write(json.dumps(ent, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _smart_decode(raw: bytes) -> Tuple[str, str, bool]:
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
            return raw.decode("utf-8-sig", errors="strict"), "utf-8-sig", False
        if raw.startswith(b"\xff\xfe"):
            return raw.decode("utf-16-le", errors="strict"), "utf-16-le", False
        if raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16-be", errors="strict"), "utf-16-be", False
    except Exception:
        pass
    # UTF-8 strict
    try:
        return raw.decode("utf-8", errors="strict"), "utf-8", False
    except Exception:
        pass
    # UTF-8 salvage with replacement (prefer this over mojibake when content is mostly ASCII)
    try:
        tmp = raw.decode("utf-8", errors="replace")
        rep = tmp.count("\ufffd")
        if rep == 0:
            return tmp, "utf-8", False
        # Heuristic: prefer salvage if replacement ratio is low and ASCII share is high
        ascii_count = sum(1 for ch in tmp if ord(ch) < 128)
        total = max(1, len(tmp))
        if (rep / total) <= 0.02 and (ascii_count / total) >= 0.6:
            return tmp, "utf-8(replace)", True
    except Exception:
        pass
    # Heuristic for UTF-16 without BOM: many NULs → try LE then BE
    try:
        nul_count = raw.count(b"\x00")
        if nul_count > max(4, len(raw)//8):
            try:
                return raw.decode("utf-16-le", errors="strict"), "utf-16-le", False
            except Exception:
                try:
                    return raw.decode("utf-16-be", errors="strict"), "utf-16-be", False
                except Exception:
                    # Last resort for UTF-16-ish data
                    try:
                        return raw.decode("utf-16-le", errors="ignore"), "utf-16-le(ignore)", True
                    except Exception:
                        return raw.decode("utf-16-be", errors="ignore"), "utf-16-be(ignore)", True
    except Exception:
        pass
    # Try GB18030 (covers GBK/GB2312)
    try:
        return raw.decode("gb18030", errors="strict"), "gb18030", False
    except Exception:
        pass
    # Fallback
    return raw.decode("latin1", errors="ignore"), "latin1(ignore)", True

def read_if_changed(path: Path, last_sha: str) -> Tuple[bool, str, str]:
    """Read mailbox file robustly and detect changes.
    Treat empty/whitespace-only as no event.
    """
    try:
        raw = path.read_bytes()
        text, enc, diag = _smart_decode(raw)
    except Exception:
        return False, "", last_sha
    text = text.strip()
    if not text:
        return False, "", last_sha
    # Ignore sentinel files entirely – treated as empty/no-event
    try:
        if is_sentinel_text(text):
            return False, "", last_sha
    except Exception:
        # Be conservative: if detection fails, continue with normal flow
        pass
    sha = sha256_text(text)
    if (diag or (enc.startswith('latin1') or 'ignore' in enc or enc.startswith('gb'))) and sha != last_sha:
        try:
            home = path.parents[2]
            prefix = raw[:24].hex()
            nul_ratio = (raw.count(b"\x00") / max(1, len(raw)))
            _ledger_append(home/"state", {
                "kind":"mailbox-diag", "file": str(path), "encoding": enc,
                "bytes": len(raw), "prefix_hex": prefix, "nul_ratio": round(nul_ratio,4)
            })
        except Exception:
            pass
    if sha != last_sha:
        return True, text, sha
    return False, "", last_sha

def scan_mailboxes(home: Path, idx: MailboxIndex) -> Dict[str, Dict[str, Any]]:
    """
    Return events per peer when mailbox files change and are non-empty.
    Example:
      { 'peerA': {'to_user': '...', 'to_peer': '...'}, 'peerB': {...} }
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
        # no patch channel (diff mechanism removed)
    return events

def reset_mailbox(home: Path):
    """Clear mailbox files (to_user.md, to_peer.md) for both peers and
    reset the seen-index to avoid stale reads at startup.
    """
    base = home/"mailbox"
    ensure_mailbox(home)
    for p in PEERS:
        d = base/p
        for fname in ("to_user.md", "to_peer.md", "inbox.md"):
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
