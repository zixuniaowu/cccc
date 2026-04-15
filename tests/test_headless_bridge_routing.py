from __future__ import annotations

import os
import tempfile
import unittest


class TestHeadlessBridgeRouting(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return td, cleanup

    def test_append_headless_chat_message_skips_empty_text(self) -> None:
        from cccc.daemon.messaging.headless_bridge import append_headless_chat_message
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        _, cleanup = self._with_home()
        try:
            reg = load_registry()
            created = create_group(reg, title="codex-empty-headless-message", topic="")
            group = load_group(created.group_id)
            self.assertIsNotNone(group)
            assert group is not None

            result = append_headless_chat_message(
                group_id=created.group_id,
                actor_id="peer1",
                text="",
                stream_id="msg-empty",
                pending_event_id="evt-empty",
                reply_to="evt-empty",
            )

            self.assertIsNone(result)
        finally:
            cleanup()

    def test_append_headless_chat_message_replies_to_original_actor_not_user(self) -> None:
        from cccc.contracts.v1.message import ChatMessageData
        from cccc.daemon.messaging.headless_bridge import append_headless_chat_message
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.ledger import append_event
        from cccc.kernel.registry import load_registry

        _, cleanup = self._with_home()
        try:
            reg = load_registry()
            created = create_group(reg, title="codex-headless-reply-routing", topic="")
            group = load_group(created.group_id)
            self.assertIsNotNone(group)
            assert group is not None
            group.doc["actors"] = [
                {"id": "claude-1", "title": "Foreman", "runtime": "claude", "runner": "headless", "enabled": True},
                {"id": "peer-reviewer", "title": "Reviewer", "runtime": "codex", "runner": "headless", "enabled": True},
            ]
            group.save()

            incoming = append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group.group_id,
                scope_key=str(group.doc.get("active_scope_key") or ""),
                by="peer-reviewer",
                data=ChatMessageData(text="请帮我复核", to=["claude-1"]).model_dump(),
            )
            incoming_id = str(incoming.get("id") or "").strip()
            self.assertTrue(incoming_id)

            outgoing = append_headless_chat_message(
                group_id=group.group_id,
                actor_id="claude-1",
                text="收到，开始看。",
                stream_id="stream-1",
                pending_event_id=incoming_id,
                reply_to=incoming_id,
            )

            self.assertIsNotNone(outgoing)
            data = outgoing.get("data") if isinstance(outgoing, dict) else {}
            self.assertEqual((data or {}).get("to"), ["peer-reviewer"])
            self.assertEqual(str((data or {}).get("reply_to") or ""), incoming_id)
        finally:
            cleanup()

    def test_append_headless_chat_stream_reuses_reply_routing(self) -> None:
        from cccc.contracts.v1.message import ChatMessageData
        from cccc.daemon.messaging.headless_bridge import append_headless_chat_stream
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.ledger import append_event
        from cccc.kernel.registry import load_registry

        _, cleanup = self._with_home()
        try:
            reg = load_registry()
            created = create_group(reg, title="codex-headless-stream-routing", topic="")
            group = load_group(created.group_id)
            self.assertIsNotNone(group)
            assert group is not None
            group.doc["actors"] = [
                {"id": "claude-1", "title": "Foreman", "runtime": "claude", "runner": "headless", "enabled": True},
                {"id": "peer-reviewer", "title": "Reviewer", "runtime": "codex", "runner": "headless", "enabled": True},
            ]
            group.save()

            incoming = append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group.group_id,
                scope_key=str(group.doc.get("active_scope_key") or ""),
                by="peer-reviewer",
                data=ChatMessageData(text="请先给个结论", to=["claude-1"]).model_dump(),
            )
            incoming_id = str(incoming.get("id") or "").strip()
            self.assertTrue(incoming_id)

            stream_event = append_headless_chat_stream(
                group_id=group.group_id,
                actor_id="claude-1",
                stream_id="stream-2",
                op="start",
                text="",
                seq=0,
                reply_to=incoming_id,
            )

            self.assertIsNotNone(stream_event)
            data = stream_event.get("data") if isinstance(stream_event, dict) else {}
            self.assertEqual((data or {}).get("to"), ["peer-reviewer"])
            self.assertEqual(str((data or {}).get("reply_to") or ""), incoming_id)
        finally:
            cleanup()
