import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class _FakeGroup:
    def __init__(self, group_id: str, root: Path) -> None:
        self.group_id = group_id
        self.path = root
        self.doc = {"actors": []}

    @property
    def ledger_path(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path / "ledger.jsonl"


class TestPetDecisionsOps(unittest.TestCase):
    def _sample_decision(self, group_id: str) -> dict:
        return {
            "id": "decision-1",
            "kind": "suggestion",
            "priority": 80,
            "summary": "Send the suggested reply.",
            "agent": "Pet Peer",
            "fingerprint": f"group:{group_id}:suggestion:evt-1",
            "action": {
                "type": "send_suggestion",
                "group_id": group_id,
                "text": "Please follow up with the user.",
                "to": ["user"],
                "reply_to": "evt-1",
            },
            "source": {
                "event_id": "evt-1",
                "suggestion_kind": "reply_required",
            },
            "updated_at": "",
            "suggestion": "Please follow up with the user.",
            "suggestion_preview": "Please follow up...",
        }

    def test_replace_load_and_clear_pet_decisions(self) -> None:
        from cccc.kernel.pet_decisions import (
            clear_pet_decisions,
            load_pet_decisions,
            replace_pet_decisions,
        )

        with tempfile.TemporaryDirectory() as tmp:
            group = _FakeGroup("g-demo", Path(tmp))
            decision = self._sample_decision(group.group_id)
            stored = replace_pet_decisions(
                group,
                decisions=[decision],
                actor_id="pet-peer",
            )
            self.assertEqual(stored, [decision])
            self.assertEqual(load_pet_decisions(group), [decision])

            clear_pet_decisions(group, actor_id="pet-peer")
            self.assertEqual(load_pet_decisions(group), [])

    def test_replace_filters_invalid_decisions(self) -> None:
        from cccc.kernel.pet_decisions import load_pet_decisions, replace_pet_decisions

        with tempfile.TemporaryDirectory() as tmp:
            group = _FakeGroup("g-demo", Path(tmp))
            valid = self._sample_decision(group.group_id)
            invalid = {
                "id": "",
                "kind": "suggestion",
                "summary": "missing required fields",
                "action": {"type": "send_suggestion"},
            }

            stored = replace_pet_decisions(
                group,
                decisions=[invalid, valid],
                actor_id="pet-peer",
            )
            self.assertEqual(stored, [valid])
            self.assertEqual(load_pet_decisions(group), [valid])

    def test_replace_preserves_automation_proposal_action(self) -> None:
        from cccc.kernel.pet_decisions import load_pet_decisions, replace_pet_decisions

        with tempfile.TemporaryDirectory() as tmp:
            group = _FakeGroup("g-demo", Path(tmp))
            decision = {
                "id": "decision-auto-1",
                "kind": "suggestion",
                "priority": 70,
                "summary": "建议增加一个临时自动提醒规则。",
                "agent": "Pet Peer",
                "fingerprint": f"group:{group.group_id}:automation:idle-followup",
                "action": {
                    "type": "automation_proposal",
                    "group_id": group.group_id,
                    "title": "Temporary reply follow-up rule",
                    "summary": "Add one short-lived notify rule for stuck replies.",
                    "actions": [
                        {
                            "type": "create_rule",
                            "rule": {
                                "id": "pet-temp-reply-followup",
                                "enabled": True,
                                "scope": "group",
                                "to": ["@foreman"],
                                "trigger": {"kind": "interval", "every_seconds": 900},
                                "action": {
                                    "kind": "notify",
                                    "message": "Check overdue reply loop.",
                                    "priority": "normal",
                                },
                            },
                        }
                    ],
                },
                "source": {},
                "updated_at": "",
            }

            stored = replace_pet_decisions(group, decisions=[decision], actor_id="pet-peer")

            self.assertEqual(stored[0]["action"]["type"], "automation_proposal")
            self.assertEqual(stored[0]["action"]["title"], "Temporary reply follow-up rule")
            self.assertEqual(stored[0]["action"]["actions"][0]["type"], "create_rule")
            self.assertEqual(load_pet_decisions(group)[0]["action"]["type"], "automation_proposal")

    def test_replace_rolls_back_when_ledger_append_fails(self) -> None:
        from cccc.daemon.pet.pet_decision_ops import handle_pet_decisions_replace
        from cccc.kernel.pet_decisions import load_pet_decisions, replace_pet_decisions

        with tempfile.TemporaryDirectory() as tmp:
            group = _FakeGroup("g-demo", Path(tmp))
            previous = self._sample_decision(group.group_id)
            replace_pet_decisions(group, decisions=[previous], actor_id="pet-peer")

            updated = dict(previous)
            updated["summary"] = "Updated summary"

            with patch("cccc.daemon.pet.pet_decision_ops.load_group", return_value=group), patch(
                "cccc.daemon.pet.pet_decision_ops.get_pet_actor",
                return_value={"id": "pet-peer", "internal_kind": "pet"},
            ), patch(
                "cccc.daemon.pet.pet_decision_ops.append_event",
                side_effect=RuntimeError("ledger failed"),
            ):
                resp = handle_pet_decisions_replace(
                    {
                        "group_id": group.group_id,
                        "actor_id": "pet-peer",
                        "decisions": [updated],
                    }
                )

            self.assertFalse(resp.ok)
            self.assertEqual(load_pet_decisions(group), [previous])

    def test_clear_rolls_back_when_ledger_append_fails(self) -> None:
        from cccc.daemon.pet.pet_decision_ops import handle_pet_decisions_clear
        from cccc.kernel.pet_decisions import load_pet_decisions, replace_pet_decisions

        with tempfile.TemporaryDirectory() as tmp:
            group = _FakeGroup("g-demo", Path(tmp))
            previous = self._sample_decision(group.group_id)
            replace_pet_decisions(group, decisions=[previous], actor_id="pet-peer")

            with patch("cccc.daemon.pet.pet_decision_ops.load_group", return_value=group), patch(
                "cccc.daemon.pet.pet_decision_ops.get_pet_actor",
                return_value={"id": "pet-peer", "internal_kind": "pet"},
            ), patch(
                "cccc.daemon.pet.pet_decision_ops.append_event",
                side_effect=RuntimeError("ledger failed"),
            ):
                resp = handle_pet_decisions_clear(
                    {
                        "group_id": group.group_id,
                        "actor_id": "pet-peer",
                    }
                )

            self.assertFalse(resp.ok)
            self.assertEqual(load_pet_decisions(group), [previous])


class TestPetActorSeed(unittest.TestCase):
    def test_pet_actor_seed_uses_existing_pet_actor_as_fallback(self) -> None:
        from cccc.kernel.pet_actor import _pet_actor_seed

        with tempfile.TemporaryDirectory() as tmp:
            group = _FakeGroup("g-demo", Path(tmp))
            seed = _pet_actor_seed(
                group,
                fallback_actor={
                    "runtime": "gpt-5.4",
                    "runner": "headless",
                    "command": ["codex"],
                    "env": {"FOO": "bar"},
                    "default_scope_key": "scope-a",
                    "submit": "ctrl-enter",
                },
            )
            self.assertEqual(seed["runtime"], "gpt-5.4")
            self.assertEqual(seed["runner"], "headless")
            self.assertEqual(seed["default_scope_key"], "scope-a")
            self.assertEqual(seed["submit"], "ctrl-enter")

    def test_pet_actor_seed_logs_when_falling_back_to_defaults(self) -> None:
        from cccc.kernel.pet_actor import _pet_actor_seed

        with tempfile.TemporaryDirectory() as tmp, self.assertLogs("cccc.kernel.pet_actor", level="WARNING") as logs:
            group = _FakeGroup("g-demo", Path(tmp))
            seed = _pet_actor_seed(group)

        self.assertEqual(seed["runtime"], "codex")
        self.assertEqual(seed["runner"], "pty")
        self.assertTrue(any("fell back to defaults" in line for line in logs.output))

    def test_pet_actor_seed_uses_available_runtime_when_seed_runtime_missing(self) -> None:
        from cccc.kernel.pet_actor import _pet_actor_seed

        with tempfile.TemporaryDirectory() as tmp, patch(
            "cccc.kernel.pet_actor.runtime_start_preflight_error",
            side_effect=lambda runtime, command, runner="pty": "missing" if runtime == "codex" else "",
        ), patch(
            "cccc.kernel.pet_actor.detect_runtime",
            side_effect=lambda name: type("RuntimeInfo", (), {"available": name == "claude"})(),
        ):
            group = _FakeGroup("g-demo", Path(tmp))
            seed = _pet_actor_seed(
                group,
                fallback_actor={
                    "runtime": "codex",
                    "runner": "pty",
                    "command": ["codex"],
                },
            )

        self.assertEqual(seed["runtime"], "claude")
        self.assertEqual(seed["command"], ["claude", "--dangerously-skip-permissions"])


if __name__ == "__main__":
    unittest.main()
