# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import re, json, time, os, shlex, tempfile, subprocess, uuid

# --- tmux helpers (aligned with orchestrator) ---
def _run(cmd: str, timeout: int = 600, cwd: Optional[Path] = None) -> Tuple[int,str,str]:
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(cwd) if cwd else None)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill(); return 124, "", "Timeout"
    return p.returncode, out, err

def _tmux(*args: str) -> Tuple[int,str,str]:
    return _run("tmux " + " ".join(shlex.quote(a) for a in args))

ANSI_RE = re.compile(r"\x1b\[.*?m|\x1b\[?[\d;]*[A-Za-z]")

def capture_pane(pane: str, lines: int = 2000) -> str:
    code,out,err = _tmux("capture-pane","-t",pane,"-p","-S",f"-{lines}")
    return ANSI_RE.sub("", out if code==0 else "")

def paste_to_pane(pane: str, text: str, profile: Dict[str,Any]):
    # Ensure pane is not in copy-mode (otherwise keystrokes/paste may be eaten by tmux)
    try:
        code,out,err = _tmux("display-message","-p","-t",pane,"#{pane_in_mode}")
        if code == 0 and out.strip() in ("1","on","yes"):
            _tmux("send-keys","-t",pane,"-X","cancel")
    except Exception:
        pass
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
        f.write(text); fname=f.name
    buf = f"buf-{int(time.time()*1000)}"
    _tmux("load-buffer","-b",buf,fname)
    # Use bracketed paste (-p) to signal paste to the CLI for more reliable handling
    _tmux("paste-buffer","-p","-t",pane,"-b",buf)
    # Small pause so the paste buffer is not swallowed; stabilizes TUI input boxes
    time.sleep(0.15)
    # After paste, send a configurable sequence of submit keys.
    # Default to a single Enter for safety if not specified in the actor profile.
    keys = list(profile.get("post_paste_keys") or ["Enter"])
    for k in keys:
        _tmux("send-keys","-t",pane,k)
    _tmux("delete-buffer","-b",buf)
    try: os.unlink(fname)
    except Exception: pass

def type_to_pane(pane: str, text: str, profile: Dict[str,Any]):
    # Type char-by-char; better for fragile TUI paste scenarios
    try:
        code,out,err = _tmux("display-message","-p","-t",pane,"#{pane_in_mode}")
        if code == 0 and out.strip() in ("1","on","yes"):
            _tmux("send-keys","-t",pane,"-X","cancel")
    except Exception:
        pass
    send_at_end = bool(profile.get("type_send_at_end", True))
    newline_key = profile.get("compose_newline_key") or "Enter"
    line_send_key = profile.get("line_send_key") or (profile.get("send_sequence") or "C-m")
    final_send_key = profile.get("send_sequence") or "C-m"
    chunk_lines = int(profile.get("chunk_lines", 0) or 0)
    chunk_delay = float(profile.get("chunk_delay_ms", 0) or 0) / 1000.0

    lines = text.splitlines()
    for i, line in enumerate(lines):
        _tmux("send-keys","-t",pane,"-l",line)
        is_last = (i == len(lines) - 1)
        if not is_last:
            # Insert a newline for multi-line bodies (no submit)
            _tmux("send-keys","-t",pane,newline_key)
        else:
            if send_at_end:
                _tmux("send-keys","-t",pane,final_send_key)
            else:
                _tmux("send-keys","-t",pane,line_send_key)

        # Chunked throttling to avoid overloading the TUI
        if chunk_lines and (i+1) % chunk_lines == 0:
            time.sleep(chunk_delay)

def send_text(pane: str, text: str, profile: Dict[str,Any]):
    mode = (profile or {}).get("input_mode") or "paste"
    if mode == "type":
        type_to_pane(pane, text, profile)
    else:
        paste_to_pane(pane, text, profile)

def send_ctrl_c(pane: str):
    _tmux("send-keys","-t",pane,"C-c")

