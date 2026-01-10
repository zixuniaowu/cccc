"""
DingTalk adapter for CCCC IM Bridge.

Uses DingTalk Open API with Stream mode for real-time messaging.
Reference: https://open.dingtalk.com/document/

Features:
- access_token auto-refresh (2h expiry)
- Stream mode event subscription (long connection)
- Rate limiting (20 msg/sec total)
- File upload/download support
"""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import IMAdapter

# DingTalk API limits
DINGTALK_MAX_MESSAGE_LENGTH = 4096
DEFAULT_MAX_CHARS = 4096
DEFAULT_MAX_LINES = 64

# API base URLs
DINGTALK_API_OLD = "https://oapi.dingtalk.com"
DINGTALK_API_NEW = "https://api.dingtalk.com"


class RateLimiter:
    """
    Rate limiter for DingTalk API.

    DingTalk limits:
    - Total: ~20 msg/sec
    - Same chat: ~5 msg/sec
    """

    def __init__(self, max_per_second: float = 5.0):
        self.min_interval = 1.0 / max_per_second
        self.last_send: Dict[str, float] = {}
        self.lock = threading.Lock()

    def acquire(self, chat_id: str) -> float:
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

    def wait_and_acquire(self, chat_id: str) -> None:
        """Wait if needed, then acquire."""
        wait_time = self.acquire(chat_id)
        if wait_time > 0:
            time.sleep(wait_time)
            self.acquire(chat_id)


