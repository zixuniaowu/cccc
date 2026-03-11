from __future__ import annotations

import signal
import unittest
from unittest.mock import patch


class TestProcessUtils(unittest.TestCase):
    def test_best_effort_signal_pid_targets_process_group_id(self) -> None:
        from cccc.util import process as process_utils

        with patch.object(process_utils.os, "name", "posix"), patch.object(
            process_utils.os,
            "getpgid",
            return_value=4321,
        ) as getpgid_mock, patch.object(process_utils.os, "killpg", return_value=None) as killpg_mock:
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
        ), patch.object(process_utils.os, "kill", return_value=None) as kill_mock:
            ok = process_utils.best_effort_signal_pid(1234, signal.SIGTERM, include_group=True)

        self.assertTrue(ok)
        kill_mock.assert_called_once_with(1234, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
