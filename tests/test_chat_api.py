import json
import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.main import app
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router

OPTIONS = {
    "start_date": "2026-11-20",
    "trip_days": 1,
    "transport": "driving",
}


def make_plan(days=1):
    return TripPlan(days=[
        DayPlan(day=number, items=[
            ScheduleItem(
                start_time="10:00",
                end_time="11:00",
                item_type="visit",
                title="모델이 작성한 장소명",
                place_id="TEST_PLACE",
                duration_minutes=60,
            )
        ])
        for number in range(1, days + 1)
    ])


def make_result(plan=None, options=None):
    return {
        "chat_reply": chat_router.ChatAgentReply(
            reply="여행 요청을 확인했습니다.",
            options=options if options is not None else {},
            plan=plan,
        ),
        "model_call_count": 1,
        "tool_call_counts": {},
    }


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.agent = self.enterContext(
            patch.object(chat_router, "run_chat_agent")
        )
        self.lookup = self.enterContext(
            patch.object(
                plan_router,
                "find_place_in_database",
                return_value={
                    "place_id": "TEST_PLACE",
                    "name": "테스트 여행지",
                    "category": "자연",
                    "latitude": 33.4,
                    "longitude": 126.5,
                },
            )
        )

    def test_message_only_question_skips_place_lookup(self):
        self.agent.return_value = make_result()

        response = self.client.post(
            "/chat", json={"message": "부모님과 여행하고 싶어요."}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNone(body["plan"])
        self.assertIsNone(body["options"]["start_date"])
        self.assertEqual(body["warnings"], [])
        self.agent.assert_called_once()
        self.lookup.assert_not_called()

    def test_history_and_options_reach_agent(self):
        self.agent.return_value = make_result()
        request_body = {
            "message": "둘째 날에는 바다를 넣어 주세요.",
            "history": [
                {"role": "user", "content": "2박 3일 여행이요."},
                {"role": "assistant", "content": "원하는 장소가 있나요?"},
            ],
            "options": {**OPTIONS, "trip_days": 3},
            "current_plan": {
                "assumptions": [],
                "days": [{"day": 1, "items": []}],
            },
        }

        response = self.client.post("/chat", json=request_body)

        self.assertEqual(response.status_code, 200)
        messages, schema = self.agent.call_args.args
        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant", "user"],
        )
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(payload["message"], request_body["message"])
        self.assertEqual(payload["options"]["trip_days"], 3)
        self.assertEqual(
            payload["current_plan"],
            request_body["current_plan"],
        )
        self.assertIs(schema, chat_router.ChatAgentReply)

    def test_multiple_day_plans_use_database_values(self):
        for days in [1, 2, 3, 5]:
            with self.subTest(days=days):
                self.agent.reset_mock()
                options = {**OPTIONS, "trip_days": days}
                self.agent.return_value = make_result(
                    make_plan(days), options
                )

                response = self.client.post(
                    "/chat",
                    json={
                        "message": "일정을 추천해 주세요.",
                        "options": options,
                    },
                )

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(
                    [day["day"] for day in body["plan"]["days"]],
                    list(range(1, days + 1)),
                )
                item = body["plan"]["days"][0]["items"][0]
                self.assertEqual(item["title"], "테스트 여행지")
                self.assertTrue(item["verified"])
                self.assertEqual(item["latitude"], 33.4)
                self.assertEqual(
                    body["options"]["start_date"], "2026-11-20"
                )
                self.assertEqual(body["warnings"], [])
                self.agent.assert_called_once()

    def test_missing_information_becomes_question(self):
        self.agent.return_value = make_result(make_plan())

        response = self.client.post(
            "/chat", json={"message": "일정을 추천해 주세요."}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNone(body["plan"])
        for label in ["출발 날짜", "여행 기간", "이동수단"]:
            self.assertIn(label, body["reply"])
        self.lookup.assert_not_called()
        self.agent.assert_called_once()

    def test_invalid_generated_plan_is_rejected(self):
        wrong_duration = make_plan()
        wrong_duration.days[0].items[0].duration_minutes = 59
        wrong_order = make_plan(3)
        wrong_order.days.reverse()

        cases = [
            (OPTIONS, wrong_duration, "소요시간"),
            ({**OPTIONS, "trip_days": 3}, make_plan(), "모든 일차"),
            ({**OPTIONS, "trip_days": 3}, wrong_order, "모든 일차"),
            (OPTIONS, TripPlan(days=[]), "모든 일차"),
        ]
        for options, plan, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                self.agent.reset_mock()
                self.agent.return_value = make_result(plan, options)

                response = self.client.post(
                    "/chat", json={"message": "일정을 추천해 주세요."}
                )

                self.assertEqual(response.status_code, 502)
                body = response.json()
                self.assertNotIn("plan", body)
                error = body["error"]
                self.assertEqual(
                    error["code"], "plan_validation_failed"
                )
                self.assertTrue(any(
                    expected_reason in detail["reason"]
                    for detail in error["details"]
                ))
                self.lookup.assert_not_called()
                self.assertEqual(self.agent.call_count, 2)

    def test_closed_day_visit_is_rejected(self):
        # 2026-11-23 월요일, P092 국립제주박물관은 매주 월요일 휴관 (실제 DB 값)
        options = {**OPTIONS, "start_date": "2026-11-23"}
        plan = make_plan()
        plan.days[0].items[0].place_id = "P092"
        self.agent.return_value = make_result(plan, options)

        response = self.client.post("/chat", json={"message": "일정을 추천해 주세요."})

        self.assertEqual(response.status_code, 502)
        error = response.json()["error"]
        self.assertEqual(error["code"], "plan_validation_failed")
        self.assertTrue(any(
            "2026-11-23(월) 휴무일" in detail["reason"]
            for detail in error["details"]
        ))
        self.lookup.assert_not_called()
        self.assertEqual(self.agent.call_count, 2)

    def test_visit_after_closing_time_is_rejected(self):
        # 2026-11-24 화요일, P092 국립제주박물관 09:00-18:00 (실제 DB 값)
        options = {**OPTIONS, "start_date": "2026-11-24"}
        plan = make_plan()
        plan.days[0].items[0].place_id = "P092"
        plan.days[0].items[0].start_time = "17:30"
        plan.days[0].items[0].end_time = "18:30"
        self.agent.return_value = make_result(plan, options)

        response = self.client.post("/chat", json={"message": "일정을 추천해 주세요."})

        self.assertEqual(response.status_code, 502)
        error = response.json()["error"]
        self.assertEqual(error["code"], "plan_validation_failed")
        self.assertTrue(any(
            "18:00에 닫는데 18:30에 끝납니다" in detail["reason"]
            for detail in error["details"]
        ))
        self.lookup.assert_not_called()

    def test_empty_days_with_explanation_are_allowed(self):
        plan = TripPlan(
            assumptions=["조건을 충족하는 장소를 찾지 못했습니다."],
            days=[
                DayPlan(day=number, items=[])
                for number in [1, 2, 3]
            ],
        )
        self.agent.return_value = make_result(
            plan, {**OPTIONS, "trip_days": 3}
        )

        response = self.client.post(
            "/chat", json={"message": "일정을 추천해 주세요."}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["plan"]["days"]), 3)
        self.assertTrue(
            all(not day["items"] for day in body["plan"]["days"])
        )
        self.assertEqual(
            body["plan"]["assumptions"], plan.assumptions
        )
        self.lookup.assert_not_called()

    def test_invalid_input_skips_agent(self):
        for body in [
            {"message": " "},
            {"message": "추천해 주세요.", "options": {"trip_days": 0}},
            {"message": "추천해 주세요.", "options": {"rest_minutes": 20}},
        ]:
            with self.subTest(body=body):
                response = self.client.post("/chat", json=body)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["error"]["code"],
                    "invalid_request",
                )
        self.agent.assert_not_called()

    def test_missing_structured_response_is_rejected(self):
        self.agent.return_value = {"chat_reply": None}

        response = self.client.post(
            "/chat", json={"message": "안녕하세요."}
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["error"]["code"],
            "llm_invalid_response",
        )
        self.lookup.assert_not_called()

    def test_agent_error_uses_shared_error_mapping(self):
        error = RuntimeError("test agent error")
        self.agent.side_effect = error

        with (
            patch.object(
                chat_router,
                "read_llm_error",
                return_value=(429, None),
            ) as read_error,
            self.assertLogs(
                "backend.app.routers.chat", level="ERROR"
            ),
        ):
            response = self.client.post(
                "/chat", json={"message": "일정을 추천해 주세요."}
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json()["error"]["code"], "rate_limited"
        )
        read_error.assert_called_once_with(error)
        self.lookup.assert_not_called()
        self.agent.assert_called_once()

    def test_database_error_returns_internal_error(self):
        self.agent.return_value = make_result(make_plan(), OPTIONS)
        self.lookup.side_effect = sqlite3.OperationalError(
            "test db error"
        )

        response = self.client.post(
            "/chat", json={"message": "일정을 추천해 주세요."}
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["error"]["code"], "internal_error"
        )
        self.agent.assert_called_once()


if __name__ == "__main__":
    unittest.main()