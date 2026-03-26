import tempfile
import unittest
from pathlib import Path


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
        from src.cccc.kernel.pet_decisions import (
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
        from src.cccc.kernel.pet_decisions import load_pet_decisions, replace_pet_decisions

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


if __name__ == "__main__":
    unittest.main()
