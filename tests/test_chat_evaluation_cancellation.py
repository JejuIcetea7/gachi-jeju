from copy import deepcopy
from datetime import date
import json
from threading import Event
import unittest
from unittest.mock import Mock, patch

from backend.app.routers import chat, chat_requests
from backend.app.services.chat_api_evaluation import evaluate_chat_record
from backend.app.services.chat_request_control import ChatRequestStopped
from backend.app.services.chat_request_runtime import ChatRequestRuntime
from scripts import chat_evaluation_runner as evaluation


def result(with_plan=False):
    options = (
        {"start_date": "2026-11-20", "trip_days": 1, "transport": "driving"}
        if with_plan else {}
    )
    return {
        "chat_reply": chat.ChatAgentReply(
            reply="테스트 답변입니다.", options=options,
            plan={"days": [{"day": 1, "items": []}]} if with_plan else None,
        ),
        "model_call_count": 1, "tool_call_counts": {}, "messages": [],
    }


class ChatEvaluationCancellationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.runtime = ChatRequestRuntime(clock=lambda: self.now)
        self.release = Event()
        self.handles = []
        self.addCleanup(self.runtime.close)
        self.addCleanup(self.release.set)
        self.enterContext(patch.object(
            chat_requests, "get_chat_request_runtime", return_value=self.runtime,
        ))
        start = self.runtime.start

        def record_handle(*args):
            handle = start(*args)
            self.handles.append(handle)
            return handle

        self.enterContext(patch.object(self.runtime, "start", side_effect=record_handle))
        self.request = {"message": "테스트 요청입니다."}

    def block_after_deadline(self):
        self.now += 150
        if not self.release.wait(3):
            raise AssertionError("test worker was not released")

    def run_request(self, runner):
        return evaluation.run_chat_request(
            self.request, agent_runner=runner, reference_date=date(2026, 11, 17),
        )

    def finish_late_worker(self):
        self.release.set()
        with self.assertRaises(ChatRequestStopped) as caught:
            self.handles[0].future.result(timeout=2)
        self.assertEqual(caught.exception.reason, "timed_out")

    def test_invalid_capture_value_does_not_change_response(self):
        with patch.object(evaluation, "_capture_result", return_value=None):
            record = self.run_request(Mock(return_value=result()))
        self.assertEqual(record["http_status"], 200)
        self.assertIsNone(record["response"]["plan"])
        self.assertEqual(record["attempts"][0]["status"], "returned")
        self.assertEqual(record["attempts"][0]["capture_error"], "TypeError")

    def test_timeout_during_capture_keeps_pending_snapshot(self):
        capture = evaluation._capture_result

        def delayed_capture(value):
            self.block_after_deadline()
            return capture(value)

        with patch.object(evaluation, "_capture_result", side_effect=delayed_capture):
            record = self.run_request(Mock(return_value=result()))
            self.assertEqual(record["http_status"], 504)
            self.assertEqual(record["response"]["error"]["code"], "request_timeout")
            self.assertEqual(record["attempts"][0]["status"], "running")
            self.assertNotIn("metrics", record["attempts"][0])
            snapshot = deepcopy(record)
            self.finish_late_worker()
            self.assertEqual(record, snapshot)

        score = evaluate_chat_record({
            "case_id": "pending-capture", "request": self.request,
            "expectation": {
                "outcome": "error", "http_status": 504, "error_code": "request_timeout",
            },
        }, record)
        self.assertFalse(score["automatic_passed"])
        self.assertFalse(score["checks"]["capture"]["passed"])

    def test_late_agent_exception_does_not_modify_snapshot(self):
        def runner(*args):
            self.block_after_deadline()
            raise RuntimeError("private-provider-detail")

        original_agent = chat.run_chat_agent
        original_builder = chat.build_chat_messages
        record = self.run_request(runner)
        self.assertEqual(record["http_status"], 504)
        self.assertEqual(record["attempts"][0]["status"], "running")
        self.assertIsNone(record["attempts"][0]["error_type"])
        snapshot = deepcopy(record)
        self.finish_late_worker()
        self.assertEqual(record, snapshot)
        self.assertNotIn("private-provider-detail", json.dumps(record))
        self.assertIs(chat.run_chat_agent, original_agent)
        self.assertIs(chat.build_chat_messages, original_builder)

    def test_retry_timeout_keeps_captured_first_attempt(self):
        calls = []

        def runner(*args):
            calls.append(True)
            if len(calls) == 2:
                self.block_after_deadline()
            return result(with_plan=True)

        with patch.object(
            chat, "_build_chat_result", side_effect=chat.PlanValidationFailure(["test-only"]),
        ):
            record = self.run_request(runner)
            self.assertEqual(record["http_status"], 504)
            self.assertEqual(len(record["attempts"]), 2)
            self.assertEqual(record["attempts"][0]["status"], "returned")
            self.assertEqual(record["attempts"][0]["metrics"]["model_call_count"], 1)
            self.assertEqual(record["attempts"][1]["status"], "running")
            self.assertTrue(record["attempts"][1]["retry_feedback"])
            self.assertNotIn("metrics", record["attempts"][1])
            snapshot = deepcopy(record)
            self.finish_late_worker()
            self.assertEqual(record, snapshot)
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()