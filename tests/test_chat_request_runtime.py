import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.routers import chat, chat_requests
from backend.app.services.chat_request_control import (
    ChatRequestStopped, get_chat_request_control,
)
from backend.app.services.chat_request_runtime import (
    ChatRequestBusy, ChatRequestConflict, ChatRequestRuntime,
    REQUEST_ID_RETENTION_SECONDS,
)


def question_result():
    return {
        "chat_reply": chat.ChatAgentReply(reply="테스트 답변입니다.", options={}),
        "model_call_count": 1, "tool_call_counts": {},
    }


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.runtime = ChatRequestRuntime(
            max_workers=1, max_records=4, clock=lambda: self.now,
        )
        self.entered = Event()
        self.release = Event()
        self.addCleanup(self.runtime.close)
        self.addCleanup(self.release.set)

    def blocked(self):
        self.entered.set()
        if not self.release.wait(3):
            raise AssertionError("test worker was not released")
        return "late-result"

    def start_blocked(self):
        handle = self.runtime.start(str(uuid4()), self.blocked)
        self.assertTrue(self.entered.wait(2))
        return handle

    def test_invalid_identifiers_never_start_work(self):
        function = Mock()
        for value in (None, "", "test", str(uuid4()).upper(), "0" * 36):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.runtime.start(value, function)
        function.assert_not_called()

    def test_worker_receives_context_and_shared_control(self):
        trace = ContextVar("test_chat_trace", default=None)
        token = trace.set("test-only")
        try:
            handle = self.runtime.start(
                str(uuid4()), lambda: (trace.get(), get_chat_request_control()),
            )
            result = handle.future.result(timeout=2)
        finally:
            trace.reset(token)
        self.assertEqual(result[0], "test-only")
        self.assertIs(result[1], handle.control)
        self.assertIsNone(get_chat_request_control())
        self.assertEqual(self.runtime.cancel(handle.request_id), "completed")

    def test_cancellation_before_start_skips_work(self):
        request_id = str(uuid4())
        function = Mock()
        self.assertEqual(self.runtime.cancel(request_id), "cancelled")
        with self.assertRaises(ChatRequestStopped) as caught:
            self.runtime.start(request_id, function)
        self.assertEqual(caught.exception.reason, "cancelled")
        function.assert_not_called()

    def test_duplicate_and_capacity_limit_do_not_queue_more_work(self):
        handle = self.start_blocked()
        function = Mock()
        with self.assertRaises(ChatRequestConflict):
            self.runtime.start(handle.request_id, function)
        with self.assertRaises(ChatRequestBusy):
            self.runtime.start(str(uuid4()), function)
        function.assert_not_called()

    def test_cancel_keeps_slot_until_worker_really_finishes(self):
        handle = self.start_blocked()
        self.assertEqual(self.runtime.cancel(handle.request_id), "cancelled")
        self.assertFalse(handle.future.done())
        with self.assertRaises(ChatRequestBusy):
            self.runtime.start(str(uuid4()), Mock())
        self.release.set()
        with self.assertRaises(ChatRequestStopped) as caught:
            handle.future.result(timeout=2)
        self.assertEqual(caught.exception.reason, "cancelled")

    def test_deadline_discards_late_result_without_freeing_running_slot(self):
        handle = self.start_blocked()
        self.now += 150
        self.assertEqual(handle.control.state, "timed_out")
        with self.assertRaises(ChatRequestBusy):
            self.runtime.start(str(uuid4()), Mock())
        self.release.set()
        with self.assertRaises(ChatRequestStopped) as caught:
            handle.future.result(timeout=2)
        self.assertEqual(caught.exception.reason, "timed_out")

    def test_recent_records_are_bounded_and_expire(self):
        for _ in range(4):
            self.runtime.cancel(str(uuid4()))
        with self.assertRaises(ChatRequestBusy):
            self.runtime.cancel(str(uuid4()))
        self.now += REQUEST_ID_RETENTION_SECONDS
        handle = self.runtime.start(str(uuid4()), lambda: "test-only")
        self.assertEqual(handle.future.result(timeout=2), "test-only")

    def test_other_request_is_not_cancelled(self):
        self.runtime.cancel(str(uuid4()))
        handle = self.runtime.start(str(uuid4()), lambda: "test-only")
        self.assertEqual(handle.future.result(timeout=2), "test-only")
        self.assertEqual(handle.control.state, "completed")

    def test_active_worker_error_is_preserved(self):
        def fail():
            raise ValueError("test-only")

        handle = self.runtime.start(str(uuid4()), fail)
        with self.assertRaisesRegex(ValueError, "test-only"):
            handle.future.result(timeout=2)
        self.assertEqual(self.runtime.cancel(handle.request_id), "completed")

    def test_close_cancels_work_and_rejects_new_requests(self):
        handle = self.start_blocked()
        self.runtime.close()
        self.runtime.close()
        self.assertEqual(handle.control.state, "cancelled")
        with self.assertRaises(ChatRequestBusy):
            self.runtime.start(str(uuid4()), Mock())

    def test_submit_failure_does_not_leak_capacity(self):
        with patch.object(self.runtime._executor, "submit", side_effect=RuntimeError("test-only")):
            with self.assertRaisesRegex(RuntimeError, "test-only"):
                self.runtime.start(str(uuid4()), Mock())
        handle = self.runtime.start(str(uuid4()), lambda: "test-only")
        self.assertEqual(handle.future.result(timeout=2), "test-only")


class RequestHttpTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.runtime = ChatRequestRuntime(
            max_workers=1, max_records=64, clock=lambda: self.now,
        )
        self.entered = Event()
        self.release = Event()
        self.addCleanup(self.runtime.close)
        self.addCleanup(self.release.set)
        self.enterContext(patch.object(
            chat_requests, "get_chat_request_runtime", return_value=self.runtime,
        ))
        self.agent = self.enterContext(patch.object(
            chat, "run_chat_agent", return_value=question_result(),
        ))
        self.client = self.enterContext(TestClient(app, raise_server_exceptions=False))

    def post(self, request_id=None):
        headers = {"X-Chat-Request-ID": request_id} if request_id is not None else {}
        return self.client.post("/chat", json={"message": "테스트 요청입니다."}, headers=headers)

    def blocked_agent(self, *args):
        self.entered.set()
        if not self.release.wait(3):
            raise AssertionError("test worker was not released")
        return question_result()

    def test_existing_request_without_header_still_works(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["plan"])
        self.agent.assert_called_once()

    def test_invalid_header_skips_agent(self):
        response = self.post("invalid-request-id")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")
        self.agent.assert_not_called()

    def test_invalid_body_skips_agent(self):
        response = self.client.post("/chat", json={"message": ""})
        self.assertEqual(response.status_code, 400)
        self.agent.assert_not_called()

    def test_cancel_before_chat_is_registered(self):
        request_id = str(uuid4())
        cancelled = self.client.post("/chat/cancel", json={"request_id": request_id})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        response = self.post(request_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "request_cancelled")
        self.agent.assert_not_called()

    def test_cancel_running_request_and_reject_duplicate_or_excess_work(self):
        self.agent.side_effect = self.blocked_agent
        request_id = str(uuid4())
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.post, request_id)
                self.assertTrue(self.entered.wait(2))
                duplicate = self.post(request_id)
                self.assertEqual(duplicate.status_code, 409)
                self.assertEqual(duplicate.json()["error"]["code"], "request_conflict")
                cancelled = self.client.post("/chat/cancel", json={"request_id": request_id})
                self.assertEqual(cancelled.json()["state"], "cancelled")
                response = pending.result(timeout=2)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error"]["code"], "request_cancelled")
                excess = self.post(str(uuid4()))
                self.assertEqual(excess.status_code, 503)
                self.assertEqual(excess.json()["error"]["code"], "chat_busy")
                self.agent.assert_called_once()
        finally:
            self.release.set()

    def test_deadline_returns_before_blocked_worker_finishes(self):
        self.agent.side_effect = self.blocked_agent
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.post, str(uuid4()))
                self.assertTrue(self.entered.wait(2))
                self.now += 150
                response = pending.result(timeout=2)
                self.assertEqual(response.status_code, 504)
                self.assertEqual(response.json()["error"]["code"], "request_timeout")
                self.assertFalse(self.release.is_set())
        finally:
            self.release.set()

    def test_unexpected_worker_value_error_is_internal_error(self):
        with patch.object(chat, "create_chat", side_effect=ValueError("test-only")):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "internal_error")

    def test_retry_shares_the_same_remaining_budget(self):
        controls = []
        remaining = []
        result = question_result()
        result["chat_reply"] = chat.ChatAgentReply(
            reply="테스트 답변입니다.",
            options={"start_date": "2026-11-20", "trip_days": 1, "transport": "driving"},
            plan={"days": [{"day": 1, "items": []}]},
        )

        def agent(*args):
            control = get_chat_request_control()
            controls.append(control)
            remaining.append(control.remaining_seconds())
            self.now += 100 if len(controls) == 1 else 1
            return result

        self.agent.side_effect = agent
        body = {
            "reply": "테스트 답변입니다.", "options": result["chat_reply"].options,
            "plan": None, "map_routes": [], "transit_routes": [], "warnings": [],
        }
        with patch.object(chat, "_build_chat_result", side_effect=[
            chat.PlanValidationFailure(["test-only"]), body,
        ]):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(controls), 2)
        self.assertIs(controls[0], controls[1])
        self.assertEqual(remaining, [150, 50])

    def test_deadline_during_validation_prevents_retry(self):
        result = question_result()
        result["chat_reply"] = chat.ChatAgentReply(
            reply="테스트 답변입니다.",
            options={"start_date": "2026-11-20", "trip_days": 1, "transport": "driving"},
            plan={"days": [{"day": 1, "items": []}]},
        )
        self.agent.return_value = result

        def fail(*args, **kwargs):
            self.now += 150
            raise chat.PlanValidationFailure(["test-only"])

        with patch.object(chat, "_build_chat_result", side_effect=fail):
            response = self.post()
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"]["code"], "request_timeout")
        self.agent.assert_called_once()

    def test_disconnect_cancels_control(self):
        class Request:
            async def is_disconnected(self):
                return True

        handle = self.runtime.start(str(uuid4()), lambda: self.blocked_agent())
        self.assertTrue(self.entered.wait(2))
        with self.assertRaises(ChatRequestStopped) as caught:
            asyncio.run(chat_requests.wait_for_chat_request(Request(), handle))
        self.assertEqual(caught.exception.reason, "cancelled")
        self.assertFalse(handle.future.done())

    def test_http_task_cancellation_stops_control(self):
        class Request:
            async def is_disconnected(self):
                return False

        handle = self.runtime.start(str(uuid4()), lambda: self.blocked_agent())
        self.assertTrue(self.entered.wait(2))

        async def cancel_wait():
            task = asyncio.create_task(chat_requests.wait_for_chat_request(Request(), handle))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(cancel_wait())
        self.assertEqual(handle.control.state, "cancelled")


if __name__ == "__main__":
    unittest.main()