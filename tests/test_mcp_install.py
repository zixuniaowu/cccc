import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cccc.daemon.mcp_install import ensure_mcp_installed, is_mcp_installed


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


if __name__ == "__main__":
    unittest.main()
