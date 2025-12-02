#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeCom (Enterprise WeChat) Bridge - Outbound Only (Webhook)

Purpose:
- Outbound-only bridge for Enterprise WeChat robot webhook
- Supports markdown_v2 format for rich message rendering
- No inbound support (webhook is push-only)

Features:
- Reads .cccc/state/outbox.jsonl and sends to WeCom webhook
- Rate limiting: 20 messages/minute (configurable)
- Supports markdown_v2, text, and news message types
- Automatic markdown formatting for better readability

Architecture:
- Uses OutboxConsumer for cursor-based tail of outbox.jsonl
- Sends HTTP POST requests to WeCom webhook URL
- No long-polling or listening (outbound-only)
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Optional
import os, sys, time, json, threading, queue
import urllib.request, urllib.parse, urllib.error

ROOT = Path.cwd()
HOME = ROOT/".cccc"

# Ensure we can import modules from .cccc
if str(HOME) not in sys.path:
    sys.path.insert(0, str(HOME))

try:
    from common.config import read_config as _read_config  # type: ignore
except Exception:
    _read_config = None

try:
    from adapters.outbox_consumer import OutboxConsumer  # type: ignore
except Exception:
    OutboxConsumer = None  # type: ignore

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
    return time.strftime('%Y-%m-%d %H:%M:%S')

def _append_log(p: Path, line: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a', encoding='utf-8') as f:
        f.write(f"{_now()} {line}\n")

def _acquire_singleton_lock(name: str = "wecom-bridge"):
    """Prevent multiple bridge instances from running concurrently."""
    try:
        import fcntl
    except Exception:
        fcntl = None  # type: ignore

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
            print("[wecom_bridge] Another instance is already running. Exiting.")
            _append_log(HOME/"state"/"bridge-wecom.log", "[warn] duplicate instance detected; exiting")
        except Exception:
            pass
        sys.exit(0)
    return f

class RateLimiter:
    """Rate limiter: max N messages per time window."""
    def __init__(self, max_messages: int = 20, window_seconds: float = 60.0):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self.timestamps: List[float] = []
        self.lock = threading.Lock()

    def acquire(self) -> bool:
        """Returns True if message can be sent, False if rate limited."""
        with self.lock:
            now = time.time()
            # Remove timestamps outside the window
            self.timestamps = [t for t in self.timestamps if now - t < self.window_seconds]

            if len(self.timestamps) < self.max_messages:
                self.timestamps.append(now)
                return True
            return False

    def wait_time(self) -> float:
        """Returns seconds to wait before next message can be sent."""
        with self.lock:
            if len(self.timestamps) < self.max_messages:
                return 0.0
            now = time.time()
            oldest = min(self.timestamps)
            return max(0.0, self.window_seconds - (now - oldest))

class WeComBridge:
    def __init__(self, webhook_url: str, config: Dict[str, Any]):
        self.webhook_url = webhook_url
        self.config = config
        self.log_path = HOME/"state"/"bridge-wecom.log"

        # Rate limiting
        rate_limit = config.get('rate_limit', {})
        max_msg = rate_limit.get('max_messages', 20)
        window = rate_limit.get('window_seconds', 60)
        self.rate_limiter = RateLimiter(max_msg, window)

        # Message formatting
        self.msg_type = config.get('message_type', 'markdown_v2')  # markdown_v2, markdown, text
        self.verbose = config.get('verbose', False)

        self._append_log("[init] WeCom bridge initialized")
        self._append_log(f"[init] webhook_url: {webhook_url[:50]}...")
        self._append_log(f"[init] message_type: {self.msg_type}")
        self._append_log(f"[init] rate_limit: {max_msg} msg/{window}s")

    def _append_log(self, msg: str):
        _append_log(self.log_path, msg)

    def _send_http(self, payload: Dict[str, Any]) -> bool:
        """Send HTTP POST to WeCom webhook. Returns True on success."""
        try:
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={'Content-Type': 'application/json; charset=utf-8'}
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                errcode = result.get('errcode', -1)
                errmsg = result.get('errmsg', 'unknown')

                if errcode == 0:
                    self._append_log(f"[send] success: {errmsg}")
                    return True
                else:
                    self._append_log(f"[send] error: errcode={errcode}, errmsg={errmsg}")
                    return False

        except urllib.error.HTTPError as e:
            self._append_log(f"[send] http error: {e.code} {e.reason}")
            return False
        except Exception as e:
            self._append_log(f"[send] exception: {type(e).__name__}: {e}")
            return False

    def _format_message_markdown_v2(self, event: Dict[str, Any]) -> str:
        """Format event as markdown_v2 content."""
        lines = []

        # Title
        from_peer = event.get('from_peer', 'System')
        lines.append(f"## 📨 {from_peer}")
        lines.append("")

        # Content
        text = event.get('text', '').strip()
        if text:
            lines.append(text)
            lines.append("")

        # Evidence (if present)
        evidence = event.get('evidence', [])
        if evidence:
            lines.append("### 📋 Evidence")
            for ev in evidence:
                lines.append(f"- {ev}")
            lines.append("")

        # Metadata
        ts = event.get('ts', '')
        if ts:
            lines.append(f"*{ts}*")

        return '\n'.join(lines)

    def _format_message_markdown(self, event: Dict[str, Any]) -> str:
        """Format event as markdown content (legacy format)."""
        lines = []

        from_peer = event.get('from_peer', 'System')
        text = event.get('text', '').strip()
        ts = event.get('ts', '')

        lines.append(f"**{from_peer}**")
        if text:
            lines.append(text)
        if ts:
            lines.append(f"<font color=\"comment\">{ts}</font>")

        return '\n'.join(lines)

    def _format_message_text(self, event: Dict[str, Any]) -> str:
        """Format event as plain text."""
        from_peer = event.get('from_peer', 'System')
        text = event.get('text', '').strip()
        ts = event.get('ts', '')

        parts = [f"[{from_peer}]"]
        if text:
            parts.append(text)
        if ts:
            parts.append(f"({ts})")

        return ' '.join(parts)

    def send_to_user(self, event: Dict[str, Any]) -> bool:
        """Send to_user event to WeCom. Returns True if successful."""
        # Rate limiting
        if not self.rate_limiter.acquire():
            wait = self.rate_limiter.wait_time()
            self._append_log(f"[rate_limit] waiting {wait:.1f}s")
            time.sleep(wait)
            if not self.rate_limiter.acquire():
                self._append_log(f"[rate_limit] still rate limited, dropping message")
                return False

        # Format message based on configured type
        if self.msg_type == 'markdown_v2':
            content = self._format_message_markdown_v2(event)
            payload = {
                "msgtype": "markdown_v2",
                "markdown_v2": {
                    "content": content
                }
            }
        elif self.msg_type == 'markdown':
            content = self._format_message_markdown(event)
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": content
                }
            }
        else:  # text
            content = self._format_message_text(event)
            payload = {
                "msgtype": "text",
                "text": {
                    "content": content
                }
            }

        if self.verbose:
            self._append_log(f"[to_user] sending: {content[:100]}...")

        return self._send_http(payload)

    def send_peer_summary(self, event: Dict[str, Any]) -> bool:
        """Send to_peer_summary event to WeCom. Returns True if successful."""
        # Rate limiting
        if not self.rate_limiter.acquire():
            wait = self.rate_limiter.wait_time()
            self._append_log(f"[rate_limit] waiting {wait:.1f}s")
            time.sleep(wait)
            if not self.rate_limiter.acquire():
                self._append_log(f"[rate_limit] still rate limited, dropping message")
                return False

        # Format as markdown_v2 for better readability
        lines = []
        lines.append("## 🔄 Peer Summary")
        lines.append("")

        summary = event.get('summary', '').strip()
        if summary:
            lines.append(summary)
            lines.append("")

        ts = event.get('ts', '')
        if ts:
            lines.append(f"*{ts}*")

        content = '\n'.join(lines)

        if self.msg_type == 'markdown_v2':
            payload = {
                "msgtype": "markdown_v2",
                "markdown_v2": {
                    "content": content
                }
            }
        elif self.msg_type == 'markdown':
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": content.replace('##', '**').replace('*', '')  # Simplify for markdown
                }
            }
        else:  # text
            payload = {
                "msgtype": "text",
                "text": {
                    "content": content.replace('#', '').replace('*', '')  # Plain text
                }
            }

        if self.verbose:
            self._append_log(f"[to_peer_summary] sending: {content[:100]}...")

        return self._send_http(payload)

