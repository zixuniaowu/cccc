# -*- coding: utf-8 -*-
"""
CCCC Orchestrator (tmux + long‑lived CLI sessions)
- Left/right panes run PeerA (Claude) and PeerB (Codex) interactive sessions.
- Uses tmux to paste messages and capture output, parses <TO_USER>/<TO_PEER> and fenced ```patch```, then preflights/applies/tests/logs.
- Injects a minimal SYSTEM prompt at startup (from prompt_weaver); runtime hot‑reload is removed for simplicity and control.
"""
import os, re, sys, json, time, shlex, tempfile, fnmatch, subprocess, select, hashlib, io, shutil
# POSIX file locking for cross-process sequencing; gracefully degrade if unavailable
try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore
from glob import glob
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from delivery import deliver_or_queue, flush_outbox_if_idle, PaneIdleJudge, new_mid, wrap_with_mid, send_text, find_acks_from_output
from mailbox import ensure_mailbox, MailboxIndex, scan_mailboxes, reset_mailbox

ANSI_RE = re.compile(r"\x1b\[.*?m|\x1b\[?[\d;]*[A-Za-z]")  # strip ANSI color/control sequences
# Console echo of AI output blocks. Default OFF to avoid disrupting typing.
CONSOLE_ECHO = False
PATCH_RE = re.compile(r"```(?:patch|diff)\s*([\s\S]*?)```", re.I)
SECTION_RE_TPL = r"<\s*{tag}\s*>([\s\S]*?)</\s*{tag}\s*>"
INPUT_END_MARK = "[CCCC_INPUT_END]"

# --- inbox/nudge settings (read at startup from cli_profiles.delivery) ---
MB_PULL_ENABLED = True
INBOX_DIRNAME = "inbox"
PROCESSED_RETENTION = 200
NUDGE_RESEND_SECONDS = 90
NUDGE_JITTER_PCT = 0.0
SOFT_ACK_ON_MAILBOX_ACTIVITY = False
INBOX_STARTUP_POLICY = "resume"  # resume | discard | archive
INBOX_STARTUP_PROMPT = False

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

def _send_raw_to_cli(home: Path, receiver_label: str, text: str,
                     modeA: str, modeB: str,
                     left_pane: str, right_pane: str):
    """Direct passthrough: send raw text to CLI without any wrappers/MID.
    - For bridge: write text directly into mailbox/<peer>/inbox.md (adapter submits with Enter)
    - For tmux: paste to pane with a single Enter
    """
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    if receiver_label == 'PeerA' and modeA == 'bridge':
        try:
            (home/"mailbox"/"peerA"/"inbox.md").write_text(text, encoding='utf-8')
            print(f"[RAW] → PeerA @ {ts}: {text[:80]}")
        except Exception as e:
            print(f"[RAW] PeerA inject failed: {e}")
    elif receiver_label == 'PeerB' and modeB == 'bridge':
        try:
            (home/"mailbox"/"peerB"/"inbox.md").write_text(text, encoding='utf-8')
            print(f"[RAW] → PeerB @ {ts}: {text[:80]}")
        except Exception as e:
            print(f"[RAW] PeerB inject failed: {e}")
    else:
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
                fpath.write_text(payload, encoding='utf-8')
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
                fpath.write_text(payload, encoding='utf-8')
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

def _send_nudge(home: Path, receiver_label: str, seq: str, mid: str,
                left_pane: str, right_pane: str,
                profileA: Dict[str,Any], profileB: Dict[str,Any],
                modeA: str, modeB: str):
    inbox_path = str(_inbox_dir(home, receiver_label))
    # Optional NUDGE suffix (per peer) to fine‑tune CLI behavior
    nudge_suffix = ""
    if receiver_label == 'PeerA':
        nudge_suffix = (profileA.get('nudge_suffix') or '').strip()
    else:
        nudge_suffix = (profileB.get('nudge_suffix') or '').strip()
    # Include concrete seq and mid to simplify ACK on CLI side
    if str((profileA if receiver_label=='PeerA' else profileB).get('dummy', '')):
        pass
    # Simplified NUDGE: only show inbox path and handling rules (no seq/mid)
    base = (
        f"[NUDGE] inbox={inbox_path} "
        f"Read the oldest message file in order. After reading/processing, move that file into the processed/ directory alongside this inbox (same mailbox). Repeat until inbox is empty."
    )
    msg = base + (" " + nudge_suffix if nudge_suffix else "")
    # Route by delivery mode: bridge → write to adapter inbox.md (adapter injects Enter);
    # tmux → send keys directly to pane with newline at end (Enter implied by profile)
    if receiver_label == 'PeerA':
        if modeA == 'bridge':
            try:
                (home/"mailbox"/"peerA"/"inbox.md").write_text(msg + "\n", encoding='utf-8')
            except Exception:
                pass
        else:
            # tmux injection: paste then single submit; avoid pre-Enter poke to prevent stray newlines
            paste_when_ready(left_pane, profileA, msg, timeout=6.0, poke=False)
    else:
        if modeB == 'bridge':
            try:
                (home/"mailbox"/"peerB"/"inbox.md").write_text(msg + "\n", encoding='utf-8')
            except Exception:
                pass
        else:
            paste_when_ready(right_pane, profileB, msg, timeout=6.0, poke=False)
            # Optional extra Enters for robustness (configurable per peer)
            try:
                extra = int((profileB.get('nudge_extra_enters') or 0))
                delay_ms = int((profileB.get('nudge_enter_delay_ms') or 200))
                if extra > 0:
                    for _ in range(min(extra, 3)):
                        time.sleep(max(0.0, delay_ms/1000.0))
                        tmux("send-keys","-t",right_pane,"Enter")
            except Exception:
                pass

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

def extract_patches(text: str) -> List[str]:
    """Extract raw unified diff blocks from fenced ```patch|diff``` without wrapping.
    The orchestrator expects standard unified diffs consumable by `git apply`.
    """
    out=[]
    for m in PATCH_RE.finditer(text):
        body=m.group(1).strip()
        out.append(body)
    return out

