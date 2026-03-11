from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch


class TestWindowsSupportDiagnostics(unittest.TestCase):
    def test_platform_support_reports_missing_pywinpty(self) -> None:
        from cccc.runners import platform_support

        missing = ModuleNotFoundError("No module named 'winpty'")
        missing.name = "winpty"
        with patch.object(platform_support.os, "name", "nt"), patch.object(
            platform_support.importlib,
            "import_module",
            side_effect=missing,
        ):
            details = platform_support.pty_support_details()

        self.assertFalse(bool(details.get("supported")))
        self.assertEqual(str(details.get("code") or ""), "pywinpty_missing")
        self.assertIn("pywinpty", str(details.get("message") or ""))
        hints = details.get("hints") if isinstance(details.get("hints"), list) else []
        self.assertTrue(any("pip install pywinpty" in str(item) for item in hints))

    def test_platform_support_matches_real_import_path(self) -> None:
        from cccc.runners import platform_support

        with patch.object(platform_support.os, "name", "nt"), patch.object(
            platform_support.importlib,
            "import_module",
            return_value=SimpleNamespace(PtyProcess=object()),
        ):
            details = platform_support.pty_support_details()
            pty_process = platform_support.load_winpty_process_class()

        self.assertTrue(bool(details.get("supported")))
        self.assertEqual(str(details.get("code") or ""), "")
        self.assertIsNotNone(pty_process)

    def test_platform_support_reports_import_failure(self) -> None:
        from cccc.runners import platform_support

        with patch.object(platform_support.os, "name", "nt"), patch.object(
            platform_support.importlib,
            "import_module",
            side_effect=RuntimeError("native import failed"),
        ):
            details = platform_support.pty_support_details()

        self.assertFalse(bool(details.get("supported")))
        self.assertEqual(str(details.get("code") or ""), "winpty_import_failed")
        self.assertIn("native import failed", str(details.get("message") or ""))

    def test_actor_runtime_returns_explicit_windows_pty_error(self) -> None:
        from cccc.daemon.actors import actor_runtime_ops

        group = SimpleNamespace(
            group_id="g1",
            doc={"active_scope_key": "scope1"},
            save=lambda: None,
            ledger_path="ledger.jsonl",
        )
        actor = {
            "id": "peer1",
            "default_scope_key": "scope1",
            "runner": "pty",
            "runtime": "codex",
            "command": ["codex"],
            "env": {},
        }

        with patch.object(actor_runtime_ops, "find_actor", return_value=actor), patch.object(
            actor_runtime_ops.pty_runner,
            "PTY_SUPPORTED",
            False,
            create=False,
        ), patch.object(
            actor_runtime_ops,
            "pty_support_error_message",
            return_value="Windows PTY backend unavailable: install pywinpty to enable ConPTY actors.",
        ):
            result = actor_runtime_ops.start_actor_process(
                group,
                "peer1",
                command=["codex"],
                env={},
                runner="pty",
                runtime="codex",
                by="user",
                find_scope_url=lambda _group, _scope_key: ".",
                effective_runner_kind=lambda runner: runner,
                merge_actor_env_with_private=lambda _gid, _aid, env: dict(env),
                normalize_runtime_command=lambda _runtime, command: list(command),
                ensure_mcp_installed=lambda _runtime, _cwd: True,
                inject_actor_context_env=lambda env, _gid, _aid: dict(env),
                prepare_pty_env=lambda env: dict(env),
                pty_backlog_bytes=lambda: 1024,
                write_headless_state=lambda _gid, _aid: None,
                write_pty_state=lambda _gid, _aid, _pid: None,
                clear_preamble_sent=lambda _group, _aid: None,
                throttle_reset_actor=lambda _gid, _aid: None,
                supported_runtimes=("codex",),
            )

        self.assertFalse(bool(result.get("success")))
        self.assertIn("pywinpty", str(result.get("error") or ""))


if __name__ == "__main__":
    unittest.main()