def main():
    """Main entry point for WeCom bridge daemon."""
    print("[wecom_bridge] Starting WeCom bridge...")

    # Acquire singleton lock
    lock_file = _acquire_singleton_lock()

    # Load configuration
    cfg_path = HOME/"settings"/"wecom.yaml"
    if not cfg_path.exists():
        print(f"[wecom_bridge] Configuration not found: {cfg_path}")
        print("[wecom_bridge] Please create wecom.yaml with webhook_url")
        sys.exit(1)

    config = read_yaml(cfg_path)

    # Get webhook URL from config or env
    webhook_url = config.get('webhook_url', '').strip()
    if not webhook_url:
        webhook_env = config.get('webhook_url_env', 'WECOM_WEBHOOK_URL')
        webhook_url = os.environ.get(webhook_env, '').strip()

    if not webhook_url:
        print("[wecom_bridge] webhook_url not configured")
        print("[wecom_bridge] Set webhook_url in wecom.yaml or WECOM_WEBHOOK_URL env var")
        sys.exit(1)

    # Initialize bridge
    bridge = WeComBridge(webhook_url, config)

    # Initialize outbox consumer
    if OutboxConsumer is None:
        bridge._append_log("[error] OutboxConsumer not available")
        print("[wecom_bridge] OutboxConsumer not available. Check .cccc/adapters/outbox_consumer.py")
        sys.exit(1)

    outbound_cfg = config.get('outbound', {})
    cursor_cfg = outbound_cfg.get('cursor', {})
    start_mode = cursor_cfg.get('start_mode', 'tail')
    replay_last = cursor_cfg.get('replay_last', 0)

    consumer = OutboxConsumer(
        HOME,
        seen_name='wecom',
        poll_seconds=1.0,
        start_mode=start_mode,
        replay_last=replay_last
    )

    bridge._append_log(f"[main] starting consumer loop (start_mode={start_mode}, replay_last={replay_last})")
    print(f"[wecom_bridge] Listening to outbox.jsonl (start_mode={start_mode})")

    # Start consumer loop
    try:
        consumer.loop(
            on_to_user=bridge.send_to_user,
            on_to_peer_summary=bridge.send_peer_summary
        )
    except KeyboardInterrupt:
        bridge._append_log("[main] keyboard interrupt, exiting")
        print("\n[wecom_bridge] Shutting down...")
    except Exception as e:
        bridge._append_log(f"[main] exception: {type(e).__name__}: {e}")
        print(f"[wecom_bridge] Error: {e}")
        raise
    finally:
        try:
            lock_file.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