def extract_inline_diff_if_any(text: str) -> Optional[str]:
    """Detect unified diff inside arbitrary text (e.g., to_peer message) and normalize."""
    # Try fenced first
    fences = extract_patches(text)
    for p in fences:
        if is_valid_diff(p):
            return p
    # Try salvage on raw text
    cand = normalize_mailbox_patch(text)
    return cand

def is_valid_diff(patch: str) -> bool:
    """Heuristic check to filter out instructional fences like ```patch ...```.
    Accept if it looks like a real unified diff.
    """
    lines = patch.splitlines()
    if any(ln.startswith("diff --git ") for ln in lines):
        return True
    has_headers = any(ln.startswith("--- ") for ln in lines) and any(ln.startswith("+++ ") for ln in lines)
    if has_headers:
        return True
    if "*** Begin Patch" in patch or "*** PATCH" in patch:
        return True
    return False

def normalize_mailbox_patch(text: str) -> Optional[str]:
    """Try hard to obtain a clean unified diff from mailbox content.
    Strategy:
    - Prefer fenced ```patch|diff``` blocks that look like real diffs.
    - Else, if raw content contains a diff header, salvage only allowed lines.
    - Allowed lines are standard unified diff headers and hunk lines.
    - Drop ledger banners, prompts, and non-diff chatter.
    """
    def _salvage(raw: str) -> Optional[str]:
        lines = raw.splitlines()
        # start after the first recognizable header
        start = 0
        for i, ln in enumerate(lines):
            if ln.startswith('diff --git ') or ln.startswith('--- ') or ln.startswith('*** Begin Patch'):
                start = i; break
        if start > 0:
            lines = lines[start:]
        allowed_prefixes = (
            'diff --git ', 'index ', '--- ', '+++ ', '@@', ' ', '+', '-',
            'Binary files ', 'new file mode ', 'deleted file mode ', 'similarity index ',
            'rename from ', 'rename to ', 'copy from ', 'copy to ', 'old mode ', 'new mode ',
            '\\ No newline at end of file', 'GIT binary patch'
        )
        kept = []
        for ln in lines:
            if ln.startswith(allowed_prefixes):
                kept.append(ln)
            # ignore mailbox banners and UI traces
            elif ln.strip().startswith('========') or 'Pasted text #' in ln or 'Ctrl+J' in ln:
                continue
            else:
                # also drop obvious panel/UI frames
                if ln.strip().startswith('╭') or ln.strip().startswith('│') or ln.strip().startswith('╰'):
                    continue
                # skip stray noise lines
                continue
        out = "\n".join(kept).strip()
        if out and is_valid_diff(out):
            return out
        return None

    # Prefer fenced blocks, but always salvage to remove any stray noise
    fences = extract_patches(text)
    for p in fences:
        salv = _salvage(p) or (p if is_valid_diff(p) else None)
        if salv:
            return salv
    # No fences; check raw and salvage regardless
    salv = _salvage(text)
    if salv:
        return salv
    return None

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
    # obvious high-signal: diff fences, unified diff, explicit sections
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

def count_changed_lines(patch: str) -> int:
    n=0
    for ln in patch.splitlines():
        if ln.startswith("+++ ") or ln.startswith("--- "): continue
        if ln.startswith("+") or ln.startswith("-"): n+=1
    return n

def extract_paths_from_patch(patch: str) -> List[str]:
    paths=set()
    for ln in patch.splitlines():
        if ln.startswith("--- ") or ln.startswith("+++ "):
            pth=ln.split("\t")[0].split(" ",1)[1].strip()
            # Ignore /dev/null (new/delete markers)
            if pth == "/dev/null":
                continue
            if pth.startswith("a/") or pth.startswith("b/"):
                pth=pth[2:]
            paths.add(pth)
    return sorted(paths)

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
    # Robust: run command inside pane via bash -lc; avoid paste-to-wrong-pane issues
    wrapped = f"bash -lc {shlex.quote(cmd)}"
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
    return ev

def allowed_by_policies(paths: List[str], policies: Dict[str,Any]) -> bool:
    allowed = policies.get("patch_queue",{}).get("allowed_paths",["**"])
    for pth in paths:
        if not any(fnmatch.fnmatch(pth,pat) for pat in allowed):
            print(f"[POLICY] Path not allowed: {pth}")
            return False
    return True

# ---------- RFD helpers ----------
def _ledger_tail(home: Path, keep: int = 400) -> List[Dict[str,Any]]:
    p = home/"state"/"ledger.jsonl"
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-keep:]
        out=[]
        for ln in lines:
            try: out.append(json.loads(ln))
            except Exception: pass
        return out
    except Exception:
        return []

def _ledger_has_rfd(home: Path, rid: str) -> bool:
    rid = str(rid or '').strip()
    if not rid: return False
    for ev in _ledger_tail(home):
        if str(ev.get('kind') or '').lower() == 'rfd' and str(ev.get('id') or '') == rid:
            return True
    return False

def _ledger_has_decision_approved(home: Path, rid: str) -> bool:
    rid = str(rid or '').strip()
    if not rid: return False
    for ev in _ledger_tail(home):
        if str(ev.get('kind') or '').lower() == 'decision' and str(ev.get('rfd_id') or '') == rid:
            dec = str(ev.get('decision') or '').lower()
            if dec in ('approve','approved','yes','ok','allow','accept'):
                return True
    return False

def _ensure_rfd_logged(home: Path, rid: str, title: str, summary: str = ""):
    if not _ledger_has_rfd(home, rid):
        log_ledger(home, {"kind": "rfd", "id": rid, "title": title, "summary": summary})

def _paths_match_patterns(paths: List[str], patterns: List[str]) -> bool:
    for p in paths:
        for pat in patterns:
            if fnmatch.fnmatch(p, pat):
                return True
    return False

def _rfd_id_for_large(paths: List[str], lines: int) -> str:
    import hashlib
    basis = ",".join(paths) + f"|{lines}"
    return f"rfd-large-{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:8]}"

