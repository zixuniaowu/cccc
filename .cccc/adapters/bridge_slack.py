#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Slack Bridge (MVP)
- Outbound: read .cccc/state/outbox.jsonl (single source) and post messages to configured channels
- Inbound: Socket Mode (optional) to accept messages and route to mailbox inbox with a:/b:/both: prefixes
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import os, sys, json, time, re, threading, hashlib, datetime, urllib.request, urllib.parse
from urllib.error import HTTPError, URLError
try:
    import fcntl  # type: ignore
except Exception:
    fcntl = None  # type: ignore

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

def _acquire_singleton_lock(name: str = "slack-bridge"):
    """Prevent multiple slack bridge instances from running concurrently."""
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
            _append_log("[warn] duplicate slack bridge instance detected; exiting")
        except Exception:
            pass
        sys.exit(0)
    return f

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

def _sha256_file(fp: Path) -> str:
    h = hashlib.sha256()
    with open(fp, 'rb') as f:
        for chunk in iter(lambda: f.read(1024*64), b''):
            h.update(chunk)
    return h.hexdigest()

def _today_dir(root: Path, sub: str) -> Path:
    dt = datetime.datetime.now().strftime('%Y%m%d')
    # root is expected to be the inbound_dir already (e.g., .cccc/work/upload/inbound)
    p = root/sub/dt
    p.mkdir(parents=True, exist_ok=True)
    return p

