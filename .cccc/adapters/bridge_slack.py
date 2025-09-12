#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Slack Bridge (MVP)
- Outbound: read .cccc/state/outbox.jsonl (single source) and post messages to configured channels
- Inbound: Socket Mode (optional) to accept messages and route to mailbox inbox with a:/b:/both: prefixes
- Dry-run friendly; loads only when tokens present
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import os, sys, json, time, re, threading

ROOT = Path.cwd(); HOME = ROOT/".cccc"
if str(HOME) not in sys.path: sys.path.insert(0, str(HOME))

def read_yaml(p: Path) -> Dict[str, Any]:
    if not p.exists(): return {}
    try:
        import yaml  # type: ignore
        return yaml.safe_load(p.read_text(encoding='utf-8')) or {}
    except Exception:
        try: return json.loads(p.read_text(encoding='utf-8'))
        except Exception: return {}

def _now(): return time.strftime('%Y-%m-%d %H:%M:%S')

def _append_log(line: str):
    p = HOME/"state"/"bridge-slack.log"; p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a', encoding='utf-8') as f: f.write(f"{_now()} {line}\n")

def _route_from_text(text: str, default_route: str) -> Tuple[List[str], str]:
    t = (text or '').strip()
    m = re.match(r"^(a:|b:|both:)\s*", t, re.I)
    if m:
        tag = m.group(1).lower(); t = t[m.end():]
        return ([{'a:':'peerA','b:':'peerB','both:':'peerA'}[tag]] + (["peerB"] if tag=='both:' else []), t)
    m2 = re.match(r"^/(a|b|both)\s+", t, re.I)
    if m2:
        cmd = m2.group(1).lower(); t = t[m2.end():]
        return ([{'a':'peerA','b':'peerB','both':'peerA'}[cmd]] + (["peerB"] if cmd=='both' else []), t)
    if default_route == 'a': return ['peerA'], t
    if default_route == 'b': return ['peerB'], t
    return ['peerA','peerB'], t

def _ensure_inbox_dirs(peer: str) -> Tuple[Path, Path, Path]:
    base = HOME/"mailbox"/peer; inbox = base/"inbox"; proc = base/"processed"; state = HOME/"state"
    for d in (inbox, proc, state): d.mkdir(parents=True, exist_ok=True)
    return inbox, proc, state

def _next_seq(inbox: Path, proc: Path, state: Path, peer: str) -> str:
    lock = state/f"inbox-seq-{peer}.lock"; counter = state/f"inbox-seq-{peer}.txt"
    def compute() -> int:
        try: return int(counter.read_text().strip()) + 1
        except Exception:
            mx = 0
            for d in (inbox, proc):
                try:
                    for f in d.iterdir():
                        n=f.name
                        if len(n)>=6 and n[:6].isdigit(): mx=max(mx,int(n[:6]))
                except Exception: pass
            return mx+1
    try:
        import fcntl
        with open(lock,'w') as lf:
            try: fcntl.flock(lf, fcntl.LOCK_EX)
            except Exception: pass
            val = compute()
            try:
                with open(counter,'w') as cf: cf.write(str(val))
            except Exception: pass
            try: fcntl.flock(lf, fcntl.LOCK_UN)
            except Exception: pass
    except Exception:
        val = compute();
        try: counter.write_text(str(val))
        except Exception: pass
    return f"{val:06d}"

def _wrap_from_user(body: str) -> str:
    if re.search(r"<\s*FROM_USER\s*>", body or '', re.I): return body
    return f"<FROM_USER>\n{(body or '').strip()}\n</FROM_USER>\n"

def _write_inbox(routes: List[str], text: str, mid: str):
    for peer in routes:
        inbox, proc, state = _ensure_inbox_dirs(peer)
        seq = _next_seq(inbox, proc, state, peer)
        (inbox/f"{seq}.{mid}.txt").write_text(_wrap_from_user(text), encoding='utf-8')
        (HOME/"mailbox"/peer/"inbox.md").write_text(_wrap_from_user(text), encoding='utf-8')

