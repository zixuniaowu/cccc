import unittest
from argparse import Namespace
from unittest.mock import patch


class TestCliSpaceCommands(unittest.TestCase):
    def test_parser_accepts_space_commands(self) -> None:
        from cccc import cli

        parser = cli.build_parser()

        args = parser.parse_args(["space", "status"])
        self.assertEqual(args.cmd, "space")
        self.assertEqual(args.action, "status")

        args = parser.parse_args(["space", "bind", "nb_123"])
        self.assertEqual(args.action, "bind")
        self.assertEqual(args.remote_space_id, "nb_123")

        args = parser.parse_args(["space", "jobs", "retry", "spj_1"])
        self.assertEqual(args.action, "jobs")
        self.assertEqual(args.jobs_action, "retry")
        self.assertEqual(args.job_id, "spj_1")

    def test_space_bind_routes_to_group_space_bind(self) -> None:
        from cccc import cli

        calls = []

        def _fake_call_daemon(req):
            calls.append(req)
            return {"ok": True, "result": {"binding": {"status": "bound"}}}

        args = Namespace(group="g_test", provider="notebooklm", by="user", remote_space_id="nb_123")
        with patch.object(cli, "_ensure_daemon_running", return_value=True), \
             patch.object(cli, "call_daemon", side_effect=_fake_call_daemon), \
             patch.object(cli, "_print_json"):
            code = cli.cmd_space_bind(args)

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        req = calls[0]
        self.assertEqual(req.get("op"), "group_space_bind")
        self.assertEqual(req.get("args", {}).get("group_id"), "g_test")
        self.assertEqual(req.get("args", {}).get("action"), "bind")
        self.assertEqual(req.get("args", {}).get("remote_space_id"), "nb_123")

    def test_space_ingest_invalid_payload_rejected_before_daemon_call(self) -> None:
        from cccc import cli

        args = Namespace(
            group="g_test",
            provider="notebooklm",
            by="user",
            kind="context_sync",
            payload="{bad json",
            idempotency_key="",
        )
        with patch.object(cli, "call_daemon") as mock_call, \
             patch.object(cli, "_print_json") as mock_print:
            code = cli.cmd_space_ingest(args)

        self.assertEqual(code, 2)
        mock_call.assert_not_called()
        printed = mock_print.call_args[0][0] if mock_print.call_args else {}
        self.assertEqual(str((printed.get("error") or {}).get("code") or ""), "invalid_payload")

    def test_space_jobs_cancel_routes_to_group_space_jobs(self) -> None:
        from cccc import cli

        calls = []

        def _fake_call_daemon(req):
            calls.append(req)
            return {"ok": True, "result": {"job": {"state": "canceled"}}}

        args = Namespace(group="g_test", provider="notebooklm", by="user", job_id="spj_1")
        with patch.object(cli, "_ensure_daemon_running", return_value=True), \
             patch.object(cli, "call_daemon", side_effect=_fake_call_daemon), \
             patch.object(cli, "_print_json"):
            code = cli.cmd_space_jobs_cancel(args)

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        req = calls[0]
        self.assertEqual(req.get("op"), "group_space_jobs")
        self.assertEqual(req.get("args", {}).get("group_id"), "g_test")
        self.assertEqual(req.get("args", {}).get("action"), "cancel")
        self.assertEqual(req.get("args", {}).get("job_id"), "spj_1")


if __name__ == "__main__":
    unittest.main()

