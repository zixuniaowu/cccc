#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bridge (MVP skeleton)
- Dry-run friendly: default to file-based mock (no network), then gate real network by token/allowlist.
- Inbound: messages -> .cccc/mailbox/<peer>/inbox.md (with optional a:/b:/both: prefix routing), append [MID].
- Outbound: tail .cccc/mailbox/peer*/to_user.md changes, debounce and send concise summaries to chat(s).
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Tuple
import os, sys, time, json, re, threading
import urllib.request, urllib.parse
try:
    import fcntl  # POSIX lock for inbox sequencing
except Exception:
    fcntl = None  # type: ignore

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

ROOT = Path.cwd()
HOME = ROOT/".cccc"

def read_yaml(p: Path) -> Dict[str, Any]:
    if not p.exists():
        return {}
    try:
        import yaml as _y
        return _y.safe_load(p.read_text(encoding='utf-8')) or {}
    except Exception:
        # Try JSON fallback
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return {}

def _now():
    import time
    return time.strftime('%Y-%m-%d %H:%M:%S')

def _acquire_singleton_lock(name: str = "telegram-bridge"):
    """Prevent multiple bridge instances from running concurrently (avoids duplicate replies).
    Returns an open file handle holding an exclusive lock for process lifetime.
    """
    lf_path = HOME/"state"/f"{name}.lock"
    lf_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lf_path, 'w')
    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # record pid for diagnostics
        f.write(str(os.getpid()))
        f.flush()
    except Exception:
        # Another instance holds the lock
        try:
            print("[telegram_bridge] Another instance is already running. Exiting.")
            _append_log(HOME/"state"/"bridge-telegram.log", "[warn] duplicate instance detected; exiting")
        except Exception:
            pass
        sys.exit(0)
    return f

def _write_text(p: Path, s: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding='utf-8')

