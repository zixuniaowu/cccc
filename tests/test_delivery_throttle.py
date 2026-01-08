import unittest


class TestDeliveryThrottle(unittest.TestCase):
    def test_reset_actor_keeps_pending_messages(self) -> None:
        from cccc.daemon.delivery import DeliveryThrottle

        t = DeliveryThrottle()
        t.queue_message(
            "g1",
            "a1",
            event_id="e1",
            by="user",
            to=["@all"],
            text="hello",
            kind="chat.message",
        )
        self.assertTrue(t.has_pending("g1", "a1"))

        t.reset_actor("g1", "a1", keep_pending=True)
        self.assertTrue(t.has_pending("g1", "a1"))

        pending = t.take_pending("g1", "a1")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].event_id, "e1")
        self.assertEqual(pending[0].text, "hello")

    def test_clear_actor_drops_pending_messages(self) -> None:
        from cccc.daemon.delivery import DeliveryThrottle

        t = DeliveryThrottle()
        t.queue_message(
            "g1",
            "a1",
            event_id="e1",
            by="user",
            to=["@all"],
            text="hello",
            kind="chat.message",
        )
        self.assertTrue(t.has_pending("g1", "a1"))

        t.clear_actor("g1", "a1")
        self.assertFalse(t.has_pending("g1", "a1"))


if __name__ == "__main__":
    unittest.main()

