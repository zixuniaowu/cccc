from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class TestCodexAppFlow(unittest.TestCase):
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

    def _call(self, op: str, args: dict):
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        return handle_request(DaemonRequest.model_validate({"op": op, "args": args}))

    def test_actor_list_uses_codex_supervisor_state_for_headless_working(self) -> None:
        from cccc.daemon.actors.actor_ops import handle_actor_list

        _, cleanup = self._with_home()
        try:
            create_resp, _ = self._call("group_create", {"title": "codex-actor-list", "topic": "", "by": "user"})
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            add_resp, _ = self._call(
                "actor_add",
                {
                    "group_id": group_id,
                    "actor_id": "peer1",
                    "title": "Peer 1",
                    "runtime": "codex",
                    "runner": "headless",
                    "by": "user",
                },
            )
            self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

            with (
                patch("cccc.daemon.actors.actor_ops.codex_app_supervisor.actor_running", return_value=True),
                patch(
                    "cccc.daemon.actors.actor_ops.codex_app_supervisor.get_state",
                    return_value={
                        "group_id": group_id,
                        "actor_id": "peer1",
                        "status": "working",
                        "current_task_id": "turn-123",
                        "updated_at": "2026-04-02T10:00:00Z",
                    },
                ),
            ):
                resp = handle_actor_list({"group_id": group_id, "include_unread": False}, effective_runner_kind=lambda runner: str(runner or "pty"))

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            actors = (resp.result or {}).get("actors") if isinstance(resp.result, dict) else []
            self.assertIsInstance(actors, list)
            assert isinstance(actors, list)
            self.assertEqual(len(actors), 1)
            actor = actors[0]
            self.assertEqual(actor.get("runner"), "headless")
            self.assertEqual(actor.get("runner_effective"), "headless")
            self.assertTrue(bool(actor.get("running")))
            self.assertEqual(actor.get("effective_working_state"), "working")
            self.assertEqual(actor.get("effective_working_reason"), "headless_working")
            self.assertEqual(actor.get("effective_active_task_id"), "turn-123")
        finally:
            cleanup()

    def test_send_routes_running_headless_codex_actor_to_app_supervisor(self) -> None:
        from cccc.daemon.messaging.chat_ops import handle_send

        _, cleanup = self._with_home()
        try:
            create_resp, _ = self._call("group_create", {"title": "codex-send", "topic": "", "by": "user"})
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            add_resp, _ = self._call(
                "actor_add",
                {
                    "group_id": group_id,
                    "actor_id": "peer1",
                    "title": "Peer 1",
                    "runtime": "codex",
                    "runner": "headless",
                    "by": "user",
                },
            )
            self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

            with (
                patch("cccc.daemon.messaging.chat_ops.codex_app_supervisor.actor_running", return_value=True),
                patch("cccc.daemon.messaging.chat_ops.codex_app_supervisor.submit_user_message") as submit_user_message,
                patch("cccc.daemon.messaging.chat_ops.queue_chat_message") as queue_chat_message,
                patch("cccc.daemon.messaging.chat_ops.request_flush_pending_messages") as request_flush_pending_messages,
                patch("cccc.daemon.messaging.chat_ops.flush_pending_messages"),
                patch("cccc.daemon.messaging.chat_ops.get_headless_targets_for_message", return_value=[]),
            ):
                resp = handle_send(
                    {
                        "group_id": group_id,
                        "by": "user",
                        "text": "hello codex",
                        "to": ["peer1"],
                        "client_id": "c1",
                    },
                    coerce_bool=lambda value: bool(value),
                    normalize_attachments=lambda _group, _attachments: [],
                    effective_runner_kind=lambda runner: str(runner or "pty"),
                    auto_wake_recipients=lambda _group, _to, _by: [],
                    automation_on_resume=lambda _group: None,
                    automation_on_new_message=lambda _group: None,
                    clear_pending_system_notifies=lambda _group_id, _reasons: None,
                )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            submit_user_message.assert_called_once()
            queue_chat_message.assert_not_called()
            request_flush_pending_messages.assert_not_called()
        finally:
            cleanup()

    def test_send_routes_running_pty_codex_actor_to_pty_delivery(self) -> None:
        from cccc.daemon.messaging.chat_ops import handle_send

        _, cleanup = self._with_home()
        try:
            create_resp, _ = self._call("group_create", {"title": "codex-send-pty", "topic": "", "by": "user"})
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            add_resp, _ = self._call(
                "actor_add",
                {
                    "group_id": group_id,
                    "actor_id": "peer1",
                    "title": "Peer 1",
                    "runtime": "codex",
                    "runner": "pty",
                    "by": "user",
                },
            )
            self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

            with (
                patch("cccc.daemon.messaging.chat_ops.codex_app_supervisor.actor_running", return_value=True),
                patch("cccc.daemon.messaging.chat_ops.codex_app_supervisor.submit_user_message") as submit_user_message,
                patch("cccc.daemon.messaging.chat_ops.queue_chat_message") as queue_chat_message,
                patch("cccc.daemon.messaging.chat_ops.request_flush_pending_messages") as request_flush_pending_messages,
                patch("cccc.daemon.messaging.chat_ops.flush_pending_messages"),
                patch("cccc.daemon.messaging.chat_ops.get_headless_targets_for_message", return_value=[]),
            ):
                resp = handle_send(
                    {
                        "group_id": group_id,
                        "by": "user",
                        "text": "hello codex",
                        "to": ["peer1"],
                        "client_id": "c1",
                    },
                    coerce_bool=lambda value: bool(value),
                    normalize_attachments=lambda _group, _attachments: [],
                    effective_runner_kind=lambda runner: str(runner or "pty"),
                    auto_wake_recipients=lambda _group, _to, _by: [],
                    automation_on_resume=lambda _group: None,
                    automation_on_new_message=lambda _group: None,
                    clear_pending_system_notifies=lambda _group_id, _reasons: None,
                )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            submit_user_message.assert_not_called()
            queue_chat_message.assert_called_once()
            request_flush_pending_messages.assert_called_once()
        finally:
            cleanup()

    def test_reply_routes_running_pty_codex_actor_to_pty_delivery(self) -> None:
        from cccc.daemon.messaging.chat_ops import handle_reply

        _, cleanup = self._with_home()
        try:
            create_resp, _ = self._call("group_create", {"title": "codex-reply-pty", "topic": "", "by": "user"})
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            add_resp, _ = self._call(
                "actor_add",
                {
                    "group_id": group_id,
                    "actor_id": "peer1",
                    "title": "Peer 1",
                    "runtime": "codex",
                    "runner": "pty",
                    "by": "user",
                },
            )
            self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

            send_resp, _ = self._call(
                "send",
                {
                    "group_id": group_id,
                    "by": "user",
                    "text": "hello codex",
                    "to": ["peer1"],
                },
            )
            self.assertTrue(send_resp.ok, getattr(send_resp, "error", None))
            original_event = (send_resp.result or {}).get("event") if isinstance(send_resp.result, dict) else {}
            self.assertIsInstance(original_event, dict)
            reply_to = str((original_event or {}).get("id") or "").strip()
            self.assertTrue(reply_to)

            with (
                patch("cccc.daemon.messaging.chat_ops.codex_app_supervisor.actor_running", return_value=True),
                patch("cccc.daemon.messaging.chat_ops.codex_app_supervisor.submit_user_message") as submit_user_message,
                patch("cccc.daemon.messaging.chat_ops.queue_chat_message") as queue_chat_message,
                patch("cccc.daemon.messaging.chat_ops.request_flush_pending_messages") as request_flush_pending_messages,
                patch("cccc.daemon.messaging.chat_ops.flush_pending_messages"),
                patch("cccc.daemon.messaging.chat_ops.get_headless_targets_for_message", return_value=[]),
            ):
                resp = handle_reply(
                    {
                        "group_id": group_id,
                        "by": "user",
                        "text": "reply codex",
                        "reply_to": reply_to,
                        "to": ["peer1"],
                    },
                    coerce_bool=lambda value: bool(value),
                    normalize_attachments=lambda _group, _attachments: [],
                    effective_runner_kind=lambda runner: str(runner or "pty"),
                    auto_wake_recipients=lambda _group, _to, _by: [],
                    automation_on_resume=lambda _group: None,
                    automation_on_new_message=lambda _group: None,
                    clear_pending_system_notifies=lambda _group_id, _reasons: None,
                )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            submit_user_message.assert_not_called()
            queue_chat_message.assert_called_once()
            request_flush_pending_messages.assert_called_once()
        finally:
            cleanup()

    def test_codex_notifications_write_stream_events_and_single_final_message(self) -> None:
        from cccc.daemon.codex_app_sessions import CodexAppSession
        from cccc.kernel.codex_events import codex_events_path
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        home, cleanup = self._with_home()
        try:
            reg = load_registry()
            group = create_group(reg, title="codex-session", topic="")
            loaded_group = load_group(group.group_id)
            self.assertIsNotNone(loaded_group)
            assert loaded_group is not None

            session = CodexAppSession(
                group_id=group.group_id,
                actor_id="peer1",
                cwd=Path(home),
                env={},
            )
            session._active_event_id = "evt-1"

            session._handle_notification("turn/started", {"turn": {"id": "turn-1"}})
            session._handle_notification(
                "item/started",
                {"turnId": "turn-1", "item": {"type": "agentMessage", "id": "commentary-1", "phase": "commentary"}},
            )
            session._handle_notification("item/agentMessage/delta", {"turnId": "turn-1", "itemId": "commentary-1", "delta": "Inspecting"})
            session._handle_notification(
                "item/completed",
                {"turnId": "turn-1", "item": {"type": "agentMessage", "id": "commentary-1", "phase": "commentary", "text": "Inspecting"}},
            )
            session._handle_notification("turn/plan/updated", {"turnId": "turn-1", "plan": [{"step": "Inspect src/app.ts", "status": "in_progress"}]})
            session._handle_notification("item/started", {"turnId": "turn-1", "item": {"type": "reasoning", "id": "rs-1"}})
            session._handle_notification("item/reasoning/summaryTextDelta", {"turnId": "turn-1", "itemId": "rs-1", "delta": "Inspecting state flow"})
            session._handle_notification(
                "item/started",
                {"turnId": "turn-1", "item": {"type": "commandExecution", "id": "cmd-1", "command": "npm run typecheck", "commandActions": [], "cwd": "/tmp", "status": "in_progress"}},
            )
            session._handle_notification("item/commandExecution/outputDelta", {"turnId": "turn-1", "itemId": "cmd-1", "delta": "typecheck started"})
            session._handle_notification(
                "item/started",
                {"turnId": "turn-1", "item": {"type": "agentMessage", "id": "msg-1", "phase": "final_answer"}},
            )
            session._handle_notification("item/agentMessage/delta", {"turnId": "turn-1", "itemId": "msg-1", "delta": "Hel"})
            session._handle_notification("item/agentMessage/delta", {"turnId": "turn-1", "itemId": "msg-1", "delta": "lo"})
            session._handle_notification(
                "item/completed",
                {"turnId": "turn-1", "item": {"type": "commandExecution", "id": "cmd-1", "command": "npm run typecheck", "commandActions": [], "cwd": "/tmp", "status": "completed"}},
            )
            session._handle_notification(
                "item/completed",
                {"turnId": "turn-1", "item": {"type": "agentMessage", "id": "msg-1", "phase": "final_answer", "text": "Hello"}},
            )
            session._handle_notification(
                "item/completed",
                {"turnId": "turn-1", "item": {"type": "agentMessage", "id": "msg-1", "phase": "final_answer", "text": "Hello"}},
            )
            session._handle_notification("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}})

            events_path = codex_events_path(loaded_group.path)
            self.assertTrue(events_path.exists())
            codex_events = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            event_types = [str(item.get("type") or "") for item in codex_events]
            self.assertIn("codex.turn.progress", event_types)
            self.assertIn("codex.activity.started", event_types)
            self.assertIn("codex.activity.updated", event_types)
            self.assertIn("codex.activity.completed", event_types)
            self.assertIn("codex.message.started", event_types)
            self.assertIn("codex.message.delta", event_types)
            self.assertIn("codex.message.completed", event_types)
            self.assertIn("codex.turn.completed", event_types)
            commentary_started = next(
                item for item in codex_events
                if str(item.get("type") or "") == "codex.message.started"
                and str(((item.get("data") or {}).get("phase") or "")) == "commentary"
            )
            self.assertEqual(str(((commentary_started.get("data") or {}).get("stream_id") or "")), "commentary-1")
            commentary_completed = next(
                item for item in codex_events
                if str(item.get("type") or "") == "codex.message.completed"
                and str(((item.get("data") or {}).get("phase") or "")) == "commentary"
            )
            self.assertEqual(str(((commentary_completed.get("data") or {}).get("text") or "")), "Inspecting")
            message_started = next(
                item for item in codex_events
                if str(item.get("type") or "") == "codex.message.started"
                and str(((item.get("data") or {}).get("phase") or "")) == "final_answer"
            )
            started_data = message_started.get("data") if isinstance(message_started.get("data"), dict) else {}
            self.assertEqual(str(started_data.get("event_id") or ""), "evt-1")
            activity_summaries = [
                str((item.get("data") or {}).get("summary") or "")
                for item in codex_events
                if str(item.get("type") or "").startswith("codex.activity.")
            ]
            self.assertIn("Inspect src/app.ts", activity_summaries)
            self.assertIn("Inspecting state flow", activity_summaries)
            self.assertIn("npm run typecheck", activity_summaries)
            self.assertIn("reply ready", activity_summaries)

            ledger_events = [
                json.loads(line)
                for line in loaded_group.ledger_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            chat_messages = [event for event in ledger_events if str(event.get("kind") or "") == "chat.message"]
            self.assertEqual(len(chat_messages), 1)
            message = chat_messages[0]
            self.assertEqual(str(message.get("by") or ""), "peer1")
            data = message.get("data") if isinstance(message.get("data"), dict) else {}
            self.assertEqual(str(data.get("text") or ""), "Hello")
            self.assertEqual(str(data.get("stream_id") or ""), "msg-1")
            self.assertEqual(str(data.get("pending_event_id") or ""), "evt-1")
            self.assertEqual(data.get("to"), ["user"])
        finally:
            cleanup()

    def test_codex_notifications_keep_streaming_when_agent_message_phase_missing(self) -> None:
        from cccc.daemon.codex_app_sessions import CodexAppSession
        from cccc.kernel.codex_events import codex_events_path
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        home, cleanup = self._with_home()
        try:
            reg = load_registry()
            group = create_group(reg, title="codex-session-phase-fallback", topic="")
            loaded_group = load_group(group.group_id)
            self.assertIsNotNone(loaded_group)
            assert loaded_group is not None

            session = CodexAppSession(
                group_id=group.group_id,
                actor_id="peer1",
                cwd=Path(home),
                env={},
            )
            session._active_event_id = "evt-fallback"

            session._handle_notification("turn/started", {"turn": {"id": "turn-fallback"}})
            session._handle_notification(
                "item/started",
                {"turnId": "turn-fallback", "item": {"type": "agentMessage", "id": "msg-fallback"}},
            )
            session._handle_notification(
                "item/agentMessage/delta",
                {"turnId": "turn-fallback", "itemId": "msg-fallback", "delta": "Hel"},
            )
            session._handle_notification(
                "item/agentMessage/delta",
                {"turnId": "turn-fallback", "itemId": "msg-fallback", "delta": "lo"},
            )
            session._handle_notification(
                "item/completed",
                {
                    "turnId": "turn-fallback",
                    "item": {"type": "agentMessage", "id": "msg-fallback", "text": "Hello"},
                },
            )
            session._handle_notification("turn/completed", {"turn": {"id": "turn-fallback", "status": "completed"}})

            events_path = codex_events_path(loaded_group.path)
            self.assertTrue(events_path.exists())
            codex_events = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            started = next(
                item for item in codex_events
                if str(item.get("type") or "") == "codex.message.started"
                and str(((item.get("data") or {}).get("stream_id") or "")) == "msg-fallback"
            )
            started_data = started.get("data") if isinstance(started.get("data"), dict) else {}
            self.assertEqual(str(started_data.get("phase") or ""), "")

            deltas = [
                item for item in codex_events
                if str(item.get("type") or "") == "codex.message.delta"
                and str(((item.get("data") or {}).get("stream_id") or "")) == "msg-fallback"
            ]
            self.assertEqual([str((item.get("data") or {}).get("delta") or "") for item in deltas], ["Hel", "lo"])

            completed = next(
                item for item in codex_events
                if str(item.get("type") or "") == "codex.message.completed"
                and str(((item.get("data") or {}).get("stream_id") or "")) == "msg-fallback"
            )
            completed_data = completed.get("data") if isinstance(completed.get("data"), dict) else {}
            self.assertEqual(str(completed_data.get("text") or ""), "Hello")
            self.assertEqual(str(completed_data.get("phase") or ""), "")

            ledger_events = [
                json.loads(line)
                for line in loaded_group.ledger_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            chat_messages = [event for event in ledger_events if str(event.get("kind") or "") == "chat.message"]
            self.assertEqual(len(chat_messages), 1)
            message = chat_messages[0]
            data = message.get("data") if isinstance(message.get("data"), dict) else {}
            self.assertEqual(str(data.get("text") or ""), "Hello")
            self.assertEqual(str(data.get("stream_id") or ""), "msg-fallback")
            self.assertEqual(str(data.get("pending_event_id") or ""), "evt-fallback")
        finally:
            cleanup()

    def test_actor_activity_thread_emits_running_headless_codex_actor(self) -> None:
        from cccc.daemon.serve_ops import start_actor_activity_thread
        from cccc.kernel.actors import add_actor
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        home, cleanup = self._with_home()
        try:
            reg = load_registry()
            created = create_group(reg, title="codex-activity", topic="")
            group = load_group(created.group_id)
            self.assertIsNotNone(group)
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="codex", runner="headless")  # type: ignore[arg-type]
            group.save()  # type: ignore[union-attr]

            published: list[dict] = []

            class _Broadcaster:
                def publish(self, event: dict) -> None:
                    published.append(event)

            stop_event = threading.Event()
            thread = start_actor_activity_thread(
                stop_event=stop_event,
                home=Path(home),
                pty_supervisor=object(),
                headless_supervisor=object(),
                codex_supervisor=type(
                    "_CodexSupervisor",
                    (),
                    {
                        "get_state": staticmethod(lambda group_id, actor_id: {
                            "group_id": group_id,
                            "actor_id": actor_id,
                            "status": "working",
                            "current_task_id": "turn-1",
                            "updated_at": "2026-04-02T10:00:00Z",
                        }),
                        "actor_running": staticmethod(lambda _group_id, _actor_id: True),
                    },
                )(),
                event_broadcaster=_Broadcaster(),
                load_group=load_group,
                interval_seconds=1.0,
            )
            time.sleep(0.15)
            stop_event.set()
            thread.join(timeout=1.0)

            actor_events = [event for event in published if str(event.get("kind") or "") == "actor.activity"]
            self.assertTrue(actor_events)
            actors = ((actor_events[-1].get("data") or {}).get("actors") or [])
            self.assertEqual(len(actors), 1)
            self.assertEqual(str(actors[0].get("id") or ""), "peer1")
            self.assertTrue(bool(actors[0].get("running")))
            self.assertEqual(str(actors[0].get("effective_working_state") or ""), "working")
        finally:
            cleanup()

    def test_codex_session_persists_headless_state_file(self) -> None:
        from cccc.daemon.codex_app_sessions import CodexAppSession
        from cccc.daemon.runner_state_ops import headless_state_path

        home, cleanup = self._with_home()
        try:
            session = CodexAppSession(
                group_id="g_test",
                actor_id="peer1",
                cwd=Path(home),
                env={},
            )

            class _Proc:
                pid = os.getpid()

                @staticmethod
                def poll():
                    return None

            session._proc = _Proc()
            session._running = True
            session._session_state.status = "working"
            session._session_state.current_task_id = "turn-1"
            session._persist_state()

            state_path = headless_state_path("g_test", "peer1")
            self.assertTrue(state_path.exists())
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("runtime") or ""), "codex")
            self.assertEqual(int(payload.get("pid") or 0), os.getpid())
            self.assertEqual(str(payload.get("status") or ""), "working")
            self.assertEqual(str(payload.get("current_task_id") or ""), "turn-1")
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
