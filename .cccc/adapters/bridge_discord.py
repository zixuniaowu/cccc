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
from typing import Dict, Any, List, Tuple, Optional
import os, sys, json, time, re, threading, asyncio, hashlib, datetime

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

def _sha256_file(fp: Path) -> str:
    h = hashlib.sha256()
    with open(fp, 'rb') as f:
        for chunk in iter(lambda: f.read(1024*64), b''):
            h.update(chunk)
    return h.hexdigest()

def _today_dir(root: Path, sub: str) -> Path:
    dt = datetime.datetime.now().strftime('%Y%m%d')
    p = root/"upload"/"inbound"/sub/dt
    p.mkdir(parents=True, exist_ok=True)
    return p

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
    try:
        from adapters.outbox_consumer import OutboxConsumer
    except Exception:
        from outbox_consumer import OutboxConsumer
    oc = OutboxConsumer(HOME, seen_name='discord', reset_on_start=reset)
    # Dynamic subscriptions (channel IDs)
    SUBS_LOCK = threading.Lock()
    def _subs_path() -> Path:
        return HOME/"state"/"discord-subs.json"
    def load_subs() -> List[int]:
        p = _subs_path()
        try:
            if p.exists():
                arr = json.loads(p.read_text(encoding='utf-8')).get('channels') or []
                out=[]
                for x in arr:
                    try: out.append(int(x))
                    except Exception: pass
                return out
        except Exception:
            pass
        return []
    def save_subs(items: List[int]):
        p = _subs_path(); p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(json.dumps({'channels': list(dict.fromkeys(items))[-2000:]}, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
    SUBS = load_subs()

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
        with q_lock:
            with SUBS_LOCK:
                chs = list(dict.fromkeys((chans_user or []) + (SUBS or [])))
        if chs:
            enqueue(chs, msg)

    def on_to_peer_summary(ev: Dict[str,Any]):
        if not show_peers: return
        frm = str(ev.get('from') or '')
        label = 'PeerA→PeerB' if frm in ('PeerA','peera','peera') else 'PeerB→PeerA'
        msg = f"[{label}]\n" + _summarize(str(ev.get('text') or ''))
        with q_lock:
            with SUBS_LOCK:
                chs = list(dict.fromkeys((chans_peer or []) + (SUBS or [])))
        if chs:
            enqueue(chs, msg)

    t = threading.Thread(target=lambda: oc.loop(on_to_user, on_to_peer_summary), daemon=True)
    t.start()

    # If no token or dry_run, continue outbound-only loop in background
    if dry or not token:
        _log("[info] dry-run or token missing; not connecting to Discord (outbound-only)")
        def dry_loop():
            while True:
                with q_lock:
                    if send_queue:
                        ch, msg = send_queue.pop(0)
                        _log(f"[dry-run] outbound to {ch}: {len(msg)} chars")
                time.sleep(0.5)
        threading.Thread(target=dry_loop, daemon=True).start()

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
            low = text.strip().lower()
            if low in ('subscribe','sub'):
                try:
                    with SUBS_LOCK:
                        if message.channel.id not in SUBS:
                            SUBS.append(message.channel.id); save_subs(SUBS)
                    await message.channel.send('Subscribed this channel for to_user/to_peer_summary.')
                except Exception:
                    pass
                return
            if low in ('unsubscribe','unsub'):
                try:
                    with SUBS_LOCK:
                        SUBS2 = [x for x in SUBS if x != message.channel.id]
                        if len(SUBS2) != len(SUBS):
                            SUBS[:] = SUBS2; save_subs(SUBS)
                    await message.channel.send('Unsubscribed this channel.')
                except Exception:
                    pass
                return
            routes, body = _route_from_text(text, default_route)
            mid = f"dc-{int(time.time())}-{str(message.author.id)[-4:]}"
            # Save attachments if any
            try:
                files_cfg = (cfg.get('files') or {})
                inbound_root = Path(str(files_cfg.get('inbound_dir') or (HOME/"work")))
                dest_dir = _today_dir(inbound_root, 'discord')
                refs = []
                for att in message.attachments:
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", att.filename or f"discord_{att.id}")
                    out = dest_dir/f"{mid}__{safe}"
                    try:
                        await att.save(out)
                        meta = {
                            'platform': 'discord', 'name': att.filename, 'bytes': out.stat().st_size,
                            'mime': att.content_type, 'sha256': _sha256_file(out), 'ts': int(time.time()), 'url_src': att.url,
                        }
                        with open(out.with_suffix(out.suffix+".meta.json"), 'w', encoding='utf-8') as mf:
                            json.dump(meta, mf, ensure_ascii=False, indent=2)
                        refs.append((out, meta))
                    except Exception as e:
                        _log(f"[error] save attachment failed: {e}")
                if refs:
                    extra = "\n".join([f"- {str(p)} ({m.get('mime','')},{m.get('bytes',0)} bytes)" for p,m in refs])
                    body = (body + ("\n\nFiles:\n" + extra if extra else "")).strip()
            except Exception:
                pass
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

    # Outbound files watcher
    async def _send_file(ch_id: int, fp: Path, caption: str):
        ch = client.get_channel(ch_id)
        if not ch:
            return
        try:
            await ch.send(content=_summarize(caption or '', 1500, 10), file=discord.File(str(fp)))
        except Exception as e:
            _log(f"[error] file send failed to {ch_id}: {e}")

    def watch_outbound_files():
        files_cfg = (cfg.get('files') or {})
        if not bool(files_cfg.get('enabled', True)):
            return
        out_root = Path(str(files_cfg.get('outbound_dir') or (HOME/"work"/"upload"/"outbound")))
        sent: Dict[str, float] = {}
        while True:
            try:
                for peer in ('peerA','peerB'):
                    for folder in ('photos','files'):
                        d = out_root/peer/folder
                        if not d.exists():
                            continue
                        for f in d.iterdir():
                            if not f.is_file():
                                continue
                            if f.name.endswith('.sent.json'):
                                continue
                            key = str(f.resolve())
                            if key in sent and (time.time() - sent[key] < 3):
                                continue
                            cap = ''
                            try:
                                for sc in (f.with_suffix(f.suffix + '.caption.txt'), f.with_name(f.name + '.caption.txt')):
                                    if sc.exists():
                                        cap = sc.read_text(encoding='utf-8')
                                        break
                            except Exception:
                                pass
                            # queue send for all configured channels (to_user + to_peer_summary)
                            for ch_id in (chans_user or []) + (chans_peer or []):
                                try:
                                    asyncio.run_coroutine_threadsafe(_send_file(ch_id, f, f"[{ 'PeerA' if peer=='peerA' else 'PeerB' }]\n" + cap), client.loop)
                                except Exception as e:
                                    _log(f"[error] schedule file send failed: {e}")
                            meta = {'platform':'discord','ts': int(time.time()), 'file': str(f.name)}
                            try:
                                with open(f.with_name(f.name + '.sent.json'), 'w', encoding='utf-8') as mf:
                                    json.dump(meta, mf, ensure_ascii=False)
                            except Exception:
                                pass
                            sent[key] = time.time()
            except Exception:
                pass
            time.sleep(1.0)

    threading.Thread(target=watch_outbound_files, daemon=True).start()

    async def runner():
        await client.login(token)
        loop = asyncio.get_running_loop()
        loop.create_task(sender_loop())
        await client.connect(reconnect=True)

    asyncio.run(runner())

if __name__ == '__main__':
    main()
