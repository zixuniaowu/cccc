"""Tests for T197 reviewer error fixes (E1-E4).

Test coverage:
- E1: Multi-subscriber fan-out (2+ chat all receive update/end)
- E2: Throttle cross-call survival (same client instance)
- E3: chat.stream.to filtering (non-target subscriber doesn't receive)
- E4: begin_stream returns None → final chat.message(stream_id=...) still delivered
"""

import os
import tempfile
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from cccc.ports.im.adapters.base import IMAdapter, OutboundStreamHandle


class MultiSubStreamAdapter(IMAdapter):
    """Adapter that records all streaming calls for fan-out verification."""

    platform = "test"

    def __init__(self):
        self.begin_calls: List[Dict[str, Any]] = []
        self.update_calls: List[Dict[str, Any]] = []
        self.end_calls: List[Dict[str, Any]] = []

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def poll(self) -> list:
        return []

    def send_message(self, chat_id: str, text: str, thread_id: Optional[int] = None) -> bool:
        return True

    def get_chat_title(self, chat_id: str) -> str:
        return "test"

    def begin_stream(self, chat_id: str, stream_id: str, *, text: str = "", thread_id: Optional[int] = None) -> Optional[OutboundStreamHandle]:
        handle: OutboundStreamHandle = {
            "stream_id": stream_id,
            "platform_handle": f"ph_{chat_id}_{stream_id}",
        }
        self.begin_calls.append({"chat_id": chat_id, "stream_id": stream_id, "thread_id": thread_id})
        return handle

    def update_stream(self, handle: OutboundStreamHandle, *, text: str = "", seq: int = 0) -> bool:
        self.update_calls.append({"handle": handle, "text": text, "seq": seq})
        return True

    def end_stream(self, handle: OutboundStreamHandle, *, text: str = "") -> bool:
        self.end_calls.append({"handle": handle, "text": text})
        return True


class PartialFailAdapter(IMAdapter):
    """Adapter where begin_stream fails for specific chat_ids."""

    platform = "test"

    def __init__(self, fail_chats: set):
        self.fail_chats = fail_chats
        self.send_calls: List[Dict[str, Any]] = []

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def poll(self) -> list:
        return []

    def send_message(self, chat_id: str, text: str, thread_id: Optional[int] = None) -> bool:
        self.send_calls.append({"chat_id": chat_id, "text": text, "thread_id": thread_id})
        return True

    def get_chat_title(self, chat_id: str) -> str:
        return "test"

    def begin_stream(self, chat_id: str, stream_id: str, *, text: str = "", thread_id: Optional[int] = None) -> Optional[OutboundStreamHandle]:
        if chat_id in self.fail_chats:
            return None
        return {"stream_id": stream_id, "platform_handle": f"ph_{chat_id}_{stream_id}"}

    def update_stream(self, handle: OutboundStreamHandle, *, text: str = "", seq: int = 0) -> bool:
        return True

    def end_stream(self, handle: OutboundStreamHandle, *, text: str = "") -> bool:
        return True


def _make_bridge(adapter, subscriber_specs):
    """Create a minimal IMBridge with N subscribers.

    subscriber_specs: list of dicts with keys chat_id, thread_id, verbose.
    """
    from cccc.kernel.group import create_group
    from cccc.kernel.registry import load_registry
    from cccc.ports.im.bridge import IMBridge

    old_home = os.environ.get("CCCC_HOME")
    td_ctx = tempfile.TemporaryDirectory()
    td = td_ctx.__enter__()
    os.environ["CCCC_HOME"] = td

    reg = load_registry()
    group = create_group(reg, title="t197-test")

    bridge = IMBridge(group, adapter)

    subs = []
    for spec in subscriber_specs:
        sub = MagicMock()
        sub.chat_id = spec["chat_id"]
        sub.thread_id = spec.get("thread_id", 0)
        sub.verbose = spec.get("verbose", True)
        subs.append(sub)

    bridge.subscribers.get_subscribed_targets = MagicMock(return_value=subs)
    bridge.key_manager.is_authorized = MagicMock(return_value=True)

    def cleanup():
        td_ctx.__exit__(None, None, None)
        if old_home is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = old_home

    return bridge, cleanup


class TestE1MultiSubscriberFanOut(unittest.TestCase):
    """E1: Multi-subscriber fan-out — 2+ chats all receive update/end."""

    def test_two_subscribers_both_receive_stream(self) -> None:
        adapter = MultiSubStreamAdapter()
        bridge, cleanup = _make_bridge(adapter, [
            {"chat_id": "chatA", "thread_id": 0, "verbose": True},
            {"chat_id": "chatB", "thread_id": 0, "verbose": True},
        ])
        try:
            # start
            bridge._forward_stream_event({
                "kind": "chat.stream", "by": "agent-1",
                "data": {"op": "start", "stream_id": "s1", "text": "hi"},
            })
            self.assertEqual(len(adapter.begin_calls), 2)
            chat_ids_started = {c["chat_id"] for c in adapter.begin_calls}
            self.assertEqual(chat_ids_started, {"chatA", "chatB"})
            self.assertIn("s1", bridge._active_streams)
            self.assertEqual(len(bridge._active_streams["s1"]), 2)

            # update — both targets should receive
            bridge._forward_stream_event({
                "kind": "chat.stream", "by": "agent-1",
                "data": {"op": "update", "stream_id": "s1", "text": "chunk", "seq": 1},
            })
            self.assertEqual(len(adapter.update_calls), 2)

            # end — both targets should receive
            bridge._forward_stream_event({
                "kind": "chat.stream", "by": "agent-1",
                "data": {"op": "end", "stream_id": "s1", "text": "final"},
            })
            self.assertEqual(len(adapter.end_calls), 2)
            # Entry kept for chat.message per-target dedup (E4)
            self.assertIn("s1", bridge._active_streams)
        finally:
            cleanup()