def _append_log(p: Path, line: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a', encoding='utf-8') as f:
        f.write(f"{_now()} {line}\n")

def _mid() -> str:
    import uuid, time
    return f"tg-{int(time.time())}-{uuid.uuid4().hex[:6]}"

def _route_from_text(text: str, default_route: str):
    t = text.strip()
    # Support plain prefixes: a:/b:/both:
    m = re.match(r"^(a:|b:|both:)\s*", t, re.I)
    if m:
        tag = m.group(1).lower()
        t = t[m.end():]
        if tag == 'a:':
            return ['peerA'], t
        if tag == 'b:':
            return ['peerB'], t
        return ['peerA','peerB'], t
    # Support slash commands (group privacy mode): /a …, /b …, /both …, with optional @BotName
    m2 = re.match(r"^/(a|b|both)(?:@\S+)?\s+", t, re.I)
    if m2:
        cmd = m2.group(1).lower()
        t = t[m2.end():]
        if cmd == 'a':
            return ['peerA'], t
        if cmd == 'b':
            return ['peerB'], t
        return ['peerA','peerB'], t
    # Support mention form: @BotName a: … or @BotName /a …
    m3 = re.match(r"^@\S+\s+(a:|b:|both:)\s*", t, re.I)
    if m3:
        tag = m3.group(1).lower()
        t = t[m3.end():]
        if tag == 'a:':
            return ['peerA'], t
        if tag == 'b:':
            return ['peerB'], t
        return ['peerA','peerB'], t
    m4 = re.match(r"^@\S+\s+/(a|b|both)(?:@\S+)?\s+", t, re.I)
    if m4:
        cmd = m4.group(1).lower()
        t = t[m4.end():]
        if cmd == 'a':
            return ['peerA'], t
        if cmd == 'b':
            return ['peerB'], t
        return ['peerA','peerB'], t
    if default_route == 'a':
        return ['peerA'], t
    if default_route == 'b':
        return ['peerB'], t
    return ['peerA','peerB'], t

def _wrap_with_mid(payload: str, mid: str) -> str:
    """Insert [MID: …] after the first recognized opening tag if present;
    otherwise prefix at the top. Keeps wrappers as the first line for peers.
    Recognized tags: FROM_USER, FROM_PeerA, FROM_PeerB, TO_PEER, TO_USER, FROM_SYSTEM
    """
    marker = f"[MID: {mid}]"
    try:
        m = re.search(r"<(\s*(FROM_USER|FROM_PeerA|FROM_PeerB|TO_PEER|TO_USER|FROM_SYSTEM)\s*)>", payload, re.I)
        if m:
            start, end = m.span()
            head = payload[:end]
            tail = payload[end:]
            # Ensure single newline after the tag
            if not head.endswith("\n"):
                head = head + "\n"
            return head + marker + "\n" + tail.lstrip("\n")
        else:
            return marker + "\n" + payload
    except Exception:
        return marker + "\n" + payload

TAG_RE = re.compile(r"<\s*(FROM_USER|FROM_PeerA|FROM_PeerB|TO_PEER|TO_USER|FROM_SYSTEM)\s*>", re.I)
def _wrap_user_if_needed(body: str) -> str:
    """Ensure inbound payload is inside <FROM_USER> … when no known tags are present."""
    if TAG_RE.search(body or ''):
        return body
    b = (body or '').strip()
    return f"<FROM_USER>\n{b}\n</FROM_USER>\n" if b else b

def _ensure_dirs(home: Path, peer: str) -> Tuple[Path, Path, Path]:
    base = home/"mailbox"/peer
    inbox_dir = base/"inbox"
    proc_dir = base/"processed"
    state = home/"state"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    proc_dir.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    return inbox_dir, proc_dir, state

def _next_seq(inbox: Path, processed: Path, state: Path, peer: str) -> str:
    lock_path = state/f"inbox-seq-{peer}.lock"
    counter_path = state/f"inbox-seq-{peer}.txt"
    def compute_from_fs() -> int:
        mx = 0
        for d in (inbox, processed):
            try:
                for f in d.iterdir():
                    n = f.name
                    if len(n) >= 6 and n[:6].isdigit():
                        mx = max(mx, int(n[:6]))
            except Exception:
                pass
        return mx + 1
    def compute() -> int:
        try:
            return int(counter_path.read_text().strip()) + 1
        except Exception:
            return compute_from_fs()
    if fcntl is not None:
        with open(lock_path, 'w') as lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX)
            except Exception:
                pass
            val = compute()
            try:
                with open(counter_path, 'w') as cf:
                    cf.write(str(val))
            except Exception:
                pass
            try:
                fcntl.flock(lf, fcntl.LOCK_UN)
            except Exception:
                pass
        return f"{val:06d}"
    # Fallback without fcntl
    val = compute()
    try:
        counter_path.write_text(str(val))
    except Exception:
        pass
    return f"{val:06d}"

def _deliver_inbound(home: Path, routes: List[str], payload: str, mid: str):
    """Write numbered inbox files per peer to integrate with orchestrator NUDGE.
    Also write inbox.md as a last-resort for bridge mode users.
    """
    for peer in routes:
        inbox_dir, proc_dir, state = _ensure_dirs(home, peer)
        seq = _next_seq(inbox_dir, proc_dir, state, peer)
        fname = f"{seq}.{mid}.txt"
        _write_text(inbox_dir/fname, payload)
        # Best-effort: also mirror to inbox.md for adapter users
        _write_text((home/"mailbox"/peer/"inbox.md"), payload)

def _append_ledger(entry: Dict[str, Any]):
    try:
        entry = {"ts": _now(), **entry}
        lp = HOME/"state"/"ledger.jsonl"
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _runtime_path() -> Path:
    return HOME/"state"/"telegram-runtime.json"

def load_runtime() -> Dict[str, Any]:
    p = _runtime_path()
    try:
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}

def save_runtime(obj: Dict[str, Any]):
    p = _runtime_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass

