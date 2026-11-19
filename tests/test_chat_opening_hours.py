import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router


class ChatOpeningHoursTests(unittest.TestCase):
    def setUp(self):
        self.options = {
            "start_date": "2026-11-20",
            "trip_days": 1,
            "transport": "driving",
            "mobility_aid": "none",
            "requirements": [],
            "return_to_origin": False,
        }
        self.agent = self.enterContext(patch.object(chat_router, "run_chat_agent"))
        self.hours = self.enterContext(patch.object(
            chat_router, "read_opening_hours_by_place_id",
            return_value={"TEST": "09:00-18:00 입장마감 17:00"},
        ))
        self.enterContext(patch.object(
            chat_router, "read_closed_days_by_place_id", return_value={},
        ))
        self.place = self.enterContext(patch.object(
            plan_router, "find_place_in_database",
            return_value={
                "place_id": "TEST", "name": "확인된 장소", "category": "공원",
                "latitude": 33.4, "longitude": 126.5,
            },
        ))
        self.request = chat_router.ChatRequest(
            message="날짜와 조건을 유지하고 운영시간에 맞춰 주세요.",
            options=self.options,
        )

    def result(self, start="09:00", end="10:00", *, options=None, question=False):
        plan = None if question else TripPlan(days=[DayPlan(day=1, items=[
            ScheduleItem(
                start_time=start, end_time=end, item_type="visit",
                title="방문 장소", place_id="TEST", duration_minutes=60,
            ),
        ])])
        return {
            "chat_reply": chat_router.ChatAgentReply(
                reply="요청을 확인했습니다.",
                options=self.options if options is None else options,
                plan=plan,
            ),
            "messages": [],
            "route_calls": [],
            "model_call_count": 1,
            "tool_call_counts": {},
        }

    def post(self):
        result = chat_router.create_chat(self.request)
        if isinstance(result, dict):
            return 200, chat_router.ChatResponse.model_validate(
                result
            ).model_dump(mode="json")
        return result.status_code, json.loads(result.body)

    def feedback(self):
        messages = self.agent.call_args_list[1].args[0]
        return json.loads(messages[-1]["content"])["plan_retry"]

    def test_visit_before_opening_can_be_corrected_once(self):
        first = self.result("08:00", "09:00")
        original = copy.deepcopy(first["chat_reply"].model_dump(mode="json"))
        self.agent.side_effect = [first, self.result()]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(self.hours.call_count, 2)
        self.assertEqual(body["plan"]["days"][0]["items"][0]["start_time"], "09:00")
        self.assertEqual(body["meta"]["model_call_count"], 2)
        self.assertTrue(any(
            "09:00에 여는데" in reason
            for reason in self.feedback()["validation_errors"]
        ))
        self.assertEqual(
            self.feedback()["locked_options"]["start_date"], "2026-11-20"
        )
        self.assertEqual(first["chat_reply"].model_dump(mode="json"), original)

    def test_visit_after_last_entry_can_be_corrected_once(self):
        self.hours.return_value = {"TEST": "09:00-18:00 입장마감 16:00"}
        self.agent.side_effect = [
            self.result("16:30", "17:30"),
            self.result("16:00", "17:00"),
        ]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["plan"]["days"][0]["items"][0]["start_time"], "16:00")
        self.assertTrue(any(
            "입장 마감은 16:00" in reason
            for reason in self.feedback()["validation_errors"]
        ))

    def test_retry_feedback_contains_all_time_violations(self):
        self.agent.side_effect = [
            self.result("17:30", "18:30"), self.result("16:00", "17:00"),
        ]
        status, _ = self.post()
        self.assertEqual(status, 200)
        reasons = self.feedback()["validation_errors"]
        self.assertEqual(len(reasons), 2)
        self.assertTrue(any("입장 마감은 17:00" in reason for reason in reasons))
        self.assertTrue(any("18:00에 닫는데" in reason for reason in reasons))

    def test_second_invalid_attempt_stops_after_two_calls(self):
        self.agent.side_effect = [
            self.result("08:00", "09:00"), self.result("08:00", "09:00"),
        ]
        status, body = self.post()
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertEqual(self.agent.call_count, 2)
        self.assertNotIn("plan", body)
        self.place.assert_not_called()

    def test_exact_opening_last_entry_and_closing_are_allowed(self):
        for start, end in (("09:00", "10:00"), ("17:00", "18:00")):
            with self.subTest(start=start):
                self.agent.reset_mock()
                self.agent.side_effect = [self.result(start, end)]
                status, _ = self.post()
                self.assertEqual(status, 200)
                self.assertEqual(self.agent.call_count, 1)

    def test_unknown_and_always_open_do_not_trigger_retry(self):
        for hours in ({}, {"TEST": "상시 개방"}):
            with self.subTest(hours=hours):
                self.agent.reset_mock()
                self.hours.return_value = hours
                self.agent.side_effect = [self.result("22:00", "23:00")]
                status, _ = self.post()
                self.assertEqual(status, 200)
                self.assertEqual(self.agent.call_count, 1)

    def test_database_error_does_not_retry(self):
        self.hours.side_effect = sqlite3.OperationalError("mock database failure")
        self.agent.side_effect = [self.result()]
        status, body = self.post()
        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["code"], "internal_error")
        self.assertEqual(self.agent.call_count, 1)
        self.place.assert_not_called()

    def test_question_skips_operating_hours_lookup(self):
        self.agent.side_effect = [self.result(question=True)]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertIsNone(body["plan"])
        self.hours.assert_not_called()

    def post_visit_time_check(self, arrival_time, duration_minutes, *, place=None):
        response = self.result(question=True)
        agent_reply = response["chat_reply"].model_dump(mode="json")
        agent_reply["reply"] = "관람 시간을 줄이면 입장할 수 있습니다."
        agent_reply["options"]["transport"] = "walking"
        agent_reply["visit_time_check"] = {
            "place_id": "TEST",
            "visit_date": "2026-11-20",
            "arrival_time": arrival_time,
            "duration_minutes": duration_minutes,
        }
        response["chat_reply"] = agent_reply
        self.agent.return_value = response
        if place is None:
            place = {
                "status": "ok", "place_id": "TEST", "name": "테스트 박물관",
                "opening_hours": "09:00-18:00 입장마감 17:30",
                "closed_day_checks": {"2026-11-20": "영업"},
            }
        with patch.object(chat_router, "read_place_details", return_value=place) as lookup:
            status, body = self.post()
        self.assertEqual(status, 200)
        lookup.assert_called_once_with(place_id="TEST", visit_dates=["2026-11-20"])
        self.assertIsNone(body["plan"])
        self.assertEqual(body["options"]["transport"], "driving")
        self.assertEqual(self.agent.call_count, 1)
        return body["reply"]

    def test_visit_after_last_entry_offers_an_earlier_arrival_only(self):
        reply = self.post_visit_time_check("17:40", 30)
        self.assertIn("입장 마감 17:30", reply)
        self.assertIn("폐장 시각 18:00", reply)
        self.assertIn("30분 관람을 유지하려면 09:00부터 17:30까지", reply)
        self.assertNotIn("줄이", reply)
        self.assertNotIn("줄여", reply)

    def test_visit_at_last_entry_and_finishing_at_closing_is_possible(self):
        reply = self.post_visit_time_check("17:30", 30)
        self.assertIn("17:30부터 30분 관람할 수 있습니다", reply)

    def test_visit_at_last_entry_can_be_shortened_when_only_closing_is_exceeded(self):
        reply = self.post_visit_time_check("17:30", 45)
        self.assertIn("45분 관람을 유지하려면 09:00부터 17:15까지", reply)
        self.assertIn("30분 이내로 줄여", reply)
        self.assertNotIn("입장 마감", reply)

    def test_visit_after_last_entry_but_before_closing_only_mentions_entry_limit(self):
        reply = self.post_visit_time_check("17:40", 15)
        self.assertIn("입장 마감 17:30", reply)
        self.assertNotIn("폐장", reply)
        self.assertNotIn("줄여", reply)

    def test_closed_day_takes_priority_over_operating_hours(self):
        reply = self.post_visit_time_check("09:00", 60, place={
            "status": "ok", "place_id": "TEST", "name": "테스트 박물관",
            "opening_hours": "09:00-18:00 입장마감 17:30",
            "closed_day_checks": {"2026-11-20": "휴무"},
        })
        self.assertIn("휴무", reply)
        self.assertNotIn("관람할 수 있습니다", reply)

    def test_missing_operating_hours_does_not_claim_a_visit_is_possible(self):
        reply = self.post_visit_time_check("17:40", None, place={
            "status": "ok", "place_id": "TEST", "name": "테스트 박물관",
            "opening_hours": "확인 안 됨",
        })
        self.assertIn("운영시간을 확인하지 못해", reply)
        self.assertNotIn("입장할 수 있습니다", reply)

    def test_seasonal_opening_uses_the_requested_month(self):
        self.hours.return_value = {
            "TEST": "3-10월 09:00-18:00;11-2월 10:00-18:00"
        }
        self.agent.side_effect = [
            self.result(), self.result("10:00", "11:00"),
        ]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["plan"]["days"][0]["items"][0]["start_time"], "10:00")
        self.assertTrue(any(
            "10:00에 여는데" in reason
            for reason in self.feedback()["validation_errors"]
        ))

    def test_retry_cannot_change_the_date_to_avoid_validation(self):
        self.agent.side_effect = [
            self.result("08:00", "09:00"),
            self.result(options={**self.options, "start_date": "2026-11-21"}),
        ]
        status, body = self.post()
        self.assertEqual(status, 502)
        self.assertEqual(self.agent.call_count, 2)
        self.assertTrue(all(
            detail["field"] == "options" for detail in body["error"]["details"]
        ))


if __name__ == "__main__":
    unittest.main()