# --- State & idle detection ---
class PaneIdleJudge:
    def __init__(self, profile: Dict[str,Any]):
        self.prompt_re = re.compile(profile.get("prompt_regex",""), re.I) if profile.get("prompt_regex") else None
        self.busy_res  = [re.compile(p, re.I) for p in profile.get("busy_regexes",[])]
        self.quiet_sec = float(profile.get("idle_quiet_seconds", 1.5))
        self._last_snapshot = ""
        self._last_change_ts = 0.0

    def refresh(self, pane: str) -> Tuple[bool, str]:
        """Return (is_idle, reason)."""
        text = capture_pane(pane, lines=1200)
        now = time.time()
        if text != self._last_snapshot:
            self._last_snapshot = text
            self._last_change_ts = now

        tail = text.splitlines()[-30:]  # inspect the most recent 30 lines
        tail_txt = "\n".join(tail)

        # If any busy regex matches → busy
        for rx in self.busy_res:
            if rx.search(tail_txt):
                return False, "busy_regex"

        # If prompt detected and quiet for a while → idle
        if self.prompt_re and self.prompt_re.search(tail_txt):
            if now - self._last_change_ts >= self.quiet_sec:
                return True, "prompt+quiet"
            else:
                return False, "prompt-but-noisy"

        # Fallback without prompt: rely on quiet duration
        if now - self._last_change_ts >= self.quiet_sec:
            return True, "quiet-only"

        return False, "changing"

# --- Outbox and ACK ---
class Outbox:
    def __init__(self, home: Path, peer: str):
        self.path = home/"state"/f"outbox-{peer}.jsonl"
        self.path.parent.mkdir(exist_ok=True)
        if not self.path.exists(): self.path.touch()

    def enqueue(self, mid: str, payload: str):
        with self.path.open("a",encoding="utf-8") as f:
            f.write(json.dumps({"mid": mid, "payload": payload}, ensure_ascii=False)+"\n")

    def load_all(self) -> List[Dict[str,Any]]:
        items=[]
        with self.path.open("r",encoding="utf-8") as f:
            for line in f:
                line=line.strip()
                if not line: continue
                try: items.append(json.loads(line))
                except: pass
        return items

    def replace_all(self, items: List[Dict[str,Any]]):
        tmp = str(self.path)+".tmp"
        with open(tmp,"w",encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it,ensure_ascii=False)+"\n")
        os.replace(tmp, self.path)

    def remove(self, mid: str):
        items=[it for it in self.load_all() if it.get("mid")!=mid]
        self.replace_all(items)

ACK_RE = re.compile(r"(?:^|;|\s)ack:\s*([A-Za-z0-9\-\._:]+)", re.I)
NACK_RE= re.compile(r"(?:^|;|\s)nack:\s*([A-Za-z0-9\-\._:]+)", re.I)
SYS_NOTES_RE = re.compile(r"<SYSTEM_NOTES>([\s\S]*?)</SYSTEM_NOTES>", re.I)
# Fallback matchers across whole output
# 1) Prefer 6-digit seq directly after ack:, regardless of surrounding chars
ANY_ACK_SEQ_RE = re.compile(r"(?i)ack\s*:\s*(\d{6})")
# 2) General token (more permissive), allow preceding whitespace, '[' or '<'
ANY_ACK_RE = re.compile(r"(?i)(?:^|[\s\[<])ack\s*:\s*([A-Za-z0-9\-\._:]+)")

def find_acks_from_output(output: str) -> Tuple[List[str], List[str]]:
    """Return (acks, nacks) tokens detected in CLI output.
    - Prefer tokens inside <SYSTEM_NOTES>…</SYSTEM_NOTES>
    - Fallback: accept bare "ack: <token>" anywhere in output (some CLIs omit SYSTEM_NOTES)
    """
    notes = SYS_NOTES_RE.findall(output)
    acks, nacks = [], []
    for nt in notes:
        acks  += ACK_RE.findall(nt)
        nacks += NACK_RE.findall(nt)
    # Fallback: scan whole output for ack seq first, then general tokens
    # This helps when SYSTEM_NOTES closing tag is malformed and ack is adjacent to '>'
    acks += ANY_ACK_SEQ_RE.findall(output)
    acks += ANY_ACK_RE.findall(output)
    return list(set(acks)), list(set(nacks))

