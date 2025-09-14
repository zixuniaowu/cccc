#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord Bridge (MVP)
- Outbound: read outbox.jsonl and post to configured channels
- Inbound: on_message (optional) to route into mailbox via a:/b:/both:
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import os, sys, json, time, re, threading, asyncio, hashlib, datetime
try:
    import fcntl  # type: ignore
except Exception:
    fcntl = None  # type: ignore

ROOT = Path.cwd(); HOME = ROOT/".cccc"
if str(HOME) not in sys.path: sys.path.insert(0, str(HOME))

def read_yaml(p: Path) -> Dict[str, Any]:
    # Back-compat shim to centralized config loader
    try:
        from common.config import read_config as _rc  # type: ignore
        return _rc(p)
    except Exception:
        try:
            import yaml  # type: ignore
            return yaml.safe_load(p.read_text(encoding='utf-8')) or {}
        except Exception:
            try:
                return json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                return {}

def _now(): return time.strftime('%Y-%m-%d %H:%M:%S')
def _log(line: str):
    p = HOME/"state"/"bridge-discord.log"; p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a', encoding='utf-8') as f: f.write(f"{_now()} {line}\n")

def _acquire_singleton_lock(name: str = "discord-bridge"):
    lf_path = HOME/"state"/f"{name}.lock"
    lf_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lf_path, 'w')
    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(str(os.getpid()))
        f.flush()
    except Exception:
        try:
            _log("[warn] duplicate discord bridge instance detected; exiting")
        except Exception:
            pass
        sys.exit(0)
    return f

TAG_RE = re.compile(r"<\s*FROM_USER\s*>", re.I)
def _wrap_from_user(s: str) -> str:
    return s if TAG_RE.search(s or '') else f"<FROM_USER>\n{(s or '').strip()}\n</FROM_USER>\n"

def _route_from_text(text: str, default_route: str) -> Tuple[List[str], str]:
    t = (text or '').strip()
    # Strip leading mention (e.g., <@1234567890>) if present
    t = re.sub(r"^\s*<@!?\d+>\s+", "", t)
    # Support ASCII / fullwidth colon with explicit key capture
    m = re.match(r"^(a|b|both)[:：]\s*", t, re.I)
    if m:
        kind = m.group(1).lower(); t = t[m.end():]
        if kind == 'a':
            return ['peerA'], t
        if kind == 'b':
            return ['peerB'], t
        return ['peerA','peerB'], t
    # Slash commands typed as plain text
    m2 = re.match(r"^/(a|b|both)(?:@\S+)?\s+", t, re.I)
    if m2:
        cmd = m2.group(1).lower(); t = t[m2.end():]
        if cmd == 'a':
            return ['peerA'], t
        if cmd == 'b':
            return ['peerB'], t
        return ['peerA','peerB'], t
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
    p = root/sub/dt
    p.mkdir(parents=True, exist_ok=True)
    return p

