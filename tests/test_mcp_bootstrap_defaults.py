import os
import unittest
from unittest.mock import patch


class TestMcpBootstrapDefaults(unittest.TestCase):
    def test_bootstrap_defaults_to_no_ledger_tail(self) -> None:
        from cccc.ports.mcp import server as mcp_server

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g_test", "CCCC_ACTOR_ID": "peer1"}, clear=False), patch.object(
            mcp_server, "bootstrap", return_value={"ok": True}
        ) as mock_bootstrap:
            mcp_server.handle_tool_call("cccc_bootstrap", {})

        kwargs = mock_bootstrap.call_args.kwargs
        self.assertEqual(kwargs["group_id"], "g_test")
        self.assertEqual(kwargs["actor_id"], "peer1")
        self.assertEqual(kwargs["ledger_tail_limit"], 0)
        self.assertEqual(kwargs["ledger_tail_max_chars"], 8000)


if __name__ == "__main__":
    unittest.main()
