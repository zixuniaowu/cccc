import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class _PollingProc:
    def __init__(self, responses):
        self._responses = list(responses)

    def poll(self):
        if self._responses:
            return self._responses.pop(0)
        return None


class TestWebRuntimeControl(unittest.TestCase):
    def test_wait_for_child_exit_interruptibly_returns_exit_code(self) -> None:
        from cccc.ports.web.runtime_control import wait_for_child_exit_interruptibly

        proc = _PollingProc([None, None, 75])
        with patch("cccc.ports.web.runtime_control.time.sleep") as mock_sleep:
            ret = wait_for_child_exit_interruptibly(proc, poll_interval_s=0.01)

        self.assertEqual(ret, 75)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_wait_for_child_exit_interruptibly_propagates_keyboard_interrupt(self) -> None:
        from cccc.ports.web.runtime_control import wait_for_child_exit_interruptibly

        proc = _PollingProc([None])
        with patch(
            "cccc.ports.web.runtime_control.time.sleep",
            side_effect=KeyboardInterrupt(),
        ):
            with self.assertRaises(KeyboardInterrupt):
                wait_for_child_exit_interruptibly(proc, poll_interval_s=0.01)

    def test_run_supervised_web_stops_child_on_keyboard_interrupt(self) -> None:
        from cccc.ports.web import main as web_main

        td_ctx = tempfile.TemporaryDirectory()
        home = Path(td_ctx.__enter__())
        try:
            proc = object()
            with patch.object(web_main, "ensure_home", return_value=home), patch.object(
                web_main, "_check_daemon_running", return_value=True
            ), patch.object(web_main, "start_supervised_web_child", return_value=(proc, None)), patch.object(
                web_main, "wait_for_child_exit_interruptibly", side_effect=KeyboardInterrupt()
            ), patch.object(web_main, "stop_web_child", return_value=True) as mock_stop:
                ret = web_main._run_supervised_web(
                    host="127.0.0.1",
                    port=8848,
                    mode="normal",
                    reload=False,
                    log_level="info",
                    launch_source="test",
                )

            self.assertEqual(ret, 0)
            mock_stop.assert_called_once_with(proc, timeout_s=2.0)
        finally:
            td_ctx.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
