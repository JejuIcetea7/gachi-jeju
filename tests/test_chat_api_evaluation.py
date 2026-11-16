import copy
import sqlite3
import unittest
from unittest.mock import patch

from backend.app.services import chat_api_evaluation as evaluation


ORIGIN = {"latitude": 33.4, "longitude": 126.5}
DESTINATION = {"latitude": 33.41, "longitude": 126.51}
OPTIONS = {
    "departure": "평가 출발지", "departure_latitude": 33.4,
    "departure_longitude": 126.5, "start_date": "2026-11-20",
    "trip_days": 1, "transport": "driving", "mobility_aid": "wheelchair",
    "requirements": ["accessible_toilet"], "return_to_origin": False,
}


def make_fixture():
    case = {
        "case_id": "E01", "description": "최종 응답 평가",
        "request": {"message": "일정을 만들어 주세요.", "options": copy.deepcopy(OPTIONS)},
        "expectation": {"outcome": "plan"},
    }
    visit = {
        "start_time": "09:10", "end_time": "10:10", "item_type": "visit",
        "title": "테스트 장소", "place_id": "TEST", "verified": True,
        "category": "공원", **DESTINATION, "transport": None,
        "duration_minutes": 60, "distance_km": None, "accessibility_notes": [],
        "route_id": None,
    }
    move = {
        **visit, "start_time": "09:00", "end_time": "09:10", "item_type": "move",
        "title": "출발지 → 방문지", "place_id": None, "verified": None,
        "category": None, "latitude": None, "longitude": None,
        "transport": "자동차", "duration_minutes": 10, "distance_km": 1,
        "route_id": "new",
    }
    body = {
        "reply": "최종 일정입니다.", "options": copy.deepcopy(OPTIONS),
        "plan": {"assumptions": [], "days": [{"day": 1, "items": [move, visit]}]},
        "warnings": [], "map_routes": [], "transit_routes": [],
        "meta": {"model_call_count": 1, "tool_call_counts": {}, "elapsed_seconds": 1},
    }
    trace = [
        {
            "call_id": "search", "tool": "search_places", "input": {},
            "output": {"status": "ok", "places": [{"place_id": "TEST"}]},
        },
        {
            "call_id": "drive", "tool": "get_driving_time", "input": {},
            "output": {"status": "ok", "duration_seconds": 600, "route_id": "new"},
        },
    ]
    routes = [{
        "route_id": "new", "transport": "driving", "origin": dict(ORIGIN),
        "destination": dict(DESTINATION), "duration_seconds": 600, "distance_m": 1000,
    }]
    attempt = {
        "attempt": 1, "status": "returned", "error_type": None,
        "capture_error": None, "retry_feedback": [],
        "chat_reply": copy.deepcopy(body), "tool_trace": trace, "route_calls": routes,
        "metrics": {"model_call_count": 1, "tool_call_counts": {}}, "process": None,
    }
    record = {
        "request": copy.deepcopy(case["request"]), "http_status": 200,
        "response": body, "attempts": [attempt], "elapsed_seconds": 1,
    }
    return case, record


def add_previous_attempt(record):
    previous = copy.deepcopy(record["attempts"][0])
    previous["route_calls"][0]["route_id"] = "old"
    previous["chat_reply"]["plan"]["days"][0]["items"][0]["route_id"] = "old"
    previous["chat_reply"]["plan"]["days"][0]["items"][0]["duration_minutes"] = 9
    record["attempts"][0]["attempt"] = 2
    record["attempts"][0]["retry_feedback"] = ["이동시간을 수정해 주세요."]
    record["attempts"].insert(0, previous)
    record["response"]["meta"]["model_call_count"] = 2


class ChatApiEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.places = self.enterContext(patch.object(
            evaluation, "read_places_by_id",
            return_value={"TEST": {"place_id": "TEST", "name": "테스트 장소"}},
        ))
        self.case, self.record = make_fixture()

    def score(self):
        return evaluation.evaluate_chat_record(self.case, self.record)

    def test_valid_final_response_passes(self):
        result = self.score()
        self.assertTrue(result["automatic_passed"], result["checks"])
        self.assertEqual(result["evaluation_status"], "ok")
        self.assertFalse(result["requires_manual_review"])

    def test_retry_is_evaluated_using_only_the_last_attempt(self):
        add_previous_attempt(self.record)
        self.record["attempts"][0]["tool_trace"] = [{
            "call_id": "old-bus", "tool": "get_bus_route_summary",
            "input": {}, "output": {"status": "ok"},
        }]
        result = self.score()
        self.assertTrue(result["automatic_passed"], result["checks"])
        self.assertEqual(result["attempt_count"], 2)

    def test_previous_search_cannot_supply_final_place_evidence(self):
        add_previous_attempt(self.record)
        self.record["attempts"][-1]["tool_trace"] = []
        result = self.score()
        self.assertFalse(result["automatic_passed"])
        self.assertFalse(result["checks"]["H5"]["passed"])

    def test_previous_route_cannot_supply_final_movement_evidence(self):
        add_previous_attempt(self.record)
        self.record["attempts"][-1]["route_calls"] = []
        result = self.score()
        self.assertFalse(result["checks"]["H6"]["passed"])
        self.assertFalse(result["automatic_passed"])

    def test_matching_detail_lookup_is_valid_place_evidence(self):
        self.record["attempts"][0]["tool_trace"][0] = {
            "call_id": "detail", "tool": "get_place_details",
            "input": {"place_id": "TEST"}, "output": {"status": "ok", "place_id": "TEST"},
        }
        self.assertTrue(self.score()["automatic_passed"])

    def test_mismatched_or_failed_detail_lookup_is_not_evidence(self):
        for output in ({"status": "ok", "place_id": "OTHER"}, {"status": "not_found"}):
            self.record["attempts"][0]["tool_trace"][0] = {
                "call_id": "detail", "tool": "get_place_details",
                "input": {"place_id": "TEST"}, "output": output,
            }
            self.assertFalse(self.score()["checks"]["H5"]["passed"])

    def test_wrong_duration_and_wrong_destination_are_detected(self):
        route = self.record["attempts"][0]["route_calls"][0]
        route["duration_seconds"] = 1200
        route["destination"] = {"latitude": 33.5, "longitude": 126.6}
        result = self.score()
        self.assertFalse(result["checks"]["H6"]["passed"])
        self.assertFalse(result["checks"]["endpoints"]["passed"])

    def test_time_span_and_requested_time_window_are_checked(self):
        self.case["time_window"] = {"start": "10:00", "end": "12:00"}
        self.record["response"]["plan"]["days"][0]["items"][1]["end_time"] = "11:00"
        self.assertFalse(self.score()["checks"]["H3"]["passed"])

    def test_date_mobility_requirements_and_false_option_are_preserved(self):
        for field, value in (
            ("start_date", "2026-11-21"), ("mobility_aid", "none"),
            ("requirements", []), ("return_to_origin", True),
        ):
            with self.subTest(field=field):
                self.case, self.record = make_fixture()
                self.record["response"]["options"][field] = value
                self.assertFalse(self.score()["checks"]["H8"]["passed"])

    def test_explicit_expected_option_can_describe_a_requested_change(self):
        self.case["expected_options"] = {"start_date": "2026-11-21"}
        self.record["response"]["options"]["start_date"] = "2026-11-21"
        self.assertTrue(self.score()["automatic_passed"])

    def test_wrong_selected_route_mode_is_detected_without_a_tool_call_name(self):
        for options in (self.case["request"]["options"], self.record["request"]["options"],
                        self.record["response"]["options"]):
            options["transport"] = "walking"
        self.record["attempts"][0]["tool_trace"] = self.record["attempts"][0]["tool_trace"][:1]
        self.assertFalse(self.score()["checks"]["H7"]["passed"])

    def test_walking_segment_is_allowed_for_a_driving_trip(self):
        self.record["response"]["plan"]["days"][0]["items"][0]["transport"] = "도보"
        self.record["attempts"][0]["route_calls"][0]["transport"] = "walking"
        self.record["attempts"][0]["tool_trace"][1]["tool"] = "get_walking_route"
        self.assertTrue(self.score()["automatic_passed"])

    def test_database_presence_does_not_replace_verified_flag(self):
        self.record["response"]["plan"]["days"][0]["items"][1]["verified"] = False
        self.assertFalse(self.score()["checks"]["H4"]["passed"])

    def test_missing_database_place_fails(self):
        self.places.return_value = {}
        self.assertFalse(self.score()["checks"]["H4"]["passed"])

    def test_evaluation_database_error_is_separate_from_server_failure(self):
        self.places.side_effect = sqlite3.OperationalError("private-db-detail")
        result = self.score()
        self.assertEqual(result["evaluation_status"], "error")
        self.assertEqual(result["outcome"], "plan")
        self.assertFalse(result["automatic_passed"])
        self.assertNotIn("private-db-detail", str(result))

    def test_expected_empty_plan_stays_pending_content_review(self):
        self.case["expectation"] = {"outcome": "empty_plan", "empty_days": [1]}
        self.record["response"]["plan"]["days"][0]["items"] = []
        result = self.score()
        self.assertTrue(result["automatic_passed"], result["checks"])
        self.assertTrue(result["requires_manual_review"])

    def test_expected_message_does_not_trigger_place_database_query(self):
        self.case["expectation"] = {"outcome": "message"}
        self.record["response"]["plan"] = None
        self.assertTrue(self.score()["requires_manual_review"])
        self.places.assert_not_called()

    def test_expected_input_error_needs_no_agent_attempt(self):
        self.case["request"] = {"message": ""}
        self.case["expectation"] = {
            "outcome": "error", "http_status": 400, "error_code": "invalid_request",
        }
        self.record.update({
            "request": copy.deepcopy(self.case["request"]), "attempts": [],
            "http_status": 400, "response": {"error": {"code": "invalid_request", "message": "입력 오류"}},
        })
        result = self.score()
        self.assertTrue(result["automatic_passed"])
        self.assertEqual(result["evaluation_status"], "ok")
        self.places.assert_not_called()

    def test_schema_failure_is_not_mistaken_for_a_valid_plan(self):
        self.record["response"].pop("meta")
        self.assertFalse(self.score()["checks"]["response_contract"]["passed"])

    def test_capture_failure_is_not_hidden_by_successful_http_response(self):
        self.record["attempts"][0]["capture_error"] = "AttributeError"
        result = self.score()
        self.assertEqual(result["evaluation_status"], "error")
        self.assertFalse(result["automatic_passed"])
        self.places.assert_not_called()

    def test_mismatched_request_missing_or_excess_attempts_are_rejected(self):
        for variant in ("request", "missing", "excess", "malformed"):
            with self.subTest(variant=variant):
                self.case, self.record = make_fixture()
                if variant == "request":
                    self.record["request"]["message"] = "다른 요청"
                elif variant == "missing":
                    self.record["attempts"] = []
                elif variant == "excess":
                    self.record["attempts"] *= 3
                else:
                    self.record["attempts"] = 1
                result = self.score()
                self.assertFalse(result["automatic_passed"])
                self.assertEqual(result["evaluation_status"], "error")

    def test_inputs_are_preserved(self):
        before = copy.deepcopy((self.case, self.record))
        result = self.score()
        result["checks"]["H4"]["reasons"].append("변경")
        self.assertEqual((self.case, self.record), before)

    def test_runner_and_evaluator_share_the_actual_http_record_contract(self):
        from datetime import date
        import json
        from unittest.mock import Mock
        from langchain_core.messages import AIMessage, ToolMessage
        from backend.app.routers import chat as chat_router
        from backend.app.routers import plan as plan_router
        from scripts.chat_evaluation_runner import run_chat_request

        body = self.record["response"]
        trace = self.record["attempts"][0]["tool_trace"]
        routes = copy.deepcopy(self.record["attempts"][0]["route_calls"])
        routes[0].update({
            "geometry_status": "ok", "requested_at": "2026-11-16T00:00:00+00:00",
            "paths": [[[126.5, 33.4], [126.51, 33.41]]],
        })
        agent_result = {
            "chat_reply": chat_router.ChatAgentReply.model_validate({
                key: body[key] for key in ("reply", "options", "plan")
            }),
            "model_call_count": 1,
            "tool_call_counts": {"search_places": 1, "get_driving_time": 1},
            "route_calls": routes,
            "messages": [
                AIMessage(content="", tool_calls=[
                    {"id": item["call_id"], "name": item["tool"], "args": item["input"]}
                    for item in trace
                ]),
                *[
                    ToolMessage(content=json.dumps(item["output"]), tool_call_id=item["call_id"])
                    for item in trace
                ],
            ],
        }
        with (
            patch.object(plan_router, "find_place_in_database", return_value={
                "place_id": "TEST", "name": "테스트 장소", "category": "공원", **DESTINATION,
            }),
            patch.object(chat_router, "read_closed_days_by_place_id", return_value={}),
            patch.object(chat_router, "read_opening_hours_by_place_id", return_value={}),
            patch.object(chat_router, "read_place_details", return_value={
                "status": "ok", "place_id": "TEST",
                "accessibility": {"accessible_toilet": "Y"}, "accessibility_facts": [],
            }),
        ):
            record = run_chat_request(
                self.case["request"], agent_runner=Mock(return_value=agent_result),
                reference_date=date(2026, 11, 16),
            )
        result = evaluation.evaluate_chat_record(self.case, record)
        self.assertEqual(record["http_status"], 200)
        self.assertTrue(result["automatic_passed"], result["checks"])

    def test_unknown_expected_option_is_rejected(self):
        self.case["expected_options"] = {"unknown": True}
        with self.assertRaises(ValueError):
            self.score()


if __name__ == "__main__":
    unittest.main()