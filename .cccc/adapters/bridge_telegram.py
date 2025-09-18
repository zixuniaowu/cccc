#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bridge (MVP)
- Network mode: gate by token/allowlist (long-poll getUpdates).
- Inbound: messages -> .cccc/mailbox/<peer>/inbox (numbered files with [MID]); supports a:/b:/both: routing.
- Outbound: read .cccc/state/outbox.jsonl (single source) and send concise summaries to chat(s).
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
    from common.config import read_config as _read_config  # type: ignore
except Exception:
    _read_config = None

# Voice processing support
try:
    import speech_recognition as sr
    import edge_tts
    import asyncio
    from pydub import AudioSegment
    from langdetect import detect, DetectorFactory
    # Set seed for consistent results
    DetectorFactory.seed = 0
    VOICE_DEPS_AVAILABLE = True
except ImportError:
    VOICE_DEPS_AVAILABLE = False

ROOT = Path.cwd()
HOME = ROOT/".cccc"
# Ensure we can import modules from .cccc (single-source preamble via prompt_weaver)
if str(HOME) not in sys.path:
    sys.path.insert(0, str(HOME))

def read_yaml(p: Path) -> Dict[str, Any]:
    if _read_config is not None:
        try:
            return _read_config(p)
        except Exception:
            pass
    # Fallback parsers
    try:
        import yaml as _y
        return _y.safe_load(p.read_text(encoding='utf-8')) or {}
    except Exception:
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
    # Support plain prefixes: a:/b:/both: (ASCII or fullwidth colon)
    m = re.match(r"^(a|b|both)[:：]\s*", t, re.I)
    if m:
        kind = m.group(1).lower()
        t = t[m.end():]
        if kind == 'a':
            return ['peerA'], t
        if kind == 'b':
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
    m3 = re.match(r"^@\S+\s+(a|b|both)[:：]\s*", t, re.I)
    if m3:
        kind = m3.group(1).lower()
        t = t[m3.end():]
        if kind == 'a':
            return ['peerA'], t
        if kind == 'b':
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
    # Lazy preamble handled centrally in orchestrator; adapter no longer injects.
    st = {"PeerA": True, "PeerB": True}
    for peer in routes:
        inbox_dir, proc_dir, state = _ensure_dirs(home, peer)
        seq = _next_seq(inbox_dir, proc_dir, state, peer)
        fname = f"{seq}.{mid}.txt"
        final = payload
        _write_text(inbox_dir/fname, final)
        # Best-effort: also mirror to inbox.md for adapter users
        _write_text((home/"mailbox"/peer/"inbox.md"), final)

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
    # Unified runtime for all bridges
    return HOME/"state"/"bridge-runtime.json"

def load_runtime() -> Dict[str, Any]:
    p = _runtime_path()
    try:
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        pass
    # Back-compat: fall back to legacy telegram-runtime.json
    try:
        legacy = HOME/"state"/"telegram-runtime.json"
        if legacy.exists():
            return json.loads(legacy.read_text(encoding='utf-8'))
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

def _summarize(text: str, max_chars: int, max_lines: int = 8) -> str:
    """Summarize while preserving line breaks for readability.
    - Normalize newlines, trim trailing spaces
    - Collapse consecutive blank lines
    - Keep at most max_lines; then cap by max_chars
    """
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "  ")
    lines = [ln.rstrip() for ln in t.split("\n")]
    # strip leading/trailing empty lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    # collapse multiple blank lines
    kept = []
    empty = 0
    for ln in lines:
        if not ln.strip():
            empty += 1
            if empty <= 1:
                kept.append("")
        else:
            empty = 0
            kept.append(ln)
    # limit lines
    kept = kept[:max_lines]
    out = "\n".join(kept).strip()
    if len(out) > max_chars:
        out = out[: max(0, max_chars - 1) ] + "…"
    return out

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