def new_mid(prefix="cccc") -> str:
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:6]}"

def wrap_with_mid(payload: str, mid: str) -> str:
    """Insert a MID marker after the first recognized opening tag.
    Recognized tags: <TO_PEER>, <FROM_USER>, <FROM_PeerA>, <FROM_PeerB>, <FROM_SYSTEM>
    If none present, prefix the payload with the marker.
    """
    marker = f"[MID: {mid}]"
    # Allowed opening tags regex
    import re
    rx = re.compile(r"<(\s*(TO_PEER|FROM_USER|FROM_PeerA|FROM_PeerB|FROM_SYSTEM)\s*)>", re.I)
    m = rx.search(payload)
    if m:
        start, end = m.span()
        return payload[:end] + "\n" + marker + payload[end:]
    return marker + "\n" + payload

# --- Main entry: deliver (queue if busy); wait for ACK when enabled ---
def deliver_or_queue(home: Path, pane: str, peer: str, payload: str,
                     profile: Dict[str,Any], delivery_conf: Dict[str,Any],
                     mid: Optional[str] = None) -> Tuple[str, str]:
    """
    Return (status, mid) where status in {"delivered","queued","failed"}
    """
    judge = PaneIdleJudge(profile)
    outbox = Outbox(home, peer)

    max_wait = float(delivery_conf.get("paste_max_wait_seconds", 6))
    interval = float(delivery_conf.get("recheck_interval_seconds", 0.6))
    require_ack = bool(delivery_conf.get("require_ack", False))

    t0 = time.time()
    while time.time() - t0 < max_wait:
        idle, reason = judge.refresh(pane)
        if idle:
            mid = mid or new_mid()
            text = wrap_with_mid(payload, mid)
            send_text(pane, text, profile)
            # Brief wait for receiver ACK (do not block too long)
            time.sleep(1.2)
            latest = capture_pane(pane, 1200)
            acks, _ = find_acks_from_output(latest)
            if mid in acks:
                return "delivered", mid
            else:
                if require_ack:
                    # No immediate ACK → enqueue for background flush
                    outbox.enqueue(mid, text)
                    return "queued", mid
                # ACK not required → treat as delivered
                return "delivered", mid
        time.sleep(interval)

    # Timeout while still not idle → perform one best-effort paste, then follow require_ack semantics
    mid = mid or new_mid()
    text = wrap_with_mid(payload, mid)
    send_text(pane, text, profile)
    if require_ack:
        # Do not block for ACK; enqueue so the background flusher can detect ACKs
        outbox.enqueue(mid, text)
        return "queued", mid
    return "delivered", mid

def flush_outbox_if_idle(home: Path, pane: str, peer: str,
                         profile: Dict[str,Any], delivery_conf: Dict[str,Any]) -> List[str]:
    """If idle, flush up to N queued items; return list of ACKed mids."""
    judge = PaneIdleJudge(profile)
    outbox = Outbox(home, peer)
    require_ack = bool(delivery_conf.get("require_ack", False))
    if not require_ack:
        # If ACK is not required, nothing to flush
        return []
    batch = int(delivery_conf.get("max_flush_batch", 3))

    idle, _ = judge.refresh(pane)
    if not idle: return []

    items = outbox.load_all()
    if not items: return []
    sent_mids=[]
    for it in items[:batch]:
        mid = it["mid"]; text = it["payload"]
        send_text(pane, text, profile)
        time.sleep(1.0)
        latest = capture_pane(pane, 1200)
        acks, nacks = find_acks_from_output(latest)
        if mid in acks:
            outbox.remove(mid); sent_mids.append(mid)
        elif mid in nacks:
            outbox.remove(mid)  # Drop and account (caller may record reason)
        else:
            # Keep in queue; try later
            pass
    return sent_mids
