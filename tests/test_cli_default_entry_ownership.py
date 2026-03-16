import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch


class TestCliDefaultEntryOwnership(unittest.TestCase):
    def _with_home(self) -> tuple[Path, Callable[[], None]]:
        td_ctx = tempfile.TemporaryDirectory()
        td = Path(td_ctx.__enter__())

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)

        return td, cleanup

    def test_acquire_default_entry_lock_reports_existing_owner(self) -> None:
        from cccc.cli import common

        home, cleanup = self._with_home()
        try:
            with patch.object(common, "acquire_lockfile", side_effect=common.LockUnavailableError("busy")):
                handle, error = common._acquire_default_entry_lock(home)
            self.assertIsNone(handle)
            self.assertIn("already running", str(error or ""))
        finally:
            cleanup()

    def test_stop_existing_web_runtime_terminates_live_pid(self) -> None:
        from cccc.cli import common

        home, cleanup = self._with_home()
        try:
            with patch.object(common, "read_web_runtime_state", return_value={"pid": 4321}), patch.object(
                common, "pid_is_alive", return_value=True
            ), patch.object(common, "terminate_pid") as mock_terminate, patch.object(
                common, "clear_web_runtime_state"
            ) as mock_clear:
                common._stop_existing_web_runtime(home)

            mock_terminate.assert_called_once_with(4321, timeout_s=2.0, include_group=True, force=True)
            mock_clear.assert_called_once_with(home=home, pid=4321)
        finally:
            cleanup()

    def test_stop_existing_daemon_shuts_down_and_cleans_state(self) -> None:
        from cccc.cli import common

        home, cleanup = self._with_home()
        try:
            daemon_dir = home / "daemon"
            daemon_dir.mkdir(parents=True, exist_ok=True)
            for name in ("ccccd.sock", "ccccd.addr.json", "ccccd.pid"):
                (daemon_dir / name).write_text("1234", encoding="utf-8")

            with patch.object(
                common,
                "call_daemon",
                side_effect=[
                    {"ok": True, "result": {"pid": 1234}},
                    {"ok": True, "result": {"message": "shutting down"}},
                    {"ok": False, "error": {"code": "daemon_unavailable"}},
                    {"ok": False, "error": {"code": "daemon_unavailable"}},
                ],
            ), patch.object(common, "terminate_pid") as mock_terminate:
                ok = common._stop_existing_daemon(home)

            self.assertTrue(ok)
            mock_terminate.assert_not_called()
            self.assertFalse((daemon_dir / "ccccd.sock").exists())
            self.assertFalse((daemon_dir / "ccccd.addr.json").exists())
            self.assertFalse((daemon_dir / "ccccd.pid").exists())
        finally:
            cleanup()

    def test_stop_existing_daemon_kills_stale_pid_file_when_ping_unavailable(self) -> None:
        from cccc.cli import common

        home, cleanup = self._with_home()
        try:
            daemon_dir = home / "daemon"
            daemon_dir.mkdir(parents=True, exist_ok=True)
            (daemon_dir / "ccccd.pid").write_text("2468", encoding="utf-8")

            with patch.object(common, "call_daemon", return_value={"ok": False}), patch.object(
                common, "pid_is_alive", return_value=True
            ), patch.object(common, "terminate_pid") as mock_terminate:
                ok = common._stop_existing_daemon(home)

            self.assertTrue(ok)
            mock_terminate.assert_called_once_with(2468, timeout_s=2.0, include_group=True, force=True)
            self.assertFalse((daemon_dir / "ccccd.pid").exists())
        finally:
            cleanup()

    def test_stop_existing_daemon_terminates_same_home_orphans(self) -> None:
        from cccc.cli import common

        home, cleanup = self._with_home()
        try:
            daemon_dir = home / "daemon"
            daemon_dir.mkdir(parents=True, exist_ok=True)

            with patch.object(common, "call_daemon", return_value={"ok": False}), patch.object(
                common, "_same_home_daemon_pids", return_value=[2468, 9753]
            ), patch.object(common, "terminate_pid") as mock_terminate:
                ok = common._stop_existing_daemon(home)

            self.assertTrue(ok)
            self.assertEqual(
                mock_terminate.call_args_list,
                [
                    unittest.mock.call(2468, timeout_s=2.0, include_group=True, force=True),
                    unittest.mock.call(9753, timeout_s=2.0, include_group=True, force=True),
                ],
            )
        finally:
            cleanup()

    def test_stop_existing_daemon_fails_when_orphan_termination_fails(self) -> None:
        from cccc.cli import common

        home, cleanup = self._with_home()
        try:
            with patch.object(common, "call_daemon", return_value={"ok": False}), patch.object(
                common, "_same_home_daemon_pids", return_value=[9753]
            ), patch.object(common, "terminate_pid", return_value=False):
                ok = common._stop_existing_daemon(home)

            self.assertFalse(ok)
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
