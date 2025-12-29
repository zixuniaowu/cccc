"""
CCCC IM Bridge - Core logic.

Handles:
- Inbound: IM messages → daemon API → ledger
- Outbound: ledger events → filter → IM
- Command processing
- Ledger watching (cursor-based tail)
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...daemon.server import call_daemon
from ...kernel.group import Group, load_group
from ...paths import ensure_home
from .adapters.base import IMAdapter
from .adapters.telegram import TelegramAdapter
from .adapters.slack import SlackAdapter
from .adapters.discord import DiscordAdapter
from .commands import (
    CommandType,
    ParsedCommand,
    format_context,
    format_help,
    format_status,
    parse_message,
)
from .subscribers import SubscriberManager


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _is_env_var_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z_][A-Z0-9_]*", (value or "").strip()))


def _acquire_singleton_lock(lock_path: Path) -> Optional[Any]:
    """
    Acquire singleton lock to prevent multiple bridge instances.
    Returns file handle on success, None on failure.
    """
    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "w")

    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(str(os.getpid()))
        f.flush()
        return f
    except Exception:
        f.close()
        return None


class LedgerWatcher:
    """
    Watch ledger.jsonl for new events using cursor-based tail.

    Reuses exactly-once semantics from v0.3.28 outbox_consumer.py:
    - Cursor file tracks (dev, inode, offset)
    - Handles file rotation/truncation
    - Resumes from last position on restart
    """

    def __init__(self, group: Group, cursor_name: str = "im_bridge"):
        self.group = group
        self.ledger_path = group.ledger_path
        self.cursor_path = group.path / "state" / f"{cursor_name}_cursor.json"

        self._offset = 0
        self._dev: Optional[int] = None
        self._ino: Optional[int] = None
        self._buf = ""

        self._load_cursor()

    def _load_cursor(self) -> None:
        """Load cursor from disk."""
        try:
            if self.cursor_path.exists():
                data = json.loads(self.cursor_path.read_text(encoding="utf-8"))
                self._dev = data.get("dev")
                self._ino = data.get("ino")
                self._offset = int(data.get("offset", 0))
        except Exception:
            self._dev = None
            self._ino = None
            self._offset = 0

    def _save_cursor(self, dev: int, ino: int, offset: int) -> None:
        """Save cursor to disk."""
        try:
            self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cursor_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"dev": dev, "ino": ino, "offset": offset}, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.cursor_path)
        except Exception:
            pass

    def poll(self) -> List[Dict[str, Any]]:
        """
        Poll for new events.

        Returns list of parsed event dicts.
        """
        events = []

        if not self.ledger_path.exists():
            return events

        try:
            st = os.stat(self.ledger_path)
            dev, ino, size = st.st_dev, st.st_ino, st.st_size

            # Check for rotation/truncation
            rotated = (
                self._dev is None
                or self._dev != dev
                or self._ino != ino
                or self._offset > size
            )

            if rotated:
                # Start from current position (don't replay history on first run)
                if self._dev is None:
                    # First time: check if file is fresh
                    is_fresh = (time.time() - st.st_mtime) <= 5.0
                    self._offset = 0 if is_fresh else size
                else:
                    # Rotation: start from end
                    self._offset = size

                self._dev = dev
                self._ino = ino
                self._save_cursor(dev, ino, self._offset)

            # Read new data
            if size > self._offset:
                with open(self.ledger_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self._offset)
                    chunk = f.read()

                if chunk:
                    data = self._buf + chunk
                    lines = data.splitlines(keepends=True)

                    # Keep incomplete line in buffer
                    if lines and not lines[-1].endswith("\n"):
                        self._buf = lines.pop()
                    else:
                        self._buf = ""

                    # Parse complete lines
                    for line in lines:
                        text = line.rstrip("\r\n")
                        if not text:
                            continue

                        self._offset += len(line)

                        try:
                            event = json.loads(text)
                            events.append(event)
                        except json.JSONDecodeError:
                            continue

                    # Save cursor
                    self._save_cursor(dev, ino, self._offset)

        except Exception:
            pass

        return events


class IMBridge:
    """
    Main IM Bridge class.

    Coordinates:
    - Adapter (platform-specific communication)
    - Subscriber manager
    - Ledger watcher
    - Command processing
    """

    def __init__(
        self,
        group: Group,
        adapter: IMAdapter,
        log_path: Optional[Path] = None,
    ):
        self.group = group
        self.adapter = adapter
        self.log_path = log_path

        self.subscribers = SubscriberManager(group.path / "state")
        self.watcher = LedgerWatcher(group)

        self._running = False
        self._last_outbound_check = 0.0

    def _log(self, msg: str) -> None:
        """Log message."""
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as f:
                    f.write(f"{_now()} {msg}\n")
            except Exception:
                pass

    def _daemon(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Call daemon API."""
        try:
            return call_daemon(req)
        except Exception as e:
            return {"ok": False, "error": {"code": "daemon_error", "message": str(e)}}

    def start(self) -> bool:
        """Start the bridge."""
        if not self.adapter.connect():
            self._log("[start] Failed to connect adapter")
            return False

        self._running = True
        self._log(f"[start] Bridge started for group {self.group.group_id}")
        return True

    def stop(self) -> None:
        """Stop the bridge."""
        self._running = False
        self.adapter.disconnect()
        self._log("[stop] Bridge stopped")

    def run_once(self) -> None:
        """Run one iteration of the bridge loop."""
        # Process inbound messages
        self._process_inbound()

        # Process outbound events (throttled)
        now = time.time()
        if now - self._last_outbound_check >= 1.0:
            self._process_outbound()
            self._last_outbound_check = now

    def run_forever(self, poll_interval: float = 0.5) -> None:
        """Run the bridge loop forever."""
        while self._running:
            try:
                self.run_once()
            except Exception as e:
                self._log(f"[error] Loop error: {e}")

            time.sleep(poll_interval)

    def _process_inbound(self) -> None:
        """Process incoming IM messages."""
        messages = self.adapter.poll()

        for msg in messages:
            chat_id = str(msg.get("chat_id") or "").strip()
            text = str(msg.get("text") or "")
            chat_title = str(msg.get("chat_title") or "")
            from_user = str(msg.get("from_user") or "user")
            chat_type = str(msg.get("chat_type") or "").strip().lower()
            try:
                thread_id = int(msg.get("thread_id") or 0)
            except Exception:
                thread_id = 0

            if not text:
                continue

            # Parse command
            parsed = parse_message(text)

            # Handle commands
            if parsed.type == CommandType.SUBSCRIBE:
                self._handle_subscribe(chat_id, chat_title, thread_id=thread_id)
            elif parsed.type == CommandType.UNSUBSCRIBE:
                self._handle_unsubscribe(chat_id, thread_id=thread_id)
            elif parsed.type == CommandType.VERBOSE:
                self._handle_verbose(chat_id, thread_id=thread_id)
            elif parsed.type == CommandType.STATUS:
                self._handle_status(chat_id, thread_id=thread_id)
            elif parsed.type == CommandType.CONTEXT:
                self._handle_context(chat_id, thread_id=thread_id)
            elif parsed.type == CommandType.PAUSE:
                self._handle_pause(chat_id, thread_id=thread_id)
            elif parsed.type == CommandType.RESUME:
                self._handle_resume(chat_id, thread_id=thread_id)
            elif parsed.type == CommandType.LAUNCH:
                self._handle_launch(chat_id, thread_id=thread_id)
            elif parsed.type == CommandType.QUIT:
                self._handle_quit(chat_id, thread_id=thread_id)
            elif parsed.type == CommandType.HELP:
                self._handle_help(chat_id, thread_id=thread_id)
            elif parsed.type == CommandType.SEND:
                self._handle_message(chat_id, parsed.text, parsed.mentions, from_user, thread_id=thread_id)
            elif parsed.type == CommandType.MESSAGE:
                routed = bool(msg.get("routed") or False)

                # Unknown slash commands should not be forwarded as chat content.
                # (Telegram groups may contain unrelated /commands; we only reply when routed/private.)
                if text.lstrip().startswith("/"):
                    if routed or chat_type in ("private",):
                        self.adapter.send_message(chat_id, "❓ Unknown command. Use /help.", thread_id=thread_id)
                    continue

                # In non-private chats/channels, avoid forwarding all chatter into CCCC.
                # Telegram: use /send. Slack/Discord: mention the bot (adapter sets routed=True).
                if chat_type and chat_type not in ("private",) and not routed:
                    continue
                self._handle_message(chat_id, parsed.text, parsed.mentions, from_user, thread_id=thread_id)

    def _process_outbound(self) -> None:
        """Process outbound events from ledger."""
        events = self.watcher.poll()

        for event in events:
            self._forward_event(event)

    def _forward_event(self, event: Dict[str, Any]) -> None:
        """Forward a ledger event to subscribed chats."""
        kind = event.get("kind", "")
        by = event.get("by", "")

        # Skip user messages (they come from IM, Web, or CLI - avoid echo)
        # We only forward agent messages and system notifications
        if by == "user":
            return

        # Determine if we should forward
        is_system = kind == "system.notify"
        is_chat = kind == "chat.message"

        if not is_system and not is_chat:
            return

        # Get message details
        data = event.get("data", {})
        text = data.get("text", "")
        to = data.get("to", [])

        if not text:
            return

        # Forward to subscribed chats
        subscribed = self.subscribers.get_subscribed_targets()

        for sub in subscribed:
            verbose = bool(sub.verbose)

            # Filter based on verbose setting
            if not self._should_forward(event, verbose):
                continue

            # Format message
            formatted = self.adapter.format_outbound(by, to, text, is_system)

            # Send
            self.adapter.send_message(sub.chat_id, formatted, thread_id=sub.thread_id)

    def _should_forward(self, event: Dict[str, Any], verbose: bool) -> bool:
        """Determine if event should be forwarded based on verbose setting."""
        kind = event.get("kind", "")

        # System notifications always forwarded
        if kind == "system.notify":
            return True

        # Chat messages
        if kind == "chat.message":
            data = event.get("data", {})
            to = data.get("to", [])
            by = event.get("by", "")

            # Verbose: forward all
            if verbose:
                return True

            # Non-verbose: only forward to:user messages
            if "user" in to or not to:
                return True

            return False

        return False

    # =========================================================================
    # Command Handlers
    # =========================================================================

    def _handle_subscribe(self, chat_id: str, chat_title: str, thread_id: int = 0) -> None:
        """Handle /subscribe command."""
        sub = self.subscribers.subscribe(chat_id, chat_title, thread_id=thread_id)
        verbose_str = "on" if sub.verbose else "off"
        platform = str(getattr(self.adapter, "platform", "") or "").strip().lower() or "telegram"
        if platform == "telegram":
            tip = "Group tip: use /send <message> to talk to agents."
        elif platform in ("slack", "discord"):
            tip = "Channel tip: mention the bot (e.g. @bot hello) to talk to agents."
        else:
            tip = "Tip: use /send <message> to talk to agents."
        self.adapter.send_message(
            chat_id,
            f"✅ Subscribed to {self.group.doc.get('title', self.group.group_id)}\n"
            f"Verbose mode: {verbose_str}\n"
            f"{tip}\n"
            f"Use /help for commands.",
            thread_id=thread_id,
        )
        self._log(f"[subscribe] chat={chat_id} thread={thread_id} title={chat_title}")

    def _handle_unsubscribe(self, chat_id: str, thread_id: int = 0) -> None:
        """Handle /unsubscribe command."""
        was_subscribed = self.subscribers.unsubscribe(chat_id, thread_id=thread_id)
        if was_subscribed:
            self.adapter.send_message(chat_id, "👋 Unsubscribed. You will no longer receive messages.", thread_id=thread_id)
        else:
            self.adapter.send_message(chat_id, "ℹ️ You were not subscribed.", thread_id=thread_id)
        self._log(f"[unsubscribe] chat={chat_id} thread={thread_id}")

    def _handle_verbose(self, chat_id: str, thread_id: int = 0) -> None:
        """Handle /verbose command (toggle)."""
        new_value = self.subscribers.toggle_verbose(chat_id, thread_id=thread_id)
        if new_value is None:
            self.adapter.send_message(chat_id, "ℹ️ Please /subscribe first.", thread_id=thread_id)
        else:
            status = "ON - showing all messages" if new_value else "OFF - showing only messages to you"
            self.adapter.send_message(chat_id, f"👁 Verbose mode: {status}", thread_id=thread_id)
        self._log(f"[verbose] chat={chat_id} thread={thread_id} new_value={new_value}")

    def _handle_status(self, chat_id: str, thread_id: int = 0) -> None:
        """Handle /status command."""
        # Get group info
        resp = self._daemon({"op": "group_show", "args": {"group_id": self.group.group_id}})
        if not resp.get("ok"):
            self.adapter.send_message(chat_id, "❌ Failed to get status", thread_id=thread_id)
            return

        group_data = resp.get("result", {}).get("group", {})
        group_title = group_data.get("title", self.group.group_id)
        group_state = group_data.get("state", "active")
        running = resp.get("result", {}).get("running", False)

        # Get actors
        actors_resp = self._daemon({"op": "actor_list", "args": {"group_id": self.group.group_id}})
        actors = []
        if actors_resp.get("ok"):
            actors = actors_resp.get("result", {}).get("actors", [])

        status_text = format_status(group_title, group_state, running, actors)
        self.adapter.send_message(chat_id, status_text, thread_id=thread_id)

    def _handle_context(self, chat_id: str, thread_id: int = 0) -> None:
        """Handle /context command."""
        resp = self._daemon({"op": "context_get", "args": {"group_id": self.group.group_id}})
        if not resp.get("ok"):
            self.adapter.send_message(chat_id, "❌ Failed to get context", thread_id=thread_id)
            return

        context = resp.get("result", {})
        context_text = format_context(context)
        self.adapter.send_message(chat_id, context_text, thread_id=thread_id)

    def _handle_pause(self, chat_id: str, thread_id: int = 0) -> None:
        """Handle /pause command."""
        resp = self._daemon({
            "op": "group_set_state",
            "args": {"group_id": self.group.group_id, "state": "paused", "by": "user"},
        })
        if resp.get("ok"):
            self.adapter.send_message(chat_id, "⏸ Group paused. Message delivery stopped.", thread_id=thread_id)
        else:
            error = resp.get("error", {}).get("message", "unknown error")
            self.adapter.send_message(chat_id, f"❌ Failed to pause: {error}", thread_id=thread_id)
        self._log(f"[pause] chat={chat_id} thread={thread_id} ok={resp.get('ok')}")

    def _handle_resume(self, chat_id: str, thread_id: int = 0) -> None:
        """Handle /resume command."""
        resp = self._daemon({
            "op": "group_set_state",
            "args": {"group_id": self.group.group_id, "state": "active", "by": "user"},
        })
        if resp.get("ok"):
            self.adapter.send_message(chat_id, "▶️ Group resumed. Message delivery active.", thread_id=thread_id)
        else:
            error = resp.get("error", {}).get("message", "unknown error")
            self.adapter.send_message(chat_id, f"❌ Failed to resume: {error}", thread_id=thread_id)
        self._log(f"[resume] chat={chat_id} thread={thread_id} ok={resp.get('ok')}")

    def _handle_launch(self, chat_id: str, thread_id: int = 0) -> None:
        """Handle /launch command."""
        resp = self._daemon({
            "op": "group_start",
            "args": {"group_id": self.group.group_id, "by": "user"},
        })
        if resp.get("ok"):
            self.adapter.send_message(chat_id, "🚀 Launching all agents...", thread_id=thread_id)
        else:
            error = resp.get("error", {}).get("message", "unknown error")
            self.adapter.send_message(chat_id, f"❌ Failed to launch: {error}", thread_id=thread_id)
        self._log(f"[launch] chat={chat_id} thread={thread_id} ok={resp.get('ok')}")

    def _handle_quit(self, chat_id: str, thread_id: int = 0) -> None:
        """Handle /quit command."""
        resp = self._daemon({
            "op": "group_stop",
            "args": {"group_id": self.group.group_id, "by": "user"},
        })
        if resp.get("ok"):
            self.adapter.send_message(chat_id, "🛑 Stopping all agents...", thread_id=thread_id)
        else:
            error = resp.get("error", {}).get("message", "unknown error")
            self.adapter.send_message(chat_id, f"❌ Failed to quit: {error}", thread_id=thread_id)
        self._log(f"[quit] chat={chat_id} thread={thread_id} ok={resp.get('ok')}")

    def _handle_help(self, chat_id: str, thread_id: int = 0) -> None:
        """Handle /help command."""
        platform = str(getattr(self.adapter, "platform", "") or "").strip().lower() or "telegram"
        self.adapter.send_message(chat_id, format_help(platform=platform), thread_id=thread_id)

    def _handle_message(
        self,
        chat_id: str,
        text: str,
        mentions: List[str],
        from_user: str,
        thread_id: int = 0,
    ) -> None:
        """Handle regular message (send to agents)."""
        if not text.strip():
            return

        # Build recipient list from mentions
        to: List[str] = []
        if mentions:
            to = mentions

        # Send via daemon
        resp = self._daemon({
            "op": "send",
            "args": {
                "group_id": self.group.group_id,
                "text": text,
                "by": "user",
                "to": to,
                "path": "",
            },
        })

        if not resp.get("ok"):
            error = resp.get("error", {}).get("message", "unknown error")
            self.adapter.send_message(chat_id, f"❌ Failed to send: {error}", thread_id=thread_id)
            self._log(f"[message] chat={chat_id} thread={thread_id} error={error}")
        else:
            self._log(f"[message] chat={chat_id} thread={thread_id} to={to} len={len(text)}")


