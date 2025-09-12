#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord Bridge (MVP)
- Outbound: read outbox.jsonl and post to configured channels
- Inbound: on_message (optional) to route into mailbox via a:/b:/both:
- If discord.py is not installed or token missing, run in dry-run outbound mode.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Tuple
import os, sys, json, time, re, threading, asyncio

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
def _log(line: str):
    p = HOME/"state"/"bridge-discord.log"; p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a', encoding='utf-8') as f: f.write(f"{_now()} {line}\n")

TAG_RE = re.compile(r"<\s*FROM_USER\s*>", re.I)
def _wrap_from_user(s: str) -> str:
    return s if TAG_RE.search(s or '') else f"<FROM_USER>\n{(s or '').strip()}\n</FROM_USER>\n"

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

def _ensure_inbox(peer: str):
    base = HOME/"mailbox"/peer; inbox=base/"inbox"; proc=base/"processed"; state=HOME/"state"
    for d in (inbox, proc, state): d.mkdir(parents=True, exist_ok=True)
    return inbox, proc, state

def _next_seq(inbox: Path, proc: Path, state: Path, peer: str) -> str:
    counter = state/f"inbox-seq-{peer}.txt"
    try: val = int(counter.read_text().strip()) + 1
    except Exception:
        mx = 0
        for d in (inbox, proc):
            try:
                for f in d.iterdir():
                    n=f.name
                    if len(n)>=6 and n[:6].isdigit(): mx=max(mx,int(n[:6]))
            except Exception: pass
        val = mx+1
    try: counter.write_text(str(val))
    except Exception: pass
    return f"{val:06d}"

def _write_inbox(routes: List[str], text: str, mid: str):
    for peer in routes:
        inbox, proc, state = _ensure_inbox(peer)
        seq = _next_seq(inbox, proc, state, peer)
        (inbox/f"{seq}.{mid}.txt").write_text(_wrap_from_user(text), encoding='utf-8')
        (HOME/"mailbox"/peer/"inbox.md").write_text(_wrap_from_user(text), encoding='utf-8')

def _summarize(t: str, max_chars: int = 1500, max_lines: int = 12) -> str:
    if not t: return ''
    t = t.replace('\r\n','\n').replace('\r','\n')
    lines=[ln.rstrip() for ln in t.split('\n')]
    while lines and not lines[0].strip(): lines.pop(0)
    while lines and not lines[-1].strip(): lines.pop()
    kept=[]; empty=0
    for ln in lines:
        if not ln.strip(): empty+=1
        else: empty=0
        if empty<=1: kept.append(ln)
    kept = kept[:max_lines]
    out='\n'.join(kept).strip()
    return out if len(out)<=max_chars else out[:max_chars-1]+'…'

def main():
    cfg = read_yaml(HOME/"settings"/"discord.yaml")
    dry = bool(cfg.get('dry_run', True))
    token = os.environ.get(str(cfg.get('bot_token_env') or 'DISCORD_BOT_TOKEN')) or cfg.get('bot_token')
    chans_user = [int(x) for x in (cfg.get('channels') or {}).get('to_user', [])]
    chans_peer = [int(x) for x in (cfg.get('channels') or {}).get('to_peer_summary', [])]
    reset = str((cfg.get('outbound') or {}).get('reset_on_start', 'baseline'))
    show_peers = bool(cfg.get('show_peer_messages', True))
    default_route = str(cfg.get('default_route','both')).lower()[0:1] if cfg.get('default_route') else 'b'

    # Outbound consumer thread
    from .outbox_consumer import OutboxConsumer
    oc = OutboxConsumer(HOME, seen_name='discord', reset_on_start=reset)

    send_queue: List[Tuple[int,str]] = []  # (channel_id, text)
    q_lock = threading.Lock()

    def enqueue(ch_list: List[int], text: str):
        with q_lock:
            for ch in ch_list:
                send_queue.append((ch, text))

    def on_to_user(ev: Dict[str,Any]):
        p = str(ev.get('peer') or '').lower()
        label = 'PeerA' if 'peera' in p or p=='peera' else 'PeerB'
        msg = f"[{label}]\n" + _summarize(str(ev.get('text') or ''))
        if chans_user:
            enqueue(chans_user, msg)

    def on_to_peer_summary(ev: Dict[str,Any]):
        if not show_peers: return
        frm = str(ev.get('from') or '')
        label = 'PeerA→PeerB' if frm in ('PeerA','peera','peera') else 'PeerB→PeerA'
        msg = f"[{label}]\n" + _summarize(str(ev.get('text') or ''))
        if chans_peer:
            enqueue(chans_peer, msg)

    t = threading.Thread(target=lambda: oc.loop(on_to_user, on_to_peer_summary), daemon=True)
    t.start()

    # If no token or dry_run, run outbound-only logging loop
    if dry or not token:
        _log("[info] dry-run or token missing; not connecting to Discord")
        while True:
            with q_lock:
                if send_queue:
                    ch, msg = send_queue.pop(0)
                    _log(f"[dry-run] outbound to {ch}: {len(msg)} chars")
            time.sleep(0.5)

    # Live mode with discord.py
    try:
        import discord  # type: ignore
    except Exception as e:
        _log(f"[warn] discord.py not installed: {e}; running outbound dry-run")
        while True:
            with q_lock:
                if send_queue:
                    ch, msg = send_queue.pop(0)
                    _log(f"[dry-run] outbound to {ch}: {len(msg)} chars")
            time.sleep(0.5)

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        _log(f"[ready] logged in as {client.user}")

    @client.event
    async def on_message(message: 'discord.Message'):
        try:
            if message.author == client.user:
                return
            text = message.content or ''
            routes, body = _route_from_text(text, default_route)
            mid = f"dc-{int(time.time())}-{str(message.author.id)[-4:]}"
            _write_inbox(routes, body, mid)
        except Exception:
            pass

    async def sender_loop():
        while True:
            await asyncio.sleep(0.3)
            item=None
            with q_lock:
                if send_queue:
                    item = send_queue.pop(0)
            if item:
                ch_id, msg = item
                ch = client.get_channel(ch_id)
                try:
                    if ch: await ch.send(msg)
                except Exception as e:
                    _log(f"[error] send failed to {ch_id}: {e}")

    async def runner():
        await client.login(token)
        loop = asyncio.get_running_loop()
        loop.create_task(sender_loop())
        await client.connect(reconnect=True)

    asyncio.run(runner())

if __name__ == '__main__':
    main()

