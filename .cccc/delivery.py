# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import re, json, time, os, shlex, tempfile, subprocess, uuid

# --- tmux helpers (复用 orchestrator 的方式) ---
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

def paste_to_pane(pane: str, text: str):
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
        f.write(text); fname=f.name
    buf = f"buf-{int(time.time()*1000)}"
    _tmux("load-buffer","-b",buf,fname); _tmux("paste-buffer","-t",pane,"-b",buf); _tmux("send-keys","-t",pane,"Enter"); _tmux("delete-buffer","-b",buf)
    try: os.unlink(fname)
    except Exception: pass

def send_ctrl_c(pane: str):
    _tmux("send-keys","-t",pane,"C-c")

# --- 状态与判空 ---
class PaneIdleJudge:
    def __init__(self, profile: Dict[str,Any]):
        self.prompt_re = re.compile(profile.get("prompt_regex",""), re.I) if profile.get("prompt_regex") else None
        self.busy_res  = [re.compile(p, re.I) for p in profile.get("busy_regexes",[])]
        self.quiet_sec = float(profile.get("idle_quiet_seconds", 1.5))
        self._last_snapshot = ""
        self._last_change_ts = 0.0

    def refresh(self, pane: str) -> Tuple[bool, str]:
        """返回 (是否空闲, 解释)"""
        text = capture_pane(pane, lines=1200)
        now = time.time()
        if text != self._last_snapshot:
            self._last_snapshot = text
            self._last_change_ts = now

        tail = text.splitlines()[-30:]  # 看最近 30 行
        tail_txt = "\n".join(tail)

        # 忙碌正则命中 → 忙
        for rx in self.busy_res:
            if rx.search(tail_txt):
                return False, "busy_regex"

        # 有提示符 + 安静一段时间 → 空闲
        if self.prompt_re and self.prompt_re.search(tail_txt):
            if now - self._last_change_ts >= self.quiet_sec:
                return True, "prompt+quiet"
            else:
                return False, "prompt-but-noisy"

        # 无提示符时，用“静默时长”兜底
        if now - self._last_change_ts >= self.quiet_sec:
            return True, "quiet-only"

        return False, "changing"

# --- 出站队列与 ACK ---
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

def find_acks_from_output(output: str) -> Tuple[List[str], List[str]]:
    notes = SYS_NOTES_RE.findall(output)
    acks, nacks = [], []
    for nt in notes:
        acks  += ACK_RE.findall(nt)
        nacks += NACK_RE.findall(nt)
    return list(set(acks)), list(set(nacks))

def new_mid(prefix="cccc") -> str:
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:6]}"

def wrap_with_mid(to_peer_payload: str, mid: str) -> str:
    # 在 <TO_PEER> 首行后插入一行 MID 标记，便于收件方在 SYSTEM_NOTES 中 ack: <mid>
    marker = f"[MID: {mid}]"
    if "<TO_PEER>" in to_peer_payload:
        return to_peer_payload.replace("<TO_PEER>", "<TO_PEER>\n"+marker, 1)
    # 若 payload 不是标准块，兜底直接前缀
    return marker + "\n" + to_peer_payload

# --- 主入口：投递（若忙则入队；空闲则发送并等待ACK） ---
def deliver_or_queue(home: Path, pane: str, peer: str, payload: str,
                     profile: Dict[str,Any], delivery_conf: Dict[str,Any]) -> Tuple[str, str]:
    """
    返回 (status, mid)
      status in {"delivered","queued","failed"}
    """
    judge = PaneIdleJudge(profile)
    outbox = Outbox(home, peer)

    max_wait = float(delivery_conf.get("paste_max_wait_seconds", 6))
    interval = float(delivery_conf.get("recheck_interval_seconds", 0.6))

    t0 = time.time()
    while time.time() - t0 < max_wait:
        idle, reason = judge.refresh(pane)
        if idle:
            mid = new_mid()
            text = wrap_with_mid(payload, mid)
            paste_to_pane(pane, f"[INPUT]\n{text}\n")
            # 简单等待对方 ACK（非阻塞太久）
            time.sleep(1.2)
            latest = capture_pane(pane, 1200)
            acks, _ = find_acks_from_output(latest)
            if mid in acks:
                return "delivered", mid
            else:
                # 未即刻ACK，入队等待后台 flush
                outbox.enqueue(mid, text)
                return "queued", mid
        time.sleep(interval)

    # 超时仍繁忙 → 入队
    mid = new_mid()
    text = wrap_with_mid(payload, mid)
    outbox.enqueue(mid, text)
    return "queued", mid

def flush_outbox_if_idle(home: Path, pane: str, peer: str,
                         profile: Dict[str,Any], delivery_conf: Dict[str,Any]) -> List[str]:
    """
    若空闲则尝试 flush 队列前 N 条；返回已确认的 mid 列表。
    """
    judge = PaneIdleJudge(profile)
    outbox = Outbox(home, peer)
    batch = int(delivery_conf.get("max_flush_batch", 3))

    idle, _ = judge.refresh(pane)
    if not idle: return []

    items = outbox.load_all()
    if not items: return []
    sent_mids=[]
    for it in items[:batch]:
        mid = it["mid"]; text = it["payload"]
        paste_to_pane(pane, f"[INPUT]\n{text}\n")
        time.sleep(1.0)
        latest = capture_pane(pane, 1200)
        acks, nacks = find_acks_from_output(latest)
        if mid in acks:
            outbox.remove(mid); sent_mids.append(mid)
        elif mid in nacks:
            outbox.remove(mid)  # 丢弃并记账（调用方可记录原因）
        else:
            # 留在队列，后续再试
            pass
    return sent_mids