class TestE3ToFiltering(unittest.TestCase):
    """E3: chat.stream.to filtering — non-target subscriber doesn't receive."""

    def test_non_verbose_skips_agent_only_stream(self) -> None:
        """Non-verbose subscriber should NOT receive agent-to-agent streams."""
        adapter = MultiSubStreamAdapter()
        bridge, cleanup = _make_bridge(adapter, [
            {"chat_id": "chatV", "thread_id": 0, "verbose": True},
            {"chat_id": "chatN", "thread_id": 0, "verbose": False},
        ])
        try:
            # Stream with to=["agent-2"] (not user-facing)
            bridge._forward_stream_event({
                "kind": "chat.stream", "by": "agent-1",
                "data": {"op": "start", "stream_id": "s2", "text": "", "to": ["agent-2"]},
            })
            # Only verbose subscriber should receive begin_stream
            self.assertEqual(len(adapter.begin_calls), 1)
            self.assertEqual(adapter.begin_calls[0]["chat_id"], "chatV")

            # update — only verbose target has a handle
            bridge._forward_stream_event({
                "kind": "chat.stream", "by": "agent-1",
                "data": {"op": "update", "stream_id": "s2", "text": "x", "seq": 1, "to": ["agent-2"]},
            })
            self.assertEqual(len(adapter.update_calls), 1)

            # end
            bridge._forward_stream_event({
                "kind": "chat.stream", "by": "agent-1",
                "data": {"op": "end", "stream_id": "s2", "text": "done", "to": ["agent-2"]},
            })
            self.assertEqual(len(adapter.end_calls), 1)
        finally:
            cleanup()

    def test_user_facing_stream_reaches_all(self) -> None:
        """Stream with to=["user"] should reach both verbose and non-verbose."""
        adapter = MultiSubStreamAdapter()
        bridge, cleanup = _make_bridge(adapter, [
            {"chat_id": "chatV", "thread_id": 0, "verbose": True},
            {"chat_id": "chatN", "thread_id": 0, "verbose": False},
        ])
        try:
            bridge._forward_stream_event({
                "kind": "chat.stream", "by": "agent-1",
                "data": {"op": "start", "stream_id": "s3", "text": "", "to": ["user"]},
            })
            self.assertEqual(len(adapter.begin_calls), 2)
        finally:
            cleanup()


class TestE4PerTargetDedup(unittest.TestCase):
    """E4: begin_stream returns None for some targets → final chat.message still delivered to those."""

    def test_failed_begin_receives_plain_text_fallback(self) -> None:
        """If begin_stream returns None for chatB, chat.message(stream_id=...)
        should still be delivered to chatB but NOT chatA."""
        adapter = PartialFailAdapter(fail_chats={"chatB"})
        bridge, cleanup = _make_bridge(adapter, [
            {"chat_id": "chatA", "thread_id": 0, "verbose": True},
            {"chat_id": "chatB", "thread_id": 0, "verbose": True},
        ])
        try:
            # Start stream — chatA succeeds, chatB returns None
            bridge._forward_stream_event({
                "kind": "chat.stream", "by": "agent-1",
                "data": {"op": "start", "stream_id": "sf1", "text": ""},
            })
            # Only chatA should be cached
            self.assertIn("sf1", bridge._active_streams)
            self.assertEqual(len(bridge._active_streams["sf1"]), 1)

            # End stream for chatA
            bridge._forward_stream_event({
                "kind": "chat.stream", "by": "agent-1",
                "data": {"op": "end", "stream_id": "sf1", "text": "final"},
            })

            # Now forward the final chat.message with stream_id=sf1
            bridge._forward_event({
                "kind": "chat.message",
                "by": "agent-1",
                "data": {
                    "text": "final message",
                    "to": ["user"],
                    "stream_id": "sf1",
                    "attachments": [],
                },
            })

            # chatA had a successful stream → should be SKIPPED (already delivered via stream)
            # chatB had no stream handle → should RECEIVE the plain-text message
            chatB_sends = [c for c in adapter.send_calls if c["chat_id"] == "chatB"]
            chatA_sends = [c for c in adapter.send_calls if c["chat_id"] == "chatA"]
            self.assertEqual(len(chatB_sends), 1, "chatB should receive plain-text fallback")
            self.assertEqual(len(chatA_sends), 0, "chatA should be skipped (already streamed)")
        finally:
            cleanup()


class TestE2ThrottleCrossCallSurvival(unittest.TestCase):
    """E2: DingTalk adapter reuses the same card client (throttle state persists)."""

    def test_card_client_is_persistent(self) -> None:
        """_get_card_client() returns the same instance across multiple calls."""
        from cccc.ports.im.adapters.dingtalk import DingTalkAdapter

        adapter = DingTalkAdapter.__new__(DingTalkAdapter)
        # Manually set required attributes
        adapter._card_client = None
        adapter._access_token = "test-token"
        adapter._token_expiry = 0
        adapter.robot_code = "test-robot"

        def fake_get_token():
            return "test-token"

        adapter._get_token = fake_get_token

        client1 = adapter._get_card_client()
        client2 = adapter._get_card_client()
        self.assertIs(client1, client2, "_get_card_client must return same instance")

    def test_throttle_state_dataclass_has_no_pending_task(self) -> None:
        """_ThrottleState should not have pending_task field after E2 cleanup."""
        from cccc.ports.im.adapters.dingtalk_card import _ThrottleState

        state = _ThrottleState()
        self.assertFalse(
            hasattr(state, "pending_task"),
            "_ThrottleState should not have pending_task field",
        )


if __name__ == "__main__":
    unittest.main()
