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
            detail["field"] == "response" for detail in body["error"]["details"]
        ))

    def time_reply(self, arrival, duration, available, text="모델이 조회 자료로 작성한 답변입니다."):
        result = self.result(question=True)
        result["chat_reply"].reply = text
        result["chat_reply"].visit_time_check = chat_router.VisitTimeCheck(
            place_id="TEST", visit_date="2026-11-20", arrival_time=arrival,
            duration_minutes=duration, available=available)
        return result

    def test_last_entry_and_closing_boundaries_are_numeric_checks(self):
        place = {"status": "ok", "place_id": "TEST", "name": "박물관",
                 "opening_hours": "09:00-18:00 입장마감 17:30",
                 "closed_day_checks": {"2026-11-20": "영업"}}
        for arrival, duration, available in (("17:30", 30, True), ("17:40", 15, False), ("17:30", 45, False)):
            with self.subTest(arrival=arrival, duration=duration):
                result = self.time_reply(arrival, duration, available)
                self.agent.side_effect = None
                self.agent.return_value = result
                self.agent.reset_mock()
                with patch.object(chat_router, "read_place_details", return_value=place):
                    status, body = self.post()
                self.assertEqual(status, 200)
                self.assertEqual(body["reply"], result["chat_reply"].reply)
                self.agent.assert_called_once()

    def test_wrong_visit_verdict_returns_fact_feedback_to_agent(self):
        place = {"status": "ok", "place_id": "TEST", "opening_hours": "09:00-18:00 입장마감 17:30"}
        first = self.time_reply("17:40", 15, True, "방문할 수 있습니다.")
        second = self.time_reply("17:40", 15, False, "입장 마감 이후 도착하므로 방문할 수 없습니다.")
        self.agent.side_effect = [first, second]
        with patch.object(chat_router, "read_place_details", return_value=place):
            status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], second["chat_reply"].reply)
        self.assertTrue(any("기대값" in reason for reason in self.feedback()["validation_errors"]))

    def test_closed_day_and_unknown_hours_remain_distinct(self):
        for hours, closed, available in (("09:00-18:00", "휴무", False), ("확인 안 됨", None, None)):
            with self.subTest(hours=hours):
                result = self.time_reply("10:00", 60, available)
                self.agent.side_effect = None
                self.agent.return_value = result
                place = {"status": "ok", "place_id": "TEST", "opening_hours": hours,
                         "closed_day_checks": {"2026-11-20": closed}}
                with patch.object(chat_router, "read_place_details", return_value=place):
                    status, body = self.post()
                self.assertEqual(status, 200)
                self.assertEqual(body["reply"], result["chat_reply"].reply)


    def test_midnight_and_always_open_verdicts(self):
        cases = (
            ("상시 개방", "23:00", 60, True),
            ("상시 개방", "23:30", 60, None),
            ("09:00-24:00", "23:30", 30, True),
            ("09:00-24:00", "23:30", 60, False),
        )
        for hours, arrival, duration, available in cases:
            with self.subTest(hours=hours, arrival=arrival, duration=duration):
                result = self.time_reply(arrival, duration, available)
                self.agent.side_effect = None
                self.agent.return_value = result
                self.agent.reset_mock()
                place = {"status": "ok", "place_id": "TEST", "opening_hours": hours,
                         "closed_day_checks": {"2026-11-20": "영업"}}
                with patch.object(chat_router, "read_place_details", return_value=place):
                    status, body = self.post()
                self.assertEqual(status, 200)
                self.agent.assert_called_once()

    def test_missing_place_expects_unknown_verdict(self):
        result = self.time_reply("10:00", 60, True)
        self.agent.side_effect = [result, self.time_reply("10:00", 60, None)]
        with patch.object(chat_router, "read_place_details", return_value={"status": "not_found"}):
            status, body = self.post()
        self.assertEqual(status, 200)
        self.assertTrue(any("찾지 못함" in reason for reason in self.feedback()["validation_errors"]))

    def test_visit_time_data_error_is_not_fed_back_as_model_error(self):
        self.agent.side_effect = None
        self.agent.return_value = self.time_reply("10:00", 60, True)
        self.agent.reset_mock()
        for place in ({"status": "db_error"}, {"status": "ok", "place_id": "OTHER"}):
            with self.subTest(place=place):
                with patch.object(chat_router, "read_place_details", return_value=place):
                    status, body = self.post()
                self.assertEqual(status, 500)
                self.assertEqual(body["error"]["code"], "internal_error")
        self.assertEqual(self.agent.call_count, 2)


if __name__ == "__main__":
    unittest.main()
