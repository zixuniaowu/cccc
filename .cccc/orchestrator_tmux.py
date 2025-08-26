# -*- coding: utf-8 -*-
"""
CCCC Orchestrator (tmux + long-lived CLI sessions)
- 左右两 pane 分别运行 PeerA(Claude) / PeerB(Codex) 的交互式会话（长连接）
- 通过 tmux 粘贴消息/抓取输出，解析 <TO_USER>/<TO_PEER> 与 ```patch```，执行预检/应用/测试/记账
- 启动时注入极简 SYSTEM（来源于 prompt_weaver）；移除运行时热更新以保持简洁可控
"""
import os, re, sys, json, time, shlex, tempfile, fnmatch, subprocess, select, hashlib, io, shutil
from glob import glob
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from delivery import deliver_or_queue, flush_outbox_if_idle, PaneIdleJudge, new_mid, wrap_with_mid, send_text
from mailbox import ensure_mailbox, MailboxIndex, scan_mailboxes, reset_mailbox

ANSI_RE = re.compile(r"\x1b\[.*?m|\x1b\[?[\d;]*[A-Za-z]")  # 去色
# 控制控制台是否回显 AI 输出块，默认关闭以避免干扰输入体验
CONSOLE_ECHO = True
PATCH_RE = re.compile(r"```(?:patch|diff)\s*([\s\S]*?)```", re.I)
SECTION_RE_TPL = r"<\s*{tag}\s*>([\s\S]*?)</\s*{tag}\s*>"
INPUT_END_MARK = "[CCCC_INPUT_END]"

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

def ensure_bin(name: str):
    code,_,_ = run(f"command -v {shlex.quote(name)}")
    if code != 0:
        print(f"[FATAL] 需要可执行: {name}")
        raise SystemExit(1)
def has_bin(name: str) -> bool:
    code,_,_ = run(f"command -v {shlex.quote(name)}"); return code==0

def ensure_git_repo():
    code, out, _ = run("git rev-parse --is-inside-work-tree")
    if code != 0 or "true" not in out:
        print("[INFO] 当前目录不是 git 仓库，正在初始化 …")
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
    if code!=0: raise RuntimeError(f"tmux new-session 失败: {err}")
    # 初始只保留一个 pane，后续按我们定义重建布局
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
    """构造稳定 2x2 布局，根据 pane 坐标映射 {'lt','rt','lb','rb'}。"""
    target = _win(session)
    # 干净起步：仅保留 pane 0
    tmux("select-pane","-t",f"{target}.0")
    tmux("kill-pane","-a","-t",f"{target}.0")
    # 横向 split，得到两个上方 pane
    rc,_,err = tmux("split-window","-h","-t",f"{target}.0")
    if rc != 0:
        print(f"[TMUX] split horizontal failed: {err.strip()}")
    tmux("select-layout","-t",target,"tiled")
    # 取坐标，识别上方左右两个 pane
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
        # 兜底使用索引
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
    # 对左右各自纵向 split，得到左下/右下
    rc,_,err = tmux("split-window","-v","-t",lt)
    if rc != 0:
        print(f"[TMUX] split lt vertical failed: {err.strip()}")
    rc,_,err = tmux("split-window","-v","-t",rt)
    if rc != 0:
        print(f"[TMUX] split rt vertical failed: {err.strip()}")
    tmux("select-layout","-t",target,"tiled")
    # 最终读取 4 个 pane，按坐标映射
    code,out,_ = tmux("list-panes","-t",target,"-F","#{pane_id} #{pane_left} #{pane_top}")
    panes=[]
    for ln in out.splitlines():
        try:
            pid, left, top = ln.strip().split()
            panes.append((pid, int(left), int(top)))
        except Exception:
            pass
    # 识别上/下行
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
    # 打印 pane 列表与坐标用于排查
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
    # 更稳健地读取控制台输入，避免特殊序列导致异常
    try:
        s = input(prompt)
    except Exception:
        s = sys.stdin.readline()
    return sanitize_console(s)


def tmux_paste(pane: str, text: str):
    # 以二进制写入，宽容处理输入中的代理码位/控制序列
    data = text.encode("utf-8", errors="replace")
    with tempfile.NamedTemporaryFile("wb", delete=False) as f:
        f.write(data); fname=f.name
    buf = f"buf-{int(time.time()*1000)}"
    tmux("load-buffer","-b",buf,fname)
    tmux("paste-buffer","-t",pane,"-b",buf)
    time.sleep(0.12)
    # 统一只发送一次回车，避免重复提交
    tmux("send-keys","-t",pane,"Enter")
    tmux("delete-buffer","-b",buf)
    try: os.unlink(fname)
    except Exception: pass

def tmux_type(pane: str, text: str):
    # 保留用于启动/应急；常规发送走 delivery.send_text
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
    # 更稳健：直接在 pane 内以 bash -lc 运行命令，避免粘贴方式误发到错误 pane
    wrapped = f"bash -lc {shlex.quote(cmd)}"
    tmux_respawn_pane(pane, wrapped)

def wait_for_ready(pane: str, profile: Dict[str,Any], *, timeout: float = 12.0) -> bool:
    """Wait until the pane appears idle (prompt+quiet or quiet-only). Pokes with a newline."""
    judge = PaneIdleJudge(profile)
    t0 = time.time(); poked = False
    while time.time() - t0 < timeout:
        idle, reason = judge.refresh(pane)
        if idle:
            return True
        # After 1.5s without prompt, send a newline to coax prompt
        if not poked and time.time() - t0 > 1.5:
            tmux("send-keys","-t",pane,"Enter")
            poked = True
        time.sleep(0.25)
    return False

