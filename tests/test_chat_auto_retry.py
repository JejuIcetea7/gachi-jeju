import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router


ORIGIN = {"latitude": 33.4, "longitude": 126.5}
FIRST = {"latitude": 33.41, "longitude": 126.51}
SECOND = {"latitude": 33.42, "longitude": 126.52}
OTHER = {"latitude": 33.5, "longitude": 126.6}
OPTIONS = {
    "departure": "테스트 출발지",
    "departure_latitude": ORIGIN["latitude"],
    "departure_longitude": ORIGIN["longitude"],
    "start_date": "2026-11-23",
    "trip_days": 1,
    "transport": "driving",
    "mobility_aid": "wheelchair",
    "travel_styles": ["자연", "공원"],
    "requirements": ["step_free", "accessible_toilet"],
    "return_to_origin": False,
}


def make_plan(route_id="route", place_id="TEST_A", *, minutes=10):
    return TripPlan(days=[DayPlan(day=1, items=[
        ScheduleItem(
            start_time="09:00", end_time="09:10", item_type="move",
            title="출발지 → 방문지", transport="자동차",
            duration_minutes=minutes, route_id=route_id,
        ),
        ScheduleItem(
            start_time="09:10", end_time="10:10", item_type="visit",
            title="방문지", place_id=place_id, duration_minutes=60,
        ),
    ])])


def make_route(route_id="route", destination=FIRST, *, seconds=600):
    return {
        "route_id": route_id,
        "transport": "driving",
        "origin": dict(ORIGIN),
        "destination": dict(destination),
        "distance_m": 1000,
        "duration_seconds": seconds,
        "geometry_status": "ok",
        "paths": [[
            [ORIGIN["longitude"], ORIGIN["latitude"]],
            [destination["longitude"], destination["latitude"]],
        ]],
        "requested_at": "2026-11-13T00:00:00+00:00",
    }


def make_result(
    plan=None, *, routes=None, options=None, model_calls=1, tool_calls=None,
):
    return {
        "chat_reply": chat_router.ChatAgentReply(
            reply="요청을 확인했습니다.",
            options=OPTIONS if options is None else options,
            plan=plan,
        ),
        "route_calls": [] if routes is None else copy.deepcopy(routes),
        "messages": [],
        "model_call_count": model_calls,
        "tool_call_counts": {} if tool_calls is None else dict(tool_calls),
    }


def good_result(route_id="new"):
    return make_result(make_plan(route_id), routes=[make_route(route_id)])


def bad_result():
    return make_result(
        make_plan("old", minutes=9),
        routes=[make_route("old")],
    )