def _rfd_id_for_protected(paths: List[str]) -> str:
    import hashlib
    basis = ",".join(paths)
    return f"rfd-prot-{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:8]}"

def _maybe_emit_rfd_from_to_peer(home: Path, who: str, text: str):
    try:
        import yaml  # type: ignore
        obj = yaml.safe_load(text)
        if isinstance(obj, dict):
            intent = str(obj.get('intent') or '').strip().lower()
            _type = str(obj.get('type') or '').strip().lower()
            if intent == 'rfd' or _type == 'rfd':
                rid = str(obj.get('id') or '').strip()
                title = str(obj.get('title') or obj.get('summary') or '')
                if not title:
                    tasks = obj.get('tasks') or []
                    if isinstance(tasks, list) and tasks:
                        t0 = tasks[0]
                        if isinstance(t0, dict):
                            title = str(t0.get('desc') or '')
                if not rid:
                    import hashlib
                    rid = 'rfd-msg-' + hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()[:8]
                _ensure_rfd_logged(home, rid, title or f"RFD from {who}")
    except Exception:
        pass

def _rfd_gate_check(home: Path, policies: Dict[str,Any], patch: str, lines: int) -> Tuple[bool, Optional[str]]:
    """Return (allowed, reject_reason_or_None). Applies RFD gates for large diff and protected paths.
    If rejected due to RFD requirement, also emits an RFD event to the ledger.
    """
    gates = (policies.get('rfd') or {}).get('gates') or {}
    prot = gates.get('protected_paths') or []
    large_requires = bool(gates.get('large_diff_requires_rfd', False))
    max_lines = int((policies.get('patch_queue') or {}).get('max_diff_lines', 150))
    paths = extract_paths_from_patch(patch)
    # Protected paths gate
    if prot and _paths_match_patterns(paths, prot):
        rid = _rfd_id_for_protected(paths)
        if _ledger_has_decision_approved(home, rid):
            return True, None
        _ensure_rfd_logged(home, rid, "Protected path change approval", f"paths={paths}")
        return False, f"rfd-required-protected-path:{rid}"
    # Large diff gate (allows override via decision)
    if lines > max_lines:
        if not large_requires:
            return False, f"too-many-lines:{lines}>{max_lines}"
        rid = _rfd_id_for_large(paths, lines)
        if _ledger_has_decision_approved(home, rid):
            return True, None
        _ensure_rfd_logged(home, rid, "Large diff line-count approval", f"lines={lines}")
        return False, f"rfd-required-large-diff:{rid}"
    return True, None


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

def git_apply_check(patch: str) -> Tuple[bool,str]:
    with tempfile.NamedTemporaryFile("w",delete=False,suffix=".patch") as f:
        f.write(patch); path=f.name
    code,out,err=run(f"git apply --check {shlex.quote(path)}")
    return (code==0, (out+err))

def git_apply(patch: str) -> Tuple[bool,str]:
    with tempfile.NamedTemporaryFile("w",delete=False,suffix=".patch") as f:
        f.write(patch); path=f.name
    code,out,err=run(f"git apply {shlex.quote(path)}")
    return (code==0, (out+err))

def git_commit(msg: str):
    run("git add -A"); run(f"git commit -m {shlex.quote(msg)}")

# ---------- prompt weaving ----------
def weave_system(home: Path, peer: str) -> str:
    from prompt_weaver import weave_system_prompt
    return weave_system_prompt(home, peer)

def weave_preamble_text(home: Path, peer: str) -> str:
    """Single-source preamble (same source as SYSTEM)."""
    try:
        from prompt_weaver import weave_preamble
        return weave_preamble(home, peer)
    except Exception:
        # Fallback to full system when preamble helper not present
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
    allowed = (policies.get("patch_queue",{}).get("allowed_paths") or ["**"])
    context_conf = policies.get("context", {}) if isinstance(policies.get("context", {}), dict) else {}
    excludes = context_conf.get("exclude", DEFAULT_CONTEXT_EXCLUDES)
    max_items = int(context_conf.get("files_limit", limit))

    # include by allowed paths first, then drop excluded patterns
    selected = [p for p in files if _matches_any(p, allowed)]
    filtered = [p for p in selected if not _matches_any(p, excludes)]
    return "\n".join(filtered[:max_items])

def context_blob(policies: Dict[str,Any], phase: str) -> str:
    return (f"# PHASE: {phase}\n# REPO FILES (partial):\n{list_repo_files(policies)}\n\n"
            f"# POLICIES:\n{json.dumps({'patch_queue':policies.get('patch_queue',{}),'rfd':policies.get('rfd',{}),'autonomy_level':policies.get('autonomy_level')},ensure_ascii=False)}\n")

# ---------- Weekly diary helpers (minimal, single-writer) ----------
def _week_file_path(home: Path, dt=None) -> Path:
    import datetime as _dt
    dt = dt or _dt.datetime.now(_dt.timezone.utc).astimezone()
    iso = dt.isocalendar()
    year = int(iso[0]); week = int(iso[1])
    # Store weekly diary under orchestrator workdir to minimize repo intrusion
    # .cccc/work/docs/weekly/YYYY-Www.md (non-authoritative; ignored by VCS)
    return home/"work"/"docs"/"weekly"/f"{year}-W{week:02d}.md"

def _today_heading(dt=None) -> str:
    import datetime as _dt
    dt = dt or _dt.datetime.now(_dt.timezone.utc).astimezone()
    return f"## {dt.strftime('%Y-%m-%d')}"

def _file_tail_lines(p: Path, n: int = 10) -> List[str]:
    try:
        with p.open('r', encoding='utf-8') as f:
            from collections import deque
            dq = deque(f, maxlen=max(1, int(n)))
            return [ln.rstrip('\n') for ln in dq]
    except Exception:
        return []

def _weekly_has_today(home: Path, dt=None) -> Tuple[Path, bool]:
    p = _week_file_path(home, dt)
    if not p.exists():
        return p, False
    try:
        heading = _today_heading(dt)
        for ln in p.read_text(encoding='utf-8').splitlines():
            if ln.strip().startswith(heading):
                return p, True
    except Exception:
        pass
    return p, False

