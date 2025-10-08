# -*- coding: utf-8 -*-
"""
CCCC Orchestrator (tmux + long‑lived CLI sessions)
- Left/right panes run PeerA and PeerB interactive sessions (actors are bound at startup).
- Uses tmux to paste messages and capture output, parses <TO_USER>/<TO_PEER>, and runs optional lint/tests before committing.
- Injects a minimal SYSTEM prompt at startup (from prompt_weaver); runtime hot‑reload is removed for simplicity and control.
"""
import os, re, sys, json, time, shlex, tempfile, fnmatch, subprocess, select, hashlib, io, shutil, random
from datetime import datetime, timedelta
# POSIX file locking for cross-process sequencing; gracefully degrade if unavailable
try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore
from glob import glob
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from delivery import deliver_or_queue, flush_outbox_if_idle, PaneIdleJudge, new_mid, wrap_with_mid, send_text, find_acks_from_output
from common.config import load_profiles, ensure_env_vars
from mailbox import ensure_mailbox, MailboxIndex, scan_mailboxes, reset_mailbox
from por_manager import ensure_por, por_path, por_status_snapshot, read_por_text

ANSI_RE = re.compile(r"\x1b\[.*?m|\x1b\[?[\d;]*[A-Za-z]")  # strip ANSI color/control sequences
# Console echo of AI output blocks. Default OFF to avoid disrupting typing.
CONSOLE_ECHO = False
# legacy patch/diff handling removed
SECTION_RE_TPL = r"<\s*{tag}\s*>([\s\S]*?)</\s*{tag}\s*>"
INPUT_END_MARK = "[CCCC_INPUT_END]"

# Aux helper state
# Aux on/off is derived from presence of roles.aux.actor; no explicit mode set
AUX_WORK_ROOT_NAME = "aux_sessions"

# ---------- REV state helpers (lightweight) ----------
INSIGHT_BLOCK_RE = re.compile(r"```\s*insight\s*([\s\S]*?)```", re.I)

# --- inbox/nudge settings (read at startup from cli_profiles.delivery) ---
MB_PULL_ENABLED = True
INBOX_DIRNAME = "inbox"
PROCESSED_RETENTION = 200
NUDGE_RESEND_SECONDS = 90
NUDGE_JITTER_PCT = 0.0
SOFT_ACK_ON_MAILBOX_ACTIVITY = False
INBOX_STARTUP_POLICY = "resume"  # resume | discard | archive
INBOX_STARTUP_PROMPT = False
# Progress-aware NUDGE coalescing (single-flight)
NUDGE_DEBOUNCE_MS = 1500.0
NUDGE_PROGRESS_TIMEOUT_S = 45.0
NUDGE_KEEPALIVE = True
NUDGE_BACKOFF_BASE_MS = 1000.0
NUDGE_BACKOFF_MAX_MS = 60000.0
NUDGE_MAX_RETRIES = 1.0  # allow at most one resend (0 = never resend)
# Debug: reduce ledger noise for outbox enqueue diagnostics
OUTBOX_DEBUG = False
# Debug: keepalive skip reasons are high-frequency; gate behind this flag
KEEPALIVE_DEBUG = False

def _append_suffix_inside(payload: str, suffix: str) -> str:
    """Append a short suffix to the end of the main body inside the outermost tag, if present.
    If no XML-like wrapper is present, append to the end.
    """
    if not suffix or not payload:
        return payload
    try:
        idx = payload.rfind("</")
        if idx >= 0:
            head = payload[:idx].rstrip()
            tail = payload[idx:]
            sep = "" if head.endswith(suffix) else (" " if not head.endswith(" ") else "")
            return head + sep + suffix + "\n" + tail
        # no wrapper; append at end
        sep = "" if payload.rstrip().endswith(suffix) else (" " if not payload.rstrip().endswith(" ") else "")
        return payload.rstrip() + sep + suffix
    except Exception:
        return payload

def _plain_text_without_tags_and_mid(s: str) -> str:
    try:
        # Remove MID markers and XML-like tags so we can judge real content
        s2 = re.sub(r"\[\s*MID\s*:[^\]]+\]", " ", s, flags=re.I)
        s2 = re.sub(r"<[^>]+>", " ", s2)
        # Collapse whitespace
        s2 = re.sub(r"\s+", " ", s2)
        return s2.strip()
    except Exception:
        return s

def _send_raw_to_cli(home: Path, receiver_label: str, text: str,
                     left_pane: str, right_pane: str):
    """Direct passthrough: send raw text to CLI without any wrappers/MID (tmux paste)."""
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    # tmux direct paste
    if receiver_label == 'PeerA':
        tmux_paste(left_pane, text)
    else:
        tmux_paste(right_pane, text)
    print(f"[RAW] → {receiver_label} @ {ts}: {text[:80]}")

def run(cmd: str, *, cwd: Optional[Path]=None, timeout: int=600) -> Tuple[int,str,str]:
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(cwd) if cwd else None)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill(); return 124, "", "Timeout"
    return p.returncode, out, err

def _peer_folder_name(label: str) -> str:
    return "peerA" if label == "PeerA" else "peerB"

def _inbox_dir(home: Path, receiver_label: str) -> Path:
    return home/"mailbox"/_peer_folder_name(receiver_label)/INBOX_DIRNAME

def _processed_dir(home: Path, receiver_label: str) -> Path:
    return home/"mailbox"/_peer_folder_name(receiver_label)/"processed"

def _short_sha(text: str) -> str:
    try:
        return hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()[:8]
    except Exception:
        return f"{int(time.time())}"

def _next_seq_for_inbox(inbox: Path, processed: Path) -> str:
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
    return f"{current+1:06d}"

def _format_local_ts() -> str:
    dt = datetime.now().astimezone()
    tzname = dt.tzname() or ""
    off = dt.utcoffset() or timedelta(0)
    total = int(off.total_seconds())
    sign = '+' if total >= 0 else '-'
    total = abs(total)
    hh = total // 3600
    mm = (total % 3600) // 60
    offset_str = f"UTC{sign}{hh:02d}:{mm:02d}"
    main = dt.strftime("%Y-%m-%d %H:%M:%S")
    return f"{main} {tzname} ({offset_str})" if tzname else f"{main} ({offset_str})"

def _compose_detailed_nudge(seq: str, preview: str, inbox_path: str, *, suffix: str = "") -> str:
    """Compose a one-line, state-anchored NUDGE with TS + trigger + preview.
    Keeps action instruction stable; optional suffix appended at the end.
    """
    ts = _format_local_ts()
    msg = (
        f"[NUDGE] [TS: {ts}] trigger={seq} preview='{preview}' — "
        f"Inbox: {inbox_path} — open oldest first, process oldest→newest."
    )
    if suffix:
        msg = msg + " " + suffix.strip()
    return msg

