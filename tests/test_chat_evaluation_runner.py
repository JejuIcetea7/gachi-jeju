import copy
from datetime import date, datetime
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, ToolMessage

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router
from scripts.chat_evaluation_runner import run_chat_request


ORIGIN = {"latitude": 33.4, "longitude": 126.5}
DESTINATION = {"latitude": 33.41, "longitude": 126.51}
OPTIONS = {
    "departure": "평가 출발지",
    "departure_latitude": ORIGIN["latitude"],
    "departure_longitude": ORIGIN["longitude"],
    "start_date": "2026-11-20",
    "trip_days": 1,
    "transport": "driving",
    "mobility_aid": "none",
    "requirements": [],
    "return_to_origin": False,
}


def make_result(route_id="current", *, minutes=10, plan=True):
    generated = None
    routes = []
    if plan:
        generated = TripPlan(days=[DayPlan(day=1, items=[
            ScheduleItem(
                start_time="09:00", end_time="09:10", item_type="move",
                title="출발지 → 방문지", transport="자동차",
                duration_minutes=minutes, route_id=route_id,
            ),
            ScheduleItem(
                start_time="09:10", end_time="10:10", item_type="visit",
                title="모델 장소명", place_id="EVAL_PLACE", duration_minutes=60,
            ),
        ])])
        routes = [{
            "route_id": route_id, "transport": "driving",
            "origin": dict(ORIGIN), "destination": dict(DESTINATION),
            "distance_m": 1000, "duration_seconds": 600,
            "geometry_status": "ok",
            "requested_at": "2026-11-16T00:00:00+00:00",
            "paths": [[[126.5, 33.4], [126.51, 33.41]]],
        }]
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "search", "name": "search_places", "args": {}}],
            usage_metadata={
                "input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
            },
        ),
        ToolMessage(
            content=json.dumps({"places": [{"place_id": "EVAL_PLACE"}]}),
            tool_call_id="search",
        ),
    ]
    return {
        "chat_reply": chat_router.ChatAgentReply(
            reply="요청 결과입니다.", options=OPTIONS, plan=generated,
        ),
        "route_calls": routes, "messages": messages,
        "model_call_count": 1, "tool_call_counts": {"search_places": 1},
    }


class ChatEvaluationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.request = {"message": "관광 일정을 만들어 주세요.", "options": OPTIONS}
        self.enterContext(patch.object(
            plan_router, "find_place_in_database",
            return_value={
                "place_id": "EVAL_PLACE", "name": "DB 장소명",
                "category": "공원", **DESTINATION,
            },
        ))
        self.closed = self.enterContext(patch.object(
            chat_router, "read_closed_days_by_place_id", return_value={},
        ))
        self.hours = self.enterContext(patch.object(
            chat_router, "read_opening_hours_by_place_id", return_value={},
        ))
        self.enterContext(patch.object(chat_router.logger, "exception"))

    def run_request(self, runner, body=None):
        return run_chat_request(
            self.request if body is None else body,
            agent_runner=runner, reference_date=date(2026, 11, 16),
        )

    def test_actual_http_response_has_database_name(self):
        record = self.run_request(Mock(return_value=make_result()))
        self.assertEqual(record["http_status"], 200)
        visit = record["response"]["plan"]["days"][0]["items"][1]
        self.assertEqual(visit["title"], "DB 장소명")
        self.assertTrue(visit["verified"])
        raw = record["attempts"][0]["chat_reply"]["plan"]["days"][0]["items"][1]
        self.assertEqual(raw["title"], "모델 장소명")

    def test_retry_keeps_attempts_separate_and_returns_final_routes(self):
        runner = Mock(side_effect=[make_result("old", minutes=9), make_result("new")])
        record = self.run_request(runner)
        self.assertEqual(record["http_status"], 200)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(
            [attempt["route_calls"][0]["route_id"] for attempt in record["attempts"]],
            ["old", "new"],
        )
        self.assertTrue(record["attempts"][1]["retry_feedback"])
        self.assertEqual(record["response"]["map_routes"][0]["route_id"], "new")
        self.assertEqual(record["response"]["meta"]["model_call_count"], 2)

    def test_second_validation_failure_is_http_error(self):
        runner = Mock(side_effect=[make_result(minutes=9), make_result(minutes=9)])
        record = self.run_request(runner)
        self.assertEqual(record["http_status"], 502)
        self.assertEqual(record["response"]["error"]["code"], "plan_validation_failed")
        self.assertNotIn("plan", record["response"])
        self.assertEqual(len(record["attempts"]), 2)

    def test_question_is_not_retried(self):
        runner = Mock(return_value=make_result(plan=False))
        record = self.run_request(runner)
        self.assertEqual(record["http_status"], 200)
        self.assertIsNone(record["response"]["plan"])
        self.assertEqual(runner.call_count, 1)
        self.closed.assert_not_called()

    def test_empty_plan_is_preserved_for_later_outcome_scoring(self):
        result = make_result(plan=False)
        result["chat_reply"].plan = TripPlan(
            assumptions=["조건에 맞는 방문지를 찾지 못했습니다."],
            days=[DayPlan(day=1, items=[])],
        )
        record = self.run_request(Mock(return_value=result))
        self.assertEqual(record["http_status"], 200)
        self.assertEqual(record["response"]["plan"]["days"][0]["items"], [])

    def test_invalid_request_never_calls_agent(self):
        runner = Mock()
        record = self.run_request(runner, {"message": ""})
        self.assertEqual(record["http_status"], 400)
        self.assertEqual(record["response"]["error"]["code"], "invalid_request")
        self.assertEqual(record["attempts"], [])
        runner.assert_not_called()

    def test_database_failure_does_not_retry(self):
        self.closed.side_effect = sqlite3.OperationalError("test")
        runner = Mock(return_value=make_result())
        record = self.run_request(runner)
        self.assertEqual(record["http_status"], 500)
        self.assertEqual(record["response"]["error"]["code"], "internal_error")
        self.assertEqual(runner.call_count, 1)

    def test_invalid_agent_schema_is_recorded_as_http_failure(self):
        result = make_result(plan=False)
        result["chat_reply"] = None
        record = self.run_request(Mock(return_value=result))
        self.assertEqual(record["http_status"], 502)
        self.assertEqual(record["response"]["error"]["code"], "llm_invalid_response")

    def test_provider_exception_keeps_type_without_private_message(self):
        runner = Mock(side_effect=TimeoutError("private-provider-detail"))
        record = self.run_request(runner)
        self.assertGreaterEqual(record["http_status"], 400)
        self.assertEqual(record["attempts"][0]["status"], "error")
        self.assertEqual(record["attempts"][0]["error_type"], "TimeoutError")
        self.assertNotIn("private-provider-detail", json.dumps(record))
        self.assertNotIn("metrics", record["attempts"][0])

    def test_exception_on_retry_keeps_successfully_captured_first_attempt(self):
        runner = Mock(side_effect=[
            make_result(minutes=9), TimeoutError("private-provider-detail"),
        ])
        record = self.run_request(runner)
        self.assertGreaterEqual(record["http_status"], 400)
        self.assertEqual(len(record["attempts"]), 2)
        self.assertEqual(record["attempts"][0]["metrics"]["model_call_count"], 1)
        self.assertEqual(record["attempts"][1]["status"], "error")
        self.assertNotIn("metrics", record["attempts"][1])

    def test_observer_failure_does_not_change_server_response(self):
        result = make_result(plan=False)
        result["messages"] = [object()]
        record = self.run_request(Mock(return_value=result))
        self.assertEqual(record["http_status"], 200)
        self.assertIsNone(record["response"]["plan"])
        self.assertEqual(record["attempts"][0]["capture_error"], "AttributeError")

    def test_geometry_is_omitted_and_snapshot_is_independent(self):
        result = make_result()
        record = self.run_request(Mock(return_value=result))
        route = record["attempts"][0]["route_calls"][0]
        self.assertNotIn("paths", route)
        self.assertEqual(route["origin"], ORIGIN)
        route["origin"]["latitude"] = 0
        self.assertEqual(result["route_calls"][0]["origin"], ORIGIN)
        record["attempts"][0]["chat_reply"]["reply"] = "변경"
        self.assertEqual(result["chat_reply"].reply, "요청 결과입니다.")

    def test_tool_trace_and_process_metrics_are_captured(self):
        record = self.run_request(Mock(return_value=make_result()))
        attempt = record["attempts"][0]
        self.assertEqual(attempt["tool_trace"][0]["tool"], "search_places")
        self.assertEqual(attempt["process"]["input_tokens"], 100)
        self.assertEqual(attempt["process"]["output_tokens"], 10)

    def test_reference_date_request_and_global_functions_are_restored(self):
        previous_agent = chat_router.run_chat_agent
        previous_builder = chat_router.build_chat_messages
        before = copy.deepcopy(self.request)
        runner = Mock(return_value=make_result(plan=False))
        record = self.run_request(runner)
        payload = json.loads(runner.call_args.args[0][-1]["content"])
        self.assertEqual(payload["reference_date"], "2026-11-16")
        self.assertEqual(record["reference_date"], "2026-11-16")
        self.assertEqual(self.request, before)
        self.assertIs(chat_router.run_chat_agent, previous_agent)
        self.assertIs(chat_router.build_chat_messages, previous_builder)

    def test_error_response_also_restores_global_functions(self):
        previous_agent = chat_router.run_chat_agent
        previous_builder = chat_router.build_chat_messages
        self.run_request(Mock(side_effect=RuntimeError("test")))
        self.assertIs(chat_router.run_chat_agent, previous_agent)
        self.assertIs(chat_router.build_chat_messages, previous_builder)

    def test_missing_message_records_are_not_reported_as_zero_token_usage(self):
        result = make_result(plan=False)
        result.pop("messages")
        record = self.run_request(Mock(return_value=result))
        self.assertIsNone(record["attempts"][0]["process"])
        self.assertEqual(record["attempts"][0]["metrics"]["model_call_count"], 1)

    def test_invalid_helper_arguments_are_rejected_before_running_agent(self):
        runner = Mock()
        with self.assertRaises(ValueError):
            run_chat_request([], agent_runner=runner)
        with self.assertRaises(ValueError):
            run_chat_request({}, agent_runner=None)
        with self.assertRaises(ValueError):
            run_chat_request({}, agent_runner=runner, reference_date=datetime.now())
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()