def _weekly_has_retro(p: Path) -> bool:
    try:
        if not p.exists():
            return False
        return any(ln.strip().startswith("## Retrospective") for ln in p.read_text(encoding='utf-8').splitlines())
    except Exception:
        return False

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
    # Wait for response: prefer <TO_USER>/<TO_PEER>/```diff, or idle prompt
    judge = PaneIdleJudge(sender_profile)
    start = time.time()
    timeout = float(delivery_conf.get("read_timeout_seconds", 8))
    window = ""
    while time.time() - start < timeout:
        content = tmux_capture(sender_pane, lines=800)
        window = content[before_len:]
        # Do not strip wrappers here; keep window for diagnostics (mailbox path does not rely on this)
        if ("<TO_USER>" in window) or ("<TO_PEER>" in window) or ("```diff" in window) or ("```patch" in window):
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
    # Do not print <TO_USER> here (the background poller will report it); focus on patches and handoffs only

    # Only scan fenced patches visible in the window; ignore non-diff fences
    patches = [p for p in extract_patches(window) if is_valid_diff(p)]
    for i,patch in enumerate(patches,1):
        print_block(f"{who} Patch#{i}", "Preflight …")
        lines = count_changed_lines(patch)
        max_lines = int(policies.get("patch_queue",{}).get("max_diff_lines",150))
        if lines>max_lines:
            print(f"[POLICY] Changed lines {lines} > {max_lines}; rejecting.")
            log_ledger(home, {"from":who,"kind":"patch-reject","reason":"too-many-lines","lines":lines}); 
            continue
        paths = extract_paths_from_patch(patch)
        if not allowed_by_policies(paths, policies):
            log_ledger(home, {"from":who,"kind":"patch-reject","reason":"path-not-allowed","paths":paths}); 
            continue
        ok,err = git_apply_check(patch)
        if not ok:
            print("[PATCH] Preflight failed:\n"+err.strip()); 
            log_ledger(home, {"from":who,"kind":"patch-precheck-fail","stderr":err.strip()[:2000]}); 
            continue
        ok2,err2 = git_apply(patch)
        if not ok2:
            print("[PATCH] Apply failed:\n"+err2.strip()); 
            log_ledger(home, {"from":who,"kind":"patch-apply-fail","stderr":err2.strip()[:2000]}); 
            continue
        try_lint()
        tests_ok = try_tests()
        git_commit(f"cccc({who}): apply patch (phase {phase})")
        log_ledger(home, {"from":who,"kind":"patch-commit","paths":paths,"lines":lines,"tests_ok":tests_ok})
        if not tests_ok:
            fb = "<TO_PEER>\ntype: EVIDENCE\nintent: fix\ntasks:\n  - desc: 'Tests failed; please provide the minimal fix patch'\n</TO_PEER>\n"
            paste_when_ready(sender_pane, sender_profile, f"[INPUT]\n{fb}\n")

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
                            modeA, modeB)
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

    patches = [p for p in extract_patches(sanitized) if is_valid_diff(p)]
    for i,patch in enumerate(patches,1):
        print_block(f"{who} Patch#{i}", "Preflight …")
        lines = count_changed_lines(patch)
        max_lines = int(policies.get("patch_queue",{}).get("max_diff_lines",150))
        if lines>max_lines:
            print(f"[POLICY] Changed lines {lines} > {max_lines}; rejecting.")
            log_ledger(home, {"from":who,"kind":"patch-reject","reason":"too-many-lines","lines":lines}); 
            continue
        paths = extract_paths_from_patch(patch)
        if not allowed_by_policies(paths, policies):
            log_ledger(home, {"from":who,"kind":"patch-reject","reason":"path-not-allowed","paths":paths}); 
            continue
        ok,err = git_apply_check(patch)
        if not ok:
            print("[PATCH] Preflight failed:\n"+err.strip()); 
            log_ledger(home, {"from":who,"kind":"patch-precheck-fail","stderr":err.strip()[:2000]}); 
            continue
        ok2,err2 = git_apply(patch)
        if not ok2:
            print("[PATCH] Apply failed:\n"+err2.strip()); 
            log_ledger(home, {"from":who,"kind":"patch-apply-fail","stderr":err2.strip()[:2000]}); 
            continue
        try_lint()
        tests_ok = try_tests()
        git_commit(f"cccc({who}): apply patch (phase {phase})")
        log_ledger(home, {"from":who,"kind":"patch-commit","paths":paths,"lines":lines,"tests_ok":tests_ok})
        if not tests_ok:
            fb = "<TO_PEER>\ntype: EVIDENCE\nintent: fix\ntasks:\n  - desc: 'Tests failed; please provide a minimal fix patch'\n</TO_PEER>\n"
            tmux_paste(pane, f"[INPUT]\n{fb}\n")

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
    # Reset preamble sent flags on each orchestrator start to ensure the first
    # user message per peer carries the preamble in this session.
    try:
        (state/"preamble_sent.json").write_text(json.dumps({"PeerA": False, "PeerB": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    roles    = read_yaml(settings/"roles.yaml")
    policies = read_yaml(settings/"policies.yaml")

    leader   = (roles.get("leader") or "peerA").strip().lower()
    session  = f"cccc-{Path.cwd().name}"

    cli_profiles = read_yaml(settings/"cli_profiles.yaml")
    profileA = cli_profiles.get("peerA", {})
    profileB = cli_profiles.get("peerB", {})
    delivery_conf = cli_profiles.get("delivery", {})
    delivery_mode = cli_profiles.get("delivery_mode", {}) if isinstance(cli_profiles.get("delivery_mode", {}), dict) else {}
    modeA = (delivery_mode.get('peerA') or 'tmux').lower()
    modeB = (delivery_mode.get('peerB') or 'tmux').lower()
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
    commands = cli_profiles.get("commands", {}) if isinstance(cli_profiles.get("commands", {}), dict) else {}
    CLAUDE_I_CMD = os.environ.get("CLAUDE_I_CMD") or commands.get("peerA") or f"python {shlex.quote(str(home/'mock_agent.py'))} --role peerA"
    GEMINI_I_CMD  = os.environ.get("GEMINI_I_CMD")  or commands.get("peerB") or f"python {shlex.quote(str(home/'mock_agent.py'))} --role peerB"
    if (commands.get("peerA") is None and os.environ.get("CLAUDE_I_CMD") is None) or \
       (commands.get("peerB") is None and os.environ.get("GEMINI_I_CMD") is None):
        print("[INFO] Some CLI commands not provided; missing side will use built-in Mock (configure in cli_profiles.yaml or via env vars).")
    else:
        print("[INFO] Using CLI commands from configuration (overridable by env).")
    if start_mode in ("has_doc", "ai_bootstrap"):
        if modeA == 'bridge':
            # Ensure pexpect is available; otherwise fallback to tmux mode for visibility
            pyexe = shlex.quote(sys.executable or 'python3')
            code,_,_ = run(f"{pyexe} -c 'import pexpect'")
            if code != 0:
                print("[WARN] pexpect not installed; PeerA bridge mode disabled. Falling back to tmux input injection (pip install pexpect).")
                modeA = 'tmux'
        if modeB == 'bridge':
            # Ensure pexpect is available; otherwise fallback to tmux mode
            pyexe = shlex.quote(sys.executable or 'python3')
            code,_,_ = run(f"{pyexe} -c 'import pexpect'")
            if code != 0:
                print("[WARN] pexpect not installed; PeerB bridge mode disabled. Falling back to tmux input injection (pip install pexpect).")
                modeB = 'tmux'
        if modeA == 'bridge':
            # Run bridge adapter in pane; it will spawn the CLI child and proxy stdout
            py = sys.executable or 'python3'
            bridge_py = str(home/"adapters"/"bridge.py")
            inbox = str(home/"mailbox"/"peerA"/"inbox.md")
            # Pass prompt regex if available to help the adapter time submission
            prx = str((profileA or {}).get('prompt_regex') or '')
            inner = f"{shlex.quote(py)} {shlex.quote(bridge_py)} --home {shlex.quote(str(home))} --peer peerA --cmd {shlex.quote(CLAUDE_I_CMD)} --inbox {shlex.quote(inbox)}"
            if prx:
                inner += f" --prompt-regex {shlex.quote(prx)}"
            cmd = f"bash -lc {shlex.quote(inner)}"
            tmux_respawn_pane(left, cmd)
            print(f"[LAUNCH] PeerA mode=bridge pane={left} bridge_cmd={inner}")
        else:
            tmux_start_interactive(left, CLAUDE_I_CMD)
            print(f"[LAUNCH] PeerA mode=tmux pane={left} cmd={CLAUDE_I_CMD}")
        if modeB == 'bridge':
            # Run bridge adapter for PeerB
            py = sys.executable or 'python3'
            bridge_py = str(home/"adapters"/"bridge.py")
            inbox = str(home/"mailbox"/"peerB"/"inbox.md")
            prx = str((profileB or {}).get('prompt_regex') or '')
            inner = f"{shlex.quote(py)} {shlex.quote(bridge_py)} --home {shlex.quote(str(home))} --peer peerB --cmd {shlex.quote(GEMINI_I_CMD)} --inbox {shlex.quote(inbox)}"
            if prx:
                inner += f" --prompt-regex {shlex.quote(prx)}"
            cmd = f"bash -lc {shlex.quote(inner)}"
            tmux_respawn_pane(right, cmd)
            print(f"[LAUNCH] PeerB mode=bridge pane={right} bridge_cmd={inner}")
        else:
            tmux_start_interactive(right, GEMINI_I_CMD)
            print(f"[LAUNCH] PeerB mode=tmux pane={right} cmd={GEMINI_I_CMD}")
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

    # Simplify: no hot-reload; changes to roles/policies/personas require restart

    # Initialize mailbox (do not clear inbox; honor startup policy)
    ensure_mailbox(home)
    mbox_idx = MailboxIndex(state)
    mbox_counts = {"peerA": {"to_user":0, "to_peer":0, "patch":0},
                   "peerB": {"to_user":0, "to_peer":0, "patch":0}}
    mbox_last = {"peerA": {"to_user": "-", "to_peer": "-", "patch": "-"},
                 "peerB": {"to_user": "-", "to_peer": "-", "patch": "-"}}
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
    # Append a minimal, always-on reminder to end with one insight block (never verbose)
    try:
        INSIGHT_REMINDER = (
            "Reminder: end with one ```insight block (ask/counter; include a next step or ≤10‑min micro‑experiment)."
        )
        if INSIGHT_REMINDER not in self_check_text:
            # Ensure single trailing newline then append reminder as the last line
            self_check_text = self_check_text.rstrip("\n") + "\n" + INSIGHT_REMINDER
    except Exception:
        # Fail-safe: keep original self_check_text
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
        """Return True if intercepted and forwarded a system teaching message (payload not forwarded)."""
        if _has_trailing_insight_block(payload):
            return False
        # Minimal 3-line message in Chinese; no templates or verbosity.
        peer_name = "peerA" if peer_label == "PeerA" else "peerB"
        tip = (
            "Missing trailing ```insight fenced block in the last message you sent; end with one insight block (include a next step or a ≤10‑min micro‑experiment).\n"
            f"Overwrite .cccc/mailbox/{peer_name}/to_peer.md and resend (do NOT append).\n"
            "If exploring, use kind: note with a one‑line next step to indicate direction."
        )
        _send_handoff("System", peer_label, f"<FROM_SYSTEM>\n{tip}\n</FROM_SYSTEM>\n")
        try:
            log_ledger(home, {"from": "system", "kind": "teach-intercept", "peer": peer_label, "reason": "missing-insight"})
        except Exception:
            pass
        return True

    def _send_handoff(sender_label: str, receiver_label: str, payload: str, require_mid: Optional[bool]=None):
        nonlocal instr_counter, in_self_check
        # Backpressure: if receiver has in-flight, enqueue
        if inflight[receiver_label] is not None:
            queued[receiver_label].append({"sender": sender_label, "payload": payload})
            log_ledger(home, {"from": sender_label, "kind": "handoff-queued", "to": receiver_label, "chars": len(payload)})
            return
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
            _send_nudge(home, receiver_label, seq, mid, left, right, profileA, profileB, modeA, modeB)
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

        # Count non-NUDGE, non-low-signal handoffs; every K sends, broadcast a self-check to both peers.
        try:
            if self_check_enabled and (not in_self_check):
                pl = (payload or "")
                is_nudge = pl.strip().startswith("[NUDGE]")
                # Treat any meaningful handoff (User/System/PeerA/PeerB) as a step toward self-check
                meaningful_sender = sender_label in ("User","System","PeerA","PeerB")
                low_signal = False
                try:
                    low_signal = is_low_signal(pl, policies)
                except Exception:
                    low_signal = False
                if meaningful_sender and (not is_nudge) and (not low_signal):
                    instr_counter += 1
                    if instr_counter % self_check_every == 0:
                        in_self_check = True
                        try:
                            peerA_msg = self_check_text
                            peerB_msg = self_check_text

                            # Append Weekly Dev Diary reminder for PeerB (single-writer) with daily cooldown
                            # Diary/retrospective prompts are managed as side-quest TODOs by the AI; do not inject from system here.

                            _send_handoff("System", "PeerA", f"<FROM_SYSTEM>\n{peerA_msg}\n</FROM_SYSTEM>\n")
                            _send_handoff("System", "PeerB", f"<FROM_SYSTEM>\n{peerB_msg}\n</FROM_SYSTEM>\n")
                            log_ledger(home, {"from":"system","kind":"self-check","every": self_check_every, "count": instr_counter})
                        finally:
                            in_self_check = False
        except Exception:
            pass

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
            # Per-label overrides: for bridge receivers, avoid resends by default; allow limited resends when require_mid=True
            eff_timeout = ack_timeout
            eff_resend = resend_attempts
            if (label == 'PeerA' and modeA == 'bridge') or (label == 'PeerB' and modeB == 'bridge'):
                eff_timeout = max(eff_timeout, 90.0)
                if not bool(infl.get('require_mid', False)):
                    eff_resend = 0
            # Soft-ACK: if receiver pane is idle, consider delivery successful
            pane, prof = _receiver_map(label)
            idle, _r = judges[label].refresh(pane)
            # Do not treat "pane idle" as ACK anymore to avoid false positives
            # Still allow strong ACK via [MID]
            if now - infl.get("ts", 0) >= eff_timeout:
                if int(infl.get("attempts", 0)) < eff_resend:
                    mid = infl.get("mid")
                    payload = infl.get("payload")
                    if label == 'PeerA' and modeA == 'bridge':
                        inbox = home/"mailbox"/"peerA"/"inbox.md"
                        try:
                            inbox.write_text(wrap_with_mid(payload, mid), encoding='utf-8')
                            status = 'delivered'; out_mid = mid
                        except Exception:
                            status = 'failed'; out_mid = mid
                    elif label == 'PeerB' and modeB == 'bridge':
                        inbox = home/"mailbox"/"peerB"/"inbox.md"
                        try:
                            inbox.write_text(wrap_with_mid(payload, mid), encoding='utf-8')
                            status = 'delivered'; out_mid = mid
                        except Exception:
                            status = 'failed'; out_mid = mid
                    else:
                        status, out_mid = deliver_or_queue(home, pane, _mailbox_peer_name(label), payload, prof, delivery_conf, mid=mid)
                    infl["attempts"] = int(infl.get("attempts", 0)) + 1
                    infl["ts"] = now
                    log_ledger(home, {"from": infl.get("sender"), "kind": "handoff-resend", "to": label, "status": status, "mid": out_mid})
                    print(f"[RESEND] {infl.get('sender')} → {label} (mid={out_mid}, attempt={infl['attempts']})")
                else:
                    # Exceeded retries (or eff_resend=0): in bridge mode, treat any mailbox activity as soft ACK; otherwise drop to avoid duplicate injection
                    last_ts = last_event_ts.get(label, 0.0)
                    if last_ts and last_ts > float(infl.get("ts", 0)):
                        kind = "handoff-timeout-soft-ack"
                    else:
                        kind = "handoff-timeout-drop"
                    log_ledger(home, {"from": infl.get("sender"), "kind": kind, "to": label, "mid": infl.get("mid")})
                    print(f"[TIMEOUT] handoff to {label} mid={infl.get('mid')} — {kind}")
                    inflight[label] = None

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
        payload = {
            "session": session,
            "paused": paused,
            "phase": phase,
            "leader": leader,
            "require_ack": bool(delivery_conf.get("require_ack", False)),
            "mailbox_counts": mbox_counts,
            "mailbox_last": mbox_last,
            "handoff_filter_enabled": eff_filter,
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
                if nowt - last_nudge_ts.get(label, 0.0) < NUDGE_RESEND_SECONDS:
                    continue
                oldest = files[0].name
                seq = oldest[:6]
                mid = oldest.split(".")[1] if "." in oldest else ""
                inbox_path = str(inbox)
                nmsg = (
                    f"[NUDGE] inbox={inbox_path} "
                    f"Read the oldest message file in order. After reading/processing, move that file into the processed/ directory alongside this inbox (same mailbox). Repeat until inbox is empty."
                )
                if label == "PeerA":
                    paste_when_ready(pane, profileA, nmsg, timeout=6.0, poke=False)
                else:
                    paste_when_ready(pane, profileB, nmsg, timeout=6.0, poke=False)
                    try:
                        extra = int((profileB.get('nudge_extra_enters') or 0))
                        delay_ms = int((profileB.get('nudge_enter_delay_ms') or 200))
                        if extra > 0:
                            for _ in range(min(extra, 3)):
                                time.sleep(max(0.0, delay_ms/1000.0))
                                tmux("send-keys","-t",pane,"Enter")
                    except Exception:
                        pass
                last_nudge_ts[label] = nowt
        except Exception:
            pass
        rlist, _, _ = select.select([sys.stdin], [], [], 0.5)
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
                    txt = events["peerA"]["to_user"].strip()
                    print_block("PeerA → USER", txt)
                    try:
                        eid = hashlib.sha1(txt.encode('utf-8', errors='ignore')).hexdigest()[:12]
                    except Exception:
                        eid = str(int(time.time()))
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
                    # If to_peer expresses RFD intent, log kind:rfd (so bridge can send a card)
                    _maybe_emit_rfd_from_to_peer(home, "PeerA", payload)
                    # If to_peer contains a unified diff, try to apply it (avoid “discuss diff but not land”)
                    inline = extract_inline_diff_if_any(payload) or ""
                    if inline:
                        print_block("PeerA inline patch", "Preflight …")
                        lines = count_changed_lines(inline)
                        allowed, reason = _rfd_gate_check(home, policies, inline, lines)
                        if allowed:
                            ok,err = git_apply_check(inline)
                            if ok:
                                ok2,err2 = git_apply(inline)
                                if ok2:
                                    try_lint(); tests_ok = try_tests(); git_commit("cccc(PeerA): apply inline patch (mailbox)")
                                    log_ledger(home, {"from":"PeerA","kind":"patch-commit-inline","lines":lines,"tests_ok":tests_ok})
                                    standby = (
                                        "<FROM_SYSTEM>\n"
                                        "Patch applied. Please standby and await the next instruction.\n"
                                        "</FROM_SYSTEM>\n"
                                    )
                                    _send_handoff("System", "PeerA", standby)
                                    _send_handoff("System", "PeerB", standby)
                                    # Clear original to_peer.md to avoid re-applying inline patch
                                    try:
                                        (home/"mailbox"/"peerA"/"to_peer.md").write_text("", encoding="utf-8")
                                    except Exception:
                                        pass
                                    # Emit a summary event for bridges (so external channels are informed)
                                    try:
                                        outbox_write(home, {"type":"to_peer_summary","from":"PeerA","to":"PeerB","text": payload, "eid": hashlib.sha1(payload.encode('utf-8','ignore')).hexdigest()[:12]})
                                    except Exception:
                                        pass
                                else:
                                    print("[PATCH] Apply failed:\n"+err2.strip())
                                    log_ledger(home, {"from":"PeerA","kind":"patch-apply-fail","stderr":err2.strip()[:2000]});
                            else:
                                print("[PATCH] Preflight failed:\n"+err.strip())
                                log_ledger(home, {"from":"PeerA","kind":"patch-precheck-fail","stderr":err.strip()[:2000]});
                        else:
                            # Record rejections due to RFD/policy reasons
                            log_ledger(home, {"from":"PeerA","kind":"patch-reject","reason":reason or "rfd-required","lines":lines})
                eff_enabled = handoff_filter_override if handoff_filter_override is not None else None
                if payload:
                    if should_forward(payload, "PeerA", "PeerB", policies, state, override_enabled=False):
                        wrapped = f"<FROM_PeerA>\n{payload}\n</FROM_PeerA>\n"
                        _send_handoff("PeerA", "PeerB", wrapped)
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
                if events["peerA"].get("patch"):
                    norm = normalize_mailbox_patch(events["peerA"]["patch"]) or ""
                    if not norm:
                        print("[PATCH] Skipped: patch.diff is not a unified diff or contains invalid content")
                    else:
                        patch = norm
                        print_block("PeerA patch", "Preflight …")
                        lines = count_changed_lines(patch)
                        allowed, reason = _rfd_gate_check(home, policies, patch, lines)
                        if allowed:
                            # Path allowlist check
                            paths = extract_paths_from_patch(patch)
                            if not allowed_by_policies(paths, policies):
                                log_ledger(home, {"from":"PeerA","kind":"patch-reject","reason":"path-not-allowed","paths":paths});
                            else:
                                ok,err = git_apply_check(patch)
                                if not ok:
                                    print("[PATCH] Preflight failed:\n"+err.strip())
                                    log_ledger(home, {"from":"PeerA","kind":"patch-precheck-fail","stderr":err.strip()[:2000]});
                                else:
                                    ok2,err2 = git_apply(patch)
                                    if not ok2:
                                        print("[PATCH] Apply failed:\n"+err2.strip())
                                        log_ledger(home, {"from":"PeerA","kind":"patch-apply-fail","stderr":err2.strip()[:2000]});
                                    else:
                                        try_lint(); tests_ok = try_tests(); git_commit("cccc(PeerA): apply patch (mailbox)")
                                        log_ledger(home, {"from":"PeerA","kind":"patch-commit","lines":lines,"tests_ok":tests_ok})
                                mbox_counts["peerA"]["patch"] += 1
                                _ack_receiver("PeerA", events["peerA"].get("patch"))
                                last_event_ts["PeerA"] = time.time()
                        else:
                            print(f"[POLICY] Patch requires RFD approval: {reason}")
                            log_ledger(home, {"from":"PeerA","kind":"patch-reject","reason":reason or "rfd-required","lines":lines})
                # PeerB events
                if events["peerB"].get("to_user"):
                    txt = events["peerB"].get("to_user"," ").strip()
                    try:
                        eid = hashlib.sha1(txt.encode('utf-8', errors='ignore')).hexdigest()[:12]
                    except Exception:
                        eid = str(int(time.time()))
                    outbox_write(home, {"type":"to_user","peer":"PeerB","text":txt,"eid":eid})
                    try:
                        (home/"mailbox"/"peerB"/"to_user.md").write_text("", encoding="utf-8")
                    except Exception:
                        pass
                if events["peerB"].get("to_peer"):
                    payload = events["peerB"]["to_peer"].strip()
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
                    _maybe_emit_rfd_from_to_peer(home, "PeerB", payload)
                    inline = extract_inline_diff_if_any(payload) or ""
                    if inline:
                        print_block("PeerB inline patch", "Preflight …")
                        lines = count_changed_lines(inline)
                        allowed, reason = _rfd_gate_check(home, policies, inline, lines)
                        if allowed:
                            ok,err = git_apply_check(inline)
                            if ok:
                                ok2,err2 = git_apply(inline)
                                if ok2:
                                    try_lint(); tests_ok = try_tests(); git_commit("cccc(PeerB): apply inline patch (mailbox)")
                                    log_ledger(home, {"from":"PeerB","kind":"patch-commit-inline","lines":lines,"tests_ok":tests_ok})
                                    standby = ("<FROM_SYSTEM>\nPatch applied. Please standby and await the next instruction.\n</FROM_SYSTEM>\n")
                                    _send_handoff("System", "PeerA", standby)
                                    _send_handoff("System", "PeerB", standby)
                                    # Clear original to_peer.md
                                    try:
                                        (home/"mailbox"/"peerB"/"to_peer.md").write_text("", encoding="utf-8")
                                    except Exception:
                                        pass
                                    # Emit a summary event
                                    try:
                                        outbox_write(home, {"type":"to_peer_summary","from":"PeerB","to":"PeerA","text": payload, "eid": hashlib.sha1(payload.encode('utf-8','ignore')).hexdigest()[:12]})
                                    except Exception:
                                        pass
                                else:
                                    print("[PATCH] Apply failed:\n"+err2.strip())
                                    log_ledger(home, {"from":"PeerB","kind":"patch-apply-fail","stderr":err2.strip()[:2000]});
                            else:
                                print("[PATCH] Preflight failed:\n"+err.strip())
                                log_ledger(home, {"from":"PeerB","kind":"patch-precheck-fail","stderr":err.strip()[:2000]});
                        else:
                            log_ledger(home, {"from":"PeerB","kind":"patch-reject","reason":reason or "rfd-required","lines":lines})
                    eff_enabled = handoff_filter_override if handoff_filter_override is not None else None
                    if payload:
                        if should_forward(payload, "PeerB", "PeerA", policies, state, override_enabled=False):
                            wrapped = f"<FROM_PeerB>\n{payload}\n</FROM_PeerB>\n"
                            _send_handoff("PeerB", "PeerA", wrapped)
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
                if events["peerB"].get("patch"):
                    norm = normalize_mailbox_patch(events["peerB"]["patch"]) or ""
                    if not norm:
                        print("[PATCH] Skipped: patch.diff is not a unified diff or contains invalid content")
                    else:
                        patch = norm
                        print_block("PeerB patch", "Preflight …")
                        lines = count_changed_lines(patch)
                        allowed, reason = _rfd_gate_check(home, policies, patch, lines)
                        if allowed:
                            paths = extract_paths_from_patch(patch)
                            if not allowed_by_policies(paths, policies):
                                log_ledger(home, {"from":"PeerB","kind":"patch-reject","reason":"path-not-allowed","paths":paths});
                            else:
                                ok,err = git_apply_check(patch)
                                if not ok:
                                    print("[PATCH] Preflight failed:\n"+err.strip())
                                    log_ledger(home, {"from":"PeerB","kind":"patch-precheck-fail","stderr":err.strip()[:2000]});
                                else:
                                    ok2,err2 = git_apply(patch)
                                    if not ok2:
                                        print("[PATCH] Apply failed:\n"+err2.strip())
                                        log_ledger(home, {"from":"PeerB","kind":"patch-apply-fail","stderr":err2.strip()[:2000]});
                                    else:
                                        try_lint(); tests_ok = try_tests(); git_commit("cccc(PeerB): apply patch (mailbox)")
                                        log_ledger(home, {"from":"PeerB","kind":"patch-commit","lines":lines,"tests_ok":tests_ok})
                                mbox_counts["peerB"]["patch"] += 1
                                _ack_receiver("PeerB", events["peerB"].get("patch"))
                                last_event_ts["PeerB"] = time.time()
                        else:
                            print(f"[POLICY] Patch requires RFD approval: {reason}")
                            log_ledger(home, {"from":"PeerB","kind":"patch-reject","reason":reason or "rfd-required","lines":lines})
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
            print("  /pause | /resume        → pause/resume A↔B handoff")
            print("  /refresh                → re-inject SYSTEM prompt")
            print("  /echo on|off|<empty>    → console echo on/off/show")
            print("  q                       → quit orchestrator")
            # Reprint prompt
        try:
            sys.stdout.write("> "); sys.stdout.flush()
        except Exception:
            pass
        continue
        if line == "/refresh":
            sysA = weave_system(home, "peerA"); sysB = weave_system(home, "peerB")
            _send_handoff("System", "PeerA", f"<FROM_SYSTEM>\n{sysA}\n</FROM_SYSTEM>\n")
            _send_handoff("System", "PeerB", f"<FROM_SYSTEM>\n{sysB}\n</FROM_SYSTEM>\n")
            print("[SYSTEM] Refreshed (mailbox delivery)."); continue
        if line == "/pause":
            deliver_paused = True
            print("[PAUSE] Paused A↔B handoff (still collect <TO_USER> and patch preflight)"); write_status(True); continue
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
        # Lazy preamble: prepend on the first user message per peer (config-driven)
        LAZY = (delivery_conf.get("lazy_preamble") or {}) if isinstance(delivery_conf.get("lazy_preamble"), dict) else {}
        LAZY_ENABLED = bool(LAZY.get("enabled", True))

        # State helpers for preamble-sent flags
        def _preamble_state_path() -> Path:
            return home/"state"/"preamble_sent.json"
        def _load_preamble_sent() -> Dict[str,bool]:
            p = _preamble_state_path();
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {"PeerA": False, "PeerB": False}
        def _save_preamble_sent(st: Dict[str,bool]):
            p = _preamble_state_path(); p.parent.mkdir(parents=True, exist_ok=True)
            try:
                p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
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
                _send_raw_to_cli(home, 'PeerA', msg, modeA, modeB, left, right)
            continue
        if line.startswith("b!"):
            msg = line[2:].strip()
            if msg:
                _send_raw_to_cli(home, 'PeerB', msg, modeA, modeB, left, right)
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
