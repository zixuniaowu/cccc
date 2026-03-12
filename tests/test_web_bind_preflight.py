from __future__ import annotations

import errno
import io
import socket
import unittest
from unittest.mock import patch


class _DeniedBindSocket:
    def bind(self, _sockaddr) -> None:
        exc = OSError(errno.EACCES, "forbidden")
        exc.winerror = 10013  # type: ignore[attr-defined]
        raise exc

    def close(self) -> None:
        return None


class TestWebBindPreflight(unittest.TestCase):
    def test_in_use_port_reports_clear_message(self) -> None:
        from cccc.ports.web.bind_preflight import ensure_tcp_port_bindable

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
        try:
            with self.assertRaises(RuntimeError) as ctx:
                ensure_tcp_port_bindable(host="127.0.0.1", port=port)
        finally:
            listener.close()

        message = str(ctx.exception)
        self.assertIn(f"Web port {port} is unavailable", message)
        self.assertIn("already using that port", message)

    def test_windows_access_denied_points_to_excluded_port_ranges(self) -> None:
        from cccc.ports.web import bind_preflight

        with patch.object(bind_preflight.sys, "platform", "win32"), patch.object(
            bind_preflight.socket,
            "getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 8848))],
        ), patch.object(bind_preflight.socket, "socket", return_value=_DeniedBindSocket()):
            with self.assertRaises(RuntimeError) as ctx:
                bind_preflight.ensure_tcp_port_bindable(host="127.0.0.1", port=8848)

        message = str(ctx.exception)
        self.assertIn("excluded TCP port range", message)
        self.assertIn("netsh interface ipv4 show excludedportrange protocol=tcp", message)

    def test_web_main_returns_error_when_preflight_fails(self) -> None:
        from cccc.ports.web import main as web_main

        stderr = io.StringIO()
        with patch.object(web_main, "_check_daemon_running", return_value=True), patch.object(
            web_main,
            "ensure_tcp_port_bindable",
            side_effect=RuntimeError("boom"),
        ), patch.object(web_main.uvicorn, "run") as mock_run, patch.object(web_main.sys, "stderr", stderr):
            rc = web_main.main(["--host", "127.0.0.1", "--port", "8848"])

        self.assertEqual(rc, 1)
        self.assertIn("boom", stderr.getvalue())
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
