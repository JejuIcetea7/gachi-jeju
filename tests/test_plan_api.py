import unittest
from unittest.mock import patch

import httpx2
import openai
from fastapi.testclient import TestClient

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.main import app
from backend.app.routers import plan as plan_router

VALID_REQUEST = {
    "departure": "제주국제공항",
    "transport": "rental_car",
    "mobility_aid": "wheelchair",
    "start_date": "2026-11-20",
    "trip_days": 1,
    "travel_styles": ["자연", "해변"],
    "message": "오전에는 덜 걷는 곳으로",
}

def build_trip_plan(place_id: str) -> TripPlan:
    move_item = ScheduleItem(
        start_time="10:00",
        end_time="10:20",
        item_type="move",
        title="제주국제공항 → 한라수목원",
        transport="렌터카",
        duration_minutes=19,
        distance_km=6.2,
    )
    visit_item = ScheduleItem(
        start_time="10:20",
        end_time="12:00",
        item_type="visit",
        title="한라 수목원",
        place_id=place_id,
        duration_minutes=100,
        accessibility_notes=["장애인 주차장(승용차 5대, 대형 1대)"],
    )
    day_plan = DayPlan(day=1, items=[move_item, visit_item])
    return TripPlan(assumptions=["10:00 제주국제공항 출발"], days=[day_plan])

def build_agent_result(place_id: str) -> dict:
    return {
        "trip_plan": build_trip_plan(place_id),
        "model_call_count": 5,
        "tool_call_counts": {"search_places": 2},
    }

def build_openai_status_error(status_code: int, error_code: str | None) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx2.Response(status_code, request=request)
    body = {"code": error_code, "message": "test error"}
    if status_code == 429:
        return openai.RateLimitError("test error", response=response, body=body)
    return openai.InternalServerError("test error", response=response, body=body)

class PlanApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.plan_trip = self.enterContext(patch.object(plan_router, "plan_trip"))

    def test_health(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_success_replaces_title_with_database_name(self):
        self.plan_trip.return_value = build_agent_result("P044")

        response = self.client.post("/plan", json=VALID_REQUEST)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        move_item, visit_item = body["plan"]["days"][0]["items"]
        self.assertIsNone(move_item["verified"])
        self.assertTrue(visit_item["verified"])
        self.assertEqual(visit_item["title"], "한라수목원")
        self.assertEqual(visit_item["category"], "자연")
        self.assertIsNotNone(visit_item["latitude"])
        self.assertEqual(body["warnings"], [])
        self.assertEqual(body["meta"]["model_call_count"], 5)

    def test_user_message_contains_selected_options(self):
        self.plan_trip.return_value = build_agent_result("P044")

        self.client.post("/plan", json=VALID_REQUEST)

        user_message = self.plan_trip.call_args.args[0]
        self.assertIn("출발지: 제주국제공항", user_message)
        self.assertIn("이동수단: 렌터카", user_message)
        self.assertIn("이동 보조기구: 휠체어", user_message)
        self.assertIn("여행 스타일: 자연, 해변", user_message)
        self.assertIn("추가 요청: 오전에는 덜 걷는 곳으로", user_message)

    def test_unknown_place_is_marked_unverified(self):
        self.plan_trip.return_value = build_agent_result("P999")

        response = self.client.post("/plan", json=VALID_REQUEST)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        visit_item = body["plan"]["days"][0]["items"][1]
        self.assertFalse(visit_item["verified"])
        self.assertEqual(visit_item["title"], "한라 수목원")
        self.assertEqual(len(body["warnings"]), 1)

    def test_invalid_requests(self):
        cases = [
            ("departure", {"departure": "   "}),
            ("message", {"message": "가" * 301}),
            ("transport", {"transport": "airplane"}),
            ("trip_days", {"trip_days": 4}),
            ("travel_styles", {"travel_styles": []}),
            ("start_date", {"start_date": "11월 20일"}),
        ]
        for field, changes in cases:
            with self.subTest(field=field):
                request_body = dict(VALID_REQUEST)
                request_body.update(changes)

                response = self.client.post("/plan", json=request_body)

                self.assertEqual(response.status_code, 400)
                error = response.json()["error"]
                self.assertEqual(error["code"], "invalid_request")
                self.assertEqual(error["details"][0]["field"], field)
        self.plan_trip.assert_not_called()

    def test_missing_field(self):
        request_body = dict(VALID_REQUEST)
        del request_body["departure"]

        response = self.client.post("/plan", json=request_body)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["details"][0]["reason"], "필수 항목입니다.")

    def test_llm_errors(self):
        request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
        cases = [
            (build_openai_status_error(429, "rate_limit_exceeded"), 429, "rate_limited"),
            (build_openai_status_error(429, "insufficient_quota"), 503, "llm_quota_exhausted"),
            (build_openai_status_error(500, None), 503, "llm_unavailable"),
            (openai.APITimeoutError(request=request), 504, "llm_timeout"),
            (openai.APIConnectionError(request=request), 503, "llm_unavailable"),
            (RuntimeError("unknown"), 500, "internal_error"),
        ]

        for error, expected_status, expected_code in cases:
            with self.subTest(expected_code=expected_code, status=expected_status):
                self.plan_trip.side_effect = error

                with self.assertLogs("backend.app.routers.plan", level="ERROR"):
                    response = self.client.post("/plan", json=VALID_REQUEST)

                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["error"]["code"], expected_code)

    def test_wrapped_llm_error_is_found_through_cause(self):
        wrapped_error = RuntimeError("wrapped")
        wrapped_error.__cause__ = build_openai_status_error(429, "rate_limit_exceeded")
        self.plan_trip.side_effect = wrapped_error

        with self.assertLogs("backend.app.routers.plan", level="ERROR"):
            response = self.client.post("/plan", json=VALID_REQUEST)

        self.assertEqual(response.status_code, 429)

    def test_plan_not_in_expected_format(self):
        self.plan_trip.return_value = {
            "trip_plan": None,
            "model_call_count": 3,
            "tool_call_counts": {},
        }

        response = self.client.post("/plan", json=VALID_REQUEST)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "llm_invalid_response")

if __name__ == "__main__":
    unittest.main()