def paste_when_ready(pane: str, profile: Dict[str,Any], text: str, *, timeout: float = 10.0):
    ok = wait_for_ready(pane, profile, timeout=timeout)
    if not ok:
        print(f"[WARN] 目标 pane 未就绪，仍尝试贴入（best-effort）。")
    # 统一通过 delivery 的 send_text，使用 per-CLI 配置（含回车发送/换行键）
    send_text(pane, text, profile)

# ---------- YAML & prompts ----------
def read_yaml(p: Path) -> Dict[str,Any]:
    if not p.exists(): return {}
    try:
        import yaml; return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except ImportError:
        d={}; 
        for line in p.read_text(encoding="utf-8").splitlines():
            line=line.strip()
            if not line or line.startswith("#") or ":" not in line: continue
            k,v=line.split(":",1); d[k.strip()]=v.strip().strip('"\'')
        return d

# Removed legacy file reader helper; config is loaded via read_yaml at startup.

# ---------- ledger & policies ----------
def log_ledger(home: Path, entry: Dict[str,Any]):
    state = home/"state"; state.mkdir(exist_ok=True)
    entry={"ts":time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
    with (state/"ledger.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps(entry,ensure_ascii=False)+"\n")

def allowed_by_policies(paths: List[str], policies: Dict[str,Any]) -> bool:
    allowed = policies.get("patch_queue",{}).get("allowed_paths",["**"])
    for pth in paths:
        if not any(fnmatch.fnmatch(pth,pat) for pat in allowed):
            print(f"[POLICY] 路径不允许: {pth}")
            return False
    return True


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
                print("[LINT] 跳过（检测到 eslint 但无配置）"); return
            cmd = "eslint . --max-warnings=0"
        else:
            print("[LINT] 跳过（未设 LINT_CMD，且未检测到 ruff/eslint）"); return
    code,out,err=run(cmd)
    print("[LINT]", "通过" if code==0 else "失败")
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
                print("[TEST] 跳过（未发现 pytest 测试文件）"); return True
        elif has_bin("npm") and Path("package.json").exists():
            try:
                pj = json.loads(Path("package.json").read_text(encoding="utf-8"))
                test_script = (pj.get("scripts") or {}).get("test")
                if not test_script:
                    print("[TEST] 跳过（package.json 无 test 脚本）"); return True
                # Skip the default placeholder script
                if "no test specified" in test_script:
                    print("[TEST] 跳过（默认占位 npm test 脚本）"); return True
                cmd="npm test --silent"
            except Exception:
                print("[TEST] 跳过（解析 package.json 失败）"); return True
        else:
            print("[TEST] 跳过（未设 TEST_CMD，也未检测到 pytest/npm）"); return True
    code,out,err=run(cmd)
    ok=(code==0)
    print("[TEST]", "通过" if ok else "失败")
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

# ---------- watcher ----------
# Note: runtime hot-reload of settings/prompts/personas removed for simplicity.

# ---------- EXCHANGE ----------
def print_block(title: str, body: str):
    """可切换的控制台回显：默认静默，避免打字时被刷屏打断。
    内容仍会进入 mailbox/ledger/面板，不丢信息。
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
    # 直接贴入极简消息，不注入上下文与包装标识（由调用方传入 FROM_* 标签）
    before_len = len(tmux_capture(sender_pane, lines=800))
    paste_when_ready(sender_pane, sender_profile, payload)
    # 等待响应：优先等到 <TO_USER>/<TO_PEER>/```diff 出现或回到空闲提示符
    judge = PaneIdleJudge(sender_profile)
    start = time.time()
    timeout = float(delivery_conf.get("read_timeout_seconds", 8))
    window = ""
    while time.time() - start < timeout:
        content = tmux_capture(sender_pane, lines=800)
        window = content[before_len:]
        # 移除我们自己的 FROM_* 包装不会产生，保留窗口供上层诊断（mailbox 模式下不依赖此路径）
        if ("<TO_USER>" in window) or ("<TO_PEER>" in window) or ("```diff" in window) or ("```patch" in window):
            break
        idle, _ = judge.refresh(sender_pane)
        if idle and time.time() - start > 1.2:
            break
        time.sleep(0.25)
    # 仅解析最近一次 INPUT 之后模型的输出，避免误采集 SYSTEM 或我们贴入的 <TO_*>
    # window 已在等待循环中计算
    # 解析最新的三分区（仅在窗口中查找）
    def last(tag):
        items=re.findall(SECTION_RE_TPL.format(tag=tag), window, re.I)
        return (items[-1].strip() if items else "")
    to_user = last("TO_USER"); to_peer = last("TO_PEER")
    # 不在此处打印 <TO_USER>，避免与后台轮询重复；仅处理补丁与交接

    # 仅扫描窗口中的补丁围栏，过滤掉非真实 diff 的围栏
    patches = [p for p in extract_patches(window) if is_valid_diff(p)]
    for i,patch in enumerate(patches,1):
        print_block(f"{who} 补丁#{i}", "预检中 …")
        lines = count_changed_lines(patch)
        max_lines = int(policies.get("patch_queue",{}).get("max_diff_lines",150))
        if lines>max_lines:
            print(f"[POLICY] 改动行数 {lines} > {max_lines}，拒绝。")
            log_ledger(home, {"from":who,"kind":"patch-reject","reason":"too-many-lines","lines":lines}); 
            continue
        paths = extract_paths_from_patch(patch)
        if not allowed_by_policies(paths, policies):
            log_ledger(home, {"from":who,"kind":"patch-reject","reason":"path-not-allowed","paths":paths}); 
            continue
        ok,err = git_apply_check(patch)
        if not ok:
            print("[PATCH] 预检失败：\n"+err.strip()); 
            log_ledger(home, {"from":who,"kind":"patch-precheck-fail","stderr":err.strip()[:2000]}); 
            continue
        ok2,err2 = git_apply(patch)
        if not ok2:
            print("[PATCH] 应用失败：\n"+err2.strip()); 
            log_ledger(home, {"from":who,"kind":"patch-apply-fail","stderr":err2.strip()[:2000]}); 
            continue
        try_lint()
        tests_ok = try_tests()
        git_commit(f"cccc({who}): apply patch (phase {phase})")
        log_ledger(home, {"from":who,"kind":"patch-commit","paths":paths,"lines":lines,"tests_ok":tests_ok})
        if not tests_ok:
            fb = "<TO_PEER>\ntype: EVIDENCE\nintent: fix\ntasks:\n  - desc: '测试失败，请提供最小修复补丁'\n</TO_PEER>\n"
            paste_when_ready(sender_pane, sender_profile, f"[INPUT]\n{fb}\n")

    if to_peer.strip():
        # 去重：避免重复交接同一内容
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
            if who == "PeerA":
                status, mid = deliver_or_queue(home, receiver_pane, "peerB", to_peer, profileB, delivery_conf)
            else:
                status, mid = deliver_or_queue(home, receiver_pane, "peerA", to_peer, profileA, delivery_conf)
            log_ledger(home, {"from": who, "kind": "handoff", "status": status, "mid": mid, "chars": len(to_peer)})
            print(f"[HANDOFF] {who} → {'PeerB' if who=='PeerA' else 'PeerA'} ({len(to_peer)} chars, status={status})")

def scan_and_process_after_input(home: Path, pane: str, other_pane: str, who: str,
                                 policies: Dict[str,Any], phase: str,
                                 profileA: Dict[str,Any], profileB: Dict[str,Any], delivery_conf: Dict[str,Any],
                                 deliver_enabled: bool, last_windows: Dict[str,int],
                                 dedup_user: Dict[str,str], dedup_peer: Dict[str,str]):
    # 捕获全窗口，并在全窗口中解析，避免因 TUI 清屏或回显策略导致的长度倒退/无增长
    content = tmux_capture(pane, lines=1000)
    # 记录总长度（用于诊断），但不作为唯一 gating 条件
    last_windows[who] = len(content)
    # 移除我们贴入且被回显的 [INPUT]...END 段，避免误解析
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
        print_block(f"{who} 补丁#{i}", "预检中 …")
        lines = count_changed_lines(patch)
        max_lines = int(policies.get("patch_queue",{}).get("max_diff_lines",150))
        if lines>max_lines:
            print(f"[POLICY] 改动行数 {lines} > {max_lines}，拒绝。")
            log_ledger(home, {"from":who,"kind":"patch-reject","reason":"too-many-lines","lines":lines}); 
            continue
        paths = extract_paths_from_patch(patch)
        if not allowed_by_policies(paths, policies):
            log_ledger(home, {"from":who,"kind":"patch-reject","reason":"path-not-allowed","paths":paths}); 
            continue
        ok,err = git_apply_check(patch)
        if not ok:
            print("[PATCH] 预检失败：\n"+err.strip()); 
            log_ledger(home, {"from":who,"kind":"patch-precheck-fail","stderr":err.strip()[:2000]}); 
            continue
        ok2,err2 = git_apply(patch)
        if not ok2:
            print("[PATCH] 应用失败：\n"+err2.strip()); 
            log_ledger(home, {"from":who,"kind":"patch-apply-fail","stderr":err2.strip()[:2000]}); 
            continue
        try_lint()
        tests_ok = try_tests()
        git_commit(f"cccc({who}): apply patch (phase {phase})")
        log_ledger(home, {"from":who,"kind":"patch-commit","paths":paths,"lines":lines,"tests_ok":tests_ok})
        if not tests_ok:
            fb = "<TO_PEER>\ntype: EVIDENCE\nintent: fix\ntasks:\n  - desc: '测试失败，请提供最小修复补丁'\n</TO_PEER>\n"
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
    # 目录
    settings = home/"settings"; state = home/"state"
    state.mkdir(exist_ok=True)

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

    # 准备 tmux 会话/面板
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
    # Enable mouse wheel scroll for history while keeping send safety (we cancel copy-mode before sending)
    tmux("bind-key","-n","WheelUpPane","copy-mode","-e")
    tmux("bind-key","-n","WheelDownPane","send-keys","-M")
    print(f"[INFO] 使用 tmux 会话: {session}（左=PeerA / 右=PeerB）")
    print(f"[INFO] pane map: left={left} right={right} lb={pos.get('lb')} rb={pos.get('rb')}")
    print(f"[TIP] 另开终端可随时 `tmux attach -t {session}` 旁观/插话")
    # 确保四分屏：左/右=A/B；左下=ledger tail；右下=帮助
    # 在左下/右下 pane 启动 ledger 与帮助
    lp = shlex.quote(str(state/"ledger.jsonl"))
    # 在 pane 内直接执行命令（使用 respawn-pane 更稳健）
    cmd_ledger_sh = f"bash -lc \"printf %s {bash_ansi_c_quote('[CCCC Ledger]\\n')}; tail -F {lp} 2>/dev/null || tail -f {lp}\""
    tmux_respawn_pane(pos['lb'], cmd_ledger_sh)
    # 右下状态面板：运行内置 Python 渲染器，实时读取 ledger/status
    py = shlex.quote(sys.executable or 'python3')
    status_py = shlex.quote(str(home/"panel_status.py"))
    cmd_status = f"bash -lc {shlex.quote(f'{py} {status_py} --home {str(home)} --interval 1.0')}"
    tmux_respawn_pane(pos['rb'], cmd_status)

    # —— PROJECT.md 启动分支：在启动 CLI 之前做选择 ——
    project_md_path = Path.cwd()/"PROJECT.md"
    project_md_exists = project_md_path.exists()
    start_mode = "has_doc" if project_md_exists else "ask"  # has_doc | ai_bootstrap | ask
    if not project_md_exists:
        print("\n[PROJECT] 未检测到 PROJECT.md。请选择：")
        print("  1) 我先编写 PROJECT.md，再继续启动 CLI")
        print("  2) 直接启动 CLI，让 AI 协作生成 PROJECT.md（仅允许修改 PROJECT.md）")
        while True:
            ans = read_console_line("选择 1 或 2 并回车: ").strip().lower()
            if ans in ("1", "a", "user", "u"):
                print("[PROJECT] 等待你在仓库根目录创建 PROJECT.md …")
                while not project_md_path.exists():
                    nxt = read_console_line("- 创建完成后回车继续；或输入 2 切换为 AI 生成: ").strip().lower()
                    if nxt in ("2", "b", "ai"):
                        start_mode = "ai_bootstrap"; break
                if project_md_path.exists():
                    start_mode = "has_doc"; project_md_exists = True
                break
            if ans in ("2", "b", "ai"):
                start_mode = "ai_bootstrap"; break
            print("[HINT] 请输入 1 或 2。")

    # 启动交互式 CLI（缺省回退到内置 Mock）
    commands = cli_profiles.get("commands", {}) if isinstance(cli_profiles.get("commands", {}), dict) else {}
    CLAUDE_I_CMD = os.environ.get("CLAUDE_I_CMD") or commands.get("peerA") or f"python {shlex.quote(str(home/'mock_agent.py'))} --role peerA"
    CODEX_I_CMD  = os.environ.get("CODEX_I_CMD")  or commands.get("peerB") or f"python {shlex.quote(str(home/'mock_agent.py'))} --role peerB"
    if (commands.get("peerA") is None and os.environ.get("CLAUDE_I_CMD") is None) or \
       (commands.get("peerB") is None and os.environ.get("CODEX_I_CMD") is None):
        print("[INFO] 未提供全部 CLI 命令，缺失的一侧将使用内置 Mock（可在 cli_profiles.yaml 的 commands 中配置，或用环境变量覆盖）。")
    else:
        print("[INFO] 已使用配置中的 CLI 命令（可用环境变量覆盖）。")
    if start_mode in ("has_doc", "ai_bootstrap"):
        if modeA == 'bridge':
            # Ensure pexpect is available; otherwise fallback to tmux mode for visibility
            pyexe = shlex.quote(sys.executable or 'python3')
            code,_,_ = run(f"{pyexe} -c 'import pexpect'")
            if code != 0:
                print("[WARN] pexpect 未安装，PeerA bridge 模式不可用，回退到 tmux 输入注入（请 pip install pexpect）")
                modeA = 'tmux'
        if modeB == 'bridge':
            # Ensure pexpect is available; otherwise fallback to tmux mode
            pyexe = shlex.quote(sys.executable or 'python3')
            code,_,_ = run(f"{pyexe} -c 'import pexpect'")
            if code != 0:
                print("[WARN] pexpect 未安装，PeerB bridge 模式不可用，回退到 tmux 输入注入（请 pip install pexpect）")
                modeB = 'tmux'
        if modeA == 'bridge':
            # Run bridge adapter in pane; it will spawn the CLI child and proxy stdout
            py = sys.executable or 'python3'
            bridge_py = str(home/"adapters"/"bridge.py")
            inbox = str(home/"mailbox"/"peerA"/"inbox.md")
            inner = f"{shlex.quote(py)} {shlex.quote(bridge_py)} --home {shlex.quote(str(home))} --peer peerA --cmd {shlex.quote(CLAUDE_I_CMD)} --inbox {shlex.quote(inbox)}"
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
            inner = f"{shlex.quote(py)} {shlex.quote(bridge_py)} --home {shlex.quote(str(home))} --peer peerB --cmd {shlex.quote(CODEX_I_CMD)} --inbox {shlex.quote(inbox)}"
            cmd = f"bash -lc {shlex.quote(inner)}"
            tmux_respawn_pane(right, cmd)
            print(f"[LAUNCH] PeerB mode=bridge pane={right} bridge_cmd={inner}")
        else:
            tmux_start_interactive(right, CODEX_I_CMD)
            print(f"[LAUNCH] PeerB mode=tmux pane={right} cmd={CODEX_I_CMD}")
        # Debug: show current commands per pane
        try:
            code,out,err = tmux('list-panes','-F','#{pane_id} #{pane_current_command}')
            if code == 0 and out.strip():
                print('[DEBUG] pane commands:\n' + out.strip())
        except Exception:
            pass
        # 等待两侧 CLI 就绪（出现提示符并短暂安静）
        wait_for_ready(left,  profileA, timeout=float(cli_profiles.get("startup_wait_seconds", 12)))
        wait_for_ready(right, profileB, timeout=float(cli_profiles.get("startup_wait_seconds", 12)))

    # 在注入之后立即记录当前捕获长度，作为解析基线
    left_snap  = tmux_capture(left,  lines=800)
    right_snap = tmux_capture(right, lines=800)
    last_windows = {"PeerA": len(left_snap), "PeerB": len(right_snap)}
    dedup_user = {}
    dedup_peer = {}

    # 简化：不再监听文件热更；修改 roles/policies/personas 需重启生效

    # 初始化并清空 mailbox，避免读取到上次残留内容
    ensure_mailbox(home)
    reset_mailbox(home)
    mbox_idx = MailboxIndex(state)
    mbox_counts = {"peerA": {"to_user":0, "to_peer":0, "patch":0},
                   "peerB": {"to_user":0, "to_peer":0, "patch":0}}
    mbox_last = {"peerA": {"to_user": "-", "to_peer": "-", "patch": "-"},
                 "peerB": {"to_user": "-", "to_peer": "-", "patch": "-"}}
    # 记录各 peer 最近一次 mailbox 活动时间（用于 timeout 软 ACK 判定）
    last_event_ts = {"PeerA": 0.0, "PeerB": 0.0}
    handoff_filter_override: Optional[bool] = None
    # 精简：不做会话顺序化；广播即发，秩序由提示词约束

    # 交接背压：按接收方维护 in-flight 与等待队列
    inflight: Dict[str, Optional[Dict[str,Any]]] = {"PeerA": None, "PeerB": None}
    queued: Dict[str, List[Dict[str,Any]]] = {"PeerA": [], "PeerB": []}
    # 简易重复发送防抖（以 payload 哈希为键，在短窗口内丢弃同内容的重复投递）
    recent_sends: Dict[str, List[Dict[str,Any]]] = {"PeerA": [], "PeerB": []}
    ack_timeout = float((cli_profiles.get("delivery", {}) or {}).get("ack_timeout_seconds", 30))
    resend_attempts = int((cli_profiles.get("delivery", {}) or {}).get("resend_attempts", 2))
    ack_require_mid = bool((cli_profiles.get("delivery", {}) or {}).get("ack_require_mid", False))
    duplicate_window = float((cli_profiles.get("delivery", {}) or {}).get("duplicate_window_seconds", 90))

    def _receiver_map(name: str) -> Tuple[str, Dict[str,Any]]:
        if name == "PeerA":
            return left, profileA
        return right, profileB

    # Pane idle judges for optional soft-ACK
    judges: Dict[str, PaneIdleJudge] = {"PeerA": PaneIdleJudge(profileA), "PeerB": PaneIdleJudge(profileB)}

    def _mailbox_peer_name(peer_label: str) -> str:
        return "peerA" if peer_label == "PeerA" else "peerB"

    def _send_handoff(sender_label: str, receiver_label: str, payload: str, require_mid: Optional[bool]=None):
        # 背压：若接收方正有在飞，进入队列
        if inflight[receiver_label] is not None:
            queued[receiver_label].append({"sender": sender_label, "payload": payload})
            log_ledger(home, {"from": sender_label, "kind": "handoff-queued", "to": receiver_label, "chars": len(payload)})
            return
        # 追加入站后缀（按来源 from_user/from_peer/from_system 可分别配置；向后兼容字符串配置）
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
            # 兼容旧配置：字符串
            if receiver == 'PeerA':
                return str(cfg).strip()
            if receiver == 'PeerB' and sender == 'User':
                return str(cfg).strip()
            return ''
        suf = _suffix_for(receiver_label, sender_label)
        if suf:
            payload = _append_suffix_inside(payload, suf)
        # 重复发送防抖：在短窗口内，丢弃与上次相同 payload 的投递
        h = hashlib.sha1(payload.encode('utf-8', errors='replace')).hexdigest()
        now = time.time()
        rs = [it for it in recent_sends[receiver_label] if now - float(it.get('ts',0)) <= duplicate_window]
        if any(it.get('hash') == h for it in rs):
            log_ledger(home, {"from": sender_label, "kind": "handoff-duplicate-drop", "to": receiver_label, "chars": len(payload)})
            return
        rs.append({"hash": h, "ts": now})
        recent_sends[receiver_label] = rs[-20:]
        # 发送并挂起等待 ACK（以 mailbox 事件作为 ACK）
        mid = new_mid()
        text_with_mid = wrap_with_mid(payload, mid)
        status = "delivered"
        out_mid = mid
        # 根据 delivery_mode 选择投递路径
        if receiver_label == 'PeerA' and modeA == 'bridge':
            # 写入 inbox.md，由 adapter 注入
            inbox = home/"mailbox"/"peerA"/"inbox.md"
            try:
                inbox.write_text(text_with_mid, encoding='utf-8')
            except Exception:
                status = "failed"
        elif receiver_label == 'PeerB' and modeB == 'bridge':
            inbox = home/"mailbox"/"peerB"/"inbox.md"
            try:
                inbox.write_text(text_with_mid, encoding='utf-8')
            except Exception:
                status = "failed"
        else:
            pane, prof = _receiver_map(receiver_label)
            status, out_mid = deliver_or_queue(home, pane, _mailbox_peer_name(receiver_label), payload, prof, delivery_conf, mid=mid)
        eff_req_mid = ack_require_mid if require_mid is None else bool(require_mid)
        inflight[receiver_label] = {"mid": out_mid, "ts": time.time(), "attempts": 1, "sender": sender_label, "payload": payload, "require_mid": eff_req_mid}
        log_ledger(home, {"from": sender_label, "kind": "handoff", "to": receiver_label, "status": status, "mid": out_mid, "chars": len(payload)})
        print(f"[HANDOFF] {sender_label} → {receiver_label} ({len(payload)} chars, status={status})")

    def _ack_receiver(label: str, event_text: Optional[str] = None):
        # ACK 策略：
        # - 若 ack_require_mid=True：仅当事件文本包含 [MID: *] 时确认
        # - 若 ack_require_mid=False：任意事件均视为 ACK（兼容不严格回显 MID 的 CLI）
        infl = inflight.get(label)
        if not infl:
            return
        if event_text:
            # per-message 强制 MID：仅当 require_mid=False 或事件文本包含 MID 时确认
            need_mid = bool(infl.get('require_mid', False))
            if (not need_mid) or (str(infl.get("mid","")) in event_text):
                cur_mid = infl.get("mid")
                inflight[label] = None
                # 清理队列中与当前 mid 相同的条目（例如超时后回队列的同一消息）
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
            # Per-label overrides: for bridge receivers，默认避免重发；但若该消息 require_mid=True，则允许有限重发
            eff_timeout = ack_timeout
            eff_resend = resend_attempts
            if (label == 'PeerA' and modeA == 'bridge') or (label == 'PeerB' and modeB == 'bridge'):
                eff_timeout = max(eff_timeout, 90.0)
                if not bool(infl.get('require_mid', False)):
                    eff_resend = 0
            # Soft-ACK: if receiver pane is idle, consider delivery successful
            pane, prof = _receiver_map(label)
            idle, _r = judges[label].refresh(pane)
            # 不再将“pane 空闲”视为 ACK，避免误判
            # 仍然允许后续基于 [MID] 的强 ACK
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
                    # 超出重试次数（或 eff_resend=0）：在 bridge 模式下，若期间有任何 mailbox 活动，视为软 ACK；否则直接丢弃以避免重复注入
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

    # 等待用户指令模式（不强制初始 requirement）
    phase = "discovery"
    ctx = context_blob(policies, phase)
    # 简化：默认不暂停交接，避免过度干涉；必要时由用户手动 /pause
    deliver_paused = False

    # 初始状态快照写入，供状态面板读取
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
    write_status(deliver_paused)

    # 统一合并首条系统消息（SYSTEM + 项目指令），一次发送，避免节奏混乱
    if start_mode in ("has_doc", "ai_bootstrap"):
        sysA = weave_system(home, "peerA"); sysB = weave_system(home, "peerB")
        if start_mode == "ai_bootstrap":
            proj_block = (
                "Initial Task: Collaborate to create a high-quality PROJECT.md for this repository.\n\n"
                "Rules:\n"
                "- Allowed file change: create/update PROJECT.md only. Do not modify .cccc/** or other files.\n"
                "- Use mailbox: write draft content to to_user.md, questions/negotiation to to_peer.md.\n"
                "- When ready, produce a unified diff for PROJECT.md in patch.diff (small, single-file patch).\n"
                "- Keep changes minimal and reversible.\n\n"
                "Content Requirements (suggested sections):\n"
                "- Purpose & Context; Scope & Non-Goals; Tech Snapshot; Build/Run/CI; Quality Gates; Constraints; Risks & Next Steps.\n\n"
                "Collaboration:\n"
                "- PeerA leads structure and wording; PeerB compiles repo facts and verifies claims. Prefer evidence.\n\n"
                "Acceptance:\n"
                "- Provide a single unified diff that adds PROJECT.md, ≤ 200 changed lines.\n"
                "- After apply, re-run lint/tests (if configured) and report status to user.\n"
            )
        else:
            proj_block = (
                "Please read PROJECT.md to understand goals, scope, constraints and CI gates.\n"
                "Then output a ≤5-line summary to the user and standby.\n"
                "Do not modify any files until further instruction.\n"
            )
        combinedA = f"<FROM_SYSTEM>\n{sysA}\n\n{proj_block}\n</FROM_SYSTEM>\n"
        combinedB = f"<FROM_SYSTEM>\n{sysB}\n\n{proj_block}\n</FROM_SYSTEM>\n"
        _send_handoff("System", "PeerA", combinedA)
        _send_handoff("System", "PeerB", combinedB)
        log_ledger(home, {"from":"system","kind":"system-boot","peer":"A","status":"queued"})
        log_ledger(home, {"from":"system","kind":"system-boot","peer":"B","status":"queued"})

    print("\n[就绪] 常用：a:/b:/both:/u: 发送消息；/pause|/resume 交接；/refresh 刷新 SYSTEM；q 退出。")
    print("[TIP] 控制台输入不被干扰：默认关闭 AI 输出回显。用 /echo on 开启，/echo off 关闭，/echo 查看状态。")
    print("[TIP] 直通模式：a! <cmd> 或 b! <cmd> 直接把命令发到对应 CLI（无包装），例如 a! /model")

    # last_windows/dedup_* 已在握手后初始化

    # 已合并发送，无需重复单独下发
    if start_mode == "ai_bootstrap":
        print("[PROJECT] 已选择由 AI 协作生成 PROJECT.md，已合并随首条 SYSTEM 一并发送。")

    # 若已存在 PROJECT.md：提示快速阅读并待命（不强制暂停 A↔B，减少干涉）
    if start_mode == "has_doc":
        print("[PROJECT] 已检测到 PROJECT.md，指令已合并随首条 SYSTEM 一并发送。")

    while True:
        # 精简：不做阶段锁/解锁；仅在启动时发送清晰指示；去除运行时 SYSTEM 热更新

        # 非阻塞轮询：优先读取控制台输入；若无输入则扫描 A/B 输出
        line = None
        rlist, _, _ = select.select([sys.stdin], [], [], 0.5)
        if rlist:
            line = read_console_line("\n> ").strip()
        else:
            # Mailbox 轮询：消费结构化输出（不再抓屏解析）。
            # 为避免打字时被回显干扰，默认静音扫描期间的 console 打印。
            _stdout_saved = sys.stdout
            if not CONSOLE_ECHO:
                sys.stdout = io.StringIO()
            try:
                events = scan_mailboxes(home, mbox_idx)
                payload = ""  # guard variable for conditional forwarding
                # PeerA 事件
                if events["peerA"].get("to_user"):
                    print_block("PeerA → USER", events["peerA"]["to_user"])
                    log_ledger(home, {"from":"PeerA","kind":"to_user","route":"mailbox","chars":len(events["peerA"]["to_user"])})
                    _ack_receiver("PeerA", events["peerA"]["to_user"])  # 视作对 PeerA 的 ACK（其刚刚收到对等方消息后回应）
                    mbox_counts["peerA"]["to_user"] += 1
                    mbox_last["peerA"]["to_user"] = time.strftime("%H:%M:%S")
                    last_event_ts["PeerA"] = time.time()
                if events["peerA"].get("to_peer"):
                    payload = events["peerA"]["to_peer"].strip()
                    # 任意 mailbox 活动均可作为 ACK
                    _ack_receiver("PeerA", payload)
                    mbox_counts["peerA"]["to_peer"] += 1
                    mbox_last["peerA"]["to_peer"] = time.strftime("%H:%M:%S")
                    last_event_ts["PeerA"] = time.time()
                    # 若 to_peer 中包含统一 diff，尝试直接应用（避免“讨论了 diff 但未落地”）
                    inline = extract_inline_diff_if_any(payload) or ""
                    if inline:
                        print_block("PeerA 内嵌补丁", "预检中 …")
                        lines = count_changed_lines(inline)
                        max_lines = int(policies.get("patch_queue",{}).get("max_diff_lines",150))
                        if lines <= max_lines:
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
                                else:
                                    print("[PATCH] 应用失败：\n"+err2.strip())
                                    log_ledger(home, {"from":"PeerA","kind":"patch-apply-fail","stderr":err2.strip()[:2000]});
                            else:
                                print("[PATCH] 预检失败：\n"+err.strip())
                                log_ledger(home, {"from":"PeerA","kind":"patch-precheck-fail","stderr":err.strip()[:2000]});
                eff_enabled = handoff_filter_override if handoff_filter_override is not None else None
                if payload:
                    if should_forward(payload, "PeerA", "PeerB", policies, state, eff_enabled):
                        wrapped = f"<FROM_PeerA>\n{payload}\n</FROM_PeerA>\n"
                        _send_handoff("PeerA", "PeerB", wrapped)
                    else:
                        log_ledger(home, {"from":"PeerA","kind":"handoff-drop","route":"mailbox","reason":"low-signal-or-cooldown","chars":len(payload)})
                if events["peerA"].get("patch"):
                    norm = normalize_mailbox_patch(events["peerA"]["patch"]) or ""
                    if not norm:
                        print("[PATCH] 跳过：patch.diff 非统一 diff 或包含无效内容")
                    else:
                        patch = norm
                        print_block("PeerA 补丁", "预检中 …")
                        lines = count_changed_lines(patch)
                        max_lines = int(policies.get("patch_queue",{}).get("max_diff_lines",150))
                        if lines>max_lines:
                            print(f"[POLICY] 改动行数 {lines} > {max_lines}，拒绝。")
                            log_ledger(home, {"from":"PeerA","kind":"patch-reject","reason":"too-many-lines","lines":lines});
                        else:
                            ok,err = git_apply_check(patch)
                            if not ok:
                                print("[PATCH] 预检失败：\n"+err.strip())
                                log_ledger(home, {"from":"PeerA","kind":"patch-precheck-fail","stderr":err.strip()[:2000]});
                            else:
                                ok2,err2 = git_apply(patch)
                                if not ok2:
                                    print("[PATCH] 应用失败：\n"+err2.strip())
                                    log_ledger(home, {"from":"PeerA","kind":"patch-apply-fail","stderr":err2.strip()[:2000]});
                                else:
                                    try_lint(); tests_ok = try_tests(); git_commit("cccc(PeerA): apply patch (mailbox)")
                                    log_ledger(home, {"from":"PeerA","kind":"patch-commit","lines":lines,"tests_ok":tests_ok})
                                    mbox_counts["peerA"]["patch"] += 1
                                    _ack_receiver("PeerA", events["peerA"].get("patch"))
                                    last_event_ts["PeerA"] = time.time()
                # PeerB 事件
                if events["peerB"].get("to_user"):
                    # 忽略 PeerB → USER 的通道（对外口径由 PeerA 统一输出）
                    pass
                if events["peerB"].get("to_peer"):
                    payload = events["peerB"]["to_peer"].strip()
                    _ack_receiver("PeerB", payload)
                    mbox_counts["peerB"]["to_peer"] += 1
                    mbox_last["peerB"]["to_peer"] = time.strftime("%H:%M:%S")
                    last_event_ts["PeerB"] = time.time()
                    inline = extract_inline_diff_if_any(payload) or ""
                    if inline:
                        print_block("PeerB 内嵌补丁", "预检中 …")
                        lines = count_changed_lines(inline)
                        max_lines = int(policies.get("patch_queue",{}).get("max_diff_lines",150))
                        if lines <= max_lines:
                            ok,err = git_apply_check(inline)
                            if ok:
                                ok2,err2 = git_apply(inline)
                                if ok2:
                                    try_lint(); tests_ok = try_tests(); git_commit("cccc(PeerB): apply inline patch (mailbox)")
                                    log_ledger(home, {"from":"PeerB","kind":"patch-commit-inline","lines":lines,"tests_ok":tests_ok})
                                    standby = ("<FROM_SYSTEM>\nPatch applied. Please standby and await the next instruction.\n</FROM_SYSTEM>\n")
                                    _send_handoff("System", "PeerA", standby)
                                    _send_handoff("System", "PeerB", standby)
                                else:
                                    print("[PATCH] 应用失败：\n"+err2.strip())
                                    log_ledger(home, {"from":"PeerB","kind":"patch-apply-fail","stderr":err2.strip()[:2000]});
                            else:
                                print("[PATCH] 预检失败：\n"+err.strip())
                                log_ledger(home, {"from":"PeerB","kind":"patch-precheck-fail","stderr":err.strip()[:2000]});
                    eff_enabled = handoff_filter_override if handoff_filter_override is not None else None
                    if payload:
                        if should_forward(payload, "PeerB", "PeerA", policies, state, eff_enabled):
                            wrapped = f"<FROM_PeerB>\n{payload}\n</FROM_PeerB>\n"
                            _send_handoff("PeerB", "PeerA", wrapped)
                        else:
                            log_ledger(home, {"from":"PeerB","kind":"handoff-drop","route":"mailbox","reason":"low-signal-or-cooldown","chars":len(payload)})
                if events["peerB"].get("patch"):
                    norm = normalize_mailbox_patch(events["peerB"]["patch"]) or ""
                    if not norm:
                        print("[PATCH] 跳过：patch.diff 非统一 diff 或包含无效内容")
                    else:
                        patch = norm
                        print_block("PeerB 补丁", "预检中 …")
                        lines = count_changed_lines(patch)
                        max_lines = int(policies.get("patch_queue",{}).get("max_diff_lines",150))
                        if lines>max_lines:
                            print(f"[POLICY] 改动行数 {lines} > {max_lines}，拒绝。")
                            log_ledger(home, {"from":"PeerB","kind":"patch-reject","reason":"too-many-lines","lines":lines});
                        else:
                            ok,err = git_apply_check(patch)
                            if not ok:
                                print("[PATCH] 预检失败：\n"+err.strip())
                                log_ledger(home, {"from":"PeerB","kind":"patch-precheck-fail","stderr":err.strip()[:2000]});
                            else:
                                ok2,err2 = git_apply(patch)
                                if not ok2:
                                    print("[PATCH] 应用失败：\n"+err2.strip())
                                    log_ledger(home, {"from":"PeerB","kind":"patch-apply-fail","stderr":err2.strip()[:2000]});
                                else:
                                    try_lint(); tests_ok = try_tests(); git_commit("cccc(PeerB): apply patch (mailbox)")
                                    log_ledger(home, {"from":"PeerB","kind":"patch-commit","lines":lines,"tests_ok":tests_ok})
                                mbox_counts["peerB"]["patch"] += 1
                                _ack_receiver("PeerB", events["peerB"].get("patch"))
                                last_event_ts["PeerB"] = time.time()
                # 持久化索引
                mbox_idx.save()
                # 刷新面板用状态
                write_status(deliver_paused)
                # 检查是否需要超时重发
                _resend_timeouts()
                # 若队列中有待发消息且接收方空闲，尝试派发
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
        if line == "/refresh":
            sysA = weave_system(home, "peerA"); sysB = weave_system(home, "peerB")
            _send_handoff("System", "PeerA", f"<FROM_SYSTEM>\n{sysA}\n</FROM_SYSTEM>\n")
            _send_handoff("System", "PeerB", f"<FROM_SYSTEM>\n{sysB}\n</FROM_SYSTEM>\n")
            print("[SYSTEM] 已刷新（mailbox 交付）。"); continue
        if line == "/pause":
            deliver_paused = True
            print("[PAUSE] 已暂停对等交接（仍会收集 <TO_USER> 与补丁预检）"); write_status(True); continue
        if line == "/resume":
            deliver_paused = False
            write_status(False)
            print("[PAUSE] 已恢复对等交接"); continue
        if line == "/echo on":
            CONSOLE_ECHO = True
            print("[ECHO] 控制台回显已开启（可能干扰输入体验）"); continue
        if line == "/echo off":
            CONSOLE_ECHO = False
            print("[ECHO] 控制台回显已关闭（推荐）"); continue
        if line == "/echo":
            print(f"[ECHO] 当前状态：{'on' if CONSOLE_ECHO else 'off'}"); continue
        # /compose 已移除：行内输入依赖 readline，后台静音避免干扰
        if line == "/anti-on":
            handoff_filter_override = True
            write_status(deliver_paused)
            print("[ANTI] 低信号过滤 override=on"); continue
        if line == "/anti-off":
            handoff_filter_override = False
            write_status(deliver_paused)
            print("[ANTI] 低信号过滤 override=off"); continue
        if line == "/anti-status":
            pol_enabled = bool((policies.get("handoff_filter") or {}).get("enabled", True))
            eff = handoff_filter_override if handoff_filter_override is not None else pol_enabled
            src = "override" if handoff_filter_override is not None else "policy"
            print(f"[ANTI] 低信号过滤: {eff} (source={src})"); continue
        if line.startswith("u:") or line.startswith("both:"):
            msg=line.split(":",1)[1].strip()
            _send_handoff("User", "PeerA", f"<FROM_USER>\n{msg}\n</FROM_USER>\n")
            _send_handoff("User", "PeerB", f"<FROM_USER>\n{msg}\n</FROM_USER>\n")
            continue
        # 直通：a! / b! 直接把命令写入对应 CLI（无包装、无 MID）
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
        # 正常带包装路由
        if line.startswith("a:"):
            msg=line.split(":",1)[1].strip()
            _send_handoff("User", "PeerA", f"<FROM_USER>\n{msg}\n</FROM_USER>\n")
            continue
        if line.startswith("b:"):
            msg=line.split(":",1)[1].strip()
            _send_handoff("User", "PeerB", f"<FROM_USER>\n{msg}\n</FROM_USER>\n")
            continue
        # 默认广播：立即发送给两侧
        _send_handoff("User", "PeerA", f"<FROM_USER>\n{line}\n</FROM_USER>\n")
        _send_handoff("User", "PeerB", f"<FROM_USER>\n{line}\n</FROM_USER>\n")
        
    print("\n[END] 最近提交：")
    run("git --no-pager log -n 5 --oneline")
    print("Ledger:", (home/"state/ledger.jsonl"))
    print(f"[TIP] 你可继续 `tmux attach -t {session}` 与两位 AI 互动。")
