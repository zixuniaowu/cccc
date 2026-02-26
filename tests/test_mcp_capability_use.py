from __future__ import annotations

import unittest
from unittest.mock import patch


class TestMcpCapabilityUse(unittest.TestCase):
    def test_capability_use_infers_pack_and_calls_tool(self) -> None:
        from cccc.ports.mcp.server import capability_use

        with patch(
            "cccc.ports.mcp.server.capability_enable",
            return_value={"state": "ready", "refresh_required": True, "enabled": True},
        ) as enable_mock, patch(
            "cccc.ports.mcp.server.handle_tool_call",
            return_value={"ok": True},
        ) as call_mock:
            result = capability_use(
                group_id="g1",
                by="peer-1",
                actor_id="peer-1",
                capability_id="",
                tool_name="cccc_space_status",
                tool_arguments={},
            )

        self.assertTrue(bool(result.get("enabled")))
        self.assertTrue(bool(result.get("tool_called")))
        self.assertEqual(str(result.get("capability_id") or ""), "pack:space")
        enable_mock.assert_called_once()
        call_mock.assert_called_once()
        tool_args = call_mock.call_args.args[1] if len(call_mock.call_args.args) > 1 else {}
        self.assertEqual(str(tool_args.get("group_id") or ""), "g1")
        self.assertEqual(str(tool_args.get("by") or ""), "peer-1")
        self.assertEqual(str(tool_args.get("actor_id") or ""), "peer-1")

    def test_capability_use_returns_pending_when_enable_not_ready(self) -> None:
        from cccc.ports.mcp.server import capability_use

        with patch(
            "cccc.ports.mcp.server.capability_enable",
            return_value={"state": "pending_approval", "refresh_required": False, "enabled": False},
        ) as enable_mock, patch(
            "cccc.ports.mcp.server.handle_tool_call",
        ) as call_mock:
            result = capability_use(
                group_id="g1",
                by="peer-1",
                actor_id="peer-1",
                capability_id="mcp:test",
                tool_name="cccc_ext_deadbeef_echo",
                tool_arguments={"message": "hello"},
            )

        self.assertFalse(bool(result.get("enabled")))
        self.assertFalse(bool(result.get("tool_called")))
        enable_mock.assert_called_once()
        call_mock.assert_not_called()

    def test_capability_use_passes_skill_payload_when_no_tool_call(self) -> None:
        from cccc.ports.mcp.server import capability_use

        with patch(
            "cccc.ports.mcp.server.capability_enable",
            return_value={
                "state": "ready",
                "refresh_required": False,
                "enabled": True,
                "skill": {
                    "capability_id": "skill:anthropic:triage",
                    "capsule": "Use triage checklist",
                },
            },
        ) as enable_mock, patch(
            "cccc.ports.mcp.server.handle_tool_call",
        ) as call_mock:
            result = capability_use(
                group_id="g1",
                by="peer-1",
                actor_id="peer-1",
                capability_id="skill:anthropic:triage",
                tool_name="",
                tool_arguments={},
            )

        self.assertTrue(bool(result.get("enabled")))
        self.assertFalse(bool(result.get("tool_called")))
        skill_payload = result.get("skill") if isinstance(result.get("skill"), dict) else {}
        self.assertEqual(str(skill_payload.get("capability_id") or ""), "skill:anthropic:triage")
        enable_mock.assert_called_once()
        call_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
