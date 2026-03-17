from __future__ import annotations

import signal
import tempfile
import unittest
from pathlib import PosixPath
from types import SimpleNamespace
from unittest.mock import patch


class TestProcessUtils(unittest.TestCase):
    def test_best_effort_signal_pid_targets_process_group_id(self) -> None:
        from cccc.util import process as process_utils

        with patch.object(process_utils.os, "name", "posix"), patch.object(
            process_utils.os,
            "getpgid",
            return_value=4321,
            create=True,
        ) as getpgid_mock, patch.object(process_utils.os, "killpg", return_value=None, create=True) as killpg_mock:
            ok = process_utils.best_effort_signal_pid(1234, signal.SIGTERM, include_group=True)

        self.assertTrue(ok)
        getpgid_mock.assert_called_once_with(1234)
        killpg_mock.assert_called_once_with(4321, signal.SIGTERM)

    def test_best_effort_signal_pid_falls_back_to_kill(self) -> None:
        from cccc.util import process as process_utils

        with patch.object(process_utils.os, "name", "posix"), patch.object(
            process_utils.os,
            "getpgid",
            side_effect=OSError("no pgid"),
            create=True,
        ), patch.object(process_utils.os, "kill", return_value=None) as kill_mock:
            ok = process_utils.best_effort_signal_pid(1234, signal.SIGTERM, include_group=True)

        self.assertTrue(ok)
        kill_mock.assert_called_once_with(1234, signal.SIGTERM)

    def test_resolve_subprocess_executable_uses_windows_cmd_shim(self) -> None:
        from cccc.util import process as process_utils

        with patch.object(process_utils.os, "name", "nt"), patch.object(process_utils.shutil, "which", return_value=r"C:\Tools\codex.cmd"):
            resolved = process_utils.resolve_subprocess_executable("codex")

        self.assertEqual(resolved, r"C:\Tools\codex.cmd")

    def test_resolve_subprocess_argv_only_rewrites_executable_token(self) -> None:
        from cccc.util import process as process_utils

        with patch.object(process_utils, "resolve_subprocess_executable", return_value=r"C:\Tools\codex.cmd") as mock_resolve:
            argv = process_utils.resolve_subprocess_argv(["codex", "mcp", "add", "cccc"])

        self.assertEqual(argv, [r"C:\Tools\codex.cmd", "mcp", "add", "cccc"])
        mock_resolve.assert_called_once_with("codex")

    def test_resolve_background_python_argv_prefers_pythonw_on_windows(self) -> None:
        from cccc.util import process as process_utils

        with patch.object(process_utils.os, "name", "nt"), patch.object(
            process_utils,
            "resolve_subprocess_argv",
            return_value=[r"D:\dev\cccc\.venv\Scripts\python.exe", "-m", "cccc.daemon_main", "run"],
        ), patch.object(
            process_utils,
            "_windows_pythonw_executable",
            return_value=r"D:\dev\cccc\.venv\Scripts\pythonw.exe",
        ):
            argv = process_utils.resolve_background_python_argv([r"D:\dev\cccc\.venv\Scripts\python.exe", "-m", "cccc.daemon_main", "run"])

        self.assertEqual(argv, [r"D:\dev\cccc\.venv\Scripts\pythonw.exe", "-m", "cccc.daemon_main", "run"])

    def test_resolve_background_python_argv_preserves_posix_venv_symlink_path(self) -> None:
        from cccc.util import process as process_utils

        argv = ["/home/dodd/dev/cccc/.venv/bin/python", "-m", "cccc.daemon_main", "run"]
        with patch.object(process_utils.os, "name", "posix"), patch.object(
            process_utils,
            "resolve_subprocess_argv",
            side_effect=AssertionError("should not resolve posix background python argv"),
        ):
            resolved = process_utils.resolve_background_python_argv(argv)

        self.assertEqual(resolved, argv)

    def test_supervised_process_popen_kwargs_windows_uses_detached_group(self) -> None:
        from cccc.util import process as process_utils

        with patch.object(process_utils.os, "name", "nt"), patch.object(
            process_utils.subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0x200,
            create=True,
        ), patch.object(
            process_utils.subprocess,
            "DETACHED_PROCESS",
            0x8,
            create=True,
        ):
            kwargs = process_utils.supervised_process_popen_kwargs()

        self.assertEqual(kwargs, {"creationflags": 0x208})

    def test_terminate_pid_windows_include_group_uses_taskkill_tree(self) -> None:
        from cccc.util import process as process_utils

        with patch.object(process_utils.os, "name", "nt"), patch.object(
            process_utils,
            "pid_is_alive",
            side_effect=[True, False],
        ), patch.object(
            process_utils.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0),
        ) as mock_run:
            ok = process_utils.terminate_pid(4321, timeout_s=0.1, include_group=True, force=True)

        self.assertTrue(ok)
        mock_run.assert_called_once_with(
            ["taskkill", "/PID", "4321", "/T"],
            stdout=process_utils.subprocess.DEVNULL,
            stderr=process_utils.subprocess.DEVNULL,
            check=False,
            text=True,
            timeout=5.0,
        )

    def test_resolve_subprocess_executable_searches_common_windows_user_bin_dirs(self) -> None:
        from cccc.util import process as process_utils

        with tempfile.TemporaryDirectory() as td:
            base = PosixPath(td)
            target = base / "kimi.exe"
            target.write_text("", encoding="utf-8")
            original_path = process_utils.Path

            def _path_factory(value):
                if value == "kimi":
                    raise NotImplementedError("cannot instantiate 'WindowsPath' on your system")
                return original_path(value)

            with patch.object(process_utils.os, "name", "nt"), patch.object(
                process_utils.shutil,
                "which",
                return_value=None,
            ), patch.object(
                process_utils,
                "_iter_windows_user_bin_dirs",
                return_value=[base],
            ), patch.object(
                process_utils,
                "_windows_command_name_candidates",
                return_value=["kimi.exe"],
            ), patch.object(
                process_utils,
                "Path",
                side_effect=_path_factory,
            ):
                resolved = process_utils.resolve_subprocess_executable("kimi")

        self.assertEqual(resolved, str(target))


if __name__ == "__main__":
    unittest.main()
