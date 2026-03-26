import unittest
from types import SimpleNamespace

from cccc.kernel.pet_task_proposals import (
    build_task_proposal_candidates,
    build_task_proposal_summary_lines,
)


def _task(
    task_id: str,
    *,
    title: str,
    status: str = "planned",
    assignee: str = "",
    blocked_by: list[str] | None = None,
    waiting_on: str = "none",
    handoff_to: str = "",
):
    return SimpleNamespace(
        id=task_id,
        title=title,
        status=SimpleNamespace(value=status),
        assignee=assignee,
        blocked_by=list(blocked_by or []),
        waiting_on=SimpleNamespace(value=waiting_on),
        handoff_to=handoff_to,
    )


class TestPetTaskProposals(unittest.TestCase):
    def test_build_task_proposal_candidates_orders_by_triage_priority(self) -> None:
        tasks = [
            _task("T1", title="Need user scope", status="active", waiting_on="user"),
            _task("T2", title="Review handoff", status="active", handoff_to="foreman"),
            _task("T3", title="Blocked on peer output", status="active", blocked_by=["T9"]),
            _task("T4", title="Backlog cleanup", status="planned"),
            _task("T5", title="Already done", status="done", waiting_on="user"),
        ]

        proposals = build_task_proposal_candidates(tasks)

        self.assertEqual(
            [item["reason"] for item in proposals],
            ["waiting_user", "handoff", "blocked", "planned_backlog"],
        )
        self.assertEqual(proposals[0]["action"]["task_id"], "T1")
        self.assertEqual(proposals[1]["action"]["task_id"], "T2")
        self.assertEqual(proposals[2]["action"]["task_id"], "T3")
        self.assertEqual(proposals[3]["action"]["task_id"], "T4")

    def test_waiting_user_candidate_prefers_move_from_planned_to_active(self) -> None:
        proposal = build_task_proposal_candidates(
            [_task("T10", title="Need product answer", status="planned", waiting_on="user")]
        )[0]

        self.assertEqual(proposal["reason"], "waiting_user")
        self.assertEqual(proposal["action"]["operation"], "move")
        self.assertEqual(proposal["action"]["status"], "active")

    def test_handoff_and_blocked_candidates_keep_expected_actions(self) -> None:
        proposals = build_task_proposal_candidates(
            [
                _task("T20", title="Take over runtime fix", status="active", handoff_to="peer-debugger"),
                _task("T21", title="Need external API back", status="active", waiting_on="external"),
            ]
        )

        self.assertEqual(proposals[0]["action"]["operation"], "handoff")
        self.assertEqual(proposals[0]["action"]["assignee"], "peer-debugger")
        self.assertEqual(proposals[1]["action"]["operation"], "update")
        self.assertEqual(proposals[1]["reason"], "blocked")

    def test_build_task_proposal_summary_lines_limits_high_value_items(self) -> None:
        lines = build_task_proposal_summary_lines(
            [
                _task("T1", title="Need user scope", status="active", waiting_on="user"),
                _task("T2", title="Review handoff", status="active", handoff_to="foreman"),
                _task("T3", title="Blocked on peer output", status="active", blocked_by=["T9"]),
            ],
            limit=2,
        )

        self.assertEqual(len(lines), 2)
        self.assertIn("T1:Need user scope", lines[0])
        self.assertIn("等待用户", lines[0])
        self.assertIn("T2:Review handoff", lines[1])
        self.assertIn("已移交给 foreman", lines[1])


if __name__ == "__main__":
    unittest.main()
