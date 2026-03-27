import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class _FakeGroup:
    def __init__(self, group_id: str, root: Path) -> None:
        self.group_id = group_id
        self.path = root
        self.doc = {"title": "demo", "actors": []}

    @property
    def ledger_path(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path / "ledger.jsonl"


class TestPetFallbackDecisions(unittest.TestCase):
    def test_builds_reply_pressure_fallback_when_ready(self) -> None:
        from cccc.kernel.pet_fallback_decisions import build_fallback_pet_decisions

        with tempfile.TemporaryDirectory() as tmp, patch(
            "cccc.kernel.pet_fallback_decisions.load_pet_signals",
            return_value={"proposal_ready": {"ready": True, "focus": "reply_pressure"}},
        ):
            group = _FakeGroup("g-demo", Path(tmp))
            decisions = build_fallback_pet_decisions(group)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(str(decisions[0]["fingerprint"]), "task_proposal:reply_pressure:oldest_followup")
        self.assertIn("拖得最久的待回复线程", str(decisions[0]["summary"]))

    def test_builds_task_candidate_fallback_for_waiting_user(self) -> None:
        from cccc.kernel.pet_fallback_decisions import build_fallback_pet_decisions

        task = SimpleNamespace(
            id="T1",
            title="Need user answer",
            status=SimpleNamespace(value="active"),
            assignee="",
            blocked_by=[],
            waiting_on=SimpleNamespace(value="user"),
            handoff_to="",
        )
        fake_storage = SimpleNamespace(list_tasks=lambda: [task])

        with tempfile.TemporaryDirectory() as tmp, patch(
            "cccc.kernel.pet_fallback_decisions.ContextStorage",
            return_value=fake_storage,
        ), patch(
            "cccc.kernel.pet_fallback_decisions.load_pet_signals",
            return_value={"proposal_ready": {"ready": True, "focus": "waiting_user"}},
        ):
            group = _FakeGroup("g-demo", Path(tmp))
            decisions = build_fallback_pet_decisions(group)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(str(decisions[0]["source"]["suggestion_kind"]), "waiting_user")
        self.assertEqual(str(((decisions[0]["action"]) or {}).get("task_id") or ""), "T1")


if __name__ == "__main__":
    unittest.main()