def main():
    _acquire_singleton_lock("slack-bridge")
    cfg = read_yaml(HOME/"settings"/"slack.yaml")
    app_token = os.environ.get(str(cfg.get('app_token_env') or 'SLACK_APP_TOKEN')) or cfg.get('app_token')
    bot_token = os.environ.get(str(cfg.get('bot_token_env') or 'SLACK_BOT_TOKEN')) or cfg.get('bot_token')
    channels_to_user = [str(x) for x in (cfg.get('channels') or {}).get('to_user', [])]
    channels_peer = [str(x) for x in (cfg.get('channels') or {}).get('to_peer_summary', [])]
    reset = str((cfg.get('outbound') or {}).get('reset_on_start', 'baseline'))
    show_peers = bool(cfg.get('show_peer_messages', True))
    default_route = str(cfg.get('default_route','both')).lower() if cfg.get('default_route') else 'both'

    # Require bot token for any Slack operations
    if not bot_token:
        _append_log("[error] SLACK_BOT_TOKEN missing; exiting")
        sys.exit(1)

    # Outbound consumer
    try:
        from adapters.outbox_consumer import OutboxConsumer
    except Exception:
        from outbox_consumer import OutboxConsumer
    oc = OutboxConsumer(HOME, seen_name='slack', reset_on_start=reset)

    # Dynamic channel subscriptions persist under state; used to avoid editing YAML.
    SUBS_LOCK = threading.Lock()
    def _subs_path() -> Path:
        return HOME/"state"/"slack-subs.json"
    def load_subs() -> List[str]:
        p = _subs_path()
        try:
            if p.exists():
                arr = json.loads(p.read_text(encoding='utf-8')).get('channels') or []
                return [str(x) for x in arr]
        except Exception:
            pass
        return []
    def save_subs(items: List[str]):
        p = _subs_path(); p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(json.dumps({'channels': list(dict.fromkeys(items))[-2000:]}, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
    SUBS = load_subs()

    def send_text(chs: List[str], text: str):
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
        with SUBS_LOCK:
            chs = list(dict.fromkeys((channels_to_user or []) + (SUBS or [])))
        if chs:
            send_text(chs, msg)

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
        with SUBS_LOCK:
            chs = list(dict.fromkeys((channels_peer or []) + (SUBS or [])))
        if chs:
            send_text(chs, msg)

    th = threading.Thread(target=lambda: oc.loop(on_to_user, on_to_peer_summary), daemon=True)
    th.start()

    # Prepare WebClient (required for outbound; exit if slack_sdk missing)
    try:
        from slack_sdk.web import WebClient  # type: ignore
    except Exception as e:
        _append_log(f"[error] slack_sdk not installed: {e}; exiting")
        sys.exit(1)
    web = WebClient(token=bot_token)
    # Discover bot user id to ignore self-messages (prevent echo loops)
    BOT_USER_ID = ""
    try:
        auth = web.auth_test()
        BOT_USER_ID = str(auth.get('user_id') or '')
        _append_log(f"[info] slack bot user_id={BOT_USER_ID}")
    except Exception as e:
        _append_log(f"[warn] slack auth_test failed: {e}")
    client = None
    socket_mode_available = False
    if app_token:
        try:
            from slack_sdk.socket_mode import SocketModeClient  # type: ignore
            from slack_sdk.socket_mode.response import SocketModeResponse  # type: ignore
            client = SocketModeClient(app_token=app_token, web_client=web)
            socket_mode_available = True
        except Exception as e:
            _append_log(f"[warn] slack socket mode unavailable: {e}; inbound disabled")
    else:
        _append_log("[info] inbound disabled (no SLACK_APP_TOKEN)")

    def _download_slack_file(file_obj: Dict[str, Any], channel_id: Optional[str] = None) -> Optional[Tuple[Path, Dict[str, Any]]]:
        """Robustly download a Slack file with token auth, preserving Authorization
        across Slack-domain redirects, validating content-type, and streaming to disk.
        """
        try:
            # Prefer fresh url_private_download; fall back to url_private; refetch via files.info when missing
            url = file_obj.get('url_private_download') or file_obj.get('url_private')
            fid = str(file_obj.get('id') or '')
            if not url and fid:
                try:
                    info = web.files_info(file=fid)
                    file2 = (info or {}).get('file') or {}
                    url = file2.get('url_private_download') or file2.get('url_private')
                    # merge enriched fields
                    for k in ('mimetype','name','size'):
                        if not file_obj.get(k) and file2.get(k):
                            file_obj[k] = file2.get(k)
                except Exception as e:
                    _append_log(f"[warn] files_info failed for {fid}: {e}")
            if not url:
                return None
            name = file_obj.get('name') or f"slack_{int(time.time())}"
            mime = file_obj.get('mimetype') or ''
            size = int(file_obj.get('size') or 0)
            cfg_files = (cfg.get('files') or {})
            max_mb = int(cfg_files.get('max_mb', 16))
            if max_mb > 0 and size > max_mb * 1024 * 1024:
                _append_log(f"[inbound] skip large file {name} {size} bytes > {max_mb} MB")
                return None
            inbound_root = Path(str(cfg_files.get('inbound_dir') or (HOME/"work"/"upload"/"inbound")))
            # Unify: inbound/<platform>/<channel>/YYYYMMDD
            ch_folder = str(channel_id or 'unknown')
            day = datetime.datetime.now().strftime('%Y%m%d')
            dest_dir = inbound_root/'slack'/ch_folder/day
            dest_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
            mid = f"slack-{int(time.time())}"
            out = dest_dir/f"{mid}__{safe}"

            # Custom redirect handler that preserves Authorization for Slack-owned hosts only
            class _AuthRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
                    new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
                    try:
                        if new_req is None:
                            return None
                        auth = req.headers.get('Authorization')
                        if auth:
                            try:
                                o = urllib.parse.urlparse(req.full_url)
                                n = urllib.parse.urlparse(newurl)
                                # Only forward token to Slack-owned domains
                                def _is_slack_host(netloc: str) -> bool:
                                    h = (netloc or '').lower()
                                    return h.endswith('slack.com') or h.endswith('slack-edge.com') or h.endswith('files.slack.com')
                                if _is_slack_host(o.netloc) and _is_slack_host(n.netloc):
                                    new_req.add_unredirected_header('Authorization', auth)
                            except Exception:
                                pass
                        # Preserve Accept / UA for subsequent hops
                        if 'Accept' in req.headers:
                            new_req.add_unredirected_header('Accept', req.headers['Accept'])
                        if 'User-Agent' in req.headers:
                            new_req.add_unredirected_header('User-Agent', req.headers['User-Agent'])
                    except Exception:
                        pass
                    return new_req

            opener = urllib.request.build_opener(_AuthRedirect())
            headers = {
                "Authorization": f"Bearer {bot_token}",
                "Accept": "application/octet-stream, */*",
                "User-Agent": "cccc-slack-bridge/0.2.9"
            }

            def _fetch_to(out_path: Path, src_url: str) -> Tuple[bool, str, int, Optional[str]]:
                try:
                    req = urllib.request.Request(src_url, headers=headers)
                    with opener.open(req, timeout=120) as r:
                        ctype = (r.headers.get('Content-Type') or '').lower()
                        clen = r.headers.get('Content-Length')
                        exp = int(clen) if clen and clen.isdigit() else -1
                        # Reject obvious error bodies
                        if 'application/json' in ctype or 'text/html' in ctype:
                            blob = r.read(512)
                            try:
                                preview = blob.decode('utf-8','ignore')
                            except Exception:
                                preview = str(blob[:80])
                            return False, ctype, 0, preview
                        written = 0
                        with open(out_path, 'wb') as f:
                            while True:
                                chunk = r.read(1024*256)
                                if not chunk:
                                    break
                                f.write(chunk)
                                written += len(chunk)
                        if exp > 0 and written != exp:
                            return False, ctype, written, f"length_mismatch expected={exp} got={written}"
                        return True, ctype, written, None
                except HTTPError as e:
                    return False, f"http_error:{e.code}", 0, str(e)
                except URLError as e:
                    return False, "url_error", 0, str(e)
                except Exception as e:
                    return False, "exception", 0, str(e)

            ok, ctype, bytes_written, err = _fetch_to(out, url)
            if not ok and fid:
                # One retry via fresh files.info (URL may rotate)
                try:
                    info = web.files_info(file=fid)
                    file2 = (info or {}).get('file') or {}
                    retry_url = file2.get('url_private_download') or file2.get('url_private') or url
                    ok, ctype, bytes_written, err = _fetch_to(out, retry_url)
                except Exception as e:
                    _append_log(f"[warn] retry files_info failed for {fid}: {e}")

            if not ok:
                _append_log(f"[error] slack download failed name={name} url={url} ctype={ctype} err={err}")
                try:
                    if out.exists():
                        out.unlink()
                except Exception:
                    pass
                return None

            meta = {
                'platform': 'slack', 'name': name, 'bytes': bytes_written,
                'mime': mime or ctype, 'sha256': _sha256_file(out), 'ts': int(time.time()), 'url_src': url,
                'mid': mid,
            }
            try:
                with open(out.with_suffix(out.suffix+".meta.json"), 'w', encoding='utf-8') as mf:
                    json.dump(meta, mf, ensure_ascii=False, indent=2)
            except Exception:
                pass
            return out, meta
        except Exception as e:
            _append_log(f"[error] download file failed: {e}")
            return None

    def handle(*args, **kwargs):
        try:
            # Accept both signatures: (req) or (client, req)
            _client = client
            if len(args) == 1:
                req = args[0]
            elif len(args) >= 2:
                _client = args[0]
                req = args[1]
            else:
                return
            # req: Slack SocketModeRequest
            typ = str(getattr(req, 'type', '') or '')
            if typ != 'events_api':
                return
            payload = getattr(req, 'payload', None) or {}
            event = (payload or {}).get('event') or {}
            # Ack early to avoid timeouts
            try:
                try:
                    from slack_sdk.socket_mode.response import SocketModeResponse  # type: ignore
                    _client.send_socket_mode_response(SocketModeResponse(envelope_id=getattr(req, 'envelope_id', '')))  # type: ignore
                except Exception:
                    pass
            except Exception:
                pass
            etype = str(event.get('type') or '')
            if etype == 'file_shared':
                # Enforce explicit routing: ignore bare file_shared without text
                _append_log("[inbound] drop file_shared without text (require a:/b:/both: with attachments)")
                return
            if etype != 'message':
                return
            sub = str(event.get('subtype') or '')
            # Allow subtype=file_share (channel message with files). Skip other subtypes (edits, bot_message, etc.)
            if sub and sub.lower() != 'file_share':
                return
            text = str(event.get('text') or '')
            ch = str(event.get('channel') or '')
            user = str(event.get('user') or '')
            # Routing prefixes only; ignore general chatter without explicit route
            prefix_re = re.compile(r"^\s*(a:|b:|both:)\s*", re.I)
            has_prefix = bool(prefix_re.search(text))
            # Mention form: <@BOT_USER_ID> a: ...
            if (not has_prefix) and BOT_USER_ID:
                if re.search(rf"^\s*<@{re.escape(BOT_USER_ID)}>\s+(a:|b:|both:)\s*", text, re.I):
                    has_prefix = True
            # Ignore self/bot messages to avoid echo loops
            if event.get('bot_id') or (BOT_USER_ID and user == BOT_USER_ID):
                return
            # Subscribe/Unsubscribe commands (plain text, not Slash Commands)
            low = text.strip().lower()
            if low in ("subscribe","sub"):
                with SUBS_LOCK:
                    if ch not in SUBS:
                        SUBS.append(ch); save_subs(SUBS)
                try:
                    web.chat_postMessage(channel=ch, text="Subscribed this channel for to_user/to_peer_summary.")
                except Exception:
                    pass
                return
            # Runtime toggle: showpeers on|off
            msp = re.match(r"^\s*/?showpeers\s+(on|off)\b", low)
            if msp:
                val = (msp.group(1) == 'on')
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
                    web.chat_postMessage(channel=ch, text=f"Peer↔Peer summary set to: {'ON' if val else 'OFF'} (global)")
                except Exception:
                    pass
                return
            if low in ("unsubscribe","unsub"):
                with SUBS_LOCK:
                    SUBS2 = [x for x in SUBS if x != ch]
                    if len(SUBS2) != len(SUBS):
                        SUBS[:] = SUBS2; save_subs(SUBS)
                try:
                    web.chat_postMessage(channel=ch, text="Unsubscribed this channel.")
                except Exception:
                    pass
                return

            # Files (if any)
            try:
                flist = event.get('files') or []
                if flist and has_prefix:  # only accept files with explicit routing
                    saved: List[Tuple[Path, Dict[str, Any]]] = []
                    missed: List[str] = []
                    for fo in flist:
                        got = _download_slack_file(fo, ch)
                        if got:
                            saved.append(got)
                        else:
                            link = str((fo or {}).get('permalink') or (fo or {}).get('url_private') or '')
                            if link:
                                missed.append(link)
                    if saved:
                        refs = "\n".join([f"- {str(p)} ({m.get('mime','')},{m.get('bytes',0)} bytes)" for p,m in saved])
                        text = (text + "\n\nFiles:\n" + refs).strip()
                    if missed:
                        refs2 = "\n".join([f"- {u}" for u in missed])
                        text = (text + "\n\nFiles (undownloaded):\n" + refs2).strip()
            except Exception as e:
                _append_log(f"[error] files in message failed: {e}")
            # Route and write inbox
            if not has_prefix:
                # Allow only subscribe/unsubscribe without prefix; drop other chatter
                _append_log(f"[inbound] drop without prefix ch={ch}")
                return
            routes, body = _route_from_text(text, default_route)
            mid = f"slack-{int(time.time())}-{user[-4:]}"
            _write_inbox(routes, body, mid)
        except Exception:
            try:
                from slack_sdk.socket_mode.response import SocketModeResponse  # type: ignore
                _client.send_socket_mode_response(SocketModeResponse(envelope_id=getattr(req, 'envelope_id', '')))  # type: ignore
            except Exception: pass
    if socket_mode_available and client is not None:
        client.socket_mode_request_listeners.append(handle)
        _append_log("[info] slack socket mode starting …")
        client.connect()
    # Outbound files watcher
    def _send_file_to_channels(fp: Path, caption: str) -> bool:
        cap = _summarize(caption or '', 1200, 10)
        chs = list(dict.fromkeys((channels_to_user or []) + (channels_peer or [])))
        try:
            from slack_sdk import WebClient  # type: ignore
            cli = WebClient(token=bot_token)
            ok_any = False
            for ch in chs:
                try:
                    # prefer files_upload_v2; fallback if not available
                    try:
                        cli.files_upload_v2(channels=ch, file=str(fp), filename=fp.name, initial_comment=cap or None)
                    except Exception:
                        cli.files_upload(channels=ch, file=str(fp), filename=fp.name, initial_comment=cap or None)
                    ok_any = True
                    time.sleep(0.5)
                except Exception as e:
                    _append_log(f"[error] slack file upload failed to {ch}: {e}")
            return ok_any
        except Exception as e:
            _append_log(f"[error] slack_sdk missing or upload failed: {e}")
            return False

    def watch_outbound_files():
        files_cfg = (cfg.get('files') or {})
        if not bool(files_cfg.get('enabled', True)):
            return
        out_root = Path(str(files_cfg.get('outbound_dir') or (HOME/"work"/"upload"/"outbound")))
        sent_files: Dict[str, float] = {}
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
                            if f.suffix.endswith('.json') or f.name.endswith('.sent.json'):
                                continue
                            key = str(f.resolve())
                            if key in sent_files and (time.time() - sent_files[key] < 3):
                                continue
                            # caption sidecar
                            cap = ''
                            try:
                                for sc in (f.with_suffix(f.suffix + '.caption.txt'), f.with_name(f.name + '.caption.txt')):
                                    if sc.exists():
                                        cap = sc.read_text(encoding='utf-8')
                                        break
                            except Exception:
                                pass
                            ok = _send_file_to_channels(f, f"[{ 'PeerA' if peer=='peerA' else 'PeerB' }]\n" + cap)
                            if ok:
                                meta = {'platform':'slack','ts': int(time.time()), 'file': str(f.name)}
                                try:
                                    with open(f.with_name(f.name + '.sent.json'), 'w', encoding='utf-8') as mf:
                                        json.dump(meta, mf, ensure_ascii=False)
                                except Exception:
                                    pass
                                sent_files[key] = time.time()
            except Exception:
                pass
            time.sleep(1.0)

    threading.Thread(target=watch_outbound_files, daemon=True).start()

    try:
        while True: time.sleep(1.0)
    finally:
        try: client.disconnect()
        except Exception: pass

if __name__ == '__main__':
    main()