def _summarize(text: str, max_chars: int = 1200, max_lines: int = 12) -> str:
    if not text: return ''
    t = text.replace('\r\n','\n').replace('\r','\n').replace('\t','  ')
    lines=[ln.rstrip() for ln in t.split('\n')]
    while lines and not lines[0].strip(): lines.pop(0)
    while lines and not lines[-1].strip(): lines.pop()
    kept=[]; empty=0
    for ln in lines:
        if not ln.strip(): empty+=1; 
        else: empty=0
        if empty<=1: kept.append(ln)
    kept = kept[:max_lines]
    out='\n'.join(kept).strip()
    return out if len(out)<=max_chars else out[:max_chars-1]+'…'

def main():
    cfg = read_yaml(HOME/"settings"/"slack.yaml")
    dry = bool(cfg.get('dry_run', True))
    app_token = os.environ.get(str(cfg.get('app_token_env') or 'SLACK_APP_TOKEN')) or cfg.get('app_token')
    bot_token = os.environ.get(str(cfg.get('bot_token_env') or 'SLACK_BOT_TOKEN')) or cfg.get('bot_token')
    channels_to_user = [str(x) for x in (cfg.get('channels') or {}).get('to_user', [])]
    channels_peer = [str(x) for x in (cfg.get('channels') or {}).get('to_peer_summary', [])]
    reset = str((cfg.get('outbound') or {}).get('reset_on_start', 'baseline'))
    show_peers = bool(cfg.get('show_peer_messages', True))
    default_route = str(cfg.get('default_route','both')).lower()[0:1] if cfg.get('default_route') else 'b'

    # Outbound consumer
    from .outbox_consumer import OutboxConsumer
    oc = OutboxConsumer(HOME, seen_name='slack', reset_on_start=reset)

    def send_text(chs: List[str], text: str):
        if dry or not bot_token:
            _append_log(f"[dry-run] outbound len={len(text)} to {chs}")
            return
        try:
            from slack_sdk import WebClient  # type: ignore
            cli = WebClient(token=bot_token)
            for ch in chs:
                cli.chat_postMessage(channel=ch, text=text)
                time.sleep(0.5)
        except Exception as e:
            _append_log(f"[error] slack post failed: {e}")

    def on_to_user(ev: Dict[str,Any]):
        p = str(ev.get('peer') or '').lower()
        label = 'PeerA' if 'peera' in p or p=='peera' else 'PeerB'
        msg = f"[{label}]\n" + _summarize(str(ev.get('text') or ''))
        if channels_to_user:
            send_text(channels_to_user, msg)

    def on_to_peer_summary(ev: Dict[str,Any]):
        if not show_peers: return
        frm = str(ev.get('from') or '')
        label = 'PeerA→PeerB' if frm in ('PeerA','peera','peera') else 'PeerB→PeerA'
        msg = f"[{label}]\n" + _summarize(str(ev.get('text') or ''))
        if channels_peer:
            send_text(channels_peer, msg)

    th = threading.Thread(target=lambda: oc.loop(on_to_user, on_to_peer_summary), daemon=True)
    th.start()

    # Inbound via Socket Mode (optional)
    if dry or not (app_token and bot_token):
        _append_log("[info] inbound disabled (dry_run or tokens missing)")
        while True: time.sleep(1.0)

    try:
        from slack_sdk.socket_mode import SocketModeClient  # type: ignore
        from slack_sdk.web import WebClient  # type: ignore
    except Exception as e:
        _append_log(f"[warn] slack_sdk not installed: {e}; inbound disabled")
        while True: time.sleep(1.0)

    web = WebClient(token=bot_token)
    client = SocketModeClient(app_token=app_token, web_client=web)

    def handle(evt):
        try:
            typ = evt.get('type') or ''
            if typ != 'events_api': return
            payload = evt.get('payload') or {}
            event = payload.get('event') or {}
            if event.get('type') != 'message': return
            if 'subtype' in event: return  # skip bot edits, etc.
            text = str(event.get('text') or '')
            ch = str(event.get('channel') or '')
            user = str(event.get('user') or '')
            # Route and write inbox
            routes, body = _route_from_text(text, default_route)
            mid = f"slack-{int(time.time())}-{user[-4:]}"
            _write_inbox(routes, body, mid)
            client.ack(evt)
        except Exception:
            try: client.ack(evt)
            except Exception: pass

    client.socket_mode_request_listeners.append(handle)
    _append_log("[info] slack socket mode starting …")
    client.connect()
    try:
        while True: time.sleep(1.0)
    finally:
        try: client.disconnect()
        except Exception: pass

if __name__ == '__main__':
    main()

