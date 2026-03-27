import tempfile
import unittest
from pathlib import Path


class _FakeGroup:
    def __init__(self, group_id: str, root: Path) -> None:
        self.group_id = group_id
        self.path = root
        self.doc = {"title": "demo", "actors": []}

    @property
    def ledger_path(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path / "ledger.jsonl"


class TestPetOutcomes(unittest.TestCase):
    def test_append_pet_decision_outcome_records_event(self) -> None:
        from cccc.kernel.inbox import iter_events
        from cccc.kernel.pet_outcomes import append_pet_decision_outcome

        with tempfile.TemporaryDirectory() as tmp:
            group = _FakeGroup("g-demo", Path(tmp))
            event = append_pet_decision_outcome(
                group,
                by="user",
                fingerprint="fp-1",
                outcome="dismissed",
                decision_id="dec-1",
                action_type="send_suggestion",
            )
            events = list(iter_events(group.ledger_path))

        self.assertEqual(str(event.get("kind") or ""), "pet.decision.outcome")
        self.assertEqual(str(events[-1].get("data", {}).get("fingerprint") or ""), "fp-1")
        self.assertEqual(str(events[-1].get("data", {}).get("outcome") or ""), "dismissed")

    def test_append_expired_pet_decision_outcomes_marks_removed_fingerprints(self) -> None:
        from cccc.kernel.inbox import iter_events
        from cccc.kernel.pet_outcomes import append_expired_pet_decision_outcomes

        with tempfile.TemporaryDirectory() as tmp:
            group = _FakeGroup("g-demo", Path(tmp))
            append_expired_pet_decision_outcomes(
                group,
                by="pet-peer",
                previous_decisions=[
                    {"id": "dec-1", "fingerprint": "fp-old", "action": {"type": "restart_actor"}},
                ],
                current_decisions=[],
            )
            events = list(iter_events(group.ledger_path))

        self.assertEqual(str(events[-1].get("kind") or ""), "pet.decision.outcome")
        self.assertEqual(str(events[-1].get("data", {}).get("fingerprint") or ""), "fp-old")
        self.assertEqual(str(events[-1].get("data", {}).get("outcome") or ""), "expired")


if __name__ == "__main__":
    unittest.main()