def _summarize(text: str, max_chars: int) -> str:
    s = re.sub(r"\s+", " ", text).strip()
    return (s[:max_chars] + '…') if len(s) > max_chars else s

def _subs_path() -> Path:
    return HOME/"state"/"telegram-subs.json"

def load_subs() -> List[int]:
    p = _subs_path()
    try:
        if p.exists():
            arr = json.loads(p.read_text(encoding='utf-8'))
            out = []
            for x in arr:
                try:
                    out.append(int(x))
                except Exception:
                    pass
            return out
    except Exception:
        pass
    return []

def save_subs(items: List[int]):
    p = _subs_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(sorted(set(int(x) for x in items))), encoding='utf-8')
    except Exception:
        pass

def dry_run_loop(cfg: Dict[str, Any]):
    _acquire_singleton_lock("telegram-bridge-dryrun")
    mock = cfg.get('mock') or {}
    inbox_dir = Path(mock.get('inbox_dir') or HOME/"work"/"telegram_inbox")
    outlog = Path(mock.get('outbox_log') or HOME/"state"/"bridge-telegram.log")
    inbox_dir.mkdir(parents=True, exist_ok=True)
    outlog.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    default_route = str(cfg.get('default_route') or 'both')
    max_chars = int(cfg.get('max_msg_chars') or 900)
    _append_log(outlog, "[dry-run] bridge started")

    def watch_outputs():
        to_user_paths = [HOME/"mailbox"/"peerA"/"to_user.md", HOME/"mailbox"/"peerB"/"to_user.md"]
        last = {str(p): '' for p in to_user_paths}
        while True:
            for p in to_user_paths:
                try:
                    txt = p.read_text(encoding='utf-8').strip()
                except Exception:
                    txt = ''
                key = str(p)
                if txt and txt != last[key]:
                    last[key] = txt
                    preview = _summarize(txt, max_chars)
                    _append_log(outlog, f"[outbound] {p.name} {len(txt)} chars | {preview}")
            time.sleep(1.0)

    th = threading.Thread(target=watch_outputs, daemon=True)
    th.start()

    while True:
        for f in sorted(inbox_dir.glob('*.txt')):
            if f in seen:
                continue
            try:
                text = f.read_text(encoding='utf-8')
            except Exception:
                text = ''
            seen.add(f)
            routes, body = _route_from_text(text, default_route)
            mid = _mid()
            body2 = _wrap_user_if_needed(body)
            payload = _wrap_with_mid(body2, mid)
            _deliver_inbound(HOME, routes, payload, mid)
            _append_log(outlog, f"[inbound] routes={routes} mid={mid} size={len(body)} from={f.name}")
        time.sleep(0.8)

