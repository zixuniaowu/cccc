from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


class TestWeixinAdapterInit(unittest.TestCase):
    def test_default_account_id(self) -> None:
        from cccc.ports.im.adapters.weixin import WeixinAdapter

        adapter = WeixinAdapter()
        self.assertEqual(adapter.account_id, "")

    def test_custom_account_id(self) -> None:
        from cccc.ports.im.adapters.weixin import WeixinAdapter

        adapter = WeixinAdapter(account_id="bot_1")
        self.assertEqual(adapter.account_id, "bot_1")


class TestWeixinAdapterPoll(unittest.TestCase):
    def test_poll_returns_queued_messages(self) -> None:
        from cccc.ports.im.adapters.weixin import WeixinAdapter

        adapter = WeixinAdapter()
        msg = {"chat_id": "u1", "text": "hi", "from_user": "u1", "message_id": "m1"}
        adapter._message_queue.append(msg)

        result = adapter.poll()
        self.assertEqual(result, [msg])
        self.assertEqual(adapter._message_queue, [])


class TestWeixinAdapterContextCache(unittest.TestCase):
    def test_persists_context_tokens(self) -> None:
        from cccc.ports.im.adapters.weixin import WeixinAdapter

        with tempfile.TemporaryDirectory() as td:
            context_path = Path(td) / "ctx.json"
            adapter = WeixinAdapter(context_cache_path=context_path)
            adapter._remember_context_token("chat-1", "ctx-token-1")

            reloaded = WeixinAdapter(context_cache_path=context_path)
            self.assertEqual(reloaded._context_tokens.get("chat-1"), "ctx-token-1")

            payload = json.loads(context_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("chat-1"), "ctx-token-1")


class TestWeixinAdapterSendMessage(unittest.TestCase):
    def test_send_message_calls_bot_send(self) -> None:
        from cccc.ports.im.adapters.weixin import WeixinAdapter

        adapter = WeixinAdapter()
        adapter._connected = True
        adapter._bot = type("Bot", (), {"send": AsyncMock()})()

        loop = asyncio.new_event_loop()
        adapter._loop = loop
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        try:
            ok = adapter.send_message("chat-1", "hello")
            self.assertTrue(ok)
            adapter._bot.send.assert_awaited_once_with("chat-1", "hello")
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            loop.close()

    def test_send_message_returns_false_when_disconnected(self) -> None:
        from cccc.ports.im.adapters.weixin import WeixinAdapter

        adapter = WeixinAdapter()
        adapter._connected = False
        ok = adapter.send_message("chat-1", "hello")
        self.assertFalse(ok)


class TestWeixinAdapterSendFile(unittest.TestCase):
    def test_send_file_sends_image_via_send_media(self) -> None:
        from cccc.ports.im.adapters.weixin import WeixinAdapter

        adapter = WeixinAdapter()
        adapter._connected = True
        adapter._bot = type(
            "Bot",
            (),
            {"send_media": AsyncMock(), "send": AsyncMock()},
        )()

        loop = asyncio.new_event_loop()
        adapter._loop = loop
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        try:
            with tempfile.TemporaryDirectory() as td:
                fp = Path(td) / "photo.jpg"
                fp.write_bytes(b"\xff\xd8\xff\xe0")

                ok = adapter.send_file("chat-1", file_path=fp, filename="photo.jpg", caption="look")

            self.assertTrue(ok)
            adapter._bot.send_media.assert_awaited_once()
            send_media_args = adapter._bot.send_media.await_args.args
            self.assertEqual(send_media_args[0], "chat-1")
            self.assertEqual(send_media_args[1], {"image": b"\xff\xd8\xff\xe0"})
            adapter._bot.send.assert_awaited_once_with("chat-1", "look")
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            loop.close()


if __name__ == "__main__":
    unittest.main()