class DingTalkAdapter(IMAdapter):
    """
    DingTalk adapter using Stream mode for inbound and REST API for outbound.
    """

    platform = "dingtalk"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        robot_code: str = "",
        log_path: Optional[Path] = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_lines: int = DEFAULT_MAX_LINES,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.robot_code = robot_code or app_key  # Robot code defaults to app_key
        self.log_path = log_path
        self.max_chars = max_chars
        self.max_lines = max_lines

        # Token management
        self._token: str = ""
        self._token_expires: float = 0
        self._token_lock = threading.Lock()

        # Message queue (thread-safe)
        self._message_queue: List[Dict[str, Any]] = []
        self._queue_lock = threading.Lock()

        # Rate limiter
        self._rate_limiter = RateLimiter(max_per_second=5.0)

        # Connection state
        self._connected = False
        self._stream_thread: Optional[threading.Thread] = None
        self._stream_running = False

        # Cache for conversation info
        self._conversation_cache: Dict[str, str] = {}

        # Cache for session webhooks (conversation_id -> (webhook_url, expires_at))
        self._session_webhook_cache: Dict[str, tuple[str, float]] = {}

    def _log(self, msg: str) -> None:
        """Append to log file if configured."""
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as f:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{ts} [dingtalk] {msg}\n")
            except Exception:
                pass

    def _get_token(self) -> str:
        """Get valid access_token, refreshing if needed."""
        with self._token_lock:
            now = time.time()
            # Refresh 5 minutes before expiry
            if self._token and now < self._token_expires - 300:
                return self._token

            # Refresh token
            if self._refresh_token():
                return self._token
            return ""

    def _refresh_token(self) -> bool:
        """
        Refresh access_token.
        Token expires in 2 hours (7200 seconds).
        """
        url = f"{DINGTALK_API_OLD}/gettoken"
        params = {
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        query = urllib.parse.urlencode(params)
        full_url = f"{url}?{query}"

        req = urllib.request.Request(full_url, method="GET")
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                result = json.loads(body)

                if result.get("errcode") == 0:
                    self._token = result.get("access_token", "")
                    expire = int(result.get("expires_in", 7200))
                    self._token_expires = time.time() + expire
                    self._log(f"[token] Refreshed, expires in {expire}s")
                    return True
                else:
                    self._log(f"[token] Failed: {result.get('errmsg', 'unknown')}")
                    return False
        except Exception as e:
            self._log(f"[token] Error: {e}")
            return False

    def _api_old(
        self,
        method: str,
        endpoint: str,
        body: Optional[Dict[str, Any]] = None,
        timeout: int = 15,
    ) -> Dict[str, Any]:
        """
        Call DingTalk old API (oapi.dingtalk.com).

        Used for: token, some legacy APIs
        """
        token = self._get_token()
        if not token and "gettoken" not in endpoint:
            return {"errcode": -1, "errmsg": "No valid token"}

        url = f"{DINGTALK_API_OLD}{endpoint}"

        if method == "GET":
            if body:
                query = urllib.parse.urlencode(body)
                url = f"{url}?{query}"
            if token and "access_token" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}access_token={token}"
            data = None
        else:
            if token and "access_token" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}access_token={token}"
            data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body else None

        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json; charset=utf-8")
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result_body = resp.read().decode("utf-8", errors="replace")
                return json.loads(result_body)
        except urllib.error.HTTPError as e:
            http_status = e.code
            err_text = ""
            try:
                err_text = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            self._log(f"[api_old] {method} {endpoint}: HTTP {http_status} - {err_text}")
            return {"errcode": http_status, "errmsg": str(e), "error": err_text}
        except Exception as e:
            self._log(f"[api_old] {method} {endpoint}: {e}")
            return {"errcode": -1, "errmsg": str(e)}

    def _api_new(
        self,
        method: str,
        endpoint: str,
        body: Optional[Dict[str, Any]] = None,
        timeout: int = 15,
    ) -> Dict[str, Any]:
        """
        Call DingTalk new API (api.dingtalk.com).

        Used for: robot messages, conversations, files
        """
        token = self._get_token()
        if not token:
            return {"code": -1, "message": "No valid token"}

        url = f"{DINGTALK_API_NEW}{endpoint}"

        if method == "GET" and body:
            query = urllib.parse.urlencode(body)
            url = f"{url}?{query}"
            data = None
        else:
            data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body else None

        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("x-acs-dingtalk-access-token", token)
        req.add_header("Content-Type", "application/json; charset=utf-8")
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result_body = resp.read().decode("utf-8", errors="replace")
                return json.loads(result_body)
        except urllib.error.HTTPError as e:
            http_status = e.code
            err_text = ""
            try:
                err_text = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            self._log(f"[api_new] {method} {endpoint}: HTTP {http_status} - {err_text}")
            return {"code": http_status, "message": str(e), "error": err_text}
        except Exception as e:
            self._log(f"[api_new] {method} {endpoint}: {e}")
            return {"code": -1, "message": str(e)}

    def connect(self) -> bool:
        """
        Initialize connection to DingTalk.

        1. Verify credentials by getting token
        2. Start Stream mode listener (if available)
        """
        # Get initial token
        if not self._refresh_token():
            self._log("[connect] Failed to get token")
            return False

        # Start Stream listener for events
        self._start_stream_listener()

        self._connected = True
        self._log(f"[connect] Connected with app_key={self.app_key[:8]}...")
        return True

    def _start_stream_listener(self) -> None:
        """
        Start Stream mode listener using DingTalk official SDK.

        Uses dingtalk-stream SDK for reliable long connection.
        """
        if self._stream_thread and self._stream_thread.is_alive():
            return

        self._stream_running = True

        def stream_loop():
            """Stream event loop using official DingTalk SDK."""
            self._log("[stream] Event listener starting...")

            # Try to import official SDK
            try:
                import dingtalk_stream
                from dingtalk_stream import AckMessage
            except ImportError:
                self._log("[stream] dingtalk-stream not installed")
                self._log("[stream] Install with: pip install dingtalk-stream")
                while self._stream_running:
                    time.sleep(5.0)
                return

            # Create handler class that references self
            adapter = self

            class CCCCChatbotHandler(dingtalk_stream.ChatbotHandler):
                """Handler for incoming chatbot messages."""

                async def process(self, callback: dingtalk_stream.CallbackMessage):
                    """Process incoming message from DingTalk."""
                    try:
                        adapter._log("[stream] Received message callback")

                        # callback.data is a dict, not an object
                        data = callback.data
                        adapter._log(f"[stream] Message data: {data}")

                        # Build event dict for _enqueue_message
                        event = {
                            "msgtype": data.get('msgtype', 'text'),
                            "conversationId": data.get('conversationId', ''),
                            "conversationType": data.get('conversationType', ''),
                            "senderId": data.get('senderId', ''),
                            "senderStaffId": data.get('senderStaffId', ''),
                            "senderNick": data.get('senderNick', ''),
                            "msgId": data.get('msgId', ''),
                            "isAdmin": data.get('isAdmin', False),
                            "chatbotUserId": data.get('chatbotUserId', ''),
                            "conversationTitle": data.get('conversationTitle', ''),
                            "sessionWebhook": data.get('sessionWebhook', ''),
                            "sessionWebhookExpiredTime": data.get('sessionWebhookExpiredTime', 0),
                        }

                        # Extract text content (text is also a dict)
                        text_data = data.get('text', {})
                        if text_data:
                            event["text"] = {"content": text_data.get('content', '')}
                            adapter._log(f"[stream] Text: {event['text']}")

                        # Enqueue the message
                        adapter._enqueue_message(event)
                        adapter._log("[stream] Message enqueued successfully")

                        return AckMessage.STATUS_OK, 'OK'

                    except Exception as e:
                        adapter._log(f"[stream] Handler error: {e}")
                        import traceback
                        adapter._log(f"[stream] Traceback: {traceback.format_exc()}")
                        return AckMessage.STATUS_SYSTEM_EXCEPTION, str(e)

            try:
                # Create credential and client
                credential = dingtalk_stream.Credential(self.app_key, self.app_secret)
                self._stream_client = dingtalk_stream.DingTalkStreamClient(credential)

                # Register chatbot handler
                self._stream_client.register_callback_handler(
                    dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
                    CCCCChatbotHandler()
                )

                self._log("[stream] Starting DingTalk Stream client...")

                # start_forever() is blocking
                self._stream_client.start_forever()

            except Exception as e:
                self._log(f"[stream] SDK error: {e}")
                import traceback
                self._log(f"[stream] Traceback: {traceback.format_exc()}")

            self._log("[stream] Event listener stopped")

        self._stream_thread = threading.Thread(target=stream_loop, daemon=True)
        self._stream_thread.start()

    def disconnect(self) -> None:
        """Disconnect from DingTalk."""
        self._connected = False
        self._stream_running = False

        # Stop SDK client if running
        if hasattr(self, '_stream_client') and self._stream_client:
            try:
                # DingTalk SDK doesn't have a clean stop method
                # The thread will exit when _stream_running is False
                pass
            except Exception:
                pass

        if self._stream_thread:
            self._stream_thread.join(timeout=2.0)
        self._log("[disconnect] Disconnected")

    def poll(self) -> List[Dict[str, Any]]:
        """
        Poll for new messages.

        Returns queued messages from Stream listener.
        """
        if not self._connected:
            return []

        with self._queue_lock:
            messages = self._message_queue.copy()
            self._message_queue.clear()

        return messages

    def _enqueue_message(self, event: Dict[str, Any]) -> None:
        """
        Process incoming event and enqueue normalized message.

        Called by Stream listener or webhook handler.
        """
        try:
            # DingTalk event structure varies by type
            # Robot callback format
            msg_type = event.get("msgtype", "")
            conversation_id = event.get("conversationId", "")
            sender_id = event.get("senderStaffId", "") or event.get("senderId", "")
            sender_nick = event.get("senderNick", "user")
            msg_id = event.get("msgId", "")

            # Extract text based on message type
            if msg_type == "text":
                content = event.get("text", {})
                text = content.get("content", "")
            elif msg_type == "richText":
                text = self._extract_rich_text(event.get("richText", []))
            elif msg_type == "picture":
                text = "[image]"
            elif msg_type == "file":
                text = f"[file: {event.get('fileName', 'unknown')}]"
            else:
                text = f"[{msg_type}]"

            if not text.strip():
                return

            # Build attachments list
            attachments: List[Dict[str, Any]] = []
            if msg_type == "picture":
                attachments.append({
                    "provider": "dingtalk",
                    "kind": "image",
                    "download_code": event.get("downloadCode", ""),
                    "file_name": "image.png",
                })
            elif msg_type == "file":
                attachments.append({
                    "provider": "dingtalk",
                    "kind": "file",
                    "download_code": event.get("downloadCode", ""),
                    "file_name": event.get("fileName", "file"),
                })

            # Determine chat type
            conversation_type = event.get("conversationType", "")
            if conversation_type == "1":
                chat_type = "p2p"
            elif conversation_type == "2":
                chat_type = "group"
            else:
                chat_type = "unknown"

            # Get chat title (use from event if available, else API)
            chat_title = event.get("conversationTitle", "")
            if not chat_title:
                chat_title = self._get_conversation_title_cached(conversation_id)

            # Cache sessionWebhook for this conversation (for replying)
            session_webhook = event.get("sessionWebhook", "")
            session_expires = event.get("sessionWebhookExpiredTime", 0)
            if session_webhook and conversation_id:
                # Convert ms to seconds
                expires_at = session_expires / 1000.0 if session_expires > 1e10 else float(session_expires)
                self._session_webhook_cache[conversation_id] = (session_webhook, expires_at)
                self._log(f"[webhook] Cached: id={conversation_id}, expires_raw={session_expires}, expires_at={expires_at:.0f}")

            # Normalize message
            normalized = {
                "chat_id": conversation_id,
                "chat_title": chat_title,
                "chat_type": chat_type,
                "thread_id": 0,  # DingTalk doesn't have threading like this
                "text": text,
                "attachments": attachments,
                "from_user": sender_nick or sender_id,
                "message_id": msg_id,
                # Keep sessionWebhook for potential reply use
                "_session_webhook": session_webhook,
            }

            with self._queue_lock:
                self._message_queue.append(normalized)

        except Exception as e:
            self._log(f"[enqueue] Error: {e}")

    def _extract_rich_text(self, rich_text: List[Dict[str, Any]]) -> str:
        """Extract plain text from rich text content."""
        texts = []
        try:
            for item in rich_text:
                if item.get("text"):
                    texts.append(item["text"])
        except Exception:
            pass
        return " ".join(texts)

    def _get_conversation_title_cached(self, conversation_id: str) -> str:
        """Get conversation title with caching."""
        if conversation_id in self._conversation_cache:
            return self._conversation_cache[conversation_id]

        title = self.get_chat_title(conversation_id)
        self._conversation_cache[conversation_id] = title
        return title

    def _send_via_webhook(self, webhook_url: str, text: str) -> bool:
        """Send message via sessionWebhook (most reliable for groups)."""
        body = {
            "msgtype": "text",
            "text": {
                "content": text
            }
        }
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')

        req = urllib.request.Request(webhook_url, data=data, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode('utf-8', errors='replace'))
                if result.get('errcode') == 0:
                    self._log(f"[webhook] Sent successfully")
                    return True
                self._log(f"[webhook] Failed: {result}")
                return False
        except Exception as e:
            self._log(f"[webhook] Error: {e}")
            return False

    def send_message(self, chat_id: str, text: str, thread_id: Optional[int] = None) -> bool:
        """
        Send a text message to a conversation.

        Args:
            chat_id: DingTalk conversationId
            text: Message text
            thread_id: Unused (DingTalk doesn't support threading)
        """
        _ = thread_id  # DingTalk doesn't support message threading

        if not text:
            return True

        if not self._connected:
            return False

        # Ensure message fits limit
        safe_text = self._compose_safe(text)

        # Rate limit
        self._rate_limiter.wait_and_acquire(chat_id)

        # Try sessionWebhook first (most reliable for group messages)
        self._log(f"[send] chat_id={chat_id}, cache_keys={list(self._session_webhook_cache.keys())}")
        if chat_id in self._session_webhook_cache:
            webhook_url, expires_at = self._session_webhook_cache[chat_id]
            current_time = time.time()
            self._log(f"[send] Found in cache: expires_at={expires_at:.0f}, current={current_time:.0f}, delta={expires_at - current_time:.0f}s")
            if current_time < expires_at:
                self._log(f"[send] Using cached webhook for {chat_id[:20]}...")
                if self._send_via_webhook(webhook_url, safe_text):
                    return True
                self._log("[send] Webhook failed, falling back to API...")
            else:
                # Webhook expired, remove from cache
                del self._session_webhook_cache[chat_id]
                self._log(f"[send] Webhook expired for {chat_id[:20]}...")
        else:
            self._log(f"[send] No cached webhook for {chat_id[:20]}...")

        # Use robot message API
        body: Dict[str, Any] = {
            "robotCode": self.robot_code,
            "msgKey": "sampleText",
            "msgParam": json.dumps({"content": safe_text}, ensure_ascii=False),
        }

        # Determine if group or 1:1
        if chat_id.startswith("cid"):
            # Group conversation
            body["openConversationId"] = chat_id
            endpoint = "/v1.0/robot/groupMessages/send"
        else:
            # 1:1 conversation - need user ID
            body["userIds"] = [chat_id]
            endpoint = "/v1.0/robot/oToMessages/batchSend"

        resp = self._api_new("POST", endpoint, body)

        if resp.get("processQueryKey") or resp.get("sendResults"):
            return True

        # Try alternative API for older bots
        if "code" in resp or "errcode" in resp:
            return self._send_message_legacy(chat_id, safe_text)

        self._log(f"[send] Failed to chat {chat_id}: {resp}")
        return False

    def _send_message_legacy(self, chat_id: str, text: str) -> bool:
        """Send message using legacy API (for older bot types)."""
        body = {
            "chatid": chat_id,
            "msg": {
                "msgtype": "text",
                "text": {
                    "content": text,
                },
            },
        }

        resp = self._api_old("POST", "/chat/send", body)

        if resp.get("errcode") == 0:
            return True

        self._log(f"[send_legacy] Failed: {resp.get('errmsg', 'unknown')}")
        return False

    def _compose_safe(self, text: str) -> str:
        """Ensure message fits within DingTalk limits."""
        summarized = self.summarize(text, self.max_chars, self.max_lines)

        if len(summarized) > DINGTALK_MAX_MESSAGE_LENGTH:
            summarized = summarized[: DINGTALK_MAX_MESSAGE_LENGTH - 1] + "..."

        return summarized

    def get_chat_title(self, chat_id: str) -> str:
        """Get conversation title via API."""
        # Try new API first
        resp = self._api_new("GET", f"/v1.0/im/conversations/{chat_id}")

        if resp.get("title"):
            return resp["title"]

        # Try legacy API
        resp = self._api_old("GET", "/chat/get", {"chatid": chat_id})

        if resp.get("errcode") == 0:
            return resp.get("name", chat_id)

        return chat_id

    def download_attachment(self, attachment: Dict[str, Any]) -> bytes:
        """Download an attachment from DingTalk."""
        download_code = attachment.get("download_code", "")
        if not download_code:
            raise ValueError("Missing download_code")

        token = self._get_token()
        if not token:
            raise ValueError("No valid token")

        # Get download URL
        resp = self._api_new("POST", "/v1.0/robot/messageFiles/download", {
            "downloadCode": download_code,
            "robotCode": self.robot_code,
        })

        download_url = resp.get("downloadUrl", "")
        if not download_url:
            raise ValueError(f"Failed to get download URL: {resp}")

        # Download file
        req = urllib.request.Request(download_url, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception as e:
            raise ValueError(f"Download failed: {e}")

    def send_file(
        self,
        chat_id: str,
        *,
        file_path: Path,
        filename: str,
        caption: str = "",
        thread_id: Optional[int] = None,
    ) -> bool:
        """Send a file to a conversation."""
        _ = thread_id  # DingTalk doesn't support threading

        if not self._connected:
            return False

        token = self._get_token()
        if not token:
            return False

        # Rate limit
        self._rate_limiter.wait_and_acquire(chat_id)

        try:
            raw = file_path.read_bytes()
        except Exception as e:
            self._log(f"[send_file] Read failed: {e}")
            return False

        # Step 1: Upload file to get media_id
        boundary = "----cccc" + uuid.uuid4().hex
        upload_url = f"{DINGTALK_API_OLD}/media/upload"
        upload_url = f"{upload_url}?access_token={token}&type=file"

        safe_fn = (filename or file_path.name or "file").replace("\\", "_").replace("/", "_")

        # Build multipart form data
        body = b""
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="media"; filename="{safe_fn}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        body += raw
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(upload_url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8", errors="replace"))

            if result.get("errcode") != 0:
                self._log(f"[send_file] Upload failed: {result.get('errmsg', 'unknown')}")
                return False

            media_id = result.get("media_id", "")
            if not media_id:
                self._log("[send_file] No media_id in response")
                return False

        except Exception as e:
            self._log(f"[send_file] Upload error: {e}")
            return False

        # Step 2: Send file message
        msg_body: Dict[str, Any] = {
            "chatid": chat_id,
            "msg": {
                "msgtype": "file",
                "file": {
                    "media_id": media_id,
                },
            },
        }

        resp = self._api_old("POST", "/chat/send", msg_body)

        if resp.get("errcode") == 0:
            # Send caption as separate message if provided
            if caption:
                self.send_message(chat_id, caption)
            return True

        self._log(f"[send_file] Send failed: {resp.get('errmsg', 'unknown')}")
        return False

    def format_outbound(self, by: str, to: List[str], text: str, is_system: bool = False) -> str:
        """Format message for DingTalk display."""
        formatted = super().format_outbound(by, to, text, is_system)
        return self._compose_safe(formatted)

    # ===== Webhook Event Handling =====

    def handle_webhook_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Handle incoming webhook event from DingTalk.

        This method should be called by an external HTTP server
        when receiving events from DingTalk webhook/callback.
        """
        self._enqueue_message(event)
        return None

    def verify_callback_signature(
        self,
        timestamp: str,
        nonce: str,
        signature: str,
    ) -> bool:
        """
        Verify DingTalk callback signature.

        Used for webhook callback security validation.
        """
        try:
            # Sort and concatenate
            sign_str = f"{timestamp}\n{nonce}\n{self.app_secret}"

            # HMAC-SHA256
            hmac_code = hmac.new(
                self.app_secret.encode("utf-8"),
                sign_str.encode("utf-8"),
                hashlib.sha256,
            ).digest()

            # Base64 encode
            computed = base64.b64encode(hmac_code).decode("utf-8")

            return computed == signature
        except Exception:
            return False
