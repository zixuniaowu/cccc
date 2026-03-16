from __future__ import annotations

import io
import os
import tempfile
import unittest
from unittest.mock import patch


class TestDaemonMain(unittest.TestCase):
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

    def test_stop_also_stops_supervised_web_runtime_after_daemon_shutdown(self) -> None:
        from cccc import daemon_main

        _, cleanup = self._with_home()
        stdout = io.StringIO()
        try:
            with patch.object(daemon_main, "call_daemon", return_value={"ok": True}), patch.object(
                daemon_main, "read_web_runtime_state", return_value={"pid": 4321}
            ), patch.object(daemon_main, "terminate_pid", return_value=True) as mock_terminate, patch.object(
                daemon_main, "clear_web_runtime_state"
            ) as mock_clear, patch.object(
                daemon_main.sys, "stdout", stdout
            ):
                rc = daemon_main.main(["stop"])
        finally:
            cleanup()

        self.assertEqual(rc, 0)
        mock_terminate.assert_called_once_with(4321, timeout_s=2.0, include_group=True, force=True)
        mock_clear.assert_called_once_with(home=unittest.mock.ANY, pid=4321)
        self.assertIn("shutdown requested", stdout.getvalue())

    def test_stop_reports_failure_when_supervised_web_runtime_cannot_stop(self) -> None:
        from cccc import daemon_main

        _, cleanup = self._with_home()
        stdout = io.StringIO()
        try:
            with patch.object(daemon_main, "call_daemon", return_value={"ok": True}), patch.object(
                daemon_main, "read_web_runtime_state", return_value={"pid": 4321}
            ), patch.object(daemon_main, "terminate_pid", return_value=False), patch.object(
                daemon_main.sys, "stdout", stdout
            ):
                rc = daemon_main.main(["stop"])
        finally:
            cleanup()

        self.assertEqual(rc, 1)
        self.assertIn("failed to stop supervised web runtime", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