def start_bridge(group_id: str, platform: str = "telegram") -> None:
    """
    Start IM bridge for a group.

    This is the main entry point called by CLI.
    """
    # Load group
    group = load_group(group_id)
    if group is None:
        print(f"[error] Group not found: {group_id}")
        sys.exit(1)

    # Get IM config from group
    im_config = group.doc.get("im", {})
    if not im_config:
        print(f"[error] No IM configuration for group {group_id}")
        print("Run: cccc im set telegram --group " + group_id)
        sys.exit(1)

    configured_platform = im_config.get("platform", "").lower()
    if configured_platform != platform.lower():
        print(f"[error] Group configured for {configured_platform}, not {platform}")
        sys.exit(1)

    # Get token(s) based on platform
    bot_token = None
    app_token = None  # Slack only (for Socket Mode inbound)

    if platform.lower() == "slack":
        # Slack requires bot_token (xoxb-) for outbound, app_token (xapp-) for inbound
        bot_token_env_raw = str(im_config.get("bot_token_env") or "").strip()
        bot_token_env = bot_token_env_raw if _is_env_var_name(bot_token_env_raw) else ""
        if bot_token_env:
            bot_token = os.environ.get(bot_token_env, "").strip()
        if not bot_token:
            bot_token = str(im_config.get("bot_token") or "").strip()
        if not bot_token and bot_token_env_raw and not bot_token_env:
            # Common misconfig: raw token pasted into *_env field.
            bot_token = bot_token_env_raw
        
        app_token_env_raw = str(im_config.get("app_token_env") or "").strip()
        app_token_env = app_token_env_raw if _is_env_var_name(app_token_env_raw) else ""
        if app_token_env:
            app_token = os.environ.get(app_token_env, "").strip()
        if not app_token:
            app_token = str(im_config.get("app_token") or "").strip()
        if not app_token and app_token_env_raw and not app_token_env:
            app_token = app_token_env_raw
        
        if not bot_token:
            print(f"[error] No bot token configured for Slack")
            if bot_token_env:
                print(f"Set environment variable: {bot_token_env}")
            sys.exit(1)
        
        # app_token is optional (inbound disabled without it)
        if not app_token:
            print("[warn] No app token configured - inbound messages disabled")
    else:
        # Telegram/Discord: single token
        token_env_raw = str(im_config.get("token_env") or im_config.get("bot_token_env") or "").strip()
        token_env = token_env_raw if _is_env_var_name(token_env_raw) else ""
        if token_env:
            bot_token = os.environ.get(token_env, "").strip()
        if not bot_token:
            bot_token = str(im_config.get("token") or "").strip()
        if not bot_token and token_env_raw and not token_env:
            # Common misconfig: raw token pasted into *_env field.
            bot_token = token_env_raw

        if not bot_token:
            print(f"[error] No token configured for {platform}")
            if token_env:
                print(f"Set environment variable: {token_env}")
            sys.exit(1)

    # Paths
    state_dir = group.path / "state"
    log_path = state_dir / "im_bridge.log"
    lock_path = state_dir / "im_bridge.lock"
    pid_path = state_dir / "im_bridge.pid"

    # Acquire singleton lock
    lock_file = _acquire_singleton_lock(lock_path)
    if lock_file is None:
        print("[error] Another bridge instance is already running")
        sys.exit(1)

    # Write PID file
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    # Create adapter
    if platform.lower() == "telegram":
        adapter = TelegramAdapter(token=bot_token, log_path=log_path)
    elif platform.lower() == "slack":
        adapter = SlackAdapter(bot_token=bot_token, app_token=app_token, log_path=log_path)
    elif platform.lower() == "discord":
        adapter = DiscordAdapter(token=bot_token, log_path=log_path)
    else:
        print(f"[error] Unsupported platform: {platform}")
        sys.exit(1)

    # Create and start bridge
    bridge = IMBridge(group=group, adapter=adapter, log_path=log_path)

    # Setup signal handlers
    def handle_signal(signum: int, frame: Any) -> None:
        print(f"\n[signal] Received signal {signum}, stopping...")
        bridge.stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Start
    if not bridge.start():
        print("[error] Failed to start bridge")
        sys.exit(1)

    print(f"[info] IM Bridge started for group {group_id}")
    print(f"[info] Platform: {platform}")
    print(f"[info] Log: {log_path}")
    print("[info] Press Ctrl+C to stop")

    # Run
    try:
        bridge.run_forever()
    finally:
        # Cleanup
        try:
            pid_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            lock_file.close()
        except Exception:
            pass

    print("[info] Bridge stopped")


# Entry point for running as module: python -m cccc.ports.im.bridge <group_id> [platform]
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m cccc.ports.im.bridge <group_id> [platform]")
        print("  platform: telegram (default), slack, discord")
        sys.exit(1)

    _group_id = sys.argv[1]
    _platform = sys.argv[2] if len(sys.argv) > 2 else "telegram"
    start_bridge(_group_id, _platform)