def main():
    cfg = read_yaml(HOME/"settings"/"telegram.yaml")
    dry = bool(cfg.get('dry_run', True))
    if dry:
        dry_run_loop(cfg)
        return
    # Real network path: gate by token and allowlist; long-poll getUpdates; send concise summaries
    _acquire_singleton_lock("telegram-bridge")
    token_env = str(cfg.get('token_env') or 'TELEGRAM_BOT_TOKEN')
    token = os.environ.get(token_env, '')
    def _coerce_allowlist(val) -> set:
        def to_int(x):
            try:
                return int(str(x).strip())
            except Exception:
                return None
        if isinstance(val, (list, tuple, set)):
            out = set()
            for x in val:
                v = to_int(x)
                if v is not None:
                    out.add(v)
            return out
        if isinstance(val, str):
            s = val.strip().strip('"\'')
            if not s:
                return set()
            # Try JSON-style list first
            if s.startswith('[') and s.endswith(']'):
                try:
                    arr = json.loads(s)
                    return _coerce_allowlist(arr)
                except Exception:
                    pass
            # Fallback: split by comma/whitespace and brackets
            s2 = s.strip('[]')
            parts = re.split(r"[\s,]+", s2)
            out = set()
            for p in parts:
                v = to_int(p)
                if v is not None:
                    out.add(v)
            return out
        return set()

    allow_raw = cfg.get('allow_chats') or []
    allow_cfg = _coerce_allowlist(allow_raw)
    subs = set(load_subs())
    allow = set(allow_cfg) | subs
    policy = str(cfg.get('autoregister') or 'off').lower()
    max_auto = int(cfg.get('max_auto_subs') or 3)
    discover = bool(cfg.get('discover_allowlist', False))
    if not token or (not allow and not discover and policy != 'open'):
        print("[telegram_bridge] Missing token or allowlist; enable dry_run, set discover_allowlist, or configure settings.")
        sys.exit(1)

    def tg_api(method: str, params: Dict[str, Any], *, timeout: int = 35) -> Dict[str, Any]:
        base = f"https://api.telegram.org/bot{token}/{method}"
        data = urllib.parse.urlencode(params).encode('utf-8')
        req = urllib.request.Request(base, data=data, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode('utf-8', errors='replace')
                return json.loads(body)
        except Exception as e:
            _append_log(HOME/"state"/"bridge-telegram.log", f"[error] api {method}: {e}")
            return {"ok": False, "error": str(e)}

    def tg_poll(offset: int) -> Tuple[int, List[Dict[str, Any]]]:
        # Use POST for consistency
        res = tg_api('getUpdates', {
            'offset': offset,
            'timeout': 25,
            'allowed_updates': json.dumps(["message", "edited_message"])  # type: ignore
        }, timeout=35)
        updates = []
        new_offset = offset
        if res.get('ok') and isinstance(res.get('result'), list):
            for u in res['result']:
                try:
                    uid = int(u.get('update_id'))
                    new_offset = max(new_offset, uid + 1)
                    updates.append(u)
                except Exception:
                    pass
        return new_offset, updates

    def redact(s: str) -> str:
        pats = cfg.get('redact_patterns') or []
        out = s
        for p in pats:
            try:
                out = re.sub(p, '[REDACTED]', out)
            except Exception:
                continue
        return out

    def is_cmd(s: str, name: str) -> bool:
        return re.match(rf"^/{name}(?:@\S+)?(?:\s|$)", s.strip(), re.I) is not None

    outlog = HOME/"state"/"bridge-telegram.log"
    _append_log(outlog, "[net] bridge started")
    # Outbound watcher (send summaries when to_user changes; debounced per peer)
    debounce = int(cfg.get('debounce_seconds') or 30)
    max_chars = int(cfg.get('max_msg_chars') or 900)
    peer_debounce = int(cfg.get('peer_debounce_seconds') or debounce)
    peer_max_chars = int(cfg.get('peer_message_max_chars') or 600)
    runtime = load_runtime()
    show_peers_default = bool(cfg.get('show_peer_messages', False))
    show_peers = bool(runtime.get('show_peer_messages', show_peers_default))
    last_sent_ts = {"peerA": 0.0, "peerB": 0.0}
    last_seen = {"peerA": "", "peerB": ""}

    def send_summary(peer: str, text: str):
        label = "PeerA" if peer == 'peerA' else "PeerB"
        msg = f"[{label}]\n" + _summarize(redact(text), max_chars)
        for chat_id in allow:
            tg_api('sendMessage', {
                'chat_id': chat_id,
                'text': msg,
                'disable_web_page_preview': True
            }, timeout=15)
        _append_log(outlog, f"[outbound] sent {label} {len(msg)} chars")
        _append_ledger({"kind":"bridge-outbound","to":"telegram","peer":label.lower(),"chars":len(msg)})

    def send_peer_summary(sender_peer: str, text: str):
        label = "PeerA→PeerB" if sender_peer == 'peerA' else "PeerB→PeerA"
        msg = f"[{label}]\n" + _summarize(redact(text), peer_max_chars)
        for chat_id in allow:
            tg_api('sendMessage', {
                'chat_id': chat_id,
                'text': msg,
                'disable_web_page_preview': True
            }, timeout=15)
        _append_log(outlog, f"[outbound] sent {label} {len(msg)} chars")
        _append_ledger({"kind":"bridge-outbound","to":"telegram","peer":"to_peer","chars":len(msg)})

    def watch_outputs():
        to_user_paths = {
            'peerA': HOME/"mailbox"/"peerA"/"to_user.md",
            'peerB': HOME/"mailbox"/"peerB"/"to_user.md",
        }
        to_peer_paths = {
            'peerA': HOME/"mailbox"/"peerA"/"to_peer.md",
            'peerB': HOME/"mailbox"/"peerB"/"to_peer.md",
        }
        while True:
            now = time.time()
            for peer, p in to_user_paths.items():
                try:
                    txt = p.read_text(encoding='utf-8').strip()
                except Exception:
                    txt = ''
                if txt and txt != last_seen[peer] and (now - last_sent_ts[peer] >= debounce):
                    last_seen[peer] = txt
                    last_sent_ts[peer] = now
                    send_summary(peer, txt)
            # to_peer (peer-to-peer) messages
            eff_show = bool(load_runtime().get('show_peer_messages', show_peers))
            if eff_show:
                for peer, p in to_peer_paths.items():
                    try:
                        txt = p.read_text(encoding='utf-8').strip()
                    except Exception:
                        txt = ''
                    key = f"peer_{peer}"
                    if txt and txt != last_seen.get(key, '') and (now - last_sent_ts.get(key, 0.0) >= peer_debounce):
                        last_seen[key] = txt
                        last_sent_ts[key] = now
                        send_peer_summary(peer, txt)
            time.sleep(1.0)

    t_out = threading.Thread(target=watch_outputs, daemon=True)
    t_out.start()

    # Inbound poll loop
    offset_path = HOME/"state"/"telegram-offset.json"
    try:
        off = int(json.loads(offset_path.read_text()).get('offset', 0)) if offset_path.exists() else 0
    except Exception:
        off = 0
    default_route = str(cfg.get('default_route') or 'both')
    while True:
        off, updates = tg_poll(off)
        if updates:
            offset_path.parent.mkdir(parents=True, exist_ok=True)
            offset_path.write_text(json.dumps({"offset": off}), encoding='utf-8')
        for u in updates:
            msg = u.get('message') or u.get('edited_message') or {}
            chat = (msg.get('chat') or {})
            chat_id = int(chat.get('id', 0) or 0)
            if chat_id not in allow:
                text = (msg.get('text') or '').strip()
                if policy == 'open' and is_cmd(text, 'subscribe'):
                    # Auto-register with cap
                    cur = set(load_subs())
                    if chat_id in cur:
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': '已订阅（已在允许列表）'}, timeout=15)
                    elif len(cur) >= max_auto:
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': '订阅人数已达上限，请联系管理员添加。'}, timeout=15)
                    else:
                        cur.add(chat_id); save_subs(sorted(cur)); allow.add(chat_id)
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': '订阅成功：本聊天将接收摘要与提示。取消可发送 /unsubscribe'}, timeout=15)
                        _append_log(outlog, f"[subscribe] chat={chat_id}")
                        _append_ledger({"kind":"bridge-subscribe","chat":chat_id})
                    continue
                if policy == 'open' and is_cmd(text, 'unsubscribe'):
                    # Allow unsub from non-allowed (no-op) for idempotence
                    cur = set(load_subs()); removed = chat_id in cur
                    if removed:
                        cur.discard(chat_id); save_subs(sorted(cur))
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': '已取消订阅' if removed else '未订阅'}, timeout=15)
                    _append_log(outlog, f"[unsubscribe] chat={chat_id}")
                    _append_ledger({"kind":"bridge-unsubscribe","chat":chat_id})
                    continue
                # Discovery or closed policy: log and optionally reply to whoami
                _append_log(outlog, f"[drop] message from not-allowed chat={chat_id}")
                _append_ledger({"kind":"bridge-drop","reason":"not-allowed","chat":chat_id})
                if discover and is_cmd(text, 'whoami'):
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': f"chat_id={chat_id} (不在允许列表，发送 /subscribe 可自助订阅)"}, timeout=15)
                elif policy == 'open':
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': '未订阅。发送 /subscribe 可订阅，/unsubscribe 取消。'}, timeout=15)
                continue
            text = msg.get('text') or ''
            if not text:
                continue
            # minimal commands
            if is_cmd(text, 'subscribe'):
                if policy == 'open':
                    cur = set(load_subs()); added = chat_id not in cur
                    if added:
                        cur.add(chat_id); save_subs(sorted(cur)); allow.add(chat_id)
                    tg_api('sendMessage', {
                        'chat_id': chat_id,
                        'text': '订阅成功：本聊天将接收摘要与提示。发送 /unsubscribe 可取消。' if added else '已订阅（已在允许列表）'
                    }, timeout=15)
                    _append_log(outlog, f"[subscribe] chat={chat_id}{' (noop)' if not added else ''}")
                    _append_ledger({"kind":"bridge-subscribe","chat":chat_id,"noop": (not added)})
                else:
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': '当前不支持自助订阅，请联系管理员添加。'}, timeout=15)
                continue
            if is_cmd(text, 'whoami'):
                tg_api('sendMessage', {'chat_id': chat_id, 'text': f"chat_id={chat_id}"}, timeout=15)
                _append_log(outlog, f"[meta] whoami chat={chat_id}")
                continue
            if is_cmd(text, 'help'):
                help_txt = "使用: a:/b:/both: 或 /a /b /both 路由到 PeerA/PeerB/两者；/whoami 查看 chat_id；/subscribe 订阅（若启用）；/unsubscribe 取消订阅；/showpeers on|off 切换是否显示 Peer↔Peer 摘要。"
                tg_api('sendMessage', {'chat_id': chat_id, 'text': help_txt}, timeout=15)
                continue
            if re.match(r"^/showpeers(?:@\S+)?\s+(on|off)\b", text.strip(), re.I):
                m = re.match(r"^/showpeers(?:@\S+)?\s+(on|off)\b", text.strip(), re.I)
                val = m.group(1).lower() == 'on' if m else False
                runtime = load_runtime(); runtime['show_peer_messages'] = bool(val); save_runtime(runtime)
                show_peers = bool(val)
                tg_api('sendMessage', {'chat_id': chat_id, 'text': f"Peer↔Peer 摘要显示已设为: {'ON' if val else 'OFF'} (全局)"}, timeout=15)
                _append_log(outlog, f"[runtime] show_peer_messages={val}")
                continue
            if is_cmd(text, 'unsubscribe'):
                if policy == 'open':
                    cur = set(load_subs()); removed = chat_id in cur
                    if removed:
                        cur.discard(chat_id); save_subs(sorted(cur)); allow.discard(chat_id)
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': '已取消订阅' if removed else '未订阅'}, timeout=15)
                    _append_log(outlog, f"[unsubscribe] chat={chat_id}")
                    _append_ledger({"kind":"bridge-unsubscribe","chat":chat_id})
                else:
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': '当前不支持自助取消订阅，请联系管理员。'}, timeout=15)
                continue
            routes, body = _route_from_text(text, default_route)
            mid = _mid()
            body2 = _wrap_user_if_needed(body)
            payload = _wrap_with_mid(body2, mid)
            _deliver_inbound(HOME, routes, payload, mid)
            _append_log(outlog, f"[inbound] routes={routes} mid={mid} size={len(body)} chat={chat_id}")
            _append_ledger({"kind":"bridge-inbound","from":"telegram","chat":chat_id,"routes":routes,"mid":mid,"chars":len(body)})
        time.sleep(0.5)

if __name__ == '__main__':
    main()