def _safe_headline(path: Path, *, max_bytes: int = 4096, max_chars: int = 32) -> str:
    """Extract a short, printable first-line preview from a mailbox file.
    - Reads up to max_bytes, decodes as UTF-8 with replacement.
    - Skips wrapper/fence lines (e.g., <TO_*> or ```... ).
    - Strips control characters and collapses whitespace.
    - Returns at most max_chars; appends an ellipsis when truncated.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(max(512, int(max_bytes)))
        text = raw.decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in text.splitlines()]
        # helper: skip wrappers/fences/empty
        def is_wrapped(ln: str) -> bool:
            if not ln:
                return True
            if ln.startswith("<") and ln.endswith(">"):
                return True
            if ln.startswith("```"):
                return True
            # Skip runtime markers injected at file head
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
        # remove C0 controls except tab/space
        head = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", head)
        # zero-width characters
        head = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", head)
        # collapse whitespace
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

    # Per-peer lock + counter file to avoid duplicate sequence numbers under concurrency
    peer = _peer_folder_name(receiver_label)
    lock_path = state/f"inbox-seq-{peer}.lock"
    counter_path = state/f"inbox-seq-{peer}.txt"

    def _compute_next_seq() -> int:
        # If a counter exists, trust it; else derive from current max of inbox+processed
        try:
            val = int(counter_path.read_text(encoding="utf-8").strip())
            return val + 1
        except Exception:
            pass
        # Fallback to directory scan
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

    # Acquire exclusive lock if available
    if fcntl is not None:
        with open(lock_path, "w") as lf:  # lock file handle lifetime holds the lock
            try:
                fcntl.flock(lf, fcntl.LOCK_EX)
            except Exception:
                pass
            seq_int = _compute_next_seq()
            seq = f"{seq_int:06d}"
            fpath = inbox/f"{seq}.{mid}.txt"
            try:
                fpath.write_text(_inject_ts_after_mid(payload), encoding='utf-8')
            except Exception as e:
                raise RuntimeError(f"write inbox failed: {e}")
            # Persist the last-used sequence for the next writer
            try:
                with open(counter_path, "w", encoding="utf-8") as cf:
                    cf.write(str(seq_int))
                    try:
                        cf.flush(); os.fsync(cf.fileno())
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                fcntl.flock(lf, fcntl.LOCK_UN)
            except Exception:
                pass
    else:
        # Fallback (non-POSIX): best-effort using a temp marker directory as a mutex
        lock_dir = state/f"inbox-seq-{peer}.lckdir"
        acquired = False
        for _ in range(50):
            try:
                lock_dir.mkdir(exist_ok=False)
                acquired = True
                break
            except Exception:
                time.sleep(0.01)
        try:
            seq_int = _compute_next_seq()
            seq = f"{seq_int:06d}"
            fpath = inbox/f"{seq}.{mid}.txt"
            try:
                fpath.write_text(_inject_ts_after_mid(payload), encoding='utf-8')
            except Exception as e:
                raise RuntimeError(f"write inbox failed: {e}")
            try:
                counter_path.write_text(str(seq_int), encoding='utf-8')
            except Exception:
                pass
        finally:
            if acquired:
                try:
                    lock_dir.rmdir()
                except Exception:
                    pass
    return seq, fpath

# ---------- POR auto‑diff helper ----------
# POR.new.md auto-diff has been removed by design. Keep no-op placeholders if needed in the future.
    return seq, fpath

def _nudge_state_path(home: Path, receiver_label: str) -> Path:
    peer = _peer_folder_name(receiver_label)
    return home/"state"/f"nudge.{peer}.json"

def _load_nudge_state(home: Path, receiver_label: str) -> Dict[str, Any]:
    p = _nudge_state_path(home, receiver_label)
    try:
        st = json.loads(p.read_text(encoding='utf-8'))
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}

def _save_nudge_state(home: Path, receiver_label: str, st: Dict[str, Any]):
    p = _nudge_state_path(home, receiver_label); p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass

def _nudge_mark_progress(home: Path, receiver_label: str, *, seq: Optional[str] = None):
    st = _load_nudge_state(home, receiver_label)
    st['inflight'] = False
    st['retries'] = 0
    st['last_progress_ts'] = time.time()
    if seq:
        st['last_ack_seq'] = str(seq)
    _save_nudge_state(home, receiver_label, st)

def _maybe_send_nudge(home: Path, receiver_label: str, pane: str,
                     profile: Dict[str,Any], *, force: bool = False, suffix: str = "",
                     custom_text: Optional[str] = None) -> bool:
    """Progress-aware, single-flight NUDGE sender. Returns True if a NUDGE was sent.
    No-miss guarantee: if no progress, keepalive resends with capped backoff.
    """
    st = _load_nudge_state(home, receiver_label)
    now = time.time()
    inflight = bool(st.get('inflight', False))
    last_sent = float(st.get('last_sent_ts') or 0.0)
    last_prog = float(st.get('last_progress_ts') or 0.0)
    retries = int(st.get('retries') or 0)
    # Current inbox count for this receiver (used to reset retry window when backlog grows)
    try:
        inbox_files_now = [f for f in _inbox_dir(home, receiver_label).iterdir() if f.is_file()]
        inbox_count_now = len(inbox_files_now)
    except Exception:
        inbox_files_now = []
        inbox_count_now = 0
    last_inbox_count = int(st.get('last_inbox_count') or 0)

    # Hard cap on number of resends (do not spam tmux)
    # If cap exceeded but backlog has grown since the last send, reset the window to allow one more nudge.
    # Additionally, if backlog is stuck (no growth) yet no progress has been observed for a while,
    # allow a stale resend after a minimum interval to avoid deadlock.
    try:
        if (not force) and inflight and (retries >= int(NUDGE_MAX_RETRIES)):
            if inbox_count_now > last_inbox_count:
                # Backlog increased → give another chance: reset inflight/retries
                inflight = False
                st['inflight'] = False
                st['retries'] = 0
            else:
                # Skip quietly; avoid high-frequency disk writes on no-op
                return False
    except Exception:
        pass

    # Debounce shortly after progress (drop nudges within this window)
    if (not force) and (now - last_prog) * 1000.0 < max(0.0, float(NUDGE_DEBOUNCE_MS)):
        # Debounce window: skip without persisting to reduce disk churn
        return False

    if inflight and not force:
        # If inflight but no progress for too long, allow a resend with backoff
        if (now - last_prog) >= max(1.0, float(NUDGE_PROGRESS_TIMEOUT_S)):
            # Exponential backoff; never more frequent than the legacy resend interval
            interval = min(float(NUDGE_BACKOFF_MAX_MS), float(NUDGE_BACKOFF_BASE_MS) * (2 ** max(0, retries))) / 1000.0
            min_legacy = max(1.0, float(NUDGE_RESEND_SECONDS))
            interval = max(interval, min_legacy)
            # Apply optional jitter to avoid synchronized reminders
            try:
                jpct = float(NUDGE_JITTER_PCT)
                if jpct and jpct > 0.0:
                    jig = 1.0 + random.uniform(-jpct, jpct)
                    interval = max(1.0, interval * jig)
            except Exception:
                pass
            if (now - last_sent) < interval:
                # Backoff window not yet elapsed: skip quietly
                return False
            # send keepalive
            st['retries'] = retries + 1
        else:
            # inflight and still within timeout → skip quietly
            return False

    # Build message (allow override text for immediate, stateful nudges)
    if custom_text and custom_text.strip():
        nmsg = custom_text.strip()
    else:
        # Default periodic nudge text (no dynamic inbox counts)
        try:
            inbox_path = _inbox_dir(home, receiver_label).as_posix()
        except Exception:
            inbox_path = ".cccc/mailbox/peerX/inbox"
        nmsg = (
            f"[NUDGE] Inbox: {inbox_path} — read the oldest message file, then move it to processed/. Repeat until inbox is empty."
        )
        if suffix:
            sfx = suffix.strip()
            if sfx:
                nmsg = nmsg + ' ' + sfx
    paste_when_ready(pane, profile, nmsg, timeout=6.0, poke=False)
    st['inflight'] = True
    st['last_sent_ts'] = now
    st['last_inbox_count'] = inbox_count_now
    _save_nudge_state(home, receiver_label, st)
    return True

def _compose_nudge_suffix_for(peer_label: str,
                              *, profileA: Dict[str,Any], profileB: Dict[str,Any], aux_mode: str,
                              aux_invoke: str = "") -> str:
    """Compose the trailing NUDGE suffix shown to the agent.
    - Always include the role's configured nudge suffix (base).
    - When Aux is ON and an invoke template is available, add exactly one
      concise Aux line that embeds the raw invoke template (agent-facing):
        "Aux is ON — delegate decoupled sub-tasks; just invoke: <template>; capture evidence and summarize outcome."
      Note: {prompt} must remain literal in the template.
    """
    base = ((profileA.get('nudge_suffix') if peer_label == 'PeerA' else profileB.get('nudge_suffix')) or '').strip()
    aux_line = ""
    if aux_mode == "on" and str(aux_invoke or '').strip():
        tpl = str(aux_invoke).replace('{prompt}', '{prompt}')
        aux_line = f"Aux is ON — delegate decoupled sub-tasks; just invoke: {tpl}; capture evidence and summarize outcome."
    combined = " ".join(filter(None, [base, aux_line]))
    return combined.strip()

def _send_nudge(home: Path, receiver_label: str, seq: str, mid: str,
                left_pane: str, right_pane: str,
                profileA: Dict[str,Any], profileB: Dict[str,Any],
                aux_mode: str = "off"):
    # Resolve Aux invoke template on demand to avoid relying on outer scope variables
    aux_invoke_tpl = ""
    try:
        from common.config import load_profiles as _lp  # late import; cheap read
        aux_inv = ((_lp(home).get('aux') or {}).get('invoke_command') or '').strip()
        aux_invoke_tpl = aux_inv
    except Exception:
        aux_invoke_tpl = ""
    combined_suffix = _compose_nudge_suffix_for(receiver_label, profileA=profileA, profileB=profileB, aux_mode=aux_mode, aux_invoke=aux_invoke_tpl)
    # Compose state-anchored one‑liner with trigger + preview
    try:
        inbox = _inbox_dir(home, receiver_label)
        # Find the path of the triggering file by seq
        trigger_file = None
        for f in sorted(inbox.iterdir(), key=lambda p: p.name):
            if f.name.startswith(str(seq)):
                trigger_file = f; break
        preview = _safe_headline(trigger_file) if trigger_file else "[unreadable-or-binary]"
    except Exception:
        preview = "[unreadable-or-binary]"
    custom = _compose_detailed_nudge(seq, preview, inbox.as_posix() if 'inbox' in locals() else ".cccc/mailbox/peerX/inbox", suffix=combined_suffix)
    # Always send via tmux injection (delivery_mode 'bridge' removed)
    if receiver_label == 'PeerA':
        _maybe_send_nudge(home, 'PeerA', left_pane, profileA, custom_text=custom, force=True)
    else:
        _maybe_send_nudge(home, 'PeerB', right_pane, profileB, custom_text=custom, force=True)

def _archive_inbox_entry(home: Path, receiver_label: str, token: str):
    # token may be seq (000123) or mid; prefer seq match first
    inbox = _inbox_dir(home, receiver_label)
    proc = _processed_dir(home, receiver_label)
    target: Optional[Path] = None
    # Try matching by 6-digit seq; if token is digits use it, else search within token
    seq_pat = None
    if token.isdigit():
        seq_pat = token
    else:
        import re as _re
        m = _re.search(r"(\d{6,})", token)
        if m:
            seq_pat = m.group(1)[:6]
    if seq_pat:
        for f in sorted(inbox.iterdir()):
            if f.name.startswith(seq_pat):
                target = f
                break
    if target is None:
        # try by mid
        for f in sorted(inbox.iterdir()):
            if f".{token}." in f.name:
                target = f; break
    if target is None:
        return False
    try:
        proc.mkdir(parents=True, exist_ok=True)
        target.rename(proc/target.name)
    except Exception:
        return False
    # enforce retention
    try:
        files = sorted(proc.iterdir(), key=lambda p: p.name)
        if len(files) > PROCESSED_RETENTION:
            remove_n = len(files) - PROCESSED_RETENTION
            for f in files[:remove_n]:
                try: f.unlink()
                except Exception: pass
    except Exception:
        pass
    return True

def ensure_bin(name: str):
    code,_,_ = run(f"command -v {shlex.quote(name)}")
    if code != 0:
        print(f"[FATAL] Executable required: {name}")
        raise SystemExit(1)
def has_bin(name: str) -> bool:
    code,_,_ = run(f"command -v {shlex.quote(name)}"); return code==0

def ensure_git_repo():
    code, out, _ = run("git rev-parse --is-inside-work-tree")
    if code != 0 or "true" not in out:
        print("[INFO] Not a git repository; initializing …")
        run("git init")
        # Ensure identity to avoid commit failures on fresh repos
        code_email, out_email, _ = run("git config --get user.email")
        code_name,  out_name,  _ = run("git config --get user.name")
        if code_email != 0 or not out_email.strip():
            run("git config user.email cccc-bot@local")
        if code_name != 0 or not out_name.strip():
            run("git config user.name CCCC Bot")
        run("git add -A")
        run("git commit -m 'init' || true")
    else:
        # Ensure identity to avoid commit failures on existing repos
        code_email, out_email, _ = run("git config --get user.email")
        code_name,  out_name,  _ = run("git config --get user.name")
        if code_email != 0 or not out_email.strip():
            run("git config user.email cccc-bot@local")
        if code_name != 0 or not out_name.strip():
            run("git config user.name CCCC Bot")

def strip_ansi(s: str) -> str: return ANSI_RE.sub("", s)
def parse_section(text: str, tag: str) -> str:
    m = re.search(SECTION_RE_TPL.format(tag=tag), text, re.I)
    return (m.group(1).strip() if m else "")

## Legacy diff/patch helpers removed (extract_patches/normalize/inline detection)

# ---------- handoff anti-loop ----------
def _read_json_safe(p: Path) -> Dict[str,Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _write_json_safe(p: Path, obj: Dict[str,Any]):
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _normalize_signal_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[\s]+", " ", s)
    s = re.sub(r"[\[\]\(\)\{\}\-_=+~`'\".,;:!?|/\\]", "", s)
    return s.strip()

def _tokenize_for_similarity(s: str) -> List[str]:
    """Lightweight tokenizer for redundancy detection: words >= 3 chars."""
    s = s.lower()
    # remove mid markers and xml tags
    s = re.sub(r"\[mid:[^\]]+\]", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    # collapse spaces
    s = re.sub(r"\s+", " ", s)
    toks = re.findall(r"[a-z0-9_\-/]{3,}", s)
    return toks[:5000]

def _jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb) or 1
    return inter / union

def _word_count(s: str) -> int:
    return len([w for w in re.split(r"\s+", s.strip()) if w])

def is_high_signal(text: str, policies: Dict[str,Any]) -> bool:
    cfg = (policies.get("handoff_filter") or {}) if isinstance(policies.get("handoff_filter"), dict) else {}
    t = text.strip()
    if not t:
        return False
    # obvious high-signal: explicit sections, substantial content, questions
    boosts_k = [k.lower() for k in (cfg.get("boost_keywords_any") or [])]
    boosts_r = cfg.get("boost_regexes") or []
    tl = t.lower()
    if any(k in tl for k in boosts_k):
        return True
    if any(re.search(rx, t, re.I) for rx in boosts_r):
        return True
    # questions or long content can be high-signal
    if '?' in t:
        return True
    if len(t) >= max(120, int(cfg.get("min_chars", 40)) * 3):
        return True
    if _word_count(t) >= max(25, int(cfg.get("min_words", 6)) * 3):
        return True
    return False

def is_low_signal(text: str, policies: Dict[str,Any]) -> bool:
    cfg = (policies.get("handoff_filter") or {}) if isinstance(policies.get("handoff_filter"), dict) else {}
    if not cfg.get("enabled", True):
        return False
    t = text.strip()
    if not t:
        return True
    # If high-signal, definitely not low-signal
    if is_high_signal(t, policies):
        return False
    min_chars = int(cfg.get("min_chars", 40))
    min_words = int(cfg.get("min_words", 6))
    is_short = len(t) < min_chars and _word_count(t) < min_words
    if not is_short:
        # not high-signal but also not short → don't flag as low-signal
        return False
    # If short, drop only when matches drop_regex and lacks any of the require_keywords
    drops = cfg.get("drop_regexes") or []
    drop_hit = any(re.search(rx, t, re.I) for rx in drops)
    if not drop_hit:
        return False
    req_k = [k.lower() for k in (cfg.get("require_keywords_any") or [])]
    if req_k:
        tl = t.lower()
        if any(k in tl for k in req_k):
            return False
    # short + drop pattern + no required keywords → low-signal
    return True

def should_forward(payload: str, sender: str, receiver: str, policies: Dict[str,Any], state_dir: Path, override_enabled: Optional[bool]=None) -> bool:
    cfg = (policies.get("handoff_filter") or {}) if isinstance(policies.get("handoff_filter"), dict) else {}
    enabled = bool(cfg.get("enabled", True)) if override_enabled is None else bool(override_enabled)
    if not enabled:
        return True
    # low signal filter
    if is_low_signal(payload, policies):
        return False
    # cooldown
    key = f"{sender}->{receiver}"
    guard_path = state_dir/"handoff_guard.json"
    guard = _read_json_safe(guard_path)
    now = time.time()
    last = (guard.get(key) or {}).get("last_ts", 0)
    cooldown = float(cfg.get("cooldown_seconds", 15))
    bypass_cool = bool(cfg.get("bypass_cooldown_when_high_signal", True))
    if now - last < cooldown:
        if bypass_cool and is_high_signal(payload, policies):
            pass
        else:
            return False
    # dedup short, low-signal repeats within a short window
    dups_path = state_dir/"handoff_dups.json"
    dups = _read_json_safe(dups_path)
    dedup_window = float(cfg.get("dedup_short_seconds", 30.0))
    dedup_keep = int(cfg.get("dedup_max_keep", 10))
    norm = _normalize_signal_text(payload)
    h = hashlib.sha1(norm.encode("utf-8", errors="ignore")).hexdigest()
    items = (dups.get(key) or [])
    items = [it for it in items if now - float(it.get("ts", 0)) <= dedup_window]
    min_chars = int(cfg.get("min_chars", 40)); min_words = int(cfg.get("min_words", 6))
    is_short = len(payload.strip()) < min_chars and _word_count(payload) < min_words
    if is_short and any(it.get("hash") == h for it in items):
        # duplicate short message → drop
        dups[key] = items
        _write_json_safe(dups_path, dups)
        return False
    # record current hash
    items.append({"hash": h, "ts": now})
    dups[key] = items[-dedup_keep:]
    _write_json_safe(dups_path, dups)

    # long-text redundancy suppression
    red_window = float(cfg.get("redundant_window_seconds", 120.0))
    red_thresh = float(cfg.get("redundant_similarity_threshold", 0.9))
    # load/keep a separate similarity log per direction
    sim_path = state_dir/"handoff_sim.json"
    sim = _read_json_safe(sim_path)
    sim_items = [it for it in (sim.get(key) or []) if now - float(it.get("ts",0)) <= red_window]
    toks_cur = _tokenize_for_similarity(payload)
    # high-signal bypass
    if not is_high_signal(payload, policies):
        for it in sim_items[-5:]:  # compare against last few
            simval = _jaccard(toks_cur, it.get("toks", []))
            if simval >= red_thresh:
                # drop redundant long content without new high-signal
                sim[key] = sim_items
                _write_json_safe(sim_path, sim)
                return False
    sim_items.append({"ts": now, "toks": toks_cur[:4000]})
    sim[key] = sim_items[-dedup_keep:]
    _write_json_safe(sim_path, sim)

    # update cooldown timestamp
    guard[key] = {"last_ts": now}
    _write_json_safe(guard_path, guard)
    return True

## Legacy diff helpers removed (count_changed_lines/extract_paths_from_patch)

# ---------- tmux ----------
def tmux(*args: str) -> Tuple[int,str,str]:
    return run("tmux " + " ".join(shlex.quote(a) for a in args))

def tmux_session_exists(name: str) -> bool:
    code,_,_ = tmux("has-session","-t",name); return code==0

def tmux_new_session(name: str) -> Tuple[str,str]:
    code,out,err = tmux("new-session","-d","-s",name,"-P","-F","#S:#I.#P")
    if code!=0: raise RuntimeError(f"tmux new-session failed: {err}")
    # Start with a single pane; rebuild layout as defined below
    code3,out3,_ = tmux("list-panes","-t",name,"-F","#P")
    panes = out3.strip().splitlines()
    return panes[0], panes[0]

def tmux_respawn_pane(pane: str, cmd: str):
    """Replace the running program in pane with given command (robust execution)."""
    tmux("respawn-pane", "-k", "-t", pane, cmd)

def _win(session: str) -> str:
    return f"{session}:0"

def _first_pane(session: str) -> str:
    target = _win(session)
    code,out,err = tmux("list-panes","-t",target,"-F","#{pane_id}")
    panes = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return panes[0] if panes else f"{target}.0"

def tmux_ensure_ledger_tail(session: str, ledger_path: Path):
    target = _win(session)
    code,out,_ = tmux("list-panes","-t",target,"-F","#P")
    panes = out.strip().splitlines()
    if len(panes) >= 3:
        return
    lp = shlex.quote(str(ledger_path))
    cmd = f"bash -lc 'printf \"[CCCC Ledger]\\n\"; tail -F {lp} 2>/dev/null || tail -f {lp}'"
    tp = _first_pane(session)
    tmux("split-window","-v","-t",tp, cmd)

def tmux_build_2x2(session: str) -> Dict[str,str]:
    """Build a stable 2x2 layout and map panes by coordinates {'lt','rt','lb','rb'}."""
    target = _win(session)
    # Clean start: keep only pane 0
    tmux("select-pane","-t",f"{target}.0")
    tmux("kill-pane","-a","-t",f"{target}.0")
    # Horizontal split to create two top panes
    rc,_,err = tmux("split-window","-h","-t",f"{target}.0")
    if rc != 0:
        print(f"[TMUX] split horizontal failed: {err.strip()}")
    tmux("select-layout","-t",target,"tiled")
    # Read coordinates and identify left/right top panes
    code,out,_ = tmux("list-panes","-t",target,"-F","#{pane_id} #{pane_left} #{pane_top}")
    panes=[]
    for ln in out.splitlines():
        try:
            pid, left, top = ln.strip().split()
            panes.append((pid, int(left), int(top)))
        except Exception:
            pass
    top_y = min(p[2] for p in panes)
    top_row = [p for p in panes if p[2] == top_y]
    top_row_sorted = sorted(top_row, key=lambda x: x[1])
    if len(top_row_sorted) < 2:
        # Fallback: use pane index mapping
        code2,out2,_ = tmux("list-panes","-t",target,"-F","#{pane_index} #{pane_id}")
        idx_to_id={}
        for ln in out2.splitlines():
            if not ln.strip():
                continue
            k,v=ln.split(" ",1); idx_to_id[int(k)]=v.strip()
        lt = idx_to_id.get(0); rt = idx_to_id.get(1)
    else:
        lt = top_row_sorted[0][0]
        rt = top_row_sorted[-1][0]
    # Vertical split on left/right to create bottom-left/bottom-right
    rc,_,err = tmux("split-window","-v","-t",lt)
    if rc != 0:
        print(f"[TMUX] split lt vertical failed: {err.strip()}")
    rc,_,err = tmux("split-window","-v","-t",rt)
    if rc != 0:
        print(f"[TMUX] split rt vertical failed: {err.strip()}")
    tmux("select-layout","-t",target,"tiled")
    # Finally list 4 panes and map by coordinates
    code,out,_ = tmux("list-panes","-t",target,"-F","#{pane_id} #{pane_left} #{pane_top}")
    panes=[]
    for ln in out.splitlines():
        try:
            pid, left, top = ln.strip().split()
            panes.append((pid, int(left), int(top)))
        except Exception:
            pass
    # Identify top/bottom rows
    min_top = min(p[2] for p in panes)
    max_top = max(p[2] for p in panes)
    top_panes = sorted([p for p in panes if p[2]==min_top], key=lambda x: x[1])
    bot_panes = sorted([p for p in panes if p[2]==max_top], key=lambda x: x[1])
    positions={
        'lt': top_panes[0][0] if len(top_panes)>0 else f"{target}.0",
        'rt': top_panes[-1][0] if len(top_panes)>0 else f"{target}.1",
        'lb': bot_panes[0][0] if len(bot_panes)>0 else f"{target}.2",
        'rb': bot_panes[-1][0] if len(bot_panes)>0 else f"{target}.3",
    }
    # Print pane list and coordinates for troubleshooting
    _,outp,_ = tmux("list-panes","-t",target,"-F","#{pane_id}:#{pane_left},#{pane_top},#{pane_right},#{pane_bottom}")
    print(f"[TMUX] panes: {outp.strip()}")
    return positions
def tmux_ensure_quadrants(session: str, ledger_path: Path):
    code,out,_ = tmux("list-panes","-t",session,"-F","#P")
    panes = out.strip().splitlines()
    if len(panes) < 3:
        tmux_ensure_ledger_tail(session, ledger_path)
        code,out,_ = tmux("list-panes","-t",session,"-F","#P")
        panes = out.strip().splitlines()
    if len(panes) == 3:
        bottom = panes[-1]
        help_text = (
            "[CCCC Controls]\n"
            "a: <text>  → PeerA    |  b: <text>  → PeerB\n"
            "both:/u: <text>       → both peers\n"
            "/pause | /resume      toggle handoff\n"
            "/refresh              re-inject SYSTEM\n"
            "q                      quit orchestrator\n"
        )
        cmd = f"bash -lc 'cat <<\'EOF\'\n{help_text}\nEOF; sleep 100000'"
        tmux("split-window","-h","-t",f"{session}.{bottom}","-p","50",cmd)

def sanitize_console(s: str) -> str:
    try:
        return s.encode("utf-8", "replace").decode("utf-8", "replace")
    except Exception:
        return s

def read_console_line(prompt: str) -> str:
    # Read console input robustly; guard against special sequences
    try:
        s = input(prompt)
    except Exception:
        s = sys.stdin.readline()
    return sanitize_console(s)

def read_console_line_timeout(prompt: str, timeout_sec: float) -> str:
    """Read a console line with timeout. Returns empty string on timeout.
    Avoids blocking CI/non-interactive runs. Uses select on POSIX stdin.
    """
    try:
        import select, sys
        sys.stdout.write(prompt)
        sys.stdout.flush()
        r, _, _ = select.select([sys.stdin], [], [], max(0.0, float(timeout_sec)))
        if r:
            line = sys.stdin.readline()
            return sanitize_console(line)
        return ""
    except Exception:
        # Fallback: no timeout-capable read; do a best-effort non-blocking attempt
        try:
            return input(prompt)
        except Exception:
            return ""


def tmux_paste(pane: str, text: str):
    # Write as binary; tolerate surrogate/escape sequences in input
    data = text.encode("utf-8", errors="replace")
    with tempfile.NamedTemporaryFile("wb", delete=False) as f:
        f.write(data); fname=f.name
    buf = f"buf-{int(time.time()*1000)}"
    tmux("load-buffer","-b",buf,fname)
    tmux("paste-buffer","-t",pane,"-b",buf)
    time.sleep(0.12)
    # Send a single Enter to avoid duplicate submissions
    tmux("send-keys","-t",pane,"Enter")
    tmux("delete-buffer","-b",buf)
    try: os.unlink(fname)
    except Exception: pass

def tmux_type(pane: str, text: str):
    # Keep for startup/emergency; normal sends go through delivery.send_text
    for line in text.splitlines():
        tmux("send-keys","-t",pane,"-l",line)
        tmux("send-keys","-t",pane,"Enter")

def tmux_capture(pane: str, lines: int=800) -> str:
    code,out,err = tmux("capture-pane","-t",pane,"-p","-S",f"-{lines}")
    return strip_ansi(out if code==0 else "")

def bash_ansi_c_quote(s: str) -> str:
    """Return a Bash ANSI-C quoted string: $'...'."""
    return "$'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"

def tmux_start_interactive(pane: str, cmd: str):
    # Robust: run command inside pane via bash -lc; enforce UTF-8 locale to avoid mojibake
    env_prefix = "LC_ALL=C.UTF-8 LANG=C.UTF-8"
    wrapped = f"bash -lc {shlex.quote(env_prefix + ' ' + cmd)}"
    tmux_respawn_pane(pane, wrapped)

def wait_for_ready(pane: str, profile: Dict[str,Any], *, timeout: float = 12.0, poke: bool = True) -> bool:
    """Wait until the pane appears idle (prompt+quiet or quiet-only).
    If poke is True, sends a single Enter after ~1.5s to coax a prompt; else never sends pre-Enter.
    """
    judge = PaneIdleJudge(profile)
    t0 = time.time(); poked = False
    while time.time() - t0 < timeout:
        idle, reason = judge.refresh(pane)
        if idle:
            return True
        # After 1.5s without prompt, send a newline to coax prompt (optional)
        if poke and (not poked) and (time.time() - t0 > 1.5):
            tmux("send-keys","-t",pane,"Enter")
            poked = True
        time.sleep(0.25)
    return False

def paste_when_ready(pane: str, profile: Dict[str,Any], text: str, *, timeout: float = 10.0, poke: bool = True):
    ok = wait_for_ready(pane, profile, timeout=timeout, poke=poke)
    if not ok:
        print(f"[WARN] Target pane not ready; pasting anyway (best-effort).")
    # Use delivery.send_text with per-CLI config (submit/newline keys)
    send_text(pane, text, profile)

# ---------- YAML & prompts ----------
def read_yaml(p: Path) -> Dict[str,Any]:
    if not p.exists(): return {}
    try:
        import yaml; return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except ImportError:
        d: Dict[str, Any] = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            # strip inline comments
            line = line.split('#', 1)[0].rstrip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            if not v.strip():
                # container keys like "peerA:" — ignore in fallback
                continue
            d[k.strip()] = v.strip().strip('"\'')
        return d

# Removed legacy file reader helper; config is loaded via read_yaml at startup.

# ---------- ledger & policies ----------
def log_ledger(home: Path, entry: Dict[str,Any]):
    state = home/"state"; state.mkdir(exist_ok=True)
    entry={"ts":time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
    with (state/"ledger.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps(entry,ensure_ascii=False)+"\n")


def outbox_write(home: Path, event: Dict[str,Any]) -> Dict[str,Any]:
    """Append a structured outbound event for bridges to consume.
    Returns the full event with id/ts populated.
    """
    state = home/"state"; state.mkdir(exist_ok=True)
    ev = dict(event)
    try:
        # Populate id/ts if missing
        if 'ts' not in ev:
            ev['ts'] = time.strftime('%Y-%m-%d %H:%M:%S')
        base = (ev.get('type') or '') + '|' + (ev.get('peer') or ev.get('from') or '') + '|' + (ev.get('text') or '')
        hid = hashlib.sha1(base.encode('utf-8','ignore')).hexdigest()[:12]
        ev.setdefault('id', hid)
    except Exception:
        ev.setdefault('id', str(int(time.time())))
        ev.setdefault('ts', time.strftime('%Y-%m-%d %H:%M:%S'))
    with (state/"outbox.jsonl").open('a', encoding='utf-8') as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    # Diagnostic: record enqueue (debug only)
    if OUTBOX_DEBUG:
        try:
            log_ledger(home, {"kind":"bridge-outbox-enqueued","type": ev.get('type'), "id": ev.get('id'), "chars": len(str(ev.get('text') or ''))})
        except Exception:
            pass
    return ev

## Legacy policy helper removed (allowed_by_policies)



def try_lint():
    LINT_CMD=os.environ.get("LINT_CMD","").strip()
    cmd = None
    if LINT_CMD:
        cmd = LINT_CMD
    else:
        # Auto-detect a lightweight linter if available; otherwise skip quietly
        if has_bin("ruff"):
            cmd = "ruff check"
        elif has_bin("eslint"):
            # Only run eslint if a config exists
            cfg_files = [
                ".eslintrc", ".eslintrc.json", ".eslintrc.js", ".eslintrc.cjs",
                ".eslintrc.yaml", ".eslintrc.yml"
            ]
            has_cfg = any(Path(p).exists() for p in cfg_files)
            if not has_cfg and Path("package.json").exists():
                try:
                    pj = json.loads(Path("package.json").read_text(encoding="utf-8"))
                    has_cfg = bool(pj.get("eslintConfig"))
                except Exception:
                    has_cfg = False
            if not has_cfg:
                print("[LINT] Skipped (eslint detected but no config)"); return
            cmd = "eslint . --max-warnings=0"
        else:
            print("[LINT] Skipped (no LINT_CMD and no ruff/eslint)"); return
    code,out,err=run(cmd)
    print("[LINT]", "OK" if code==0 else "FAIL")
    if out.strip(): print(out.strip())
    if err.strip(): print(err.strip())

def try_tests() -> bool:
    TEST_CMD=os.environ.get("TEST_CMD","").strip()
    cmd=None
    if TEST_CMD:
        cmd=TEST_CMD
    else:
        if has_bin("pytest"):
            # Only run pytest if tests exist
            py_patterns = ["tests/**/*.py", "test_*.py", "*_test.py"]
            has_tests = any(glob(p, recursive=True) for p in py_patterns)
            if has_tests:
                cmd="pytest -q"
            else:
                print("[TEST] Skipped (no pytest tests found)"); return True
        elif has_bin("npm") and Path("package.json").exists():
            try:
                pj = json.loads(Path("package.json").read_text(encoding="utf-8"))
                test_script = (pj.get("scripts") or {}).get("test")
                if not test_script:
                    print("[TEST] Skipped (package.json has no test script)"); return True
                # Skip the default placeholder script
                if "no test specified" in test_script:
                    print("[TEST] Skipped (default placeholder npm test script)"); return True
                cmd="npm test --silent"
            except Exception:
                print("[TEST] Skipped (failed to parse package.json)"); return True
        else:
            print("[TEST] Skipped (no TEST_CMD and no pytest/npm)"); return True
    code,out,err=run(cmd)
    ok=(code==0)
    print("[TEST]", "OK" if ok else "FAIL")
    if out.strip(): print(out.strip())
    if err.strip(): print(err.strip())
    return ok

## Legacy apply helpers removed (git apply precheck/apply)

def git_commit(msg: str):
    run("git add -A"); run(f"git commit -m {shlex.quote(msg)}")

# ---------- prompt weaving ----------
def weave_system(home: Path, peer: str) -> str:
    ensure_por(home)
    from prompt_weaver import weave_minimal_system_prompt, ensure_rules_docs
    try:
        ensure_rules_docs(home)
    except Exception:
        pass
    return weave_minimal_system_prompt(home, peer)

def weave_preamble_text(home: Path, peer: str) -> str:
    """Preamble for the very first user message (full SYSTEM)."""
    try:
        from prompt_weaver import weave_system_prompt
        ensure_por(home)
        return weave_system_prompt(home, peer)
    except Exception:
        # Fallback to minimal system if full generation fails
        return weave_system(home, peer)

DEFAULT_CONTEXT_EXCLUDES = [
    ".venv/**", "node_modules/**", "**/__pycache__/**", "**/*.pyc",
    ".tox/**", "dist/**", "build/**", ".mypy_cache/**"
]

def _matches_any(path: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)

def list_repo_files(policies: Dict[str,Any], limit:int=200)->str:
    code,out,_ = run("git ls-files")
    files = out.splitlines()
    context_conf = policies.get("context", {}) if isinstance(policies.get("context", {}), dict) else {}
    excludes = context_conf.get("exclude", DEFAULT_CONTEXT_EXCLUDES)
    max_items = int(context_conf.get("files_limit", limit))
    # Drop excluded patterns only; no diff/patch-based allowlist
    filtered = [p for p in files if not _matches_any(p, excludes)]
    return "\n".join(filtered[:max_items])

def context_blob(policies: Dict[str,Any], phase: str) -> str:
    # Present a compact policy snapshot (without any diff/patch settings)
    pol_view = {k: v for k, v in policies.items() if k not in ("patch_queue",)}
    return (f"# PHASE: {phase}\n# REPO FILES (partial):\n{list_repo_files(policies)}\n\n"
            f"# POLICIES:\n{json.dumps(pol_view, ensure_ascii=False)}\n")


# ---------- watcher ----------
# Note: runtime hot-reload of settings/prompts/personas removed for simplicity.

# ---------- EXCHANGE ----------
def print_block(title: str, body: str):
    """Optional console echo. Default is quiet to avoid interrupting typing.
    Content still goes to mailbox/ledger/panel; nothing is lost.
    """
    if not body.strip():
        return
    global CONSOLE_ECHO
    if not CONSOLE_ECHO:
        return
    print(f"\n======== {title} ========\n{body.strip()}\n")

def exchange_once(home: Path, sender_pane: str, receiver_pane: str, payload: str,
                  context: str, who: str, policies: Dict[str,Any], phase: str,
                  profileA: Dict[str,Any], profileB: Dict[str,Any], delivery_conf: Dict[str,Any],
                  deliver_enabled: bool=True,
                  dedup_peer: Optional[Dict[str,str]] = None):
    sender_profile = profileA if who=="PeerA" else profileB
    # Paste minimal message; no extra context/wrappers (caller supplies FROM_* tags)
    before_len = len(tmux_capture(sender_pane, lines=800))
    paste_when_ready(sender_pane, sender_profile, payload)
    # Wait for response: prefer <TO_USER>/<TO_PEER>, or idle prompt
    judge = PaneIdleJudge(sender_profile)
    start = time.time()
    timeout = float(delivery_conf.get("read_timeout_seconds", 8))
    window = ""
    while time.time() - start < timeout:
        content = tmux_capture(sender_pane, lines=800)
        window = content[before_len:]
        # Do not strip wrappers here; keep window for diagnostics (mailbox path does not rely on this)
        if ("<TO_USER>" in window) or ("<TO_PEER>" in window):
            break
        idle, _ = judge.refresh(sender_pane)
        if idle and time.time() - start > 1.2:
            break
        time.sleep(0.25)
    # Parse only output after the last INPUT to avoid picking up SYSTEM or our injected <TO_*>.
    # The 'window' slice is computed in the wait loop; parse the latest sections (window-only).
    def last(tag):
        items=re.findall(SECTION_RE_TPL.format(tag=tag), window, re.I)
        return (items[-1].strip() if items else "")
    to_user = last("TO_USER"); to_peer = last("TO_PEER");
    # Extract the last ```insight fenced block (no backward-compat for tags)
    def _last_insight(text: str) -> str:
        try:
            m = re.findall(r"```insight\s*([\s\S]*?)```", text, re.I)
            return (m[-1].strip() if m else "")
        except Exception:
            return ""
    # Note: insight is present in window for diagnostics only; forwarding uses mailbox path
    _insight_diag = _last_insight(window)
    # Do not print <TO_USER> here (the background poller will report it); focus on handoffs only

    # patch/diff scanning removed

    if to_peer.strip():
        # De-duplicate: avoid handing off the same content repeatedly
        if dedup_peer is not None:
            h = hashlib.sha1(to_peer.encode("utf-8", errors="replace")).hexdigest()
            key = f"{who}:to_peer"
            if dedup_peer.get(key) == h:
                pass
            else:
                dedup_peer[key] = h
        
        if not deliver_enabled:
            log_ledger(home, {"from": who, "kind": "handoff-skipped", "reason": "paused", "chars": len(to_peer)})
        else:
            # use inbox + nudge; wrap with outer source marker and append META as sibling block
            recv = "PeerB" if who == "PeerA" else "PeerA"
            outer = f"FROM_{who}"
            body = f"<{outer}>\n{to_peer}\n</{outer}>\n\n"
            if meta_tag and meta_text.strip():
                body += f"<{meta_tag}>\n{meta_text}\n</{meta_tag}>\n"
            mid = new_mid()
            text_with_mid = wrap_with_mid(body, mid)
            try:
                seq, _ = _write_inbox_message(home, recv, text_with_mid, mid)
                _send_nudge(home, recv, seq, mid, left, right, profileA, profileB,
                            aux_mode)
                try:
                    last_nudge_ts[recv] = time.time()
                except Exception:
                    pass
                status = "nudged"
            except Exception as e:
                status = f"failed:{e}"
                seq = "000000"
            log_ledger(home, {"from": who, "kind": "handoff", "status": status, "mid": mid, "seq": seq, "chars": len(to_peer)})
            print(f"[HANDOFF] {who} → {recv} ({len(to_peer)} chars, status={status}, seq={seq})")

def scan_and_process_after_input(home: Path, pane: str, other_pane: str, who: str,
                                 policies: Dict[str,Any], phase: str,
                                 profileA: Dict[str,Any], profileB: Dict[str,Any], delivery_conf: Dict[str,Any],
                                 deliver_enabled: bool, last_windows: Dict[str,int],
                                 dedup_user: Dict[str,str], dedup_peer: Dict[str,str]):
    # Capture the whole window and parse it to avoid TUI clear/echo policies causing length regressions/no growth
    content = tmux_capture(pane, lines=1000)
    # Record total length (diagnostic only), not a gating condition
    last_windows[who] = len(content)
    # Remove echoed [INPUT]...END sections we injected to avoid mis-parsing
    sanitized = re.sub(r"\[INPUT\][\s\S]*?"+re.escape(INPUT_END_MARK), "", content, flags=re.I)

    def last(tag):
        items=re.findall(SECTION_RE_TPL.format(tag=tag), sanitized, re.I)
        return (items[-1].strip() if items else "")
    to_user = last("TO_USER"); to_peer = last("TO_PEER")
    if to_user:
        h = hashlib.sha1(to_user.encode("utf-8", errors="replace")).hexdigest()
        key = f"{who}:to_user"
        if dedup_user.get(key) != h:
            dedup_user[key] = h
            to_user_print = (to_user[:2000] + ("\n…[truncated]" if len(to_user) > 2000 else ""))
            print_block(f"{who} → USER", to_user_print)
            log_ledger(home, {"from":who,"kind":"to_user","chars":len(to_user)})

    # patch/diff scanning removed

    if to_peer and to_peer.strip():
        h2 = hashlib.sha1(to_peer.encode("utf-8", errors="replace")).hexdigest()
        key2 = f"{who}:to_peer"
        if dedup_peer.get(key2) == h2:
            return
        dedup_peer[key2] = h2
        if not deliver_enabled:
            log_ledger(home, {"from": who, "kind": "handoff-skipped", "reason": "paused", "chars": len(to_peer)})
        else:
            if who == "PeerA":
                status, mid = deliver_or_queue(home, other_pane, "peerB", to_peer, profileB, delivery_conf)
            else:
                status, mid = deliver_or_queue(home, other_pane, "peerA", to_peer, profileA, delivery_conf)
            log_ledger(home, {"from": who, "kind": "handoff", "status": status, "mid": mid, "chars": len(to_peer)})
            print(f"[HANDOFF] {who} → {'PeerB' if who=='PeerA' else 'PeerA'} ({len(to_peer)} chars, status={status})")


# ---------- MAIN ----------
def main(home: Path):
    global CONSOLE_ECHO
    ensure_bin("tmux"); ensure_git_repo()
    # Directories
    settings = home/"settings"; state = home/"state"
    state.mkdir(exist_ok=True)
    # Note: rules are rebuilt after the roles wizard (post-binding) to reflect
    # the current Aux/IM state and avoid stale "Aux disabled" banners.
    # Reset preamble sent flags on each orchestrator start to ensure the first
    # user message per peer carries the preamble in this session.
    try:
        (state/"preamble_sent.json").write_text(json.dumps({"PeerA": False, "PeerB": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    policies = read_yaml(settings/"policies.yaml")
    governance_cfg = read_yaml(settings/"governance.yaml") if (settings/"governance.yaml").exists() else {}

    session  = f"cccc-{Path.cwd().name}"

    por_markdown = ensure_por(home)
    try:
        por_display_path = por_markdown.relative_to(Path.cwd())
    except ValueError:
        por_display_path = por_markdown
    por_update_last_request = 0.0

    def _perform_reset(mode: str, *, trigger: str, reason: str) -> str:
        mode_norm = (mode or "").lower()
        if mode_norm not in ("compact", "clear"):
            raise ValueError("reset mode must be compact or clear")
        ts = time.strftime('%Y-%m-%d %H:%M')
        if mode_norm == "compact":
            try:
                _send_raw_to_cli(home, 'PeerA', '/compact', left, right)
                _send_raw_to_cli(home, 'PeerB', '/compact', left, right)
            except Exception:
                pass
            try:
                sysA = weave_system(home, "peerA"); sysB = weave_system(home, "peerB")
                _send_handoff("System", "PeerA", f"<FROM_SYSTEM>\nManual compact at {ts}.\n{sysA}\n</FROM_SYSTEM>\n")
                _send_handoff("System", "PeerB", f"<FROM_SYSTEM>\nManual compact at {ts}.\n{sysB}\n</FROM_SYSTEM>\n")
            except Exception:
                pass
            log_ledger(home, {"from": "system", "kind": "reset", "mode": "compact", "trigger": trigger})
            write_status(deliver_paused)
            _request_por_refresh(f"reset-{mode_norm}", force=True)
            return "Manual compact executed"
        clear_msg = (
            "<FROM_SYSTEM>\nReset requested: treat this as a fresh exchange. Discard interim scratch context and rely on POR.md for direction.\n"
            "</FROM_SYSTEM>\n"
        )
        _send_handoff("System", "PeerA", clear_msg)
        _send_handoff("System", "PeerB", clear_msg)
        log_ledger(home, {"from": "system", "kind": "reset", "mode": "clear", "trigger": trigger})
        write_status(deliver_paused)
        _request_por_refresh(f"reset-{mode_norm}", force=True)
        return "Manual clear notice issued"

    conversation_cfg = governance_cfg.get("conversation") if isinstance(governance_cfg.get("conversation"), dict) else {}
    reset_cfg = conversation_cfg.get("reset") if isinstance(conversation_cfg.get("reset"), dict) else {}
    conversation_reset_policy = str(reset_cfg.get("policy") or "compact").strip().lower()
    if conversation_reset_policy not in ("compact", "clear"):
        conversation_reset_policy = "compact"
    try:
        conversation_reset_interval = int(reset_cfg.get("interval_handoffs") or 0)
    except Exception:
        conversation_reset_interval = 0

    default_reset_mode = conversation_reset_policy if conversation_reset_policy in ("compact", "clear") else "compact"

    aux_mode = "off"

    aux_last_reason = ""
    aux_last_reminder: Dict[str, float] = {"PeerA": 0.0, "PeerB": 0.0}
    aux_work_root = home/"work"/AUX_WORK_ROOT_NAME
    aux_work_root.mkdir(parents=True, exist_ok=True)

    def _aux_snapshot() -> Dict[str, Any]:
        return {
            "mode": aux_mode,
            "command": aux_command,
            "last_reason": aux_last_reason,
        }

    def _prepare_aux_bundle(reason: str, stage: str, peer_label: Optional[str], payload: Optional[str]) -> Optional[Path]:
        try:
            session_id = time.strftime("%Y%m%d-%H%M%S")
            if peer_label:
                session_id += f"-{peer_label.lower()}"
            session_path = aux_work_root/session_id
            session_path.mkdir(parents=True, exist_ok=True)
            try:
                por_snapshot = read_por_text(home)
            except Exception:
                por_snapshot = ""
            (session_path/"POR.md").write_text(por_snapshot, encoding="utf-8")
            details: List[str] = []
            details.append("# Aux Helper Context")
            details.append(f"Reason: {reason}")
            details.append(f"Stage: {stage}")
            if aux_command:
                details.append(f"Suggested command: {aux_command}")
            details.append("")
            details.append("## What you can do")
            details.append("- You may inspect repository files and `.cccc/work` artifacts as needed.")
            details.append("- Feel free to create additional notes or scratch files under `.cccc/work/` (e.g., run experiments, capture logs).")
            # No special change format required; peers validate via minimal checks/tests/logs.
            details.append("- Summarize findings, highlight risks, and propose concrete next steps for the peers.")
            details.append("")
            # Aux CLI examples - reflect current binding when available
            details.append(f"## Aux CLI examples (actor={(_resolve_bindings(home).get('aux_actor') or 'none')})")
            details.append("```bash")
            if aux_command:
                details.append("# Prompt with inline text")
                details.append(f"{aux_command.replace('{prompt}', 'Review the latest POR context and suggest improvements')}")
                details.append("# Point to specific files or directories")
                details.append(f"{aux_command.replace('{prompt}', '@docs/ @.cccc/work/aux_sessions/{session_id} Provide a review summary')}")
            else:
                details.append("# Aux not configured; select an Aux actor at startup to enable one-line invokes.")
            details.append("```")
            details.append("")
            details.append("## Data in this bundle")
            details.append("- `POR.md`: snapshot of the current Plan-of-Record.")
            details.append("- `peer_message.txt`: the triggering message or artifact from the peer.")
            details.append("- `notes.txt`: this instruction file.")
            (session_path/"notes.txt").write_text("\n".join(details), encoding="utf-8")
            if payload:
                (session_path/"peer_message.txt").write_text(payload, encoding="utf-8")
            return session_path
        except Exception:
            return None

    def _run_aux_cli(prompt: str) -> Tuple[int, str, str, str]:
        safe_prompt = prompt.replace('"', '\\"')
        template = aux_command_template  # no hard fallback to a specific actor/CLI
        if not template:
            # Aux not configured — explicit error instead of silently falling back
            return 1, "", "Aux is not configured (no actor bound or invoke_command missing).", ""
        if "{prompt}" in template:
            command = template.replace("{prompt}", safe_prompt)
        else:
            command = f"{template} {safe_prompt}"
        try:
            run_cwd = Path(aux_cwd) if aux_cwd else Path.cwd()
            if not run_cwd.is_absolute():
                run_cwd = Path.cwd()/run_cwd
            proc = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=str(run_cwd))
            return proc.returncode, proc.stdout, proc.stderr, command
        except Exception as exc:
            return 1, "", str(exc), command

    def _send_aux_reminder(reason: str, peers: Optional[List[str]] = None, *, stage: str = "manual", payload: Optional[str] = None, source_peer: Optional[str] = None):
        nonlocal aux_last_reason
        bundle_path = _prepare_aux_bundle(reason, stage, source_peer, payload)
        targets = peers or ["PeerA", "PeerB"]
        lines = ["Aux helper reminder.", f"Reason: {reason}."]
        if aux_command:
            lines.append(f"Run helper command: {aux_command}")
        else:
            lines.append("Aux not configured (no actor bound). Bind an Aux actor at next start to enable one-line invokes.")
        lines.append("You may inspect `.cccc/work` resources created for this session and perform extended analysis.")
        lines.append("Share verdict/actions/checks in your next response.")
        if bundle_path:
            lines.append(f"Context bundle: {bundle_path}")
        message = "\n".join(lines)
        for label in targets:
            payload = f"<FROM_SYSTEM>\n{message}\n</FROM_SYSTEM>\n"
            _send_handoff("System", label, payload)
            aux_last_reminder[label] = time.time()
        aux_last_reason = reason
        log_ledger(home, {"from": "system", "kind": "aux_reminder", "peers": targets, "reason": reason})
        write_status(deliver_paused)

    # Note: auto Aux trigger based on YAML payload has been removed. Use manual /review or one-off /c (Aux CLI).


    cli_profiles_path = settings/"cli_profiles.yaml"
    cli_profiles = read_yaml(cli_profiles_path)
    # --- Roles/Actors interactive binding (first thing; before load_profiles) ---
    def _write_yaml(p: Path, obj: Dict[str, Any]):
        try:
            import yaml  # type: ignore
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(yaml.safe_dump(obj, allow_unicode=True, sort_keys=False), encoding='utf-8')
        except Exception:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

    def _actors_available() -> List[str]:
        try:
            actors_doc = read_yaml(settings/"agents.yaml")
            acts = actors_doc.get('actors') if isinstance(actors_doc.get('actors'), dict) else {}
            return sorted(list(acts.keys()))
        except Exception:
            return []

    def _current_roles(cp: Dict[str, Any]) -> Tuple[str, str, str, str]:
        roles = cp.get('roles') if isinstance(cp.get('roles'), dict) else {}
        # Do not fall back to specific actors; reflect config as-is.
        pa = str(((roles.get('peerA') or {}).get('actor')) or '').strip()
        pb = str(((roles.get('peerB') or {}).get('actor')) or '').strip()
        ax = str(((roles.get('aux') or {}).get('actor')) or '').strip()
        aux_mode = 'on' if ax else 'off'
        return pa, pb, ax, aux_mode

    def _persist_roles(cp: Dict[str, Any], peerA_actor: str, peerB_actor: str, aux_actor: str, aux_mode: str):
        cp = dict(cp or {})
        roles = dict(cp.get('roles') or {})
        roles['peerA'] = dict(roles.get('peerA') or {})
        roles['peerB'] = dict(roles.get('peerB') or {})
        roles['aux']   = dict(roles.get('aux') or {})
        roles['peerA']['actor'] = peerA_actor
        roles['peerB']['actor'] = peerB_actor
        roles['aux']['actor']   = aux_actor
        roles['peerA'].setdefault('cwd','.')
        roles['peerB'].setdefault('cwd','.')
        roles['aux'].setdefault('cwd','.')
        cp['roles'] = roles
        _write_yaml(cli_profiles_path, cp)

    try:
        interactive = sys.stdin.isatty()
    except Exception:
        interactive = False
    if interactive:
        # Wizard config
        wiz = cli_profiles.get('roles_wizard') if isinstance(cli_profiles.get('roles_wizard'), dict) else {}
        wiz_enabled = bool(wiz.get('enabled', True))
        try:
            wiz_timeout = float(wiz.get('timeout_seconds', 10))
        except Exception:
            wiz_timeout = 10.0
        pa, pb, ax, aum = _current_roles(cli_profiles)
        acts = _actors_available()
        if wiz_enabled and acts:
            print("\n[ROLES] Current bindings:")
            print(f"  - PeerA: {pa}\n  - PeerB: {pb}\n  - Aux:   {ax if ax else 'none'}")
            ans = read_console_line_timeout(f"> Use previous bindings? Enter=yes / r=reconfigure [{int(wiz_timeout)}s]: ", wiz_timeout).strip().lower()
            if ans in ("r","reconf","reconfigure","no","n"):
                def _choose(label: str, options: list[str], allow_none: bool = False) -> str:
                    opts = list(options)
                    disp = [f"{i+1}) {name}" for i, name in enumerate(opts + (["none"] if allow_none else []))]
                    print(f"[ROLES] Options for {label}: ", ", ".join(disp))
                    while True:
                        sel = read_console_line(f"> Choose {label}: ").strip().lower()
                        if sel.isdigit():
                            idx = int(sel) - 1
                            if 0 <= idx < len(opts):
                                return opts[idx]
                            if allow_none and idx == len(opts):
                                return ''
                        if allow_none and sel in ("none","off"):
                            return ''
                        if sel in opts:
                            return sel
                        print("[HINT] Enter one of indices or names shown above.")
                # PeerA selection
                pa = _choose('PeerA', acts, allow_none=False)
                # PeerB selection (must differ)
                pb = _choose('PeerB', [x for x in acts if x != pa], allow_none=False)
                # Aux selection: can be none; must differ from A/B when set
                aux_choice = _choose('Aux', [x for x in acts if (x != pa and x != pb)], allow_none=True)
                ax = aux_choice  # '' means none/off
                aum = 'on' if ax else 'off'
                _persist_roles(cli_profiles, pa, pb, ax, aum)
                print(f"[ROLES] Saved: PeerA={pa} PeerB={pb} Aux={ax or 'none'}")
                cli_profiles = read_yaml(cli_profiles_path)
    # Rebuild rules once after bindings are finalized (either from wizard or existing config),
    # so that Aux mode and timestamps are accurate for this run.
    try:
        from prompt_weaver import rebuild_rules_docs  # type: ignore
        rebuild_rules_docs(home)
    except Exception:
        pass
    # Load roles + actors; ensure required env vars in memory (never persist)
    try:
        resolved = load_profiles(home)
        missing_env = ensure_env_vars(resolved.get('env_require') or [], prompt=True)
        if missing_env:
            log_ledger(home, {"kind":"missing-env", "keys": missing_env})
    except Exception as exc:
        print(f"[FATAL] config load failed: {exc}")
        raise SystemExit(1)
    try:
        from prompt_weaver import ensure_rules_docs  # type: ignore
        ensure_rules_docs(home)
    except Exception:
        pass

    # legacy _rewrite_aux_mode_block removed; aux on/off is derived from roles.aux.actor

    
    # Role profiles merged with actor IO settings
    profileA = (resolved.get('peerA') or {}).get('profile', {})
    profileB = (resolved.get('peerB') or {}).get('profile', {})
    delivery_conf = cli_profiles.get("delivery", {})
    try:
        SYSTEM_REFRESH_EVERY = int(delivery_conf.get("system_refresh_every_self_checks") or 3)
        if SYSTEM_REFRESH_EVERY <= 0:
            SYSTEM_REFRESH_EVERY = 3
    except Exception:
        SYSTEM_REFRESH_EVERY = 3
    # Delivery mode (tmux only). Legacy 'bridge' mode removed.
    # Delivery mode fixed to tmux (legacy bridge removed)
    # Source AUX template from bound actor (agents.yaml); role may override rate
    aux_resolved = resolved.get('aux') or {}
    aux_command_template = str(aux_resolved.get('invoke_command') or '').strip()
    aux_command = aux_command_template
    aux_cwd = str(aux_resolved.get('cwd') or '.')
    rate_limit_per_minute = int(aux_resolved.get("rate_limit_per_minute") or 2)
    if rate_limit_per_minute <= 0:
        rate_limit_per_minute = 1
    aux_min_interval = 60.0 / rate_limit_per_minute
    # Aux on/off is derived from presence of roles.aux.actor (no separate mode flag)
    aux_mode = "on" if str((aux_resolved.get('actor') or '')).strip() else "off"

    # Merge input_mode per peer if provided
    imodes = cli_profiles.get("input_mode", {}) if isinstance(cli_profiles.get("input_mode", {}), dict) else {}
    if imodes.get("peerA"):
        profileA["input_mode"] = imodes.get("peerA")
    if imodes.get("peerB"):
        profileB["input_mode"] = imodes.get("peerB")

    # Read debug echo switch (effective at startup)
    try:
        ce = cli_profiles.get("console_echo")
        if isinstance(ce, bool):
            global CONSOLE_ECHO
            CONSOLE_ECHO = ce
    except Exception:
        pass

    # Read inbox+NUDGE parameters (effective at startup)
    try:
        global MB_PULL_ENABLED, INBOX_DIRNAME, PROCESSED_RETENTION, NUDGE_RESEND_SECONDS, NUDGE_JITTER_PCT, SOFT_ACK_ON_MAILBOX_ACTIVITY
        MB_PULL_ENABLED = bool(delivery_conf.get("mailbox_pull_enabled", True))
        INBOX_DIRNAME = str(delivery_conf.get("inbox_dirname", "inbox"))
        PROCESSED_RETENTION = int(delivery_conf.get("processed_retention", 200))
        NUDGE_RESEND_SECONDS = float(delivery_conf.get("nudge_resend_seconds", 90))
        NUDGE_JITTER_PCT = float(delivery_conf.get("nudge_jitter_pct", 0.0) or 0.0)
        SOFT_ACK_ON_MAILBOX_ACTIVITY = bool(delivery_conf.get("soft_ack_on_mailbox_activity", False))
        INBOX_STARTUP_POLICY = str(delivery_conf.get("inbox_startup_policy", "resume") or "resume").strip().lower()
        INBOX_STARTUP_PROMPT = bool(delivery_conf.get("inbox_startup_prompt", False))
        # Progress-aware NUDGE parameters
        global NUDGE_DEBOUNCE_MS, NUDGE_PROGRESS_TIMEOUT_S, NUDGE_KEEPALIVE, NUDGE_BACKOFF_BASE_MS, NUDGE_BACKOFF_MAX_MS, NUDGE_MAX_RETRIES
        NUDGE_DEBOUNCE_MS = float(delivery_conf.get("nudge_debounce_ms", NUDGE_DEBOUNCE_MS))
        NUDGE_PROGRESS_TIMEOUT_S = float(delivery_conf.get("nudge_progress_timeout_s", NUDGE_PROGRESS_TIMEOUT_S))
        NUDGE_KEEPALIVE = bool(delivery_conf.get("nudge_keepalive", NUDGE_KEEPALIVE))
        NUDGE_BACKOFF_BASE_MS = float(delivery_conf.get("nudge_backoff_base_ms", NUDGE_BACKOFF_BASE_MS))
        NUDGE_BACKOFF_MAX_MS = float(delivery_conf.get("nudge_backoff_max_ms", NUDGE_BACKOFF_MAX_MS))
        try:
            NUDGE_MAX_RETRIES = float(delivery_conf.get("nudge_max_retries", NUDGE_MAX_RETRIES))
        except Exception:
            pass
    except Exception:
        pass

    # Lazy preamble (applies to both console input and mailbox-driven inbound)
    LAZY = (delivery_conf.get("lazy_preamble") or {}) if isinstance(delivery_conf.get("lazy_preamble"), dict) else {}
    LAZY_ENABLED = bool(LAZY.get("enabled", True))

    def _preamble_state_path() -> Path:
        return state/"preamble_sent.json"
    def _load_preamble_sent() -> Dict[str,bool]:
        try:
            return json.loads(_preamble_state_path().read_text(encoding="utf-8"))
        except Exception:
            return {"PeerA": False, "PeerB": False}
    def _save_preamble_sent(st: Dict[str,bool]):
        try:
            _preamble_state_path().write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    def _maybe_prepend_preamble_inbox(receiver_label: str):
        """If first user message for this peer arrived via mailbox inbox, prepend preamble in-file."""
        if not LAZY_ENABLED:
            return
        try:
            st = _load_preamble_sent()
            if bool(st.get(receiver_label)):
                return
            ib = _inbox_dir(home, receiver_label)
            files = sorted([f for f in ib.iterdir() if f.is_file()], key=lambda p: p.name)
            if not files:
                return
            target = files[0]
            try:
                body = target.read_text(encoding='utf-8')
            except Exception:
                return
            # Only modify <FROM_USER> payloads; otherwise keep as-is
            m = re.search(r"<\s*FROM_USER\s*>\s*([\s\S]*?)<\s*/FROM_USER\s*>", body, re.I)
            if not m:
                return
            peer_key = "peerA" if receiver_label == "PeerA" else "peerB"
            pre = weave_preamble_text(home, peer_key)
            # If preamble text already present (e.g., injected by adapter), skip to avoid duplication
            try:
                if pre and pre.strip() and (pre.strip() in body):
                    st[receiver_label] = True
                    _save_preamble_sent(st)
                    return
            except Exception:
                pass
            inner = m.group(1)
            combined = f"<FROM_USER>\n{pre}\n\n{inner.strip()}\n</FROM_USER>\n"
            target.write_text(combined, encoding='utf-8')
            st[receiver_label] = True
            _save_preamble_sent(st)
            log_ledger(home, {"from":"system","kind":"lazy-preamble-sent","peer":receiver_label, "route":"mailbox"})
        except Exception:
            pass

    # Prepare tmux session/panes
    if not tmux_session_exists(session):
        _,_ = tmux_new_session(session)
        # Ensure the detached session window uses our current terminal size (avoid 80x24 default)
        try:
            tsz = shutil.get_terminal_size(fallback=(160, 48))
            tmux("resize-window","-t",session,"-x",str(tsz.columns),"-y",str(tsz.lines))
        except Exception:
            pass
        pos = tmux_build_2x2(session)
        left,right = pos['lt'], pos['rt']
        (state/"session.json").write_text(json.dumps({"session":session,"left":left,"right":right,**pos}), encoding="utf-8")
    else:
        # Resize to current terminal as well to avoid stale small size from background server
        try:
            tsz = shutil.get_terminal_size(fallback=(160, 48))
            tmux("resize-window","-t",session,"-x",str(tsz.columns),"-y",str(tsz.lines))
        except Exception:
            pass
        pos = tmux_build_2x2(session)
        left,right = pos['lt'], pos['rt']
        (state/"session.json").write_text(json.dumps({"session":session,"left":left,"right":right,**pos}), encoding="utf-8")

    # Improve usability: larger history for all panes; keep mouse on but avoid binding wheel to copy-mode
    tmux("set-option","-g","mouse","on")
    # Let windows follow the size of the attached client aggressively
    tmux("set-window-option","-g","aggressive-resize","on")
    tmux("set-option","-g","history-limit","100000")
    # Optional: disable alternate-screen to keep scrollback (some CLIs toggle full-screen modes)
    try:
        tmux_cfg = cli_profiles.get("tmux", {}) if isinstance(cli_profiles.get("tmux", {}), dict) else {}
        if bool(tmux_cfg.get("alternate_screen_off", False)):
            tmux("set-option","-g","alternate-screen","off")
        else:
            tmux("set-option","-g","alternate-screen","on")
    except Exception:
        pass
    # Enable mouse wheel scroll for history while keeping send safety (we cancel copy-mode before sending)
    tmux("bind-key","-n","WheelUpPane","copy-mode","-e")
    tmux("bind-key","-n","WheelDownPane","send-keys","-M")
    print(f"[INFO] Using tmux session: {session} (left=PeerA / right=PeerB)")
    print(f"[INFO] pane map: left={left} right={right} lb={pos.get('lb')} rb={pos.get('rb')}")
    print(f"[TIP] In another terminal: `tmux attach -t {session}` to observe/input")
    # Ensure 2x2 layout: left/right=A/B; bottom-left=ledger tail; bottom-right=help
    # Start ledger and help panes at bottom-left/bottom-right
    lp = shlex.quote(str(state/"ledger.jsonl"))
    # Execute commands inside panes directly (more robust with respawn-pane)
    cmd_ledger_sh = f"bash -lc \"printf %s {bash_ansi_c_quote('[CCCC Ledger]\\n')}; tail -F {lp} 2>/dev/null || tail -f {lp}\""
    tmux_respawn_pane(pos['lb'], cmd_ledger_sh)
    # Bottom-right status panel: run built-in Python renderer to read ledger/status in real time
    py = shlex.quote(sys.executable or 'python3')
    status_py = shlex.quote(str(home/"panel_status.py"))
    cmd_status = f"bash -lc {shlex.quote(f'{py} {status_py} --home {str(home)} --interval 1.0')}"
    tmux_respawn_pane(pos['rb'], cmd_status)

    # IM command queue (bridge initiated)
    im_command_dir = state/"im_commands"
    im_command_processed = im_command_dir/"processed"
    try:
        im_command_dir.mkdir(parents=True, exist_ok=True)
        im_command_processed.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def _record_im_command_result(src_path: Path, request_id: str, result: Dict[str, Any]):
        try:
            im_command_processed.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        res_path = im_command_processed/f"{request_id}.result.json"
        tmp = res_path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(res_path)
        except Exception:
            pass
        archive_path = im_command_processed/f"{request_id}.req.json"
        try:
            src_path.replace(archive_path)
        except Exception:
            try:
                src_path.unlink()
            except Exception:
                pass

    def _process_im_commands():
        nonlocal aux_mode
        try:
            files = sorted(im_command_dir.glob("*.json"))
        except Exception:
            return
        for fp in files:
            try:
                data = json.loads(fp.read_text(encoding='utf-8'))
            except Exception:
                data = {}
            request_id = str(data.get("request_id") or fp.stem)
            command = str(data.get("command") or "").lower().strip()
            source = str(data.get("source") or "im")
            args = data.get("args") or {}
            result: Dict[str, Any] = {"ok": False, "message": "unsupported"}
            try:
                if command == "focus":
                    raw = str(args.get("raw") or "")
                    _request_por_refresh(f"focus-{source}", hint=raw or None, force=True)
                    result = {"ok": True, "message": "POR refresh requested."}
                elif command == "reset":
                    mode = str(args.get("mode") or default_reset_mode)
                    message = _perform_reset(mode, trigger=source, reason=f"{source}-{mode}")
                    result = {"ok": True, "message": message}
                elif command == "aux":
                    action = str(args.get("action") or "").lower()
                    if action in ("", "status"):
                        last = aux_last_reason or "-"
                        cmd_display = aux_command or "-"
                        result = {"ok": True, "message": f"Aux status: mode={aux_mode}, command={cmd_display}, last_reason={last}"}
                    else:
                        result = {"ok": False, "message": "unsupported (only status)"}
                elif command == "aux_cli":
                    prompt_text = str(args.get("prompt") or "").strip()
                    if not prompt_text:
                        result = {"ok": False, "message": "Aux CLI prompt is empty."}
                    else:
                        rc, out, err, cmd_line = _run_aux_cli(prompt_text)
                        summary_lines = [f"[Aux CLI] exit={rc}", f"command: {cmd_line}"]
                        if out:
                            summary_lines.append("stdout:\n" + out.strip())
                        if err:
                            summary_lines.append("stderr:\n" + err.strip())
                        summary = "\n".join(summary_lines)
                        # Limit message length to avoid IM overflow
                        if len(summary) > 3500:
                            summary = summary[:3490] + "..."
                        result = {"ok": rc == 0, "message": summary, "returncode": rc}
                elif command == "review":
                    _send_aux_reminder("manual-review")
                    result = {"ok": True, "message": "Aux review reminder triggered"}
                elif command == "passthrough":
                    peer = str(args.get("peer") or "").lower()
                    text = str(args.get("text") or "").strip()
                    if not text:
                        raise ValueError("empty command text")
                    if peer in ("a", "peera", "peer_a"):
                        labels = ["PeerA"]
                    elif peer in ("b", "peerb", "peer_b"):
                        labels = ["PeerB"]
                    elif peer in ("both", "ab", "ba"):
                        labels = ["PeerA", "PeerB"]
                    else:
                        raise ValueError("unknown peer")
                    for label in labels:
                        _send_raw_to_cli(home, label, text, left, right)
                        try:
                            log_ledger(home, {"from": source, "kind": "im-passthrough", "to": label, "chars": len(text)})
                        except Exception:
                            pass
                    msg = f"Command sent to {' & '.join(labels)}"
                    result = {"ok": True, "message": msg}
                else:
                    raise ValueError("unknown command")
            except Exception as exc:
                result = {"ok": False, "message": str(exc)}
            result.update({"command": command or "unknown", "request_id": request_id, "source": source, "ts": time.strftime('%Y-%m-%d %H:%M:%S')})
            _record_im_command_result(fp, request_id, result)

    # PROJECT.md bootstrap branch: choose before starting the CLIs
    project_md_path = Path.cwd()/"PROJECT.md"
    project_md_exists = project_md_path.exists()
    start_mode = "has_doc" if project_md_exists else "ask"  # has_doc | ai_bootstrap | ask
    if not project_md_exists:
        print("\n[PROJECT] No PROJECT.md found. Choose:")
        print("  1) I will create PROJECT.md, then continue")
        print("  2) Start CLI and let AIs co-create PROJECT.md (only modify PROJECT.md)")
        while True:
            ans = read_console_line("> Enter 1 or 2 and press Enter: ").strip().lower()
            if ans in ("1", "a", "user", "u"):
                print("[PROJECT] Waiting for PROJECT.md at repo root …")
                while not project_md_path.exists():
                    nxt = read_console_line("- After creation press Enter to continue; or enter 2 to switch to AI bootstrap: ").strip().lower()
                    if nxt in ("2", "b", "ai"):
                        start_mode = "ai_bootstrap"; break
                if project_md_path.exists():
                    start_mode = "has_doc"; project_md_exists = True
                break
            if ans in ("2", "b", "ai"):
                start_mode = "ai_bootstrap"; break
            print("[HINT] Enter 1 or 2.")

    # Start interactive CLIs (fallback to built-in Mock when not configured)
    # Build peer launch commands from actor templates (env can still override)
    pa_cmd = (resolved.get('peerA') or {}).get('command') or ''
    pb_cmd = (resolved.get('peerB') or {}).get('command') or ''
    CLAUDE_I_CMD = os.environ.get("CLAUDE_I_CMD") or pa_cmd or f"python {shlex.quote(str(home/'mock_agent.py'))} --role peerA"
    CODEX_I_CMD  = os.environ.get("CODEX_I_CMD")  or pb_cmd or f"python {shlex.quote(str(home/'mock_agent.py'))} --role peerB"
    if start_mode in ("has_doc", "ai_bootstrap"):
        # Wrap with role cwd when provided; tmux_start_interactive will add bash -lc
        def _wrap_cwd(cmd: str, cwd: str | None) -> str:
            if cwd and cwd not in (".", ""):
                return f"cd {cwd} && {cmd}"
            return cmd
        pa_cwd = (resolved.get('peerA') or {}).get('cwd') or '.'
        pb_cwd = (resolved.get('peerB') or {}).get('cwd') or '.'
        tmux_start_interactive(left, _wrap_cwd(CLAUDE_I_CMD, pa_cwd))
        print(f"[LAUNCH] PeerA mode=tmux pane={left} cmd={CLAUDE_I_CMD} cwd={pa_cwd}")
        tmux_start_interactive(right, _wrap_cwd(CODEX_I_CMD, pb_cwd))
        print(f"[LAUNCH] PeerB mode=tmux pane={right} cmd={CODEX_I_CMD} cwd={pb_cwd}")
        # Debug: show current commands per pane
        try:
            code,out,err = tmux('list-panes','-F','#{pane_id} #{pane_current_command}')
            if code == 0 and out.strip():
                print('[DEBUG] pane commands:\n' + out.strip())
        except Exception:
            pass
        # Define startup policy handling early to avoid late binding
        def _startup_handle_inbox(label: str, policy_override: Optional[str] = None):
            try:
                ensure_mailbox(home)
            except Exception:
                pass
            inbox = _inbox_dir(home, label)
            proc = _processed_dir(home, label)
            try:
                files = sorted([f for f in inbox.iterdir() if f.is_file()], key=lambda p: p.name)
            except FileNotFoundError:
                files = []
            if not files:
                return 0
            policy = (policy_override or INBOX_STARTUP_POLICY or "resume").strip().lower()
            if policy in ("discard", "archive"):
                moved = 0
                for f in files:
                    try:
                        proc.mkdir(parents=True, exist_ok=True)
                        f.rename(proc/f.name); moved += 1
                    except Exception:
                        pass
                try:
                    allp = sorted(proc.iterdir(), key=lambda p: p.name)
                    if len(allp) > PROCESSED_RETENTION:
                        for ff in allp[:len(allp)-PROCESSED_RETENTION]:
                            try: ff.unlink()
                            except Exception: pass
                except Exception:
                    pass
                log_ledger(home, {"from":"system","kind":"startup-inbox-discard","peer":label,"moved":moved})
                return moved
            log_ledger(home, {"from":"system","kind":"startup-inbox-resume","peer":label,"pending":len(files)})
            return len(files)
        # Startup policy: handle leftover inbox (always prompt; default applies after 30s of inactivity)
        try:
            ensure_mailbox(home)
            def _count_inbox(label: str) -> int:
                try:
                    ib = _inbox_dir(home, label)
                    return len([f for f in ib.iterdir() if f.is_file()])
                except Exception:
                    return 0
            cntA = _count_inbox("PeerA"); cntB = _count_inbox("PeerB")
            if (cntA > 0 or cntB > 0):
                chosen_policy = (INBOX_STARTUP_POLICY or "resume").strip().lower()
                # Interactive vs non-interactive: shorter timeout in non-interactive runs (avoid hanging CI)
                try:
                    is_interactive = sys.stdin.isatty()
                except Exception:
                    is_interactive = False
                t_conf = (cli_profiles.get("delivery", {}) or {})
                timeout_s = float(t_conf.get("inbox_startup_prompt_timeout_seconds", 30))
                timeout_nonint = float(t_conf.get("inbox_startup_prompt_noninteractive_timeout_seconds", 0))
                eff_timeout = timeout_s if is_interactive else timeout_nonint
                print("\n[INBOX] Residual inbox detected:")
                print(f"  - PeerA: {cntA} @ {str(_inbox_dir(home,'PeerA'))}")
                print(f"  - PeerB: {cntB} @ {str(_inbox_dir(home,'PeerB'))}")
                print(f"  Policy for this session: [r] resume  [a] archive  [d] discard; default: {chosen_policy}")
                if eff_timeout > 0:
                    print(f"  Will apply default policy {chosen_policy} after {int(eff_timeout)}s of inactivity.")
                ans = read_console_line_timeout("> Choose r/a/d and Enter (or Enter to use default): ", eff_timeout).strip().lower()
                if ans in ("r","resume"):
                    chosen_policy = "resume"
                elif ans in ("a","archive"):
                    chosen_policy = "archive"
                elif ans in ("d","discard"):
                    chosen_policy = "discard"
                else:
                    # Enter/timeout/invalid input → use default policy
                    pass
                print(f"[INBOX] Using policy: {chosen_policy}")
                _startup_handle_inbox("PeerA", chosen_policy)
                _startup_handle_inbox("PeerB", chosen_policy)
        except Exception as e:
            # Log only; do not block startup
            try:
                log_ledger(home, {"from":"system","kind":"startup-inbox-check-error","error":str(e)[:200]})
            except Exception:
                pass

        # Wait until both CLIs are ready (prompt + brief quiet period);
        # In pull+NUDGE mode, use a lower wait cap to avoid delaying the first NUDGE for too long
        sw = float(cli_profiles.get("startup_wait_seconds", 12))
        sn = float(cli_profiles.get("startup_nudge_seconds", 10))
        to = min(sw, sn) if MB_PULL_ENABLED else sw
        wait_for_ready(left,  profileA, timeout=to)
        wait_for_ready(right, profileB, timeout=to)

    # After initial injection, record capture lengths as the parsing baseline
    left_snap  = tmux_capture(left,  lines=800)
    right_snap = tmux_capture(right, lines=800)
    last_windows = {"PeerA": len(left_snap), "PeerB": len(right_snap)}
    dedup_user = {}
    dedup_peer = {}

    # Simplify: no hot-reload; changes to governance/policies/personas require restart

    # Initialize mailbox (do not clear inbox; honor startup policy)
    ensure_mailbox(home)
    mbox_idx = MailboxIndex(state)
    mbox_counts = {"peerA": {"to_user":0, "to_peer":0},
                   "peerB": {"to_user":0, "to_peer":0}}
    mbox_last = {"peerA": {"to_user": "-", "to_peer": "-"},
                 "peerB": {"to_user": "-", "to_peer": "-"}}
    # Track last mailbox activity per peer (used for timeout-based soft ACK)
    last_event_ts = {"PeerA": 0.0, "PeerB": 0.0}
    handoff_filter_override: Optional[bool] = None
    # Minimalism: no session serialization; broadcast immediately; order governed by prompts

    # Handoff backpressure: maintain in-flight and waiting queues per receiver
    inflight: Dict[str, Optional[Dict[str,Any]]] = {"PeerA": None, "PeerB": None}
    queued: Dict[str, List[Dict[str,Any]]] = {"PeerA": [], "PeerB": []}
    # Simple resend de-bounce (hash payload; drop duplicates within a short window)
    recent_sends: Dict[str, List[Dict[str,Any]]] = {"PeerA": [], "PeerB": []}
    delivery_cfg = (cli_profiles.get("delivery", {}) or {})
    ack_timeout = float(delivery_cfg.get("ack_timeout_seconds", 30))
    resend_attempts = int(delivery_cfg.get("resend_attempts", 2))
    ack_require_mid = bool(delivery_cfg.get("ack_require_mid", False))
    duplicate_window = float(delivery_cfg.get("duplicate_window_seconds", 90))
    ack_mode = str(delivery_cfg.get("ack_mode", "ack_text")).strip().lower()
    # Main loop tick (poll interval)
    try:
        main_loop_tick_seconds = float(delivery_cfg.get("main_loop_tick_seconds", 2.0))
        if main_loop_tick_seconds < 0.2:
            main_loop_tick_seconds = 0.2
    except Exception:
        main_loop_tick_seconds = 2.0
    # Progress keepalive (lightweight): delayed system echo back to sender to keep CLI alive
    keepalive_enabled = bool(delivery_cfg.get("keepalive_enabled", True))
    try:
        keepalive_delay_s = float(delivery_cfg.get("keepalive_delay_seconds", 60))
        if keepalive_delay_s < 5:
            keepalive_delay_s = 5.0
    except Exception:
        keepalive_delay_s = 60.0
    pending_keepalive: Dict[str, Optional[Dict[str, Any]]] = {"PeerA": None, "PeerB": None}

    # Periodic self-check configuration
    _sc_every = int(delivery_cfg.get("self_check_every_handoffs", 0) or 0)
    self_check_enabled = _sc_every > 0
    self_check_every = max(1, _sc_every) if self_check_enabled else 0
    instr_counter = 0
    in_self_check = False
    # self-check text from config (fallback to a sane default)
    _sc_text = str(delivery_cfg.get("self_check_text") or "").strip()
    DEFAULT_SELF_CHECK = (
        "[Self-check] Briefly answer (≤2 line each):\n"
        "1) Any drift from goal?\n"
        "2) What’s still unclear? Any new confusion created? Any better ideas?\n"
        "3) What was missed?\n"
        "4) The single next check (hook/path/metric).\n"
        "Continue only after answering."
    )
    self_check_text = _sc_text if _sc_text else DEFAULT_SELF_CHECK

    auto_reset_interval_cfg = conversation_reset_interval
    reset_interval_effective = auto_reset_interval_cfg if auto_reset_interval_cfg > 0 else 0
    # Append a minimal, always-on reminder to end with one insight block (never verbose)
    try:
        INSIGHT_REMINDER = (
            "Insight: add one new angle not restating body (lens + hook/assumption/risk/trade-off/next/delta)."
        )
        if INSIGHT_REMINDER not in self_check_text:
            self_check_text = self_check_text.rstrip("\n") + "\n" + INSIGHT_REMINDER
        if aux_mode == "on":
            aux_prompts = (
                "Note: Just trigger Aux for any task in which you think it would help.\n"
                " Schedule a thorough high-order Aux review to your recent works based on the goal now."
            )
            if "Note: Could Aux" not in self_check_text:
                self_check_text = self_check_text.rstrip("\n") + "\n" + aux_prompts
    except Exception:
        pass

    def _receiver_map(name: str) -> Tuple[str, Dict[str,Any]]:
        if name == "PeerA":
            return left, profileA
        return right, profileB

    # Pane idle judges for optional soft-ACK
    judges: Dict[str, PaneIdleJudge] = {"PeerA": PaneIdleJudge(profileA), "PeerB": PaneIdleJudge(profileB)}

    # Track inbox filenames to detect file-move ACKs (file_move mode)
    def _list_inbox_files(label: str) -> List[str]:
        try:
            ib = _inbox_dir(home, label)
            return sorted([f.name for f in ib.iterdir() if f.is_file()])
        except Exception:
            return []
    prev_inbox: Dict[str, List[str]] = {"PeerA": _list_inbox_files("PeerA"), "PeerB": _list_inbox_files("PeerB")}

    def _mailbox_peer_name(peer_label: str) -> str:
        return "peerA" if peer_label == "PeerA" else "peerB"

    # (Defined earlier before startup)

    # Minimal teaching-intercept: require a trailing fenced ```insight block in mailbox to_peer payloads.
    # Rationale: single structural invariant; high ROI; no timers/windows; intercept every time until satisfied.
    INSIGHT_FENCE = "```insight"

    def _has_trailing_insight_block(text: str) -> bool:
        if not text:
            return False
        try:
            s = str(text).rstrip()
            # Fast checks to avoid heavy regex: must end with closing fence and contain an opening insight fence
            if not s.endswith("```"):
                return False
            open_pos = s.rfind(INSIGHT_FENCE)
            if open_pos < 0:
                return False
            # Ensure the last closing fence occurs after the opening insight fence
            close_pos = s.rfind("```")
            return close_pos > open_pos
        except Exception:
            return False

    def _teach_intercept_missing_insight(home: Path, peer_label: str, payload: str) -> bool:
        """Warn (do not block) when the trailing insight block is missing.

        Returns False so that callers never suppress forwarding. We still send a
        short system reminder to reinforce the invariant.
        """
        if _has_trailing_insight_block(payload):
            return False
        peer_name = "peerA" if peer_label == "PeerA" else "peerB"
        tip = (
            "Missing trailing ```insight fenced block; please end each to_peer message with exactly one insight block (include a Next or a ≤10‑min micro‑experiment).\n"
            f"Overwrite .cccc/mailbox/{peer_name}/to_peer.md and resend (do NOT append).\n"
            "If exploring, use kind: note with a one‑line Next to indicate direction."
        )
        _send_handoff("System", peer_label, f"<FROM_SYSTEM>\n{tip}\n</FROM_SYSTEM>\n")
        try:
            log_ledger(home, {"from": "system", "kind": "teach-warn", "peer": peer_label, "reason": "missing-insight"})
        except Exception:
            pass
        return False

    # --- progress keepalive helpers (lightweight) ---
    # Body event-line detection (Progress/Next) — do not rely on insight.kind anymore
    EVENT_PROGRESS_RE = re.compile(r"(?mi)^\s*(?:[-*]\s*)?Progress\s*(?:\(|:)\s*")
    EVENT_NEXT_RE     = re.compile(r"(?mi)^\s*(?:[-*]\s*)?Next\s*(?:\(|:)\s*(.+)$")

    def _has_progress_event(payload: str) -> bool:
        try:
            m = re.search(r"<\s*TO_PEER\s*>([\s\S]*?)<\s*/TO_PEER\s*>", payload or "", re.I)
            body = m.group(1) if m else (payload or "")
            return bool(EVENT_PROGRESS_RE.search(body))
        except Exception:
            return False

    def _extract_next_from_body(payload: str) -> str:
        try:
            m = re.search(r"<\s*TO_PEER\s*>([\s\S]*?)<\s*/TO_PEER\s*>", payload or "", re.I)
            body = m.group(1) if m else (payload or "")
            mm = EVENT_NEXT_RE.findall(body)
            return (mm[0].strip() if mm else "")
        except Exception:
            return ""

    def _schedule_keepalive(sender_label: str, payload: str):
        """Schedule a delayed system keepalive back to the sender on progress messages.
        Trigger: presence of a Progress: line in the body; suppressed at dispatch time.
        """
        if not keepalive_enabled:
            return
        # Only consider <TO_PEER> messages
        if "<TO_PEER>" not in (payload or ""):
            return
        if not _has_progress_event(payload):
            return
        nx = _extract_next_from_body(payload)
        due = time.time() + float(keepalive_delay_s)
        pending_keepalive[sender_label] = {"due": due, "next": nx}
        try:
            log_ledger(home, {"from":"system","kind":"keepalive-scheduled","peer": sender_label, "delay_s": keepalive_delay_s})
        except Exception:
            pass

    # --- Passive event parser: Item + event lines → ledger (no reminders, no gates) ---
    ITEM_HEAD_RE = re.compile(r"(?mi)^\s*(?:[-*]\s*)?Item\s*\(\s*([^\)]+?)\s*\)\s*:\s*(.+)$")
    # Canonical keys (English only)
    KEY_ALIASES = {
        'progress': { 'progress' },
        'evidence': { 'evidence' },
        'ask':      { 'ask' },
        'counter':  { 'counter' },
        'risk':     { 'risk' },
        'next':     { 'next' },
    }
    CANON_KEYS = {a: k for k, vv in KEY_ALIASES.items() for a in vv}
    EVENT_LINE_RE = re.compile(r"(?mi)^\s*(?:[-*]\s*)?([A-Za-z]+)\s*(?:\(([^)]*)\))?\s*:\s*(.*)$")

    def _parse_params(s: str) -> Dict[str,str]:
        out: Dict[str,str] = {}
        if not s:
            return out
        try:
            # split by comma not inside brackets
            parts = re.split(r",(?=(?:[^\[]*\[[^\]]*\])*[^\]]*$)", s)
            for p in parts:
                if '=' in p:
                    k,v = p.split('=',1)
                    out[k.strip().lower()] = v.strip().strip()
        except Exception:
            pass
        return out

    def _extract_body(payload: str) -> str:
        m = re.search(r"<\s*TO_PEER\s*>([\s\S]*?)<\s*/TO_PEER\s*>", payload or "", re.I)
        return m.group(1) if m else (payload or "")

    def _parse_events_from_body(body: str) -> List[Dict[str,Any]]:
        events: List[Dict[str,Any]] = []
        cur_label = 'misc'
        for raw in (body or '').splitlines():
            m = ITEM_HEAD_RE.match(raw)
            if m:
                cur_label = m.group(1).strip() or 'misc'
                continue
            mm = EVENT_LINE_RE.match(raw)
            if not mm:
                continue
            key_raw, param_s, text = mm.group(1).strip(), (mm.group(2) or '').strip(), (mm.group(3) or '').strip()
            key = CANON_KEYS.get(key_raw.lower())
            if key not in KEY_ALIASES:
                continue
            params = _parse_params(param_s)
            tag = (params.get('tag') or cur_label or 'misc').strip()
            ent: Dict[str,Any] = {'type': key, 'tag': tag, 'text': text}
            # optional fields
            if 'to' in params:
                ent['to'] = params.get('to')
            if 'strength' in params:
                ent['strength'] = params.get('strength')
            if 'sev' in params:
                ent['sev'] = params.get('sev')
            # refs=[...] minimal parse
            refs = params.get('refs') or ''
            if refs.startswith('[') and refs.endswith(']'):
                inside = refs[1:-1]
                ent['refs'] = [r.strip() for r in inside.split(',') if r.strip()]
            events.append(ent)
        return events

    def _ledger_events_from_payload(home: Path, sender_label: str, payload: str):
        try:
            body = _extract_body(payload)
            evs = _parse_events_from_body(body)
            for ev in evs:
                rec = {
                    'from': sender_label,
                    'kind': f"event-{ev.get('type')}",
                    'tag': ev.get('tag'),
                    'text': ev.get('text'),
                }
                for k in ('to','strength','sev','refs'):
                    if ev.get(k) is not None:
                        rec[k] = ev.get(k)
                log_ledger(home, rec)
        except Exception:
            pass

    def _send_handoff(sender_label: str, receiver_label: str, payload: str, require_mid: Optional[bool]=None, *, nudge_text: Optional[str]=None):
        nonlocal instr_counter, in_self_check
        # Backpressure: if receiver has in-flight, enqueue
        if inflight[receiver_label] is not None:
            queued[receiver_label].append({"sender": sender_label, "payload": payload})
            log_ledger(home, {"from": sender_label, "kind": "handoff-queued", "to": receiver_label, "chars": len(payload)})
            return
        # Drop empty-body peer handoffs early (avoid forwarding suffix-only messages)
        try:
            plain = _plain_text_without_tags_and_mid(payload)
            if not plain:
                log_ledger(home, {"from": sender_label, "kind": "handoff-drop", "to": receiver_label, "reason": "empty-body", "chars": len(payload)})
                return
        except Exception:
            pass
        # Progress keepalive: schedule when a peer sends a to_peer message with Progress lines
        try:
            if sender_label in ("PeerA","PeerB"):
                _schedule_keepalive(sender_label, payload)
        except Exception:
            pass
        # Append inbound suffix (per source: from_user/from_peer/from_system); keep backward-compatible string config
        def _suffix_for(receiver: str, sender: str) -> str:
            key = 'from_peer'
            if sender == 'User':
                key = 'from_user'
            elif sender == 'System':
                key = 'from_system'
            prof = profileA if receiver == 'PeerA' else profileB
            cfg = (prof or {}).get('inbound_suffix', '')
            if isinstance(cfg, dict):
                return (cfg.get(key) or '').strip()
            # Backward compatibility: string value
            if receiver == 'PeerA':
                return str(cfg).strip()
            if receiver == 'PeerB' and sender == 'User':
                return str(cfg).strip()
            return ''
        suf = _suffix_for(receiver_label, sender_label)
        if suf:
            payload = _append_suffix_inside(payload, suf)
        # Resend de-bounce: drop identical payloads within a short window
        h = hashlib.sha1(payload.encode('utf-8', errors='replace')).hexdigest()
        now = time.time()
        rs = [it for it in recent_sends[receiver_label] if now - float(it.get('ts',0)) <= duplicate_window]
        if any(it.get('hash') == h for it in rs):
            log_ledger(home, {"from": sender_label, "kind": "handoff-duplicate-drop", "to": receiver_label, "chars": len(payload)})
            return
        rs.append({"hash": h, "ts": now})
        recent_sends[receiver_label] = rs[-20:]
        # New: inbox + NUDGE mode
        mid = new_mid()
        text_with_mid = wrap_with_mid(payload, mid)
        try:
            seq, _ = _write_inbox_message(home, receiver_label, text_with_mid, mid)
            if nudge_text and nudge_text.strip():
                if receiver_label == 'PeerA':
                    _maybe_send_nudge(home, 'PeerA', left, profileA, custom_text=nudge_text, force=True)
                else:
                    _maybe_send_nudge(home, 'PeerB', right, profileB, custom_text=nudge_text, force=True)
            else:
                _send_nudge(home, receiver_label, seq, mid, left, right, profileA, profileB, aux_mode)
            try:
                last_nudge_ts[receiver_label] = time.time()
            except Exception:
                pass
            status = "nudged"
        except Exception as e:
            status = f"failed:{e}"
            seq = "000000"
        inflight[receiver_label] = None  # Stop tracking live ACK; rely on inbox+ACK
        log_ledger(home, {"from": sender_label, "kind": "handoff", "to": receiver_label, "status": status, "mid": mid, "seq": seq, "chars": len(payload)})
        print(f"[HANDOFF] {sender_label} → {receiver_label} ({len(payload)} chars, status={status}, seq={seq})")

        # Self-check cadence: count meaningful handoffs and trigger periodic check-ins.
        try:
            if self_check_enabled and (not in_self_check) and self_check_every > 0:
                pl = payload or ""
                is_nudge = pl.strip().startswith("[NUDGE]")
                meaningful_sender = sender_label in ("User", "System", "PeerA", "PeerB")
                try:
                    low_signal = is_low_signal(pl, policies)
                except Exception:
                    low_signal = False
                if meaningful_sender and (not is_nudge) and (not low_signal):
                    instr_counter += 1
                    if instr_counter % self_check_every == 0:
                        in_self_check = True
                        try:
                            try:
                                sc_index = int(instr_counter // self_check_every) if self_check_every > 0 else 0
                            except Exception:
                                sc_index = 0
                            # Base self-check message (no inline timestamp; inbox file now contains [TS: ...])
                            peerA_msg = self_check_text
                            peerB_msg = self_check_text

                            # Every K-th self-check, append full SYSTEM rules for each peer
                            try:
                                K = SYSTEM_REFRESH_EVERY
                                rules_dir = home/"rules"
                                if sc_index > 0 and (sc_index % K) == 0:
                                    try:
                                        rulesA = (rules_dir/"PEERA.md").read_text(encoding='utf-8')
                                    except Exception:
                                        rulesA = ""
                                    try:
                                        rulesB = (rules_dir/"PEERB.md").read_text(encoding='utf-8')
                                    except Exception:
                                        rulesB = ""
                                    if rulesA:
                                        peerA_msg = peerA_msg.rstrip("\n") + "\n\n" + rulesA.strip()
                                    if rulesB:
                                        peerB_msg = peerB_msg.rstrip("\n") + "\n\n" + rulesB.strip()
                            except Exception:
                                pass

                            # Append a stable rules path line for quick re-anchoring
                            try:
                                peerA_msg = (peerA_msg.rstrip("\n") + "\nRules: .cccc/rules/PEERA.md")
                                peerB_msg = (peerB_msg.rstrip("\n") + "\nRules: .cccc/rules/PEERB.md")
                            except Exception:
                                pass
                            _send_handoff("System", "PeerA", f"<FROM_SYSTEM>\n{peerA_msg}\n</FROM_SYSTEM>\n")
                            _send_handoff("System", "PeerB", f"<FROM_SYSTEM>\n{peerB_msg}\n</FROM_SYSTEM>\n")
                            log_ledger(home, {"from": "system", "kind": "self-check", "every": self_check_every, "count": instr_counter})
                            _request_por_refresh("self-check", force=False)
                        finally:
                            in_self_check = False
        except Exception:
            pass

    def _request_por_refresh(trigger: str, hint: Optional[str] = None, *, force: bool = False):
        nonlocal por_update_last_request
        now = time.time()
        if (not force) and (now - por_update_last_request) < 60.0:
            return
        lines = [
            f"POR update requested (trigger: {trigger}).",
            f"File: {por_display_path}",
            "Also review all active SUBPORs (docs/por/T######-slug/SUBPOR.md):",
            "- For each: confirm Goal/Scope, 3-5 Acceptance, Cheapest Probe, Kill, single Next (decidable).",
            "- Align POR Now/Next with each SUBPOR Next; close/rescope stale items; ensure evidence/risks/decisions have recent refs (commit/test/log).",
            "- Check for gaps: missing tasks, unowned work, new risks; propose a new SUBPOR (after peer ACK) when needed.",
            "- Sanity-check portfolio coherence across POR/SUBPOR: priorities, sequencing, ownership.",
            "If everything is current, reply in to_peer.md with 1-2 verified points. Tools: .cccc/por_subpor.py subpor new | lint"
        ]
        if hint:
            lines.append(f"Hint: {hint}")
        lines.append("Keep the POR as the single source of truth; avoid duplicating content elsewhere.")
        payload = f"<FROM_SYSTEM>\n{'\n'.join(lines)}\n</FROM_SYSTEM>\n"
        _send_handoff("System", "PeerB", payload)
        por_update_last_request = now
        log_ledger(home, {"from": "system", "kind": "por-refresh", "trigger": trigger, "hint": hint or ""})

    def _ack_receiver(label: str, event_text: Optional[str] = None):
        # ACK policy:
        # - If ack_require_mid=True: confirm only when event text contains [MID: *]
        # - If ack_require_mid=False: treat any event as ACK (compat with CLIs that don’t echo MID strictly)
        infl = inflight.get(label)
        if not infl:
            return
        if event_text:
            # Per-message MID enforcement: confirm only when require_mid=False or when event contains MID
            need_mid = bool(infl.get('require_mid', False))
            if (not need_mid) or (str(infl.get("mid","")) in event_text):
                cur_mid = infl.get("mid")
                inflight[label] = None
                # Clean up entries in queue with the same mid (e.g., requeued after timeout)
                if queued[label]:
                    queued[label] = [q for q in queued[label] if q.get("mid") != cur_mid]
                if queued[label]:
                    nxt = queued[label].pop(0)
                    _send_handoff(nxt.get("sender","System"), label, nxt.get("payload",""))

    def _resend_timeouts():
        now = time.time()
        for label, infl in list(inflight.items()):
            if not infl:
                continue
            eff_timeout = ack_timeout
            eff_resend = resend_attempts
            # Soft-ACK: if receiver pane is idle, consider delivery successful
            pane, prof = _receiver_map(label)
            idle, _r = judges[label].refresh(pane)
            # Do not treat "pane idle" as ACK anymore to avoid false positives
            # Still allow strong ACK via [MID]
            if now - infl.get("ts", 0) >= eff_timeout:
                if int(infl.get("attempts", 0)) < eff_resend:
                    mid = infl.get("mid"); payload = infl.get("payload")
                    status, out_mid = deliver_or_queue(home, pane, _mailbox_peer_name(label), payload, prof, delivery_conf, mid=mid)
                    infl["attempts"] = int(infl.get("attempts", 0)) + 1
                    infl["ts"] = now
                    log_ledger(home, {"from": infl.get("sender"), "kind": "handoff-resend", "to": label, "status": status, "mid": out_mid})
                    print(f"[RESEND] {infl.get('sender')} → {label} (mid={out_mid}, attempt={infl['attempts']})")
                else:
                    # Exceeded retries: drop to avoid duplicate injection
                    kind = "handoff-timeout-drop"
                    log_ledger(home, {"from": infl.get("sender"), "kind": kind, "to": label, "mid": infl.get("mid")})
                    print(f"[TIMEOUT] handoff to {label} mid={infl.get('mid')} — {kind}")
                    inflight[label] = None
        # Also check delayed keepalives (coalesced per sender)
        _maybe_dispatch_keepalive()

    def _maybe_dispatch_keepalive():
        """Send pending keepalive if due and not suppressed by inbox/inflight/queued."""
        if not keepalive_enabled:
            return
        now = time.time()
        for label in ("PeerA","PeerB"):
            ent = pending_keepalive.get(label)
            if not ent:
                continue
            if float(ent.get('due') or 0.0) > now:
                continue
            # Suppress when inbox has pending files
            try:
                ib = _inbox_dir(home, label)
                has_inbox = any(ib.iterdir())
            except Exception:
                has_inbox = False
            if has_inbox:
                pending_keepalive[label] = None
                continue
            # Suppress when there are in-flight/queued handoffs targeting this label
            if inflight.get(label) is not None or (queued.get(label) or []):
                pending_keepalive[label] = None
                continue
            nxt = str(ent.get('next') or '').strip()
            hint = f"Continue: {nxt}" if nxt else "Continue with your next step."
            txt = f"<FROM_SYSTEM>\n[keepalive] {hint}\n</FROM_SYSTEM>\n"
            # Keepalive NUDGE: neutral, no preview/trigger
            ka_suffix = _compose_nudge_suffix_for(label, profileA=profileA, profileB=profileB, aux_mode=aux_mode, aux_invoke=aux_command)
            try:
                inbox_path = _inbox_dir(home, label).as_posix()
            except Exception:
                inbox_path = ".cccc/mailbox/peerX/inbox"
            ka_nudge = f"[NUDGE] [TS: {_format_local_ts()}] Inbox: {inbox_path} — continue your work; open oldest→newest." + (f" {ka_suffix}" if ka_suffix else "")
            _send_handoff("System", label, txt, nudge_text=ka_nudge)
            pending_keepalive[label] = None
            try:
                log_ledger(home, {"from":"system","kind":"keepalive-sent","peer": label})
            except Exception:
                pass

    def _try_send_from_queue(label: str):
        if inflight.get(label) is not None:
            return
        if not queued.get(label):
            return
        pane, prof = _receiver_map(label)
        idle, _r = judges[label].refresh(pane)
        if not idle:
            return
        nxt = queued[label].pop(0)
        _send_handoff(nxt.get("sender","System"), label, nxt.get("payload",""))

    # Wait for user input mode (no hard initial requirement)
    phase = "discovery"
    ctx = context_blob(policies, phase)
    # Simplify: do not pause handoff by default; let user /pause when needed
    deliver_paused = False

    # Write initial status snapshot for panel
    def write_status(paused: bool):
        state = home/"state"
        pol_enabled = bool((policies.get("handoff_filter") or {}).get("enabled", True))
        eff_filter = handoff_filter_override if handoff_filter_override is not None else pol_enabled
        # Compute remaining rounds to next self-check
        next_self = None
        if self_check_enabled and self_check_every > 0:
            try:
                rem = (self_check_every - (instr_counter % self_check_every))
                next_self = (rem if rem > 0 else self_check_every)
            except Exception:
                pass
        payload = {
            "session": session,
            "paused": paused,
            "phase": phase,
            "require_ack": bool(delivery_conf.get("require_ack", False)),
            "mailbox_counts": mbox_counts,
            "mailbox_last": mbox_last,
            "handoff_filter_enabled": eff_filter,
            "por": por_status_snapshot(home),
            "aux": _aux_snapshot(),
            "reset": {
                "policy": conversation_reset_policy,
                "default_mode": default_reset_mode,
                "interval_handoffs": auto_reset_interval_cfg if auto_reset_interval_cfg > 0 else None,
                "interval_effective": reset_interval_effective if reset_interval_effective > 0 else None,
                "self_check_every": self_check_every if self_check_enabled else None,
                "handoffs_total": instr_counter,
                "next_self_check_in": next_self,
            },
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            (state/"status.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    
    def write_queue_and_locks():
        state = home/"state"
        # Queue snapshot
        try:
            q_payload = {
                'peerA': len(queued.get('PeerA') or []),
                'peerB': len(queued.get('PeerB') or []),
                'inflight': {
                    'peerA': bool(inflight.get('PeerA')),
                    'peerB': bool(inflight.get('PeerB')),
                }
            }
            (state/"queue.json").write_text(json.dumps(q_payload, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass
        # Locks snapshot (proxy: inbox seq locks present + inflight)
        try:
            locks = []
            for nm in ('inbox-seq-peerA.lock','inbox-seq-peerB.lock'):
                if (state/nm).exists():
                    locks.append(nm)
            l_payload = {
                'inbox_seq_locks': locks,
                'inflight': {
                    'peerA': bool(inflight.get('PeerA')),
                    'peerB': bool(inflight.get('PeerB')),
                }
            }
            (state/"locks.json").write_text(json.dumps(l_payload, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass
    write_status(deliver_paused)
    write_queue_and_locks()

    # Initialize NUDGE de-dup and ACK de-dup state before first injection
    last_nudge_ts: Dict[str,float] = {"PeerA": 0.0, "PeerB": 0.0}
    seen_acks: Dict[str,set] = {"PeerA": set(), "PeerB": set()}


    print("\n[READY] Common: a:/b:/both:/u: send; /pause|/resume handoff; /refresh SYSTEM; q quit.")
    print("[TIP] Console echo is off by default. Use /echo on|off|<empty> to toggle/view.")
    print("[TIP] Passthrough: a! <cmd> / b! <cmd> sends raw input to the CLI (no wrapper), e.g., a! /model")
    # Show a clear input hint on first entry
    try:
        sys.stdout.write("[READY] Type h or /help for command hints.\n> ")
        sys.stdout.flush()
    except Exception:
        pass

    # last_windows/dedup_* initialized after handshake

    # Already injected; no need to re-send
    if start_mode == "ai_bootstrap":
        print("[PROJECT] Selected AI bootstrap for PROJECT.md.")

    # If PROJECT.md exists: hint to read and standby (do not force pause for A↔B)
    if start_mode == "has_doc":
        print("[PROJECT] Found PROJECT.md.")

    while True:
        # Keep it simple: no phase locks; send clear instructions at start; remove runtime SYSTEM hot-reload

        # Non-blocking loop: prioritize console input; otherwise scan A/B mailbox outputs
        _process_im_commands()
        # Progress keepalive: fire when due and still needed (inbox empty, no inflight/queue)
        try:
            if keepalive_enabled:
                nowk = time.time()
                for label in ("PeerA","PeerB"):
                    pend = pending_keepalive.get(label)
                    if not pend:
                        continue
                    if nowk < float(pend.get("due", 0)):
                        continue
                    # Check conditions to avoid noise: skip when inbox has messages or handoff already in flight/queued
                    inbox_files = _list_inbox_files(label)
                    reason = None
                    if inbox_files:
                        reason = "inbox-not-empty"
                    elif inflight.get(label):
                        reason = "inflight"
                    elif queued.get(label):
                        reason = "queued"
                    if reason:
                        if KEEPALIVE_DEBUG:
                            try:
                                log_ledger(home, {"from":"system","kind":"keepalive-skipped","peer":label, "reason": reason})
                            except Exception:
                                pass
                        pending_keepalive[label] = None
                        continue
                    # Safe to send a minimal FROM_SYSTEM nudge back to the sender
                    nxt = (pend.get("next") or "").strip()
                    if nxt:
                        msg = f"<FROM_SYSTEM>\nOK. Continue: {nxt}\n</FROM_SYSTEM>\n"
                    else:
                        msg = "<FROM_SYSTEM>\nOK. Continue.\n</FROM_SYSTEM>\n"
                    # Keepalive NUDGE: neutral, no preview/trigger
                    ka_suffix = _compose_nudge_suffix_for(label, profileA=profileA, profileB=profileB, aux_mode=aux_mode, aux_invoke=aux_command)
                    try:
                        inbox_path = _inbox_dir(home, label).as_posix()
                    except Exception:
                        inbox_path = ".cccc/mailbox/peerX/inbox"
                    ka_nudge = f"[NUDGE] [TS: {_format_local_ts()}] Inbox: {inbox_path} — continue your work; open oldest→newest." + (f" {ka_suffix}" if ka_suffix else "")
                    _send_handoff("System", label, msg, nudge_text=ka_nudge)
                    try:
                        log_ledger(home, {"from":"system","kind":"keepalive-sent","peer":label})
                    except Exception:
                        pass
                    pending_keepalive[label] = None
        except Exception:
            pass
        line = None
        # Handle ACK first: by mode (file_move watches moves; ack_text parses echoes)
        try:
            if ack_mode == 'file_move':
                for label in ("PeerA","PeerB"):
                    cur = _list_inbox_files(label)
                    prev = prev_inbox.get(label, [])
                    # Detect disappeared files from inbox (consider ACK)
                    disappeared = [fn for fn in prev if fn not in cur]
                    if disappeared:
                        proc = _processed_dir(home, label)
                        for fn in disappeared:
                            ok = (proc/(fn)).exists()
                            seq = fn[:6]
                            try:
                                print(f"[ACK-FILE] {label} seq={seq} file={fn} ok={bool(ok)}")
                                log_ledger(home, {"from":label,"kind":"ack-file","seq":seq,"file":fn,"ok":bool(ok)})
                                # Treat file movement as progress for NUDGE single-flight
                                _nudge_mark_progress(home, label, seq=seq)
                            except Exception:
                                pass
                    # Detect newly arrived inbox files (external sources), send an immediate detailed NUDGE once per loop
                    try:
                        added = [fn for fn in cur if fn not in prev]
                        if added:
                            # Choose the oldest newly added to avoid spamming
                            fn = sorted(added)[0]
                            # Skip files created by our own orchestrator handoff (mid starts with 'cccc-')
                            if ".cccc-" not in fn:
                                seq = fn[:6]
                                path = _inbox_dir(home, label)/fn
                                preview = _safe_headline(path)
                                suffix = _compose_nudge_suffix_for(label, profileA=profileA, profileB=profileB, aux_mode=aux_mode, aux_invoke=aux_command)
                                custom = _compose_detailed_nudge(seq, preview, (_inbox_dir(home, label).as_posix()), suffix=suffix)
                                pane = left if label == "PeerA" else right
                                prof = profileA if label == "PeerA" else profileB
                                _maybe_send_nudge(home, label, pane, prof, custom_text=custom, force=True)
                                try:
                                    last_nudge_ts[label] = time.time()
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    prev_inbox[label] = cur
            else:
                for label, pane in (("PeerA", left), ("PeerB", right)):
                    out = tmux_capture(pane, lines=800)
                    acks, _ = find_acks_from_output(out)
                    if not acks:
                        continue
                    inbox = _inbox_dir(home, label)
                    files = [f for f in inbox.iterdir() if f.is_file()]
                    for tok in acks:
                        if tok in seen_acks[label]:
                            continue
                        ok = _archive_inbox_entry(home, label, tok)
                        # Treat 'inbox-empty' or no files as benign ACKs to avoid loops
                        if (not ok) and (tok.strip().lower() in ("inbox-empty","empty","none") or len(files)==0):
                            ok = True
                        seen_acks[label].add(tok)
                        try:
                            print(f"[ACK] {label} token={tok} ok={bool(ok)}")
                            log_ledger(home, {"from":label,"kind":"ack","token":tok,"ok":bool(ok)})
                            # Any ACK token implies progress; clear inflight
                            _nudge_mark_progress(home, label)
                        except Exception:
                            pass
        except Exception:
            pass

        # Periodic NUDGE: when inbox non-empty and enough time has passed since the last reminder
        try:
            nowt = time.time()
            for label, pane in (("PeerA", left), ("PeerB", right)):
                inbox = _inbox_dir(home, label)
                files = sorted([f for f in inbox.iterdir() if f.is_file()], key=lambda p: p.name)
                if not files:
                    continue
                # Before nudging the peer to read the first message, ensure lazy preamble is prepended once
                _maybe_prepend_preamble_inbox(label)
                # Coalesced NUDGE: send only when needed; backoff otherwise
                if label == "PeerA":
                    sent = _maybe_send_nudge(home, label, pane, profileA,
                                              suffix=_compose_nudge_suffix_for('PeerA', profileA=profileA, profileB=profileB, aux_mode=aux_mode, aux_invoke=aux_command))
                else:
                    sent = _maybe_send_nudge(home, label, pane, profileB,
                                              suffix=_compose_nudge_suffix_for('PeerB', profileA=profileA, profileB=profileB, aux_mode=aux_mode, aux_invoke=aux_command))
                if sent:
                    last_nudge_ts[label] = nowt
        except Exception:
            pass
        rlist, _, _ = select.select([sys.stdin], [], [], float(main_loop_tick_seconds))
        if rlist:
            line = read_console_line("> ").strip()
        else:
            # Mailbox polling: consume structured outputs (no screen scraping).
            # To avoid echo interfering with typing, mute console printing during scan.
            _stdout_saved = sys.stdout
            if not CONSOLE_ECHO:
                sys.stdout = io.StringIO()
            try:
                events = scan_mailboxes(home, mbox_idx)
                payload = ""  # guard variable for conditional forwarding
                # PeerA events
                if events["peerA"].get("to_user"):
                    # Removed soft REV reminder in favor of hard gate on to_peer
                    txt = events["peerA"]["to_user"].strip()
                    print_block("PeerA → USER", txt)
                    try:
                        eid = hashlib.sha1(txt.encode('utf-8', errors='ignore')).hexdigest()[:12]
                    except Exception:
                        eid = str(int(time.time()))
                    # Mark a single concise event in ledger for human audit
                    try:
                        log_ledger(home, {"from":"PeerA","kind":"to_user","eid": eid, "chars": len(txt)})
                    except Exception:
                        pass
                    outbox_write(home, {"type":"to_user","peer":"PeerA","text":txt,"eid":eid})
                    _ack_receiver("PeerA", events["peerA"]["to_user"])  # Consider as ACK (responded after peer handoff)
                    mbox_counts["peerA"]["to_user"] += 1
                    mbox_last["peerA"]["to_user"] = time.strftime("%H:%M:%S")
                    last_event_ts["PeerA"] = time.time()
                    # Clear mailbox file after logging to ledger (core is authoritative outbox)
                    try:
                        (home/"mailbox"/"peerA"/"to_user.md").write_text("", encoding="utf-8")
                    except Exception:
                        pass
                if events["peerA"].get("to_peer"):
                    payload = events["peerA"]["to_peer"].strip()
                    try:
                        log_ledger(home, {"from":"PeerA","kind":"to_peer-seen","route":"mailbox","chars":len(payload)})
                    except Exception:
                        pass
                    # Passive: parse Item/event lines and write to ledger (no reminders)
                    _ledger_events_from_payload(home, "PeerA", payload)
                    # (revise soft reminder/state removed)
                    # Any mailbox activity can count as ACK
                    _ack_receiver("PeerA", payload)
                    mbox_counts["peerA"]["to_peer"] += 1
                    mbox_last["peerA"]["to_peer"] = time.strftime("%H:%M:%S")
                    last_event_ts["PeerA"] = time.time()
                    # Minimal teaching-intercept: require trailing insight fence; intercept every time until satisfied
                    try:
                        if _teach_intercept_missing_insight(home, "PeerA", payload):
                            payload = ""
                    except Exception:
                        pass
                    # inline patch/diff handling removed
                eff_enabled = handoff_filter_override if handoff_filter_override is not None else None
                if payload:
                    # auto Aux trigger removed; /review remains as the simple reminder
                    
                    if should_forward(payload, "PeerA", "PeerB", policies, state, override_enabled=False):
                        wrapped = f"<FROM_PeerA>\n{payload}\n</FROM_PeerA>\n"
                        _send_handoff("PeerA", "PeerB", wrapped)
                        try:
                            log_ledger(home, {"from":"PeerA","to":"PeerB","kind":"to_peer-forward","route":"mailbox","chars":len(payload)})
                        except Exception:
                            pass
                        try:
                            outbox_write(home, {"type":"to_peer_summary","from":"PeerA","to":"PeerB","text": payload, "eid": hashlib.sha1(payload.encode('utf-8','ignore')).hexdigest()[:12]})
                        except Exception:
                            pass
                        # Clear to_peer.md after successful forward to avoid accidental resends
                        try:
                            (home/"mailbox"/"peerA"/"to_peer.md").write_text("", encoding="utf-8")
                        except Exception:
                            pass
                    else:
                        log_ledger(home, {"from":"PeerA","kind":"handoff-drop","route":"mailbox","reason":"low-signal-or-cooldown","chars":len(payload)})
                # patch/diff mailbox path removed
                # POR.new.md auto-diff removed (per latest design)
                # PeerB events
                if events["peerB"].get("to_user"):
                    # Removed soft REV reminder in favor of hard gate on to_peer
                    txt = events["peerB"].get("to_user"," ").strip()
                    try:
                        eid = hashlib.sha1(txt.encode('utf-8', errors='ignore')).hexdigest()[:12]
                    except Exception:
                        eid = str(int(time.time()))
                    # Still record for diagnostics if PeerB emits to_user
                    try:
                        log_ledger(home, {"from":"PeerB","kind":"to_user","eid": eid, "chars": len(txt)})
                    except Exception:
                        pass
                    outbox_write(home, {"type":"to_user","peer":"PeerB","text":txt,"eid":eid})
                    try:
                        (home/"mailbox"/"peerB"/"to_user.md").write_text("", encoding="utf-8")
                    except Exception:
                        pass
                if events["peerB"].get("to_peer"):
                    payload = events["peerB"]["to_peer"].strip()
                    try:
                        log_ledger(home, {"from":"PeerB","kind":"to_peer-seen","route":"mailbox","chars":len(payload)})
                    except Exception:
                        pass
                    # Passive: parse Item/event lines and write to ledger (no reminders)
                    _ledger_events_from_payload(home, "PeerB", payload)
                    
                    _ack_receiver("PeerB", payload)
                    mbox_counts["peerB"]["to_peer"] += 1
                    mbox_last["peerB"]["to_peer"] = time.strftime("%H:%M:%S")
                    last_event_ts["PeerB"] = time.time()
                    # Minimal teaching-intercept
                    try:
                        if _teach_intercept_missing_insight(home, "PeerB", payload):
                            payload = ""
                    except Exception:
                        pass
                    # inline patch/diff handling removed
                    eff_enabled = handoff_filter_override if handoff_filter_override is not None else None
                    if payload:
                        # auto Aux trigger removed; /review remains as the simple reminder
                        
                        if should_forward(payload, "PeerB", "PeerA", policies, state, override_enabled=False):
                            wrapped = f"<FROM_PeerB>\n{payload}\n</FROM_PeerB>\n"
                            _send_handoff("PeerB", "PeerA", wrapped)
                            try:
                                log_ledger(home, {"from":"PeerB","to":"PeerA","kind":"to_peer-forward","route":"mailbox","chars":len(payload)})
                            except Exception:
                                pass
                            try:
                                outbox_write(home, {"type":"to_peer_summary","from":"PeerB","to":"PeerA","text": payload, "eid": hashlib.sha1(payload.encode('utf-8','ignore')).hexdigest()[:12]})
                            except Exception:
                                pass
                            # Clear to_peer.md after successful forward
                            try:
                                (home/"mailbox"/"peerB"/"to_peer.md").write_text("", encoding="utf-8")
                            except Exception:
                                pass
                        else:
                            log_ledger(home, {"from":"PeerB","kind":"handoff-drop","route":"mailbox","reason":"low-signal-or-cooldown","chars":len(payload)})
                # patch/diff mailbox path removed
                # POR.new.md auto-diff removed (per latest design)
                # Persist index
                mbox_idx.save()
                # Refresh status for panel
                write_status(deliver_paused); write_queue_and_locks()
                # Check resend timeouts
                _resend_timeouts()
                # Try to send next from queue when receiver idle
                _try_send_from_queue("PeerA")
                _try_send_from_queue("PeerB")
            finally:
                if not CONSOLE_ECHO:
                    sys.stdout = _stdout_saved
            continue
        if not line:
            flush_outbox_if_idle(home, left,  "peerA", profileA, delivery_conf)
            flush_outbox_if_idle(home, right, "peerB", profileB, delivery_conf)
            _resend_timeouts()
            _try_send_from_queue("PeerA"); _try_send_from_queue("PeerB")
            continue
        if line.lower() == "q":
            break
        if line.lower() in ("h", "/help"):
            print("[HELP]")
            print("  a: <text>    → PeerA    |  b: <text> → PeerB")
            print("  both:/u: <text>         → send to both A/B")
            print("  a! <cmd> / b! <cmd>     → passthrough to respective CLI (no wrapper)")
            print("  /focus [hint]           → ask PeerB to refresh POR.md (optional hint)")
            print("  /pause | /resume        → pause/resume A↔B handoff")
            print("  /refresh                → re-inject SYSTEM prompt")
            print("  /reset compact|clear    → context maintenance (compact = fold context, clear = fresh restart)")
            print("  /c <prompt> | c: <prompt> → run configured Aux once (one-off helper)")
            print("  /review                 → request Aux review bundle")
            print("  /echo on|off|<empty>    → console echo on/off/show")
            print("  q                       → quit orchestrator")
            # Reprint prompt
        try:
            sys.stdout.write("> "); sys.stdout.flush()
        except Exception:
            pass
        continue

        if line.lower().startswith("c:") or line.lower().startswith("/c"):
            if line.lower().startswith("c:"):
                prompt_text = line[2:].strip()
            else:
                prompt_text = line[2:].lstrip(" :").strip()
            if not prompt_text:
                print("[AUX] Usage: c: <prompt>  or  /c <prompt>")
                continue
            rc, out, err, cmd_line = _run_aux_cli(prompt_text)
            print(f"[AUX] command: {cmd_line}")
            print(f"[AUX] exit={rc}")
            if out:
                print(out.rstrip())
            if err:
                print("[AUX][stderr]")
                print(err.rstrip())
            continue

        if line == "/refresh":
            sysA = weave_system(home, "peerA"); sysB = weave_system(home, "peerB")
            _send_handoff("System", "PeerA", f"<FROM_SYSTEM>\n{sysA}\n</FROM_SYSTEM>\n")
            _send_handoff("System", "PeerB", f"<FROM_SYSTEM>\n{sysB}\n</FROM_SYSTEM>\n")
            print("[SYSTEM] Refreshed (mailbox delivery)."); continue
        if line.startswith("/focus"):
            tokens = line.split(maxsplit=1)
            hint = tokens[1].strip() if len(tokens) > 1 else ""
            _request_por_refresh("focus-cli", hint=hint or None, force=True)
            print("[FOCUS] Requested POR refresh from PeerB.")
            continue
        if line.startswith("/reset"):
            parts = line.split()
            mode = parts[1].lower() if len(parts) > 1 else default_reset_mode
            try:
                message = _perform_reset(mode, trigger="manual", reason=f"manual-{mode}")
                print(f"[RESET] {message}")
            except ValueError as exc:
                print(f"[RESET] {exc}")
            continue
        if line == "/review":
            _send_aux_reminder("manual-review")
            continue
        # /aux toggles/status removed. Use /c or /review instead.
        if line == "/pause":
            deliver_paused = True
            print("[PAUSE] Paused A↔B handoff (still collect <TO_USER>)"); write_status(True); continue
        if line == "/resume":
            deliver_paused = False
            write_status(False)
            print("[PAUSE] Resumed A↔B handoff"); continue
        if line == "/echo on":
            CONSOLE_ECHO = True
            print("[ECHO] Console echo ON (may interfere with input)"); continue
        if line == "/echo off":
            CONSOLE_ECHO = False
            print("[ECHO] Console echo OFF (recommended)"); continue
        if line == "/echo":
            print(f"[ECHO] Status: {'on' if CONSOLE_ECHO else 'off'}"); continue
        # /compose removed: line input relies on readline; background stays quiet to avoid interference
        if line == "/anti-on":
            handoff_filter_override = True
            write_status(deliver_paused); write_queue_and_locks()
            print("[ANTI] Low-signal filter override=on"); continue
        if line == "/anti-off":
            handoff_filter_override = False
            write_status(deliver_paused)
            print("[ANTI] Low-signal filter override=off"); continue
        if line == "/anti-status":
            pol_enabled = bool((policies.get("handoff_filter") or {}).get("enabled", True))
            eff = handoff_filter_override if handoff_filter_override is not None else pol_enabled
            src = "override" if handoff_filter_override is not None else "policy"
            print(f"[ANTI] Low-signal filter: {eff} (source={src})"); continue
        # Lazy preamble: helpers defined earlier in this function
        def _maybe_prepend_preamble(receiver_label: str, user_payload: str) -> str:
            if not LAZY_ENABLED:
                return user_payload
            st = _load_preamble_sent()
            if bool(st.get(receiver_label)):
                return user_payload
            try:
                peer_key = "peerA" if receiver_label == "PeerA" else "peerB"
                pre = weave_preamble_text(home, peer_key)
                # Merge preamble into the first user message as one instruction block
                m = re.search(r"<\s*FROM_USER\s*>\s*([\s\S]*?)<\s*/FROM_USER\s*>", user_payload, re.I)
                inner = m.group(1) if m else user_payload
                combined = f"<FROM_USER>\n{pre}\n\n{inner.strip()}\n</FROM_USER>\n"
                st[receiver_label] = True
                _save_preamble_sent(st)
                log_ledger(home, {"from":"system","kind":"lazy-preamble-sent","peer":receiver_label})
                return combined
            except Exception:
                return user_payload

        if line.startswith("u:") or line.startswith("both:"):
            msg=line.split(":",1)[1].strip()
            up = f"<FROM_USER>\n{msg}\n</FROM_USER>\n"
            _send_handoff("User", "PeerA", _maybe_prepend_preamble("PeerA", up))
            _send_handoff("User", "PeerB", _maybe_prepend_preamble("PeerB", up))
            continue
        # Passthrough: a! / b! writes raw command to target CLI (no wrapper/MID)
        if line.startswith("a!"):
            msg = line[2:].strip()
            if msg:
                _send_raw_to_cli(home, 'PeerA', msg, left, right)
            continue
        if line.startswith("b!"):
            msg = line[2:].strip()
            if msg:
                _send_raw_to_cli(home, 'PeerB', msg, left, right)
            continue
        # Normal wrapped routing
        if line.startswith("a:"):
            msg=line.split(":",1)[1].strip()
            up = f"<FROM_USER>\n{msg}\n</FROM_USER>\n"
            _send_handoff("User", "PeerA", _maybe_prepend_preamble("PeerA", up))
            continue
        if line.startswith("b:"):
            msg=line.split(":",1)[1].strip()
            up = f"<FROM_USER>\n{msg}\n</FROM_USER>\n"
            _send_handoff("User", "PeerB", _maybe_prepend_preamble("PeerB", up))
            continue
        # Default broadcast: send to both peers immediately
        up = f"<FROM_USER>\n{line}\n</FROM_USER>\n"
        _send_handoff("User", "PeerA", _maybe_prepend_preamble("PeerA", up))
        _send_handoff("User", "PeerB", _maybe_prepend_preamble("PeerB", up))
        
    print("\n[END] Recent commits:")
    run("git --no-pager log -n 5 --oneline")
    print("Ledger:", (home/"state/ledger.jsonl"))
    print(f"[TIP] You can `tmux attach -t {session}` to continue interacting with both AIs.")
