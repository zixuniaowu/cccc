import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from cccc.daemon.mcp_install import ensure_mcp_installed, is_mcp_installed
from cccc.kernel.runtime import get_cccc_mcp_stdio_command


class TestMcpInstall(unittest.TestCase):
    def test_is_mcp_installed_unknown_runtime_false(self) -> None:
        self.assertFalse(is_mcp_installed("unknown-runtime"))

    def test_ensure_mcp_installed_skips_non_auto_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("cccc.daemon.mcp_install.subprocess.run") as mock_run:
                ok = ensure_mcp_installed("unknown-runtime", cwd, auto_mcp_runtimes=("claude", "codex"))
                self.assertTrue(ok)
                mock_run.assert_not_called()

    def test_is_mcp_installed_kimi_uses_mcp_list(self) -> None:
        with patch("cccc.daemon.mcp_install.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "cccc\n"
            mock_run.return_value.stderr = ""
            self.assertTrue(is_mcp_installed("kimi"))
            mock_run.assert_called_once_with(
                ["kimi", "mcp", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )

    def test_ensure_mcp_installed_kimi_adds_cccc_stdio(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("cccc.daemon.mcp_install.is_mcp_installed", return_value=False), patch(
                "cccc.daemon.mcp_install.get_cccc_mcp_stdio_command",
                return_value=["/abs/cccc", "mcp"],
            ):
                with patch("cccc.daemon.mcp_install.subprocess.run") as mock_run:
                    mock_run.return_value.returncode = 0
                    ok = ensure_mcp_installed("kimi", cwd, auto_mcp_runtimes=("kimi",))
                    self.assertTrue(ok)
                    mock_run.assert_called_once_with(
                        ["kimi", "mcp", "add", "cccc", "--command", "/abs/cccc", "mcp"],
                        capture_output=True,
                        text=True,
                        cwd=str(cwd),
                        timeout=30,
                    )

    def test_ensure_mcp_installed_claude_uses_absolute_cccc_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("cccc.daemon.mcp_install.is_mcp_installed", return_value=False), patch(
                "cccc.daemon.mcp_install.get_cccc_mcp_stdio_command",
                return_value=["C:\\CCCC\\cccc.exe", "mcp"],
            ):
                with patch("cccc.daemon.mcp_install.subprocess.run") as mock_run:
                    mock_run.return_value.returncode = 0
                    ok = ensure_mcp_installed("claude", cwd, auto_mcp_runtimes=("claude",))
                    self.assertTrue(ok)
                    mock_run.assert_called_once_with(
                        ["claude", "mcp", "add", "-s", "user", "cccc", "--", "C:\\CCCC\\cccc.exe", "mcp"],
                        capture_output=True,
                        text=True,
                        cwd=str(cwd),
                        timeout=30,
                    )

    def test_ensure_mcp_installed_codex_uses_absolute_cccc_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("cccc.daemon.mcp_install.is_mcp_installed", side_effect=[False, True]), patch(
                "cccc.daemon.mcp_install.get_cccc_mcp_stdio_command",
                return_value=["C:\\CCCC\\cccc.exe", "mcp"],
            ):
                with patch("cccc.daemon.mcp_install.subprocess.run") as mock_run:
                    mock_run.return_value.returncode = 0
                    ok = ensure_mcp_installed("codex", cwd, auto_mcp_runtimes=("codex",))
                    self.assertTrue(ok)
                    mock_run.assert_called_once_with(
                        ["codex", "mcp", "add", "cccc", "--", "C:\\CCCC\\cccc.exe", "mcp"],
                        capture_output=True,
                        text=True,
                        cwd=str(cwd),
                        timeout=30,
                    )

    def test_is_mcp_installed_codex_windows_rejects_stale_relative_command(self) -> None:
        with patch("cccc.daemon.mcp_install.sys.platform", "win32"), patch(
            "cccc.daemon.mcp_install.get_cccc_mcp_stdio_command",
            return_value=["C:\\CCCC\\cccc.exe", "mcp"],
        ):
            with patch("cccc.daemon.mcp_install.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = (
                    "cccc\n"
                    "  enabled: true\n"
                    "  transport: stdio\n"
                    "  command: cccc\n"
                    "  args: mcp\n"
                )
                self.assertFalse(is_mcp_installed("codex"))
                mock_run.assert_called_once_with(
                    ["codex", "mcp", "get", "cccc"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

    def test_is_mcp_installed_codex_windows_accepts_expected_absolute_command(self) -> None:
        with patch("cccc.daemon.mcp_install.sys.platform", "win32"), patch(
            "cccc.daemon.mcp_install.get_cccc_mcp_stdio_command",
            return_value=["C:\\CCCC\\cccc.exe", "mcp"],
        ):
            with patch("cccc.daemon.mcp_install.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = (
                    "cccc\n"
                    "  enabled: true\n"
                    "  transport: stdio\n"
                    "  command: C:\\CCCC\\cccc.exe\n"
                    "  args: mcp\n"
                )
                self.assertTrue(is_mcp_installed("codex"))

    def test_ensure_mcp_installed_codex_windows_repairs_stale_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("cccc.daemon.mcp_install.sys.platform", "win32"), patch(
                "cccc.daemon.mcp_install.get_cccc_mcp_stdio_command",
                return_value=["C:\\CCCC\\cccc.exe", "mcp"],
            ):
                with patch("cccc.daemon.mcp_install.subprocess.run") as mock_run:
                    mock_run.side_effect = [
                        Mock(
                            returncode=0,
                            stdout=(
                                "cccc\n"
                                "  enabled: true\n"
                                "  transport: stdio\n"
                                "  command: cccc\n"
                                "  args: mcp\n"
                            ),
                        ),
                        Mock(returncode=0, stdout="", stderr=""),
                        Mock(
                            returncode=0,
                            stdout=(
                                "cccc\n"
                                "  enabled: true\n"
                                "  transport: stdio\n"
                                "  command: C:\\CCCC\\cccc.exe\n"
                                "  args: mcp\n"
                            ),
                        ),
                    ]
                    ok = ensure_mcp_installed("codex", cwd, auto_mcp_runtimes=("codex",))
                    self.assertTrue(ok)
                    self.assertEqual(
                        mock_run.call_args_list,
                        [
                            call(
                                ["codex", "mcp", "get", "cccc"],
                                capture_output=True,
                                text=True,
                                timeout=10,
                            ),
                            call(
                                ["codex", "mcp", "add", "cccc", "--", "C:\\CCCC\\cccc.exe", "mcp"],
                                capture_output=True,
                                text=True,
                                cwd=str(cwd),
                                timeout=30,
                            ),
                            call(
                                ["codex", "mcp", "get", "cccc"],
                                capture_output=True,
                                text=True,
                                timeout=10,
                            ),
                        ],
                    )

    def test_get_cccc_mcp_stdio_command_prefers_sibling_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td)
            python_exe = bin_dir / "python.exe"
            cccc_exe = bin_dir / "cccc.exe"
            python_exe.write_text("", encoding="utf-8")
            cccc_exe.write_text("", encoding="utf-8")
            with patch("cccc.kernel.runtime.sys.platform", "win32"), patch(
                "cccc.kernel.runtime.sys.executable",
                str(python_exe),
            ), patch("cccc.kernel.runtime.shutil.which", return_value=None):
                self.assertEqual(get_cccc_mcp_stdio_command(), [str(cccc_exe.resolve()), "mcp"])

    def test_get_cccc_mcp_stdio_command_falls_back_to_python_module(self) -> None:
        with patch("cccc.kernel.runtime.sys.platform", "win32"), patch(
            "cccc.kernel.runtime.sys.executable",
            "C:\\Python312\\python.exe",
        ), patch("cccc.kernel.runtime.shutil.which", return_value=None):
            self.assertEqual(
                get_cccc_mcp_stdio_command(),
                ["C:\\Python312\\python.exe", "-m", "cccc.ports.mcp.main"],
            )


if __name__ == "__main__":
    unittest.main()