def main():
    cfg = read_yaml(HOME/"settings"/"telegram.yaml")
    # Real network path: gate by token and allowlist; long-poll getUpdates; send concise summaries
    _acquire_singleton_lock("telegram-bridge")
    token_env = str(cfg.get('token_env') or 'TELEGRAM_BOT_TOKEN')
    # Token is injected by parent process into env[token_env]; do not consult other env by default here
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
        print("[telegram_bridge] Missing token or allowlist; set discover_allowlist or configure allow_chats.")
        sys.exit(1)

    def tg_api(method: str, params: Dict[str, Any], *, timeout: int = 35) -> Dict[str, Any]:
        base = f"https://api.telegram.org/bot{token}/{method}"
        # Use JSON consistently to avoid encoding issues with non-ASCII text
        data = json.dumps(params, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(base, data=data, method='POST')
        req.add_header('Content-Type', 'application/json; charset=utf-8')
        req.add_header('Accept', 'application/json')
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
            'allowed_updates': json.dumps(["message", "edited_message", "callback_query"])  # type: ignore
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

    def clean_text(text: str) -> str:
        """Clean text by removing control characters that cause display issues"""
        if not text:
            return text
        
        # Remove control characters (except \n, \r, \t)
        # Keep only printable characters plus newline, carriage return, and tab
        cleaned = ""
        for char in text:
            code = ord(char)
            # Keep normal printable characters (32-126), newline (10), carriage return (13), tab (9)
            # and Unicode characters above 127
            if (32 <= code <= 126) or code in (9, 10, 13) or code > 127:
                cleaned += char
        
        return cleaned

    def is_cmd(s: str, name: str) -> bool:
        return re.match(rf"^/{name}(?:@\S+)?(?:\s|$)", s.strip(), re.I) is not None

    outlog = HOME/"state"/"bridge-telegram.log"
    _append_log(outlog, "[net] bridge started")
    # Outbound watcher (send summaries when to_user changes; debounced per peer)
    debounce = int(cfg.get('debounce_seconds') or 3)
    max_chars = int(cfg.get('max_msg_chars') or 900)
    max_lines = int(cfg.get('max_msg_lines') or 8)
    peer_debounce = int(cfg.get('peer_debounce_seconds') or debounce)
    peer_max_chars = int(cfg.get('peer_message_max_chars') or 600)
    peer_max_lines = int(cfg.get('peer_message_max_lines') or 6)
    runtime = load_runtime()
    show_peers_default = bool(cfg.get('show_peer_messages', True))
    show_peers = bool(runtime.get('show_peer_messages', show_peers_default))

    # Routing policy
    routing = cfg.get('routing') or {}
    require_explicit = bool(routing.get('require_explicit', True))
    allow_prefix = bool(routing.get('allow_prefix', True))
    require_mention = bool(routing.get('require_mention', False))
    dm_conf = cfg.get('dm') or {}
    dm_route_default = str(dm_conf.get('route_default', 'both'))
    hints = cfg.get('hints') or {}
    hint_cooldown = int(hints.get('cooldown_seconds', 300))

    # Files policy
    files_conf = cfg.get('files') or {}
    files_enabled = bool(files_conf.get('enabled', True))
    max_mb = int(files_conf.get('max_mb', 16))
    max_bytes = max_mb * 1024 * 1024
    allowed_mime = [str(x) for x in (files_conf.get('allowed_mime') or [])]
    inbound_dir = Path(files_conf.get('inbound_dir') or HOME/"work"/"upload"/"inbound")
    outbound_dir = Path(files_conf.get('outbound_dir') or HOME/"work"/"upload"/"outbound")

    # Voice policy
    voice_conf = cfg.get('voice') or {}
    voice_enabled = bool(voice_conf.get('enabled', False)) and VOICE_DEPS_AVAILABLE
    stt_conf = voice_conf.get('speech_to_text') or {}
    stt_enabled = bool(stt_conf.get('enabled', False))
    stt_languages = stt_conf.get('languages', ['zh-CN'])
    stt_default_lang = stt_conf.get('default_language', 'zh-CN')
    tts_conf = voice_conf.get('text_to_speech') or {}
    tts_enabled = bool(tts_conf.get('enabled', False))
    tts_languages = tts_conf.get('languages', ['zh-CN'])
    tts_default_lang = tts_conf.get('default_language', 'zh-CN')
    tts_voice_gender = tts_conf.get('voice_gender', 'female')
    
    # Auto voice reply policy
    auto_voice_conf = voice_conf.get('auto_voice_reply') or {}
    auto_voice_enabled = bool(auto_voice_conf.get('enabled', False))
    auto_voice_for_user = bool(auto_voice_conf.get('for_user_messages', True))
    auto_voice_for_peer = bool(auto_voice_conf.get('for_peer_messages', False))
    auto_voice_min_len = int(auto_voice_conf.get('min_text_length', 10))
    auto_voice_max_len = int(auto_voice_conf.get('max_text_length', 500))

    # Hint cooldown memory { (chat_id,user_id): ts }
    hint_last: Dict[Tuple[int,int], float] = {}
    
    # Track user input method for smart voice reply { chat_id: 'voice' | 'text' }
    user_input_method: Dict[int, str] = {}

    def _mime_allowed(m: str) -> bool:
        if not allowed_mime:
            return True
        for pat in allowed_mime:
            if pat.endswith('/*'):
                if m.startswith(pat[:-1]):
                    return True
            if m.lower() == pat.lower():
                return True
        return False

    def _detect_language(text: str) -> str:
        """Detect language of text and return appropriate language code"""
        try:
            detected = detect(text)
            # Map detected languages to our supported codes
            lang_map = {
                'zh-cn': 'zh-CN',
                'zh': 'zh-CN',
                'en': 'en-US', 
                'ja': 'ja-JP',
                'so': 'en-US'  # Sometimes 'so' is detected for short English text
            }
            result = lang_map.get(detected, 'zh-CN')
            
            # Additional heuristics for better detection
            if any(ord(c) > 127 for c in text):  # Contains non-ASCII characters
                if any('\u4e00' <= c <= '\u9fff' for c in text):  # Chinese characters
                    result = 'zh-CN'
                elif any('\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff' for c in text):  # Japanese characters
                    result = 'ja-JP'
            
            return result
        except:
            return 'zh-CN'  # Default fallback

    def _convert_voice_to_text(audio_path: Path, language: str = 'zh-CN') -> Tuple[str, str]:
        """Convert voice file to text using speech recognition, return (text, detected_language)"""
        if not voice_enabled or not stt_enabled:
            return "", language
        
        try:
            # Convert audio to WAV format if necessary
            if audio_path.suffix.lower() in ['.ogg', '.m4a', '.mp3']:
                audio = AudioSegment.from_file(str(audio_path))
                wav_path = audio_path.with_suffix('.wav')
                audio.export(str(wav_path), format="wav")
                audio_path = wav_path
            
            # Initialize recognizer
            recognizer = sr.Recognizer()
            
            # Load audio file
            with sr.AudioFile(str(audio_path)) as source:
                audio_data = recognizer.record(source)
            
            # Try to recognize with multiple languages
            text = ""
            detected_lang = language
            
            # First try with default Chinese
            try:
                text = recognizer.recognize_google(audio_data, language='zh-CN')
                detected_lang = 'zh-CN'
            except:
                # If Chinese fails, try English
                try:
                    text = recognizer.recognize_google(audio_data, language='en-US')
                    detected_lang = 'en-US'
                except:
                    # If English fails, try Japanese
                    try:
                        text = recognizer.recognize_google(audio_data, language='ja-JP')
                        detected_lang = 'ja-JP'
                    except:
                        # Final fallback
                        text = recognizer.recognize_google(audio_data)
                        detected_lang = _detect_language(text) if text else language
            
            # If we got text, refine language detection
            if text:
                detected_lang = _detect_language(text)
            
            return text.strip(), detected_lang
            
        except Exception as e:
            _append_log(outlog, f"[voice-stt-error] {e}")
            return "", language

    def _convert_text_to_voice(text: str, output_path: Path, language: str = 'zh-CN') -> bool:
        """Convert text to voice file using Edge-TTS"""
        if not voice_enabled or not tts_enabled:
            return False
            
        try:
            # Map language codes to Edge-TTS voice names
            voice_map = {
                'zh-CN': 'zh-CN-XiaoxiaoNeural',  # 中文女声
                'zh-TW': 'zh-TW-HsiaoyuNeural',   # 台湾中文女声
                'en-US': 'en-US-AriaNeural',      # 英文女声
                'ja-JP': 'ja-JP-NanamiNeural',    # 日文女声
            }
            
            voice_name = voice_map.get(language, 'zh-CN-XiaoxiaoNeural')
            
            # Create async function to handle Edge-TTS
            async def generate_speech():
                communicate = edge_tts.Communicate(text, voice_name)
                await communicate.save(str(output_path))
            
            # Run the async function
            asyncio.run(generate_speech())
            
            # Wait a bit to ensure file is fully written
            time.sleep(0.1)
            
            # Check if file exists and has content
            if output_path.exists():
                file_size = output_path.stat().st_size
                _append_log(outlog, f"[voice-tts] generated {file_size} bytes for {voice_name}")
                
                # Double check - wait a bit more if file is empty initially
                if file_size == 0:
                    time.sleep(0.5)
                    file_size = output_path.stat().st_size
                    _append_log(outlog, f"[voice-tts] after wait: {file_size} bytes")
                
                return file_size > 0  # Return True only if file has content
            else:
                _append_log(outlog, f"[voice-tts] file not created: {output_path}")
                return False
            
        except Exception as e:
            _append_log(outlog, f"[voice-edge-tts-error] {e}")
            return False

    def _sanitize_name(name: str) -> str:
        name = re.sub(r"[^A-Za-z0-9_.\-]+", "_", name)
        return name[:120] or f"file_{int(time.time())}"

    def _save_file_from_telegram(file_id: str, orig_name: str, chat_id: int, mid: str) -> Tuple[Path, Dict[str,Any]]:
        meta: Dict[str,Any] = {}
        # getFile
        res = tg_api('getFile', {'file_id': file_id}, timeout=20)
        if not res.get('ok'):
            raise RuntimeError(f"getFile failed: {res}")
        file_path = (res.get('result') or {}).get('file_path')
        if not file_path:
            raise RuntimeError("file_path missing")
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        # Prepare path
        day = time.strftime('%Y%m%d')
        safe = _sanitize_name(orig_name or os.path.basename(file_path))
        # Unify: inbound/<platform>/<chat_id>/<YYYYMMDD>
        out_dir = inbound_dir/"telegram"/str(chat_id)/day
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir/f"{mid}__{safe}"
        # Download
        with urllib.request.urlopen(url, timeout=60) as resp, open(out_path, 'wb') as f:
            data = resp.read()
            if len(data) > max_bytes:
                raise RuntimeError(f"file too large: {len(data)} bytes > {max_bytes}")
            f.write(data)
            meta['bytes'] = len(data)
        # Hash
        import hashlib
        h = hashlib.sha256()
        with open(out_path, 'rb') as f:
            while True:
                chunk = f.read(1024*64)
                if not chunk: break
                h.update(chunk)
        meta['sha256'] = h.hexdigest()
        meta['path'] = str(out_path)
        meta['name'] = safe
        return out_path, meta

    def _maybe_hint(chat_id: int, user_id: int):
        now = time.time()
        key = (chat_id, user_id)
        if now - float(hint_last.get(key, 0)) < hint_cooldown:
            return
        hint_last[key] = now
        tg_api('sendMessage', {'chat_id': chat_id, 'text': 'No route detected. Prefix with /a /b /both or a: b: both: to route.'}, timeout=15)
    # Per-peer rate tracking for outbound chat messages (keys: 'peerA'|'peerB')
    last_sent_ts = {"peerA": 0.0, "peerB": 0.0}
    last_seen = {"peerA": "", "peerB": ""}

    # Outbound mailbox baseline persistence removed; OutboxConsumer handles structured baselines

    # Delete-on-success semantics for outbound files; no persistent sent-cache needed


    def _preflight_msg(peer_key: str, msg: str, min_interval_s: float) -> tuple:
        """Return (ok, reason). Validate JSON-encodability and per-peer rate only.
        Size constraints are applied by summarization before this point.
        """
        try:
            _ = json.dumps({'m': msg})
        except Exception as e:
            return False, f'json-encode-failed: {e}'
        now = time.time(); last = float(last_sent_ts.get(peer_key, 0.0) or 0.0)
        if now - last < float(min_interval_s or 0.0):
            return False, f'rate-limited: interval<{min_interval_s}s'
        return True, ''

    def send_summary(peer: str, text: str):
        # peer in {'peerA','peerB'}
        label = "PeerA" if peer == 'peerA' else "PeerB"
        # Clean text to remove control characters, then build final message
        cleaned_text = clean_text(text)
        msg = f"[{label}]\n" + _summarize(redact(cleaned_text), max_chars, max_lines)
        ok, reason = _preflight_msg(peer, msg, float(str(cfg.get('to_user_min_interval_s') or 1.5)))
        if not ok:
            _append_ledger({'kind':'bridge-outbox-blocked','type':'to_user','peer': peer, 'reason': reason})
            return
        for chat_id in allow:
            tg_api('sendMessage', {
                'chat_id': chat_id,
                'text': msg,
                'disable_web_page_preview': True
            }, timeout=15)
        _append_log(outlog, f"[outbound] sent {label} {len(msg)} chars")
        _append_ledger({"kind":"bridge-outbound","to":"telegram","peer":label.lower(),"chars":len(msg)})
        last_sent_ts[peer] = time.time()

    def send_peer_summary(sender_peer: str, text: str):
        # sender_peer in {'peerA','peerB'}. Build summarized message then preflight
        label = "PeerA→PeerB" if sender_peer == 'peerA' else "PeerB→PeerA"
        cleaned_text = clean_text(text)
        msg = f"[{label}]\n" + _summarize(redact(cleaned_text), peer_max_chars, peer_max_lines)
        ok, reason = _preflight_msg(sender_peer, msg, float(str(cfg.get('peer_summary_min_interval_s') or 1.5)))
        if not ok:
            _append_ledger({'kind':'bridge-outbox-blocked','type':'to_peer_summary','peer': sender_peer, 'reason': reason})
            return
        for chat_id in allow:
            tg_api('sendMessage', {
                'chat_id': chat_id,
                'text': msg,
                'disable_web_page_preview': True
            }, timeout=15)
        _append_log(outlog, f"[outbound] sent {label} {len(msg)} chars")
        _append_ledger({"kind":"bridge-outbound","to":"telegram","peer":"to_peer","chars":len(msg)})
        last_sent_ts['peerA' if sender_peer=='peerA' else 'peerB'] = time.time()

    def watch_outputs():
        outbound_conf = cfg.get('outbound') or {}
        # RFD watcher state with persistence to avoid resending on restart
        def _rfd_seen_path() -> Path:
            return HOME/"state"/"rfd-seen.json"
        def _load_rfd_seen() -> set:
            p = _rfd_seen_path()
            try:
                if p.exists():
                    obj = json.loads(p.read_text(encoding='utf-8'))
                    arr = obj.get('ids') or []
                    return set(str(x) for x in arr)
            except Exception:
                pass
            return set()
        def _save_rfd_seen(ids: set):
            p = _rfd_seen_path(); p.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Trim to last 2000 items to bound file size
                arr = list(ids)[-2000:]
                p.write_text(json.dumps({'ids': arr}, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception:
                pass

        def watch_ledger_for_rfd():
            ledger = HOME/"state"/"ledger.jsonl"
            seen_rfd_ids = _load_rfd_seen()
            baseline_done = False
            window = 1000  # scan recent lines window
            while True:
                try:
                    if ledger.exists():
                        lines = ledger.read_text(encoding='utf-8').splitlines()[-window:]
                        changed = False
                        for line in lines:
                            try:
                                ev = json.loads(line)
                            except Exception:
                                continue
                            kind = str(ev.get('kind') or '').lower()
                            if kind != 'rfd':
                                continue
                            rid = str(ev.get('id') or '')
                            if not rid:
                                import hashlib
                                rid = hashlib.sha1(line.encode('utf-8')).hexdigest()[:8]
                            if rid in seen_rfd_ids:
                                continue
                            # On first run, baseline: mark existing RFDs as seen but do not send
                            if not baseline_done:
                                seen_rfd_ids.add(rid); changed = True
                                continue
                            # New RFD → send interactive card once
                            text = ev.get('title') or ev.get('summary') or f"RFD {rid}"
                            markup = {
                                'inline_keyboard': [[
                                    {'text': 'Approve', 'callback_data': f'rfd:{rid}:approve'},
                                    {'text': 'Reject', 'callback_data': f'rfd:{rid}:reject'},
                                    {'text': 'Ask More', 'callback_data': f'rfd:{rid}:askmore'},
                                ]]
                            }
                            for chat_id in allow:
                                tg_api('sendMessage', {
                                    'chat_id': chat_id,
                                    'text': f"[RFD] {text}",
                                    'reply_markup': json.dumps(markup)
                                }, timeout=15)
                            _append_ledger({'kind':'bridge-rfd-card','id':rid})
                            seen_rfd_ids.add(rid); changed = True
                        if changed:
                            _save_rfd_seen(seen_rfd_ids)
                        baseline_done = True
                except Exception:
                    pass
                time.sleep(2.0)

        threading.Thread(target=watch_ledger_for_rfd, daemon=True).start()

        # Outbox consumer (unified across bridges)
        try:
            from adapters.outbox_consumer import OutboxConsumer  # type: ignore
            # Respect reset_on_start from config (baseline|clear)
            reset = str((cfg.get('outbound') or {}).get('reset_on_start') or cfg.get('reset_on_start') or 'baseline')
            oc = OutboxConsumer(HOME, seen_name='telegram', reset_on_start=reset)
            def _on_to_user(ev: Dict[str, Any]):
                peer = str(ev.get('peer') or '')
                original_text = str(ev.get('text') or '')
                if not peer or not original_text:
                    return
                p = 'peerA' if peer.lower() in ('peera','peera') or peer=='PeerA' else 'peerB'
                
                # Send text message first (no translation wrapper)
                send_summary(p, original_text)
                
                # Auto voice reply if enabled and user used voice input
                _append_log(outlog, f"[auto-voice-check] voice_enabled={voice_enabled}, tts_enabled={tts_enabled}, auto_voice_enabled={auto_voice_enabled}, auto_voice_for_user={auto_voice_for_user}")
                _append_log(outlog, f"[auto-voice-check] text_len={len(original_text)}, min_len={auto_voice_min_len}, max_len={auto_voice_max_len}")
                if (voice_enabled and tts_enabled and auto_voice_enabled and auto_voice_for_user and 
                    len(original_text) >= auto_voice_min_len):  # Remove max length check, we'll truncate if needed
                    try:
                        # Check if any user in allowed chats used voice input recently
                        rt = load_runtime()
                        user_input_methods = rt.get('user_input_methods', {})
                        should_send_voice = False
                        
                        for chat_id in allow:
                            if user_input_methods.get(str(chat_id)) == 'voice':
                                should_send_voice = True
                                break
                        
                        if should_send_voice:
                            # Determine language to use based on user's detected language
                            user_languages = rt.get('user_languages', {})
                            reply_lang = tts_default_lang
                            
                            # Use the language from the chat that used voice input
                            for chat_id in allow:
                                if user_input_methods.get(str(chat_id)) == 'voice':
                                    detected_lang = user_languages.get(str(chat_id))
                                    if detected_lang:
                                        reply_lang = detected_lang
                                        break
                            
                            # Generate voice file
                            timestamp = int(time.time())
                            voice_filename = f"auto_voice_{peer}_{timestamp}.mp3"
                            voice_path = outbound_dir / voice_filename
                            outbound_dir.mkdir(parents=True, exist_ok=True)
                            
                            # Convert text to voice (limit text length for voice and clean control characters)
                            cleaned_text = clean_text(original_text)
                            if len(cleaned_text) > auto_voice_max_len:
                                voice_text = cleaned_text[:auto_voice_max_len]
                                _append_log(outlog, f"[auto-voice] text truncated from {len(cleaned_text)} to {len(voice_text)} chars")
                            else:
                                voice_text = cleaned_text
                            _append_log(outlog, f"[auto-voice] generating for {peer} in {reply_lang}, text_len={len(voice_text)}")
                            if _convert_text_to_voice(voice_text, voice_path, reply_lang):
                                # Create route file to send to telegram
                                route_file = voice_path.with_suffix('.route')
                                route_file.write_text('telegram', encoding='utf-8')
                                
                                # Create sendas file to specify it should be sent as voice
                                sendas_file = voice_path.with_suffix('.mp3.sendas')
                                sendas_file.write_text('voice', encoding='utf-8')
                                
                                _append_log(outlog, f"[auto-voice] generated for {peer} in {reply_lang}: '{voice_text[:50]}...'")
                            else:
                                _append_log(outlog, f"[auto-voice-error] failed to generate voice for {peer}")
                        else:
                            _append_log(outlog, f"[auto-voice] skipped for {peer} - user used text input")
                    except Exception as e:
                        _append_log(outlog, f"[auto-voice-error] {peer}: {e}")
            def _on_to_peer_summary(ev: Dict[str, Any]):
                fromp = str(ev.get('from') or '')
                text = str(ev.get('text') or '')
                if not fromp or not text:
                    return
                sp = 'peerA' if fromp.lower() in ('peera','peera') or fromp=='PeerA' else 'peerB'
                eff_show = bool(load_runtime().get('show_peer_messages', show_peers))
                if eff_show:
                    send_peer_summary(sp, text)
            threading.Thread(target=lambda: oc.loop(_on_to_user, _on_to_peer_summary), daemon=True).start()
        except Exception as e:
            _append_ledger({'kind':'error','where':'telegram.outbox_consumer','error': str(e)})
            # Fail fast: missing OutboxConsumer is a code/config error
            raise
        # Outbound files watcher state
        # Track attempts within this run only (avoid rapid duplicates if filesystem timestamps don't change)
        sent_files: Dict[str, float] = {}
        def _is_image(path: Path) -> bool:
            return path.suffix.lower() in ('.jpg','.jpeg','.png','.gif','.webp')
        def _send_file(peer: str, fp: Path, caption: str) -> bool:
            cap = f"[{ 'PeerA' if peer=='peerA' else 'PeerB' }]\n" + _summarize(redact(caption or ''), max_chars, max_lines)
            # Choose send method: sidecar override > dir/ext heuristic
            if _is_image(fp) or fp.parent.name == 'photos':
                method = 'sendPhoto'
            elif fp.suffix.lower() in ['.ogg', '.mp3', '.wav', '.m4a'] or 'voice' in fp.name.lower():
                method = 'sendVoice'
            else:
                method = 'sendDocument'
            any_fail = False
            try:
                sidecars = [fp.with_suffix(fp.suffix + '.sendas'), fp.with_name(fp.name + '.sendas')]
                for sc in sidecars:
                    if sc.exists():
                        try:
                            m = (sc.read_text(encoding='utf-8').strip() or '').lower()
                            if m == 'photo':
                                method = 'sendPhoto'
                            elif m == 'document':
                                method = 'sendDocument'
                            elif m == 'voice':
                                method = 'sendVoice'
                        except Exception:
                            pass
                        break
            except Exception:
                pass
            for chat_id in allow:
                try:
                    with open(fp, 'rb') as f:
                        data = f.read()
                    # Use multipart/form-data via urllib is complex; rely on Telegram auto-download for MVP: send link not possible.
                    # For simplicity in MVP, fall back to sendDocument by URL is not allowed; so we will skip if too large to read.
                    # Here we implement minimal upload using `urllib.request` with manual boundary.
                    boundary = f"----cccc{int(time.time()*1000)}"
                    def _multipart(fields, files):
                        crlf = "\r\n"; lines=[]
                        for k,v in fields.items():
                            lines.append(f"--{boundary}")
                            lines.append(f"Content-Disposition: form-data; name=\"{k}\"")
                            lines.append("")
                            lines.append(str(v))
                        for k, (filename, content, mime) in files.items():
                            lines.append(f"--{boundary}")
                            lines.append(f"Content-Disposition: form-data; name=\"{k}\"; filename=\"{filename}\"")
                            lines.append(f"Content-Type: {mime}")
                            lines.append("")
                            lines.append(content)
                        lines.append(f"--{boundary}--")
                        body = b""
                        for part in lines:
                            if isinstance(part, bytes):
                                body += part + b"\r\n"
                            else:
                                body += part.encode('utf-8') + b"\r\n"
                        return body, boundary
                    api_url = f"https://api.telegram.org/bot{token}/{method}"
                    fields = { 'chat_id': chat_id, 'caption': cap }
                    import mimetypes
                    mt = mimetypes.guess_type(fp.name)[0] or ''
                    if method=='sendPhoto':
                        mime = mt if mt.startswith('image/') else 'image/jpeg'
                        field_name = 'photo'
                    elif method=='sendVoice':
                        mime = 'audio/ogg' if fp.suffix.lower() == '.ogg' else (mt or 'audio/mpeg')
                        field_name = 'voice'
                    else:
                        mime = mt or 'application/octet-stream'
                        field_name = 'document'
                    files = { field_name: (fp.name, data, mime) }
                    body, bnd = _multipart(fields, files)
                    req = urllib.request.Request(api_url, data=body, method='POST')
                    req.add_header('Content-Type', f'multipart/form-data; boundary={bnd}')
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        _ = resp.read()
                except Exception as e:
                    any_fail = True
                    try:
                        import urllib.error as _ue
                        if isinstance(e, _ue.HTTPError):
                            try:
                                detail = e.read().decode('utf-8','ignore')
                            except Exception:
                                detail = ''
                            _append_log(outlog, f"[error] outbound-file send {fp}: {e} {detail[:200]}")
                        else:
                            _append_log(outlog, f"[error] outbound-file send {fp}: {e}")
                    except Exception:
                        _append_log(outlog, f"[error] outbound-file send {fp}: {e}")
            _append_log(outlog, f"[outbound-file] {fp}")
            _append_ledger({"kind":"bridge-file-outbound","peer":peer,"path":str(fp)})
            # Delete file and sidecars only when all sends succeeded
            if not any_fail:
                # Minimal file ACK: write a sidecar with sent metadata before deleting the payload
                try:
                    import hashlib as _hl, datetime as _dt
                    sha = _hl.sha256(data).hexdigest() if 'data' in locals() else ''
                    sent_meta = {
                        "sent": True,
                        "ts": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(),
                        "bytes": len(data) if 'data' in locals() else None,
                        "sha256": sha,
                        "method": method,
                        "peer": peer,
                        "name": fp.name,
                    }
                    (fp.with_suffix(fp.suffix + '.sent.json')).write_text(json.dumps(sent_meta, ensure_ascii=False, indent=2), encoding='utf-8')
                    _append_ledger({"kind":"bridge-file-sent","peer":peer,"path":str(fp),"bytes":sent_meta.get('bytes'),"sha256":sha})
                except Exception as _e:
                    _append_log(outlog, f"[warn] failed to write sent sidecar for {fp}: {_e}")
                try:
                    for side in (
                        fp.with_suffix(fp.suffix + '.caption.txt'),
                        fp.with_suffix(fp.suffix + '.sendas'),
                        fp.with_name(fp.name + '.sendas'),
                        fp.with_suffix(fp.suffix + '.meta.json'),
                    ):
                        try:
                            if side.exists():
                                side.unlink()
                        except Exception:
                            pass
                    fp.unlink()
                except Exception as de:
                    _append_log(outlog, f"[warn] failed to delete outbound file {fp}: {de}")
                return True
            return False
        
        # Optional reset on start: baseline|archive|clear
        # Prefer nested 'outbound.reset_on_start', fallback to top-level 'reset_on_start'. Default 'baseline'.
        reset_mode = str((outbound_conf.get('reset_on_start') or cfg.get('reset_on_start') or 'baseline')).lower()
        try:
            if reset_mode in ('archive','clear'):
                arch = HOME/'state'/'outbound-archive'; arch.mkdir(parents=True, exist_ok=True)
                # Clear/archive outbound files to avoid blasting residual uploads (single-level per peer)
                for peer in ('peerA','peerB'):
                    d = outbound_dir/peer
                    if not d.exists():
                        continue
                    for fp in sorted(d.glob('*')):
                        if fp.is_dir():
                            continue
                            nm = str(fp.name).lower()
                            if nm.endswith('.caption.txt') or nm.endswith('.sendas') or nm.endswith('.meta.json'):
                                continue
                            if reset_mode == 'archive':
                                import time as _t
                                ts = _t.strftime('%Y%m%d-%H%M%S')
                                dest_dir = arch/peer
                                dest_dir.mkdir(parents=True, exist_ok=True)
                                try:
                                    (dest_dir/f"{fp.name}-{ts}").write_bytes(fp.read_bytes())
                                except Exception:
                                    pass
                            try:
                                fp.unlink()
                            except Exception:
                                pass
                # After clearing, outbound directory is empty; no extra bookkeeping needed
        except Exception:
            pass
        # No mailbox baseline: OutboxConsumer ensures baseline for structured events

        def _read_caption(fp: Path) -> str:
            cap_fp = fp.with_suffix(fp.suffix + '.caption.txt')
            if cap_fp.exists():
                try:
                    return cap_fp.read_text(encoding='utf-8').strip()
                except Exception:
                    return ''
            return ''
        def _detect_route_from_caption(cap: str) -> Tuple[str, str]:
            t = (cap or '').lstrip()
            m = re.match(r"^(a:|b:|both:)\s*", t, re.I)
            if m:
                tag = m.group(1).lower(); body = t[m.end():]
                return ({'a:':'peerA','b:':'peerB','both:':'both'}[tag], body)
            return ('', cap)
        def _route_sidecar(fp: Path) -> str:
            for sc in (fp.with_suffix(fp.suffix + '.route'), fp.with_name(fp.name + '.route')):
                if sc.exists():
                    try:
                        val = (sc.read_text(encoding='utf-8').strip() or '').lower()
                        if val in ('a','peera','peera','peera'.lower(), 'peera'):
                            return 'peerA'
                        if val in ('b','peerb','peerb','peerb'.lower(), 'peerb'):
                            return 'peerB'
                        if val in ('both','all','ab','a+b'):
                            return 'both'
                    except Exception:
                        pass
            return ''
        def _iter_targets():
            # Flat-only scheme: scan outbound root directory for files
            if outbound_dir.exists():
                for f in sorted(outbound_dir.glob('*')):
                    if f.is_file():
                        yield f
        while True:
            now = time.time()
            # Peer↔Peer summaries now come from outbox (no file polling)
            # Outbound files (flat-only directory)
            try:
                for fp in _iter_targets():
                    if fp.is_dir():
                        continue
                    name=str(fp.name).lower()
                    if name.endswith('.caption.txt') or name.endswith('.sendas') or name.endswith('.meta.json') or name.endswith('.sent.json'):
                        continue
                    cap = _read_caption(fp)
                    route = _route_sidecar(fp)
                    if not route:
                        route, cap = _detect_route_from_caption(cap)
                    if not route:
                        route = 'both'
                    peer = 'peerA' if route=='peerA' else ('peerB' if route=='peerB' else 'peerA')
                    # Send and delete on success; on failure, keep file for retry
                    _send_file(peer, fp, cap)
            except Exception as e:
                _append_log(outlog, f"[error] watch_outbound: {e}")
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
            # Handle inline button callbacks (e.g., RFD approvals)
            if u.get('callback_query'):
                cq = u['callback_query']
                data = str(cq.get('data') or '')
                cchat = ((cq.get('message') or {}).get('chat') or {})
                cchat_id = int(cchat.get('id', 0) or 0)
                try:
                    if data.startswith('rfd:'):
                        parts = data.split(':', 2)
                        rid = parts[1] if len(parts) > 1 else ''
                        decision = parts[2] if len(parts) > 2 else ''
                        _append_ledger({'kind':'decision','rfd_id':rid,'decision':decision,'chat':cchat_id})
                        tg_api('answerCallbackQuery', {'callback_query_id': cq.get('id'), 'text': f'Decision recorded: {decision}'}, timeout=10)
                        tg_api('sendMessage', {'chat_id': cchat_id, 'text': f"[RFD] {rid} → {decision}"}, timeout=15)
                    else:
                        tg_api('answerCallbackQuery', {'callback_query_id': cq.get('id')}, timeout=10)
                except Exception:
                    pass
                continue
            msg = u.get('message') or u.get('edited_message') or {}
            chat = (msg.get('chat') or {})
            chat_id = int(chat.get('id', 0) or 0)
            chat_type = str(chat.get('type') or '')
            if chat_id not in allow:
                text = (msg.get('text') or '').strip()
                if policy == 'open' and is_cmd(text, 'subscribe'):
                    # Auto-register with cap
                    cur = set(load_subs())
                    if chat_id in cur:
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Already subscribed (allowlist)'}, timeout=15)
                    elif len(cur) >= max_auto:
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Subscription limit reached; contact admin.'}, timeout=15)
                    else:
                        cur.add(chat_id); save_subs(sorted(cur)); allow.add(chat_id)
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Subscribed. This chat will receive summaries. Send /unsubscribe to leave.'}, timeout=15)
                        _append_log(outlog, f"[subscribe] chat={chat_id}")
                        _append_ledger({"kind":"bridge-subscribe","chat":chat_id})
                    continue
                if policy == 'open' and is_cmd(text, 'unsubscribe'):
                    # Allow unsub from non-allowed (no-op) for idempotence
                    cur = set(load_subs()); removed = chat_id in cur
                    if removed:
                        cur.discard(chat_id); save_subs(sorted(cur))
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Unsubscribed' if removed else 'Not subscribed'}, timeout=15)
                    _append_log(outlog, f"[unsubscribe] chat={chat_id}")
                    _append_ledger({"kind":"bridge-unsubscribe","chat":chat_id})
                    continue
                # Discovery or closed policy: log and optionally reply to whoami
                _append_log(outlog, f"[drop] message from not-allowed chat={chat_id}")
                _append_ledger({"kind":"bridge-drop","reason":"not-allowed","chat":chat_id})
                if discover and is_cmd(text, 'whoami'):
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': f"chat_id={chat_id} (not allowed; send /subscribe to opt-in)"}, timeout=15)
                elif policy == 'open':
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Not subscribed. Send /subscribe to opt-in, /unsubscribe to leave.'}, timeout=15)
                continue
            text = (msg.get('text') or '').strip()
            caption = (msg.get('caption') or '').strip()
            is_dm = (chat_type == 'private')
            route_source = text or caption
            # Enforce mention in group if configured
            if (not is_dm) and require_mention:
                ents = msg.get('entities') or []
                mentions = any(e.get('type')=='mention' for e in ents)
                if not mentions:
                    _maybe_hint(chat_id, int((msg.get('from') or {}).get('id', 0) or 0))
                    continue
            # Enforce explicit routing for groups
            has_explicit = bool(re.match(r"^(?:/(?:a|b|both)(?:@\S+)?|(?:a[:：]|b[:：]|both[:：]))", (route_source or '').strip(), re.I))
            dr = dm_route_default if is_dm else default_route
            if (not is_dm) and require_explicit and not has_explicit and not (msg.get('document') or msg.get('photo')):
                _maybe_hint(chat_id, int((msg.get('from') or {}).get('id', 0) or 0))
                continue
            # Reply routing: if message contains only a route and replies to another message,
            # use the replied message's content/files.
            rmsg = msg.get('reply_to_message') or {}
            if rmsg and has_explicit and not (text.strip().split(maxsplit=1)[1:] if text else []) and not caption:
                rtext = (rmsg.get('text') or rmsg.get('caption') or '').strip()
                if rtext:
                    route_source = rtext
                if files_enabled and (rmsg.get('document') or rmsg.get('photo')):
                    metas = []
                    try:
                        if rmsg.get('document'):
                            doc = rmsg['document']
                            fn = doc.get('file_name') or 'document.bin'
                            mime = doc.get('mime_type') or 'application/octet-stream'
                            if _mime_allowed(mime):
                                midf = _mid()
                                path, meta = _save_file_from_telegram(doc.get('file_id'), fn, chat_id, midf)
                                meta.update({'mime': mime, 'caption': rtext, 'mid': midf}); metas.append(meta)
                        if rmsg.get('photo'):
                            ph = sorted(rmsg['photo'], key=lambda p: int(p.get('file_size') or 0))[-1]
                            fn = 'photo.jpg'; mime = 'image/jpeg'
                            midf = _mid(); path, meta = _save_file_from_telegram(ph.get('file_id'), fn, chat_id, midf)
                            meta.update({'mime': mime, 'caption': rtext, 'mid': midf}); metas.append(meta)
                    except Exception as e:
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': f'Failed to receive quoted file: {e}'}, timeout=15)
                        _append_log(outlog, f"[error] inbound-file-reply: {e}")
                        metas = []
                    if metas:
                        routes, _ = _route_from_text(text or '/both', dr)
                        lines = ["<FROM_USER>", f"[MID: {_mid()}]"]
                        if rtext:
                            lines.append(f"Quoted: {redact(rtext)[:200]}")
                        for mta in metas:
                            rel = os.path.relpath(mta['path'], start=ROOT)
                            lines.append(f"File: {rel}")
                            lines.append(f"SHA256: {mta['sha256']}  Size: {mta['bytes']}  MIME: {mta['mime']}")
                            try:
                                side = Path(mta['path']).with_suffix(Path(mta['path']).suffix + '.meta.json')
                                side.write_text(json.dumps({
                                    'chat_id': chat_id,
                                    'path': rel,
                                    'sha256': mta['sha256'],
                                    'bytes': mta['bytes'],
                                    'mime': mta['mime'],
                                    'caption': rtext,
                                    'mid': mta.get('mid'),
                                    'ts': time.strftime('%Y-%m-%d %H:%M:%S')
                                }, ensure_ascii=False, indent=2), encoding='utf-8')
                            except Exception:
                                pass
                        # Append inbound index (reply case)
                        try:
                            idx = HOME/"state"/"inbound-index.jsonl"; idx.parent.mkdir(parents=True, exist_ok=True)
                            for mta in metas:
                                rec = {
                                    'ts': int(time.time()), 'path': mta['path'], 'platform': 'telegram',
                                    'routes': routes, 'mid': mta.get('mid'), 'mime': mta['mime'], 'bytes': mta['bytes'], 'sha256': mta['sha256']
                                }
                                with idx.open('a', encoding='utf-8') as f:
                                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        except Exception:
                            pass
                        lines.append("</FROM_USER>")
                        payload = "\n".join(lines) + "\n"
                        _deliver_inbound(HOME, routes, payload, _mid())
                        _append_log(outlog, f"[inbound-file-reply] routes={routes} files={len(metas)} chat={chat_id}")
                        _append_ledger({'kind': 'bridge-file-inbound', 'chat': chat_id, 'routes': routes,
                                        'files': [{'path': m['path'], 'sha256': m['sha256']} for m in metas]})
                        continue
# Inbound voice messages
            if voice_enabled and stt_enabled and msg.get('voice'):
                if (not is_dm) and require_explicit and not has_explicit:
                    _maybe_hint(chat_id, int((msg.get('from') or {}).get('id', 0) or 0))
                    continue
                try:
                    voice = msg['voice']
                    file_id = voice.get('file_id')
                    duration = voice.get('duration', 0)
                    mime_type = voice.get('mime_type', 'audio/ogg')
                    
                    # Save voice file
                    midf = _mid()
                    fn = f"voice_{midf}.ogg"
                    path, meta = _save_file_from_telegram(file_id, fn, chat_id, midf)
                    
                    # Convert voice to text with language detection
                    detected_text, detected_lang = _convert_voice_to_text(path, stt_default_lang)
                    
                    if detected_text:
                        # Track that user used voice input and the detected language
                        user_input_method[chat_id] = 'voice'
                        # Save to runtime for outbound callbacks
                        rt = load_runtime()
                        rt.setdefault('user_input_methods', {})[str(chat_id)] = 'voice'
                        rt.setdefault('user_languages', {})[str(chat_id)] = detected_lang
                        save_runtime(rt)
                        
                        # Build inbox payload with voice transcription and language instruction
                        routes, _ = _route_from_text(route_source or '', dr)
                        
                        # Add language instruction for AI
                        lang_instruction = ""
                        if detected_lang == 'zh-CN':
                            lang_instruction = "请用中文回复。"
                        elif detected_lang == 'en-US':
                            lang_instruction = "Please reply in English."
                        elif detected_lang == 'ja-JP':
                            lang_instruction = "日本語で返事してください。"
                        
                        lines = ["<FROM_USER>"]
                        lines.append(f"[MID: {_mid()}]")
                        lines.append(f"Voice message (duration: {duration}s, language: {detected_lang}):")
                        if lang_instruction:
                            lines.append(f"[Language instruction: {lang_instruction}]")
                        lines.append(detected_text)
                        if caption:
                            lines.append(f"Caption: {caption}")
                        lines.append("</FROM_USER>")
                        payload = "\n".join(lines) + "\n"
                        _deliver_inbound(HOME, routes, payload, _mid())
                        _append_log(outlog, f"[inbound-voice] routes={routes} lang={detected_lang} text='{detected_text[:50]}...' chat={chat_id}")
                        _append_ledger({"kind":"bridge-voice-inbound","chat":chat_id,"routes":routes,"text":detected_text,"language":detected_lang,"duration":duration})
                    else:
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Sorry, I could not understand the voice message.'}, timeout=15)
                        _append_log(outlog, f"[voice-stt-failed] chat={chat_id}")
                        
                except Exception as e:
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': f'Failed to process voice message: {e}'}, timeout=15)
                    _append_log(outlog, f"[error] inbound-voice: {e}")
                continue

# Inbound files
            if files_enabled and (msg.get('document') or msg.get('photo')):
                if (not is_dm) and require_explicit and not has_explicit:
                    _maybe_hint(chat_id, int((msg.get('from') or {}).get('id', 0) or 0))
                    continue
                metas = []
                try:
                    if msg.get('document'):
                        doc = msg['document']
                        fn = doc.get('file_name') or 'document.bin'
                        mime = doc.get('mime_type') or 'application/octet-stream'
                        if not _mime_allowed(mime):
                            tg_api('sendMessage', {'chat_id': chat_id, 'text': f'File type not allowed: {mime}'}, timeout=15)
                            continue
                        midf = _mid()
                        path, meta = _save_file_from_telegram(doc.get('file_id'), fn, chat_id, midf)
                        meta.update({'mime': mime, 'caption': caption, 'mid': midf})
                        metas.append(meta)
                    if msg.get('photo'):
                        ph = sorted(msg['photo'], key=lambda p: int(p.get('file_size') or 0))[-1]
                        fn = 'photo.jpg'
                        mime = 'image/jpeg'
                        midf = _mid()
                        path, meta = _save_file_from_telegram(ph.get('file_id'), fn, chat_id, midf)
                        meta.update({'mime': mime, 'caption': caption, 'mid': midf})
                        metas.append(meta)
                    # Build inbox payload
                    routes, _ = _route_from_text(route_source or '', dr)
                    lines = ["<FROM_USER>"]
                    lines.append(f"[MID: {_mid()}]")
                    if caption:
                        lines.append(f"Caption: {redact(caption)}")
                    for mta in metas:
                        rel = os.path.relpath(mta['path'], start=ROOT)
                        lines.append(f"File: {rel}")
                        lines.append(f"SHA256: {mta['sha256']}  Size: {mta['bytes']}  MIME: {mta['mime']}")
                        # write sidecar meta json
                        try:
                            side = Path(mta['path']).with_suffix(Path(mta['path']).suffix + '.meta.json')
                            side.write_text(json.dumps({
                                'chat_id': chat_id,
                                'path': rel,
                                'sha256': mta['sha256'],
                                'bytes': mta['bytes'],
                                'mime': mta['mime'],
                                'caption': caption,
                                'mid': mta.get('mid'),
                                'ts': time.strftime('%Y-%m-%d %H:%M:%S')
                            }, ensure_ascii=False, indent=2), encoding='utf-8')
                        except Exception:
                            pass
                    # Append inbound index (normal case)
                    try:
                        idx = HOME/"state"/"inbound-index.jsonl"; idx.parent.mkdir(parents=True, exist_ok=True)
                        for mta in metas:
                            rec = {
                                'ts': int(time.time()), 'path': mta['path'], 'platform': 'telegram',
                                'routes': routes, 'mid': mta.get('mid'), 'mime': mta['mime'], 'bytes': mta['bytes'], 'sha256': mta['sha256']
                            }
                            with idx.open('a', encoding='utf-8') as f:
                                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                    lines.append("</FROM_USER>")
                    payload = "\n".join(lines) + "\n"
                    _deliver_inbound(HOME, routes, payload, _mid())
                    _append_log(outlog, f"[inbound-file] routes={routes} files={len(metas)} chat={chat_id}")
                    _append_ledger({"kind":"bridge-file-inbound","chat":chat_id,"routes":routes,"files":[{"path":m['path'],"sha256":m['sha256'],"bytes":m['bytes'],"mime":m['mime']} for m in metas]})
                    continue
                except Exception as e:
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': f'Failed to receive file: {e}'}, timeout=15)
                    _append_log(outlog, f"[error] inbound-file: {e}")
                    continue
            # minimal commands
            if is_cmd(text, 'subscribe'):
                if policy == 'open':
                    cur = set(load_subs()); added = chat_id not in cur
                    if added:
                        cur.add(chat_id); save_subs(sorted(cur)); allow.add(chat_id)
                    tg_api('sendMessage', {
                        'chat_id': chat_id,
                        'text': 'Subscribed. This chat will receive summaries. Send /unsubscribe to leave.' if added else 'Already subscribed (allowlist)'
                    }, timeout=15)
                    _append_log(outlog, f"[subscribe] chat={chat_id}{' (noop)' if not added else ''}")
                    _append_ledger({"kind":"bridge-subscribe","chat":chat_id,"noop": (not added)})
                else:
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Self-subscribe disabled; contact admin.'}, timeout=15)
                continue
            if is_cmd(text, 'status'):
                st_path = HOME/"state"/"status.json"
                try:
                    st = json.loads(st_path.read_text(encoding='utf-8')) if st_path.exists() else {}
                except Exception:
                    st = {}
                phase = st.get('phase'); paused = st.get('paused'); leader = st.get('leader')
                counts = st.get('mailbox_counts') or {}
                a = counts.get('peerA') or {}; b = counts.get('peerB') or {}
                por = st.get('por') or {}
                por_goal = por.get('goal') or '-' 
                por_next = por.get('next_step') or '-'
                por_note = por.get('last_note') or ''
                coach = st.get('coach') or {}
                lines = [
                    f"Phase: {phase}  Paused: {paused}",
                    f"Leader: {leader}",
                    f"peerA to_user:{a.get('to_user',0)} to_peer:{a.get('to_peer',0)} patch:{a.get('patch',0)}",
                    f"peerB to_user:{b.get('to_user',0)} to_peer:{b.get('to_peer',0)} patch:{b.get('patch',0)}",
                    f"POR.goal: {por_goal}",
                    f"POR.next_step: {por_next}",
                ]
                if por_note:
                    lines.append(f"POR.note: {por_note}")
                if coach:
                    lines.append(f"Coach.mode: {coach.get('mode','off')} (cmd: {(coach.get('command') or '-')})")
                    if coach.get('last_reason'):
                        lines.append(f"Coach.last: {coach.get('last_reason')}")
                tg_api('sendMessage', {'chat_id': chat_id, 'text': "\n".join(lines)}, timeout=15)
                continue
            if is_cmd(text, 'queue'):
                q_path = HOME/"state"/"queue.json"; qA=qB=0; inflA=inflB=False
                try:
                    q = json.loads(q_path.read_text(encoding='utf-8')) if q_path.exists() else {}
                    qA = int(q.get('peerA') or 0); qB = int(q.get('peerB') or 0)
                    infl = q.get('inflight') or {}; inflA = bool(infl.get('peerA')); inflB = bool(infl.get('peerB'))
                except Exception:
                    pass
                tg_api('sendMessage', {'chat_id': chat_id, 'text': f"Queue: PeerA={qA} inflight={inflA} | PeerB={qB} inflight={inflB}"}, timeout=15)
                continue
            if is_cmd(text, 'locks'):
                l_path = HOME/"state"/"locks.json"
                try:
                    l = json.loads(l_path.read_text(encoding='utf-8')) if l_path.exists() else {}
                    locks = l.get('inbox_seq_locks') or []
                    infl = l.get('inflight') or {}
                    lines=[
                        f"InboxSeqLocks: {', '.join(locks) if locks else 'none'}",
                        f"Inflight: PeerA={bool(infl.get('peerA'))} PeerB={bool(infl.get('peerB'))}",
                    ]
                except Exception:
                    lines=["No locks info"]
                tg_api('sendMessage', {'chat_id': chat_id, 'text': "\n".join(lines)}, timeout=15)
                continue
            if is_cmd(text, 'whoami'):
                tg_api('sendMessage', {'chat_id': chat_id, 'text': f"chat_id={chat_id}"}, timeout=15)
                _append_log(outlog, f"[meta] whoami chat={chat_id}")
                continue
            # /voice <text> - convert text to voice and send as audio
            if voice_enabled and tts_enabled and re.match(r"^/voice(?:@\S+)?\s+.+", text.strip(), re.I):
                voice_text = re.sub(r"^/voice(?:@\S+)?\s+", "", text.strip(), flags=re.I)
                if voice_text:
                    try:
                        # Create voice file
                        timestamp = int(time.time())
                        voice_filename = f"voice_response_{timestamp}.wav"
                        voice_path = outbound_dir / voice_filename
                        outbound_dir.mkdir(parents=True, exist_ok=True)
                        
                        # Convert text to voice (clean control characters first)
                        clean_voice_text = clean_text(voice_text)
                        if _convert_text_to_voice(clean_voice_text, voice_path, tts_default_lang):
                            # Create route file to send to current chat
                            route_file = voice_path.with_suffix('.route')
                            route_file.write_text('telegram', encoding='utf-8')
                            
                            # Create sendas file to specify it should be sent as voice
                            sendas_file = voice_path.with_suffix('.wav.sendas')
                            sendas_file.write_text('voice', encoding='utf-8')
                            
                            tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Voice message generated and will be sent shortly.'}, timeout=15)
                            _append_log(outlog, f"[voice-command] text='{voice_text[:50]}...' chat={chat_id}")
                        else:
                            tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Failed to generate voice message.'}, timeout=15)
                    except Exception as e:
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': f'Voice command error: {e}'}, timeout=15)
                        _append_log(outlog, f"[error] voice-command: {e}")
                continue
            if is_cmd(text, 'help'):
                help_txt = (
                    "Usage: a:/b:/both: or /a /b /both to route to PeerA/PeerB/both; /whoami shows chat_id; /status shows status; /queue shows queue; /locks shows locks; "
                    "/subscribe opt-in (if enabled); /unsubscribe opt-out; /showpeers on|off toggle Peer↔Peer summary; /files [in|out] [N] list recent files; /file N view; /rfd list|show <id>."
                )
                if voice_enabled:
                    help_txt += " /voice <text> convert text to voice message."
                tg_api('sendMessage', {'chat_id': chat_id, 'text': help_txt}, timeout=15)
                continue
            # /rfd list|show <id>
            if re.match(r"^/rfd(?:@\S+)?\b", text.strip(), re.I):
                try:
                    cmd = text.strip().split()
                    sub = cmd[1].lower() if len(cmd) > 1 else 'list'
                except Exception:
                    sub = 'list'
                ledger = HOME/"state"/"ledger.jsonl"
                entries = []
                try:
                    lines = ledger.read_text(encoding='utf-8').splitlines()[-500:]
                    for ln in lines:
                        try:
                            ev = json.loads(ln)
                            entries.append(ev)
                        except Exception:
                            pass
                except Exception:
                    entries = []
                if sub == 'list':
                    rfds = [e for e in entries if str(e.get('kind') or '').lower() == 'rfd'][-10:]
                    if not rfds:
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': 'No RFD entries'}, timeout=15)
                        continue
                    lines = [f"{e.get('id') or '?'} | {e.get('title') or e.get('summary') or ''}" for e in rfds]
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': "\n".join(lines)}, timeout=15)
                    continue
                if sub == 'show':
                    rid = cmd[2] if len(cmd) > 2 else ''
                    if not rid:
                        tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Usage: /rfd show <id>'}, timeout=15)
                        continue
                    # Find the RFD and latest decision
                    rfd = None; decision = None
                    for ev in entries:
                        k = str(ev.get('kind') or '').lower()
                        if k == 'rfd' and str(ev.get('id') or '') == rid:
                            rfd = ev
                        if k == 'decision' and str(ev.get('rfd_id') or '') == rid:
                            decision = ev
                    text_out = [f"RFD {rid}"]
                    if rfd:
                        text_out.append(f"title={rfd.get('title') or rfd.get('summary') or ''}")
                        text_out.append(f"ts={rfd.get('ts')}")
                    else:
                        text_out.append('not found in tail')
                    if decision:
                        text_out.append(f"decision={decision.get('decision')} by chat={decision.get('chat')} ts={decision.get('ts')}")
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': "\n".join(text_out)}, timeout=15)
                    continue
            # /files and /file
            if re.match(r"^/files(?:@\S+)?\b", text.strip(), re.I):
                m = re.match(r"^/files(?:@\S+)?\s*(in|out)?\s*(\d+)?", text.strip(), re.I)
                mode = (m.group(1).lower() if (m and m.group(1)) else 'in')
                limit = int(m.group(2)) if (m and m.group(2)) else 10
                base = inbound_dir if mode == 'in' else outbound_dir
                items = []
                for root, dirs, files in os.walk(base):
                    for fn in files:
                        if fn.endswith('.meta.json') or fn.endswith('.caption.txt'):
                            continue
                        fp = Path(root)/fn
                        try:
                            st = fp.stat()
                            items.append((st.st_mtime, fp))
                        except Exception:
                            pass
                items.sort(key=lambda x: x[0], reverse=True)
                items = items[:max(1, min(limit, 50))]
                lines=[f"Recent files ({ 'inbound' if mode=='in' else 'outbound' }, top {len(items)}):"]
                map_paths=[str(p) for _,p in items]
                # Save last listing map for /file N
                rt = load_runtime(); lm = rt.get('last_files',{})
                lm[str(chat_id)] = {'mode': mode, 'items': map_paths}
                rt['last_files']=lm; save_runtime(rt)
                for idx,(ts,fp) in enumerate(items, start=1):
                    rel = os.path.relpath(fp, start=ROOT)
                    try:
                        size = fp.stat().st_size
                    except Exception:
                        size = 0
                    lines.append(f"{idx}. {rel}  ({size} bytes)")
                tg_api('sendMessage', {'chat_id': chat_id, 'text': "\n".join(lines)}, timeout=15)
                continue
            if re.match(r"^/file(?:@\S+)?\s+\d+\b", text.strip(), re.I):
                m = re.match(r"^/file(?:@\S+)?\s+(\d+)\b", text.strip(), re.I)
                n = int(m.group(1)) if m else 0
                rt = load_runtime(); lm = (rt.get('last_files') or {}).get(str(chat_id)) or {}
                arr = lm.get('items') or []
                if n<=0 or n>len(arr):
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Invalid index. Run /files, then /file N.'}, timeout=15)
                    continue
                fp = Path(arr[n-1])
                info=[]
                rel = os.path.relpath(fp, start=ROOT)
                info.append(f"Path: {rel}")
                try:
                    st=fp.stat(); info.append(f"Size: {st.st_size} bytes  MTime: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}")
                except Exception:
                    pass
                meta_fp = fp.with_suffix(fp.suffix+'.meta.json')
                if meta_fp.exists():
                    try:
                        meta = json.loads(meta_fp.read_text(encoding='utf-8'))
                        sha = meta.get('sha256'); mime = meta.get('mime'); cap = meta.get('caption')
                        if sha: info.append(f"SHA256: {sha}")
                        if mime: info.append(f"MIME: {mime}")
                        if cap: info.append(f"Caption: {redact(cap)}")
                    except Exception:
                        pass
                tg_api('sendMessage', {'chat_id': chat_id, 'text': "\n".join(info)}, timeout=15)
                continue
            if re.match(r"^/showpeers(?:@\S+)?\s+(on|off)\b", text.strip(), re.I):
                m = re.match(r"^/showpeers(?:@\S+)?\s+(on|off)\b", text.strip(), re.I)
                val = m.group(1).lower() == 'on' if m else False
                runtime = load_runtime(); runtime['show_peer_messages'] = bool(val); save_runtime(runtime)
                show_peers = bool(val)
                tg_api('sendMessage', {'chat_id': chat_id, 'text': f"Peer↔Peer summary set to: {'ON' if val else 'OFF'} (global)"}, timeout=15)
                _append_log(outlog, f"[runtime] show_peer_messages={val}")
                continue
            if is_cmd(text, 'unsubscribe'):
                if policy == 'open':
                    cur = set(load_subs()); removed = chat_id in cur
                    if removed:
                        cur.discard(chat_id); save_subs(sorted(cur)); allow.discard(chat_id)
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Unsubscribed' if removed else 'Not subscribed'}, timeout=15)
                    _append_log(outlog, f"[unsubscribe] chat={chat_id}")
                    _append_ledger({"kind":"bridge-unsubscribe","chat":chat_id})
                else:
                    tg_api('sendMessage', {'chat_id': chat_id, 'text': 'Self-unsubscribe disabled; contact admin.'}, timeout=15)
                continue
            
            # Track that user used text input for smart voice reply
            user_input_method[chat_id] = 'text'
            # Save to runtime for outbound callbacks
            rt = load_runtime()
            rt.setdefault('user_input_methods', {})[str(chat_id)] = 'text'
            save_runtime(rt)
            
            routes, body = _route_from_text(route_source, dr)
            mid = _mid()
            body2 = _wrap_user_if_needed(body)
            payload = _wrap_with_mid(body2, mid)
            _deliver_inbound(HOME, routes, payload, mid)
            _append_log(outlog, f"[inbound] routes={routes} mid={mid} size={len(body)} chat={chat_id}")
            _append_ledger({"kind":"bridge-inbound","from":"telegram","chat":chat_id,"routes":routes,"mid":mid,"chars":len(body)})
        time.sleep(0.5)

if __name__ == '__main__':
    main()
