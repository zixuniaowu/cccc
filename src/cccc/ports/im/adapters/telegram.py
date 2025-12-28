"""
Telegram Bot API adapter for CCCC IM Bridge.

Reuses battle-tested code from v0.3.28 bridge_telegram.py:
- tg_api(): API call wrapper with JSON encoding, timeout, error handling
- tg_poll(): Long-poll getUpdates
- Rate limiting and message formatting
- Singleton lock to prevent duplicate instances
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import IMAdapter

# Telegram API limits
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
DEFAULT_MAX_CHARS = 900
DEFAULT_MAX_LINES = 8


class RateLimiter:
    """
    Rate limiter for Telegram API.

    Telegram limits:
    - Same chat: ~1 msg/sec
    - Different chats: ~30 msg/sec
    """

    def __init__(self, max_per_second: float = 1.0):
        self.min_interval = 1.0 / max_per_second
        self.last_send: Dict[int, float] = {}  # chat_id -> timestamp
        self.lock = threading.Lock()

    def acquire(self, chat_id: int) -> float:
        """
        Check if we can send to this chat.
        Returns wait time in seconds (0 if can send immediately).
        """
        with self.lock:
            now = time.time()
            last = self.last_send.get(chat_id, 0)
            elapsed = now - last

            if elapsed >= self.min_interval:
                self.last_send[chat_id] = now
                return 0.0
            else:
                return self.min_interval - elapsed

    def wait_and_acquire(self, chat_id: int) -> None:
        """Wait if needed, then acquire."""
        wait_time = self.acquire(chat_id)
        if wait_time > 0:
            time.sleep(wait_time)
            self.acquire(chat_id)


class TelegramAdapter(IMAdapter):
    """
    Telegram Bot API adapter using long-poll getUpdates.
    """

    def __init__(
        self,
        token: str,
        log_path: Optional[Path] = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_lines: int = DEFAULT_MAX_LINES,
    ):
        self.token = token
        self.log_path = log_path
        self.max_chars = max_chars
        self.max_lines = max_lines

        self._offset = 0
        self._rate_limiter = RateLimiter(max_per_second=1.0)
        self._connected = False
        self._bot_info: Optional[Dict[str, Any]] = None

    def _log(self, msg: str) -> None:
        """Append to log file if configured."""
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as f:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{ts} {msg}\n")
            except Exception:
                pass

    def _api(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 35,
    ) -> Dict[str, Any]:
        """
        Call Telegram Bot API.

        Uses JSON body for consistent encoding (handles non-ASCII text).
        """
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = json.dumps(params or {}, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            http_status = e.code
            err_text = ""
            try:
                err_text = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            self._log(f"[error] api {method}: HTTP {http_status} - {err_text}")
            return {"ok": False, "error": str(e), "http_status": http_status}
        except Exception as e:
            self._log(f"[error] api {method}: {e}")
            return {"ok": False, "error": str(e)}

    def connect(self) -> bool:
        """Verify token and get bot info."""
        resp = self._api("getMe", timeout=10)
        if resp.get("ok"):
            self._bot_info = resp.get("result", {})
            self._connected = True
            bot_username = self._bot_info.get("username", "unknown")
            self._log(f"[connect] Connected as @{bot_username}")
            return True
        else:
            self._log(f"[connect] Failed: {resp.get('error', 'unknown error')}")
            return False

    def disconnect(self) -> None:
        """Disconnect (no-op for Telegram, just mark as disconnected)."""
        self._connected = False
        self._log("[disconnect] Disconnected")

    def poll(self) -> List[Dict[str, Any]]:
        """
        Long-poll for new messages using getUpdates.

        Returns list of normalized message dicts.
        """
        if not self._connected:
            return []

        resp = self._api(
            "getUpdates",
            {
                "offset": self._offset,
                "timeout": 25,
                "allowed_updates": ["message", "edited_message", "channel_post"],
            },
            timeout=35,
        )

        messages = []
        if resp.get("ok") and isinstance(resp.get("result"), list):
            for update in resp["result"]:
                try:
                    update_id = int(update.get("update_id", 0))
                    self._offset = max(self._offset, update_id + 1)

                    # Extract message from update
                    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
                    if not msg:
                        continue

                    # Extract text
                    text = msg.get("text") or msg.get("caption") or ""
                    if not text:
                        continue

                    # Extract chat info
                    chat = msg.get("chat", {})
                    chat_id = int(chat.get("id", 0))
                    chat_title = chat.get("title") or chat.get("first_name") or str(chat_id)

                    # Extract sender info
                    from_user = msg.get("from", {})
                    username = from_user.get("username") or from_user.get("first_name") or "user"

                    messages.append({
                        "chat_id": chat_id,
                        "chat_title": chat_title,
                        "text": text,
                        "from_user": username,
                        "message_id": msg.get("message_id", 0),
                        "update_id": update_id,
                    })
                except Exception as e:
                    self._log(f"[poll] Error parsing update: {e}")
                    continue

        return messages

    def send_message(self, chat_id: int, text: str) -> bool:
        """
        Send a message to a chat.

        Handles:
        - Rate limiting
        - Message length limits
        - Retry on failure
        """
        if not text:
            return True

        # Ensure message fits Telegram limit
        safe_text = self._compose_safe(text)

        # Rate limit
        self._rate_limiter.wait_and_acquire(chat_id)

        # Send with retry
        return self._send_with_retry(chat_id, safe_text)

    def _compose_safe(self, text: str) -> str:
        """Ensure message fits within Telegram limits."""
        # First summarize
        summarized = self.summarize(text, self.max_chars, self.max_lines)

        # Then ensure hard limit
        if len(summarized) > TELEGRAM_MAX_MESSAGE_LENGTH:
            summarized = summarized[: TELEGRAM_MAX_MESSAGE_LENGTH - 1] + "…"

        return summarized

    def _send_with_retry(self, chat_id: int, text: str, retries: int = 1) -> bool:
        """Send message with retry on failure."""
        resp = self._api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )

        if resp.get("ok"):
            return True

        # Retry once
        if retries > 0:
            time.sleep(1.0)
            return self._send_with_retry(chat_id, text, retries - 1)

        self._log(f"[send] Failed to chat {chat_id}: {resp.get('error', 'unknown')}")
        return False

    def get_chat_title(self, chat_id: int) -> str:
        """Get chat title via API."""
        resp = self._api("getChat", {"chat_id": chat_id}, timeout=10)
        if resp.get("ok"):
            chat = resp.get("result", {})
            return chat.get("title") or chat.get("first_name") or str(chat_id)
        return str(chat_id)

    def format_outbound(self, by: str, to: List[str], text: str, is_system: bool = False) -> str:
        """Format message for Telegram display."""
        # Use base implementation but ensure it fits
        formatted = super().format_outbound(by, to, text, is_system)
        return self._compose_safe(formatted)
