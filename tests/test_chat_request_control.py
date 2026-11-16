from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Barrier
import unittest

from backend.app.services.chat_request_control import (
    CHAT_REQUEST_TIMEOUT_SECONDS, ChatRequestControl, ChatRequestStopped,
    check_chat_request_control, get_chat_request_control, get_chat_request_timeout,
    use_chat_request_control,
)


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ChatRequestControlTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.control = ChatRequestControl(clock=self.clock)

    def assert_stopped(self, control, reason):
        with self.assertRaises(ChatRequestStopped) as caught:
            control.checkpoint()
        self.assertEqual(caught.exception.reason, reason)

    def test_default_budget_is_150_seconds(self):
        self.assertEqual(CHAT_REQUEST_TIMEOUT_SECONDS, 150)
        self.assertEqual(self.control.remaining_seconds(), 150)
        self.assertEqual(self.control.state, "active")

    def test_retry_reuses_remaining_budget(self):
        deadline = self.control.deadline
        with use_chat_request_control(self.control):
            self.clock.advance(30)
            self.assertEqual(get_chat_request_timeout(120), 120)
        with use_chat_request_control(self.control):
            self.clock.advance(60)
            self.assertEqual(get_chat_request_timeout(120), 60)
        self.assertEqual(self.control.deadline, deadline)

    def test_deadline_boundary_stops_new_work(self):
        self.clock.advance(150)
        self.assert_stopped(self.control, "timed_out")
        self.assertEqual(self.control.remaining_seconds(), 0)

    def test_cancel_is_idempotent_and_blocks_work(self):
        self.assertTrue(self.control.cancel())
        self.assertFalse(self.control.cancel())
        self.assert_stopped(self.control, "cancelled")
        self.assertFalse(self.control.complete())
        with self.assertRaises(ChatRequestStopped):
            self.control.timeout_seconds(20)

    def test_cancellation_reason_survives_later_deadline(self):
        self.control.cancel()
        self.clock.advance(200)
        self.assertEqual(self.control.state, "cancelled")

    def test_expired_request_cannot_be_changed_to_cancelled(self):
        self.clock.advance(150)
        self.assertFalse(self.control.cancel())
        self.assertFalse(self.control.complete())
        self.assertEqual(self.control.state, "timed_out")

    def test_late_result_cannot_complete(self):
        self.clock.advance(151)
        self.assertFalse(self.control.complete())
        self.assert_stopped(self.control, "timed_out")

    def test_completed_request_ignores_late_cancel(self):
        self.assertTrue(self.control.complete())
        self.assertTrue(self.control.complete())
        self.assertFalse(self.control.cancel())
        self.clock.advance(200)
        self.assertEqual(self.control.state, "completed")
        self.assert_stopped(self.control, "completed")

    def test_provider_timeout_is_capped_by_remaining_budget(self):
        self.clock.advance(149.75)
        self.assertEqual(self.control.timeout_seconds(20), 0.25)
        self.assertEqual(self.control.timeout_seconds(0.1), 0.1)

    def test_invalid_time_limits_are_rejected(self):
        for value in (0, -1, True, None, "20", float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ChatRequestControl(value, clock=self.clock)
                with self.assertRaises(ValueError):
                    get_chat_request_timeout(value)

    def test_invalid_clock_and_overflow_are_rejected(self):
        for clock in (None, lambda: None, lambda: True, lambda: float("nan")):
            with self.subTest(clock=clock):
                with self.assertRaises(ValueError):
                    ChatRequestControl(clock=clock)
        with self.assertRaises(ValueError):
            ChatRequestControl(1e308, clock=lambda: 1e308)

    def test_uncontrolled_calls_keep_existing_timeout(self):
        self.assertIsNone(get_chat_request_control())
        self.assertIsNone(check_chat_request_control())
        self.assertEqual(get_chat_request_timeout(20), 20)

    def test_nested_scope_and_exception_restore_previous_control(self):
        other = ChatRequestControl(clock=self.clock)
        with use_chat_request_control(self.control):
            with self.assertRaisesRegex(ValueError, "test-only"):
                with use_chat_request_control(other):
                    self.assertIs(get_chat_request_control(), other)
                    raise ValueError("test-only")
            self.assertIs(get_chat_request_control(), self.control)
        self.assertIsNone(get_chat_request_control())

    def test_cancelled_scope_cannot_replace_an_active_scope(self):
        other = ChatRequestControl(clock=self.clock)
        other.cancel()
        with use_chat_request_control(self.control):
            with self.assertRaises(ChatRequestStopped):
                with use_chat_request_control(other):
                    self.fail("A cancelled scope must not start")
            self.assertIs(get_chat_request_control(), self.control)

    def test_independent_request_is_not_cancelled(self):
        other = ChatRequestControl(clock=self.clock)
        self.control.cancel()
        with use_chat_request_control(other):
            check_chat_request_control()
            self.assertEqual(get_chat_request_timeout(20), 20)
        self.assertEqual(other.state, "active")

    def test_copied_worker_context_sees_cancel_without_leaking(self):
        with use_chat_request_control(self.control):
            context = copy_context()
        self.control.cancel()
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertIsNone(pool.submit(get_chat_request_control).result(timeout=2))
            with self.assertRaises(ChatRequestStopped):
                pool.submit(context.run, check_chat_request_control).result(timeout=2)
            self.assertIsNone(pool.submit(get_chat_request_control).result(timeout=2))

    def test_completion_and_cancellation_have_one_winner(self):
        barrier = Barrier(3)

        def call(method):
            barrier.wait(timeout=2)
            return method()

        with ThreadPoolExecutor(max_workers=2) as pool:
            completed = pool.submit(call, self.control.complete)
            cancelled = pool.submit(call, self.control.cancel)
            barrier.wait(timeout=2)
            results = (completed.result(timeout=2), cancelled.result(timeout=2))
        self.assertIn(results, ((True, False), (False, True)))
        self.assertEqual(
            self.control.state, "completed" if results[0] else "cancelled",
        )


if __name__ == "__main__":
    unittest.main()