def main():
    _acquire_singleton_lock("discord-bridge")
    cfg = read_yaml(HOME/"settings"/"discord.yaml")
    token = os.environ.get(str(cfg.get('bot_token_env') or 'DISCORD_BOT_TOKEN')) or cfg.get('bot_token')
    chans_user = [int(x) for x in (cfg.get('channels') or {}).get('to_user', [])]
    chans_peer = [int(x) for x in (cfg.get('channels') or {}).get('to_peer_summary', [])]
    reset = str((cfg.get('outbound') or {}).get('reset_on_start', 'baseline'))
    show_peers = bool(cfg.get('show_peer_messages', True))
    default_route = str(cfg.get('default_route','both')).lower() if cfg.get('default_route') else 'both'

    # Outbound consumer thread
    try:
        from adapters.outbox_consumer import OutboxConsumer  # type: ignore
    except Exception as e:
        _log(f"[error] OutboxConsumer import failed: {e}; exiting")
        sys.exit(1)
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
        # Runtime override via shared bridge-runtime.json
        eff_show = show_peers
        try:
            rp = HOME/"state"/"bridge-runtime.json"
            if rp.exists():
                eff_show = bool((json.loads(rp.read_text(encoding='utf-8')) or {}).get('show_peer_messages', show_peers))
        except Exception:
            pass
        if not eff_show:
            return
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

    # Require token for any Discord operations
    if not token:
        _log("[error] DISCORD_BOT_TOKEN missing; exiting")
        sys.exit(1)

    # Live mode with discord.py
    try:
        import discord  # type: ignore
    except Exception as e:
        _log(f"[error] discord.py not installed: {e}; exiting")
        sys.exit(1)

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
            low = (text or '').strip().lower()
            # Require routing prefixes to avoid forwarding general chatter (support fullwidth colon & mentions)
            has_prefix = bool(re.search(r"^\s*(?:<@!?\d+>\s+)?(a[:：]|b[:：]|both[:：])\s*", text, re.I) or
                               re.search(r"^\s*(?:<@!?\d+>\s+)?/(a|b|both)(?:@\S+)?\s+", text, re.I))
            if not has_prefix:
                if low not in ('subscribe','sub','unsubscribe','unsub','showpeers on','showpeers off') and not message.attachments:
                    # Drop chatter without explicit prefix; keep logs quiet in normal operation
                    return
            if low in ('subscribe','sub'):
                try:
                    with SUBS_LOCK:
                        if message.channel.id not in SUBS:
                            SUBS.append(message.channel.id); save_subs(SUBS)
                    await message.channel.send('Subscribed this channel for to_user/to_peer_summary.')
                except Exception:
                    pass
                return
            if low in ('showpeers on','showpeers off'):
                val = (low.endswith('on'))
                rt_path = HOME/"state"/"bridge-runtime.json"; rt_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    cur = {}
                    if rt_path.exists():
                        cur = json.loads(rt_path.read_text(encoding='utf-8'))
                    cur['show_peer_messages'] = bool(val)
                    rt_path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding='utf-8')
                except Exception:
                    pass
                try:
                    await message.channel.send(f"Peer↔Peer summary set to: {'ON' if val else 'OFF'} (global)")
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
                inbound_root = Path(str(files_cfg.get('inbound_dir') or (HOME/"work"/"upload"/"inbound")))
                # Unified inbound layout: inbound/YYYYMMDD
                day = datetime.datetime.now().strftime('%Y%m%d')
                dest_dir = inbound_root/day
                dest_dir.mkdir(parents=True, exist_ok=True)
                refs = []
                # Only accept attachments if explicit routing prefix present in text
                for att in (message.attachments or []) if has_prefix else []:
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", att.filename or f"discord_{att.id}")
                    out = dest_dir/f"{mid}__{safe}"
                    try:
                        await att.save(out)
                        meta = {
                            'platform': 'discord', 'name': att.filename, 'bytes': out.stat().st_size,
                            'mime': att.content_type, 'sha256': _sha256_file(out), 'ts': int(time.time()), 'url_src': att.url,
                            'mid': mid,
                        }
                        with open(out.with_suffix(out.suffix+".meta.json"), 'w', encoding='utf-8') as mf:
                            json.dump(meta, mf, ensure_ascii=False, indent=2)
                        refs.append((out, meta))
                    except Exception as e:
                        _log(f"[error] save attachment failed: {e}")
                if refs:
                    extra = "\n".join([f"- {str(p)} ({m.get('mime','')},{m.get('bytes',0)} bytes)" for p,m in refs])
                    body = (body + ("\n\nFiles:\n" + extra if extra else "")).strip()
                    # Index inbound files with computed routes
                    try:
                        idx = HOME/"state"/"inbound-index.jsonl"; idx.parent.mkdir(parents=True, exist_ok=True)
                        for pth, mt in refs:
                            rec = { 'ts': int(time.time()), 'path': str(pth), 'platform': 'discord', **mt, 'routes': routes }
                            with idx.open('a', encoding='utf-8') as f:
                                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
            except Exception:
                pass
            _write_inbox(routes, body, mid)
        except Exception as e:
            try:
                _log(f"[error] on_message: {e}")
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
        def _read_caption(fp: Path) -> str:
            cap = ''
            try:
                for sc in (fp.with_suffix(fp.suffix + '.caption.txt'), fp.with_name(fp.name + '.caption.txt')):
                    if sc.exists():
                        cap = sc.read_text(encoding='utf-8')
                        break
            except Exception:
                pass
            return cap
        def _detect_route_from_caption(cap: str) -> Tuple[str, str]:
            t = (cap or '').lstrip()
            m = re.match(r"^(a:|b:|both:)\s*", t, re.I)
            if m:
                tag = m.group(1).lower(); body = t[m.end():]
                return ({'a:':'peerA','b:':'peerB','both:':'both'}[tag], body)
            return ('', cap)
        def _route_sidecar(fp: Path) -> str:
            try:
                for sc in (fp.with_suffix(fp.suffix + '.route'), fp.with_name(fp.name + '.route')):
                    if sc.exists():
                        val = (sc.read_text(encoding='utf-8').strip() or '').lower()
                        if val in ('a','peera','peera','peera'.lower()):
                            return 'peerA'
                        if val in ('b','peerb','peerb','peerb'.lower()):
                            return 'peerB'
                        if val in ('both','all','ab','a+b'):
                            return 'both'
            except Exception:
                pass
            return ''
        def _iter_targets():
            # Flat-only scheme: scan outbound root directory for files
            if out_root.exists():
                for f in out_root.iterdir():
                    if f.is_file():
                        yield f
        while True:
            try:
                for f in _iter_targets():
                    if not f.is_file():
                        continue
                    nm = f.name.lower()
                    if nm.endswith('.sent.json') or nm.endswith('.meta.json') or nm.endswith('.caption.txt') or nm.endswith('.route'):
                        continue
                    # If a sent sidecar exists, skip and best-effort cleanup payload
                    if f.with_name(f.name + '.sent.json').exists():
                        try:
                            f.unlink()
                        except Exception:
                            pass
                        continue
                    key = str(f.resolve())
                    if key in sent and (time.time() - sent[key] < 3):
                        continue
                    cap0 = _read_caption(f)
                    route = _route_sidecar(f)
                    if not route:
                        route, cap0 = _detect_route_from_caption(cap0)
                    if not route:
                        route = 'both'
                    label = 'PeerA' if route=='peerA' else ('PeerB' if route=='peerB' else 'PeerA+PeerB')
                        # queue send for all configured + subscribed channels (to_user + to_peer_summary + SUBS)
                    with SUBS_LOCK:
                        all_chs = list(dict.fromkeys((chans_user or []) + (chans_peer or []) + (SUBS or [])))
                    for ch_id in all_chs:
                        try:
                            asyncio.run_coroutine_threadsafe(_send_file(ch_id, f, f"[{label}]\n" + cap0), client.loop)
                        except Exception as e:
                            _log(f"[error] schedule file send failed: {e}")
                    meta = {'platform':'discord','ts': int(time.time()), 'file': str(f.name)}
                    try:
                        with open(f.with_name(f.name + '.sent.json'), 'w', encoding='utf-8') as mf:
                            json.dump(meta, mf, ensure_ascii=False)
                    except Exception:
                        pass
                    # Delete payload and sidecars to avoid repeat sends
                    try:
                        for side in (
                            f.with_suffix(f.suffix + '.caption.txt'),
                            f.with_suffix(f.suffix + '.route'),
                            f.with_suffix(f.suffix + '.meta.json'),
                        ):
                            try:
                                if side.exists():
                                    side.unlink()
                            except Exception:
                                pass
                        f.unlink()
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