class ChatAutoRetryTests(unittest.TestCase):
    def setUp(self):
        self.agent = self.enterContext(
            patch.object(chat_router, "run_chat_agent")
        )
        places = {
            "TEST_A": {"place_id": "TEST_A", "name": "첫 장소", **FIRST},
            "TEST_B": {"place_id": "TEST_B", "name": "대체 장소", **SECOND},
        }
        self.lookup = self.enterContext(patch.object(
            plan_router, "find_place_in_database",
            side_effect=lambda place_id: {
                **places[place_id], "category": "공원"
            },
        ))
        self.closed_lookup = self.enterContext(patch.object(
            chat_router, "read_closed_days_by_place_id", return_value={},
        ))
        self.enterContext(patch.object(chat_router.logger, "exception"))
        self.request = chat_router.ChatRequest(
            message="여행 조건을 유지해 일정을 만들어 주세요.",
            options=OPTIONS,
        )

    def post(self):
        result = chat_router.create_chat(self.request)
        if isinstance(result, dict):
            return 200, chat_router.ChatResponse.model_validate(
                result
            ).model_dump(mode="json")
        return result.status_code, json.loads(result.body)

    def retry_payload(self):
        messages = self.agent.call_args_list[1].args[0]
        return json.loads(messages[-1]["content"])

    def assert_validation_error(self, status, body):
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertNotIn("plan", body)
        self.assertNotIn("map_routes", body)

    def test_valid_first_attempt_does_not_retry(self):
        self.agent.side_effect = [good_result()]
        status, body = self.post()

        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 1)
        self.assertEqual(body["meta"]["model_call_count"], 1)
        messages = self.agent.call_args.args[0]
        self.assertNotIn("plan_retry", json.loads(messages[-1]["content"]))

    def test_time_error_is_fixed_with_feedback_and_combined_usage(self):
        first = bad_result()
        first.update({
            "model_call_count": 2,
            "tool_call_counts": {"search_places": 1},
        })
        second = good_result()
        second.update({
            "model_call_count": 3,
            "tool_call_counts": {"search_places": 2, "get_driving_time": 1},
        })
        self.request = chat_router.ChatRequest(
            message="2026년 11월 23일 자동차로 공원에 가고 싶어요.",
            history=[{"role": "assistant", "content": "조건을 알려 주세요."}],
        )
        before = copy.deepcopy(self.request.model_dump(mode="json"))
        self.agent.side_effect = [first, second]

        status, body = self.post()

        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["plan"]["days"][0]["items"][0]["route_id"], "new")
        self.assertEqual(body["meta"]["model_call_count"], 5)
        self.assertEqual(body["meta"]["tool_call_counts"], {
            "search_places": 3, "get_driving_time": 1,
        })
        initial = json.loads(self.agent.call_args_list[0].args[0][-1]["content"])
        retry = self.retry_payload()
        for key, value in initial.items():
            self.assertEqual(retry[key], value)
        locked = first["chat_reply"].options.model_dump(mode="json")
        self.assertEqual(retry["plan_retry"]["locked_options"], locked)
        self.assertTrue(retry["plan_retry"]["validation_errors"])
        for item in retry["plan_retry"]["failed_plan"]["days"][0]["items"]:
            self.assertIsNone(item["route_id"])
        self.assertEqual(self.request.model_dump(mode="json"), before)
        self.assertEqual(first["chat_reply"].plan.days[0].items[0].route_id, "old")

    def test_second_validation_failure_stops_after_two_calls(self):
        second = make_result(
            make_plan("second", minutes=8), routes=[make_route("second")]
        )
        self.agent.side_effect = [bad_result(), second]
        status, body = self.post()

        self.assert_validation_error(status, body)
        self.assertEqual(self.agent.call_count, 2)
        self.assertTrue(any(
            "8" in detail["reason"] for detail in body["error"]["details"]
        ))
        self.lookup.assert_not_called()

    def test_changed_conditions_are_rejected(self):
        for changes in [
            {"transport": "walking"},
            {"requirements": []},
            {"start_date": "2026-11-24"},
            {"mobility_aid": "none"},
        ]:
            with self.subTest(changes=changes):
                self.agent.reset_mock()
                changed = make_result(
                    make_plan("new"), routes=[make_route("new")],
                    options={**OPTIONS, **changes},
                )
                self.agent.side_effect = [bad_result(), changed]
                status, body = self.post()

                self.assert_validation_error(status, body)
                self.assertEqual(self.agent.call_count, 2)
                self.assertTrue(all(
                    detail["field"] == "options"
                    for detail in body["error"]["details"]
                ))
                self.lookup.assert_not_called()

    def test_reordered_conditions_are_allowed(self):
        options = copy.deepcopy(OPTIONS)
        options["requirements"].reverse()
        options["travel_styles"].reverse()
        self.agent.side_effect = [
            bad_result(),
            make_result(
                make_plan("new"), routes=[make_route("new")], options=options,
            ),
        ]
        status, body = self.post()

        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)

    def test_previous_route_records_are_not_reused(self):
        self.agent.side_effect = [
            bad_result(), make_result(make_plan("old"), routes=[]),
        ]
        status, body = self.post()

        self.assert_validation_error(status, body)
        self.assertEqual(self.agent.call_count, 2)
        self.assertTrue(any(
            "이번 요청" in detail["reason"] for detail in body["error"]["details"]
        ))
        self.lookup.assert_not_called()

    def test_second_coordinate_failure_does_not_retry_again(self):
        self.agent.side_effect = [
            bad_result(),
            make_result(
                make_plan("new"), routes=[make_route("new", OTHER)],
            ),
        ]
        status, body = self.post()

        self.assert_validation_error(status, body)
        self.assertEqual(self.agent.call_count, 2)
        self.assertTrue(any(
            "도착 위치" in detail["reason"] for detail in body["error"]["details"]
        ))

    def test_coordinate_error_can_be_corrected(self):
        self.agent.side_effect = [
            make_result(
                make_plan("wrong"), routes=[make_route("wrong", OTHER)],
            ),
            good_result(),
        ]
        status, body = self.post()

        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(self.lookup.call_count, 2)
        self.assertTrue(any(
            "도착 위치" in reason
            for reason in self.retry_payload()["plan_retry"]["validation_errors"]
        ))

    def test_closed_place_can_be_replaced_with_open_place(self):
        self.closed_lookup.return_value = {
            "TEST_A": "매주 월요일",
            "TEST_B": "연중무휴",
        }
        self.agent.side_effect = [
            good_result("closed"),
            make_result(
                make_plan("open", "TEST_B"),
                routes=[make_route("open", SECOND)],
            ),
        ]
        status, body = self.post()

        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["plan"]["days"][0]["items"][1]["place_id"], "TEST_B")
        self.lookup.assert_called_once_with("TEST_B")
        self.assertTrue(any(
            "휴무일" in reason
            for reason in self.retry_payload()["plan_retry"]["validation_errors"]
        ))

    def test_question_does_not_retry(self):
        self.agent.side_effect = [make_result()]
        status, body = self.post()

        self.assertEqual(status, 200)
        self.assertIsNone(body["plan"])
        self.assertEqual(self.agent.call_count, 1)
        self.lookup.assert_not_called()

    def test_retry_can_return_question_without_changing_conditions(self):
        self.agent.side_effect = [bad_result(), make_result()]
        status, body = self.post()

        self.assertEqual(status, 200)
        self.assertIsNone(body["plan"])
        self.assertEqual(body["map_routes"], [])
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["options"]["requirements"], OPTIONS["requirements"])

    def test_invalid_response_schema_does_not_retry(self):
        self.agent.side_effect = [{"chat_reply": None}]
        status, body = self.post()

        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "llm_invalid_response")
        self.assertEqual(self.agent.call_count, 1)

    def test_provider_error_does_not_retry(self):
        self.agent.side_effect = RuntimeError("mock provider failure")
        with patch.object(
            chat_router, "read_llm_error", return_value=(429, "rate_limit"),
        ):
            status, body = self.post()

        self.assertEqual(status, 429)
        self.assertEqual(self.agent.call_count, 1)

    def test_provider_error_on_second_attempt_does_not_repeat(self):
        self.agent.side_effect = [bad_result(), RuntimeError("mock timeout")]
        with patch.object(
            chat_router, "read_llm_error", return_value=(504, None),
        ):
            status, body = self.post()

        self.assertEqual(status, 504)
        self.assertEqual(self.agent.call_count, 2)

    def test_database_error_does_not_retry(self):
        self.lookup.side_effect = sqlite3.OperationalError("mock database failure")
        self.agent.side_effect = [good_result()]
        status, body = self.post()

        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["code"], "internal_error")
        self.assertEqual(self.agent.call_count, 1)

    def test_unknown_route_id_can_be_corrected(self):
        self.agent.side_effect = [
            make_result(make_plan("unknown"), routes=[make_route("different")]),
            good_result(),
        ]
        status, body = self.post()

        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)

    def test_tool_duration_error_can_be_corrected(self):
        self.agent.side_effect = [
            make_result(
                make_plan("wrong"), routes=[make_route("wrong", seconds=1800)],
            ),
            good_result(),
        ]
        status, body = self.post()

        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertTrue(any(
            "30분" in reason
            for reason in self.retry_payload()["plan_retry"]["validation_errors"]
        ))


if __name__ == "__main__":
    unittest.main()
