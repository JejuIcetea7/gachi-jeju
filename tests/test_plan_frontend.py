import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.agent.planner import DayPlan, TripPlan
from backend.app.main import app
from backend.app.routers import plan as plan_router


FRONTEND_REQUEST = {
    "departure": "한림공원",
    "departure_latitude": 33.39022486,
    "departure_longitude": 126.2396912,
    "transport": "walking",
    "start_date": "2026-11-20",
    "trip_days": 1,
    "start_time": "13:00",
    "end_time": "17:00",
    "travel_styles": ["자연", "공원"],
    "requirements": ["step_free", "accessible_toilet", "accessible_parking"],
    "rest_minutes": 20,
    "return_to_origin": True,
    "message": "천천히 둘러보고 싶어요.",
}


class PlanFrontendTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.plan_trip = self.enterContext(
            patch.object(plan_router, "plan_trip")
        )
        self.plan_trip.return_value = {
            "trip_plan": TripPlan(
                assumptions=["테스트용 응답입니다."],
                days=[DayPlan(day=1, items=[])],
            ),
            "model_call_count": 1,
            "tool_call_counts": {},
        }

    def test_frontend_conditions_are_forwarded(self):
        modes = [
            ("walking", "도보"),
            ("driving", "자동차"),
            ("transit", "대중교통(버스·도보)"),
        ]
        for mode, label in modes:
            with self.subTest(mode=mode):
                request_body = {**FRONTEND_REQUEST, "transport": mode}
                response = self.client.post("/plan", json=request_body)
                self.assertEqual(response.status_code, 200)

                user_message = self.plan_trip.call_args.args[0]
                expected = [
                    "출발지: 한림공원",
                    f"이동수단: {label}",
                    "이동 보조기구: 미선택",
                    "확정한 출발지 좌표: 위도 33.39022486, 경도 126.2396912",
                    "여행 가능 시간: 13:00 ~ 17:00",
                    "필수 이용 조건: 계단 피하기, 장애인 화장실, 장애인 주차구역",
                    "중간 휴식: 20분, 1회",
                    "여행 종료: 출발지로 돌아오기",
                    "추가 요청: 천천히 둘러보고 싶어요.",
                ]
                for text in expected:
                    self.assertIn(text, user_message)

    def test_invalid_frontend_conditions_skip_agent(self):
        cases = [
            {"departure_latitude": None},
            {"departure_longitude": None},
            {"departure_latitude": 91},
            {"departure_longitude": 181},
            {"start_time": None},
            {"end_time": None},
            {"start_time": "9:00"},
            {"end_time": "25:00"},
            {"end_time": "13:00"},
            {"end_time": "12:00"},
            {"rest_minutes": -1},
            {"rest_minutes": 240},
            {"requirements": ["unknown"]},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                request_body = {**FRONTEND_REQUEST, **changes}
                response = self.client.post("/plan", json=request_body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["error"]["code"], "invalid_request"
                )
        self.plan_trip.assert_not_called()

    def test_message_length_boundary(self):
        request_body = {**FRONTEND_REQUEST, "message": "가" * 1000}
        response = self.client.post("/plan", json=request_body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            self.plan_trip.call_args.args[0].endswith("가" * 1000)
        )

        self.plan_trip.reset_mock()
        request_body["message"] = "가" * 1001
        response = self.client.post("/plan", json=request_body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"]["details"][0]["field"], "message"
        )
        self.plan_trip.assert_not_called()


if __name__ == "__main__":
    unittest.main()