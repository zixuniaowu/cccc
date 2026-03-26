import time
import unittest
from unittest.mock import patch

from cccc.daemon.pet import review_scheduler


class TestPetReviewScheduler(unittest.TestCase):
    def tearDown(self) -> None:
        review_scheduler.cancel_pet_review("g-test")

    def test_chat_messages_are_debounced_into_single_review(self) -> None:
        emitted: list[tuple[str, set[str], str]] = []

        def _capture(group_id: str, reasons: set[str], source_event_id: str) -> None:
            emitted.append((group_id, set(reasons), source_event_id))

        with patch.object(review_scheduler, "_can_review_now", return_value=True), patch.object(
            review_scheduler,
            "_emit_pet_review",
            side_effect=_capture,
        ), patch.object(review_scheduler, "PET_REVIEW_DEBOUNCE_SECONDS", 0.05), patch.object(
            review_scheduler,
            "PET_REVIEW_MIN_INTERVAL_SECONDS",
            0.01,
        ), patch.object(review_scheduler, "PET_REVIEW_MAX_DELAY_SECONDS", 0.2):
            review_scheduler.request_pet_review("g-test", reason="chat_message", source_event_id="evt-1")
            review_scheduler.request_pet_review("g-test", reason="chat_message", source_event_id="evt-2")
            review_scheduler.request_pet_review("g-test", reason="chat_reply", source_event_id="evt-3")
            time.sleep(0.12)

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0][0], "g-test")
        self.assertEqual(emitted[0][1], {"chat_message", "chat_reply"})
        self.assertEqual(emitted[0][2], "evt-3")

    def test_immediate_review_bypasses_debounce(self) -> None:
        emitted: list[float] = []

        def _capture(group_id: str, reasons: set[str], source_event_id: str) -> None:
            del group_id, reasons, source_event_id
            emitted.append(time.monotonic())

        with patch.object(review_scheduler, "_can_review_now", return_value=True), patch.object(
            review_scheduler,
            "_emit_pet_review",
            side_effect=_capture,
        ), patch.object(review_scheduler, "PET_REVIEW_DEBOUNCE_SECONDS", 0.2), patch.object(
            review_scheduler,
            "PET_REVIEW_MIN_INTERVAL_SECONDS",
            0.01,
        ), patch.object(review_scheduler, "PET_REVIEW_MAX_DELAY_SECONDS", 0.2):
            started_at = time.monotonic()
            review_scheduler.request_pet_review(
                "g-test",
                reason="actor_stop",
                source_event_id="evt-stop",
                immediate=True,
            )
            time.sleep(0.06)

        self.assertEqual(len(emitted), 1)
        self.assertLess(emitted[0] - started_at, 0.15)


if __name__ == "__main__":
    unittest.main()
