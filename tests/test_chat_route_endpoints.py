import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router
from backend.app.services.route_endpoints import validate_route_endpoints


ORIGIN = {"latitude": 33.4, "longitude": 126.5}
DESTINATION = {"latitude": 33.41, "longitude": 126.51}
OTHER = {"latitude": 33.5, "longitude": 126.6}
OPTIONS = {
    "departure": "테스트 출발지",
    "departure_latitude": ORIGIN["latitude"],
    "departure_longitude": ORIGIN["longitude"],
    "start_date": "2026-11-20",
    "trip_days": 1,
    "transport": "driving",
    "return_to_origin": True,
}


def make_plan():
    return TripPlan(days=[
        DayPlan(day=1, items=[
            ScheduleItem(
                start_time="09:00", end_time="09:10",
                item_type="move", title="출발지 → 방문 장소",
                route_id="out", transport="자동차", duration_minutes=10,
            ),
            ScheduleItem(
                start_time="09:10", end_time="10:10",
                item_type="visit", title="모델이 작성한 이름",
                place_id="TEST_PLACE", duration_minutes=60,
            ),
            ScheduleItem(
                start_time="10:10", end_time="10:20",
                item_type="move", title="방문 장소 → 출발지",
                route_id="back", transport="자동차", duration_minutes=10,
            ),
        ]),
    ])


def make_route(route_id, origin, destination):
    return {
        "route_id": route_id,
        "transport": "driving",
        "origin": dict(origin),
        "destination": dict(destination),
        "distance_m": 1000,
        "duration_seconds": 600,
        "geometry_status": "ok",
        "paths": [[
            [origin["longitude"], origin["latitude"]],
            [destination["longitude"], destination["latitude"]],
        ]],
        "requested_at": "2026-11-12T00:00:00+00:00",
    }


def make_records():
    return [
        make_route("out", ORIGIN, DESTINATION),
        make_route("back", DESTINATION, ORIGIN),
    ]


class ChatRouteEndpointTests(unittest.TestCase):
    def setUp(self):
        self.agent = self.enterContext(
            patch.object(chat_router, "run_chat_agent")
        )
        self.lookup = self.enterContext(patch.object(
            plan_router,
            "find_place_in_database",
            return_value={
                "place_id": "TEST_PLACE",
                "name": "DB에서 확인한 이름",
                "category": "자연",
                **DESTINATION,
            },
        ))
        self.closed_lookup = self.enterContext(patch.object(
            chat_router,
            "read_closed_days_by_place_id",
            return_value={},
        ))
        self.endpoint_check = self.enterContext(patch.object(
            chat_router,
            "validate_route_endpoints",
            wraps=validate_route_endpoints,
        ))

    def run_request(self, plan, records, messages=(), *, message="일정을 추천해 주세요."):
        self.agent.return_value = {
            "chat_reply": chat_router.ChatAgentReply(
                reply="요청을 확인했습니다.",
                options=OPTIONS,
                plan=plan,
            ),
            "route_calls": copy.deepcopy(records),
            "messages": list(messages),
            "model_call_count": 1,
            "tool_call_counts": {},
        }
        result = chat_router.create_chat(chat_router.ChatRequest(
            message=message,
            options=OPTIONS,
        ))
        if isinstance(result, dict):
            return 200, chat_router.ChatResponse.model_validate(
                result
            ).model_dump(mode="json")
        return result.status_code, json.loads(result.body)

    def assert_validation_error(self, status, body):
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertNotIn("plan", body)
        self.assertNotIn("map_routes", body)

    def restaurant_plan(self):
        plan = make_plan()
        visit = plan.days[0].items[1]
        visit.place_id = None
        visit.restaurant_ref = "found:meal"
        return plan

    def restaurant_lookup(self, *, category="FD6", reference="found:meal"):
        return [
            SimpleNamespace(type="ai", tool_calls=[{
                "id": "restaurant", "name": "find_location", "args": {"keyword": "식당"},
            }]),
            SimpleNamespace(type="tool", tool_call_id="restaurant", content=json.dumps({
                "status": "ok", "category_group_code": category,
                "point_ref": reference, "name": "카카오에서 확인한 식당",
                "address": "제주시 테스트 주소", **DESTINATION,
            })),
        ]

    def test_restaurant_uses_current_external_location_and_marks_details_unconfirmed(self):
        status, body = self.run_request(self.restaurant_plan(), make_records(), self.restaurant_lookup())

        self.assertEqual(status, 200)
        meal = body["plan"]["days"][0]["items"][1]
        self.assertEqual(meal["title"], "카카오에서 확인한 식당")
        self.assertEqual(meal["restaurant_address"], "제주시 테스트 주소")
        self.assertEqual(meal["latitude"], DESTINATION["latitude"])
        self.assertEqual(meal["longitude"], DESTINATION["longitude"])
        self.assertIs(meal["verified"], False)
        self.assertIsNone(meal["place_id"])
        self.assertIn("영업시간과 접근성은 방문 전에 확인해 주세요.", meal["accessibility_notes"])
        self.assertEqual([route["route_id"] for route in body["map_routes"]], ["out", "back"])
        self.lookup.assert_not_called()

    def test_restaurant_rejects_nonrestaurant_and_stale_location_references(self):
        for messages in (self.restaurant_lookup(category="CE7"), self.restaurant_lookup(reference="found:other")):
            with self.subTest(messages=messages):
                status, body = self.run_request(self.restaurant_plan(), make_records(), messages)
                self.assert_validation_error(status, body)
                self.assertTrue(any("음식점 참조" in detail["reason"] for detail in body["error"]["details"]))

    def test_restaurant_does_not_accept_wrong_or_missing_travel(self):
        bad_records = [make_route("out", ORIGIN, OTHER), make_route("back", DESTINATION, ORIGIN)]
        status, body = self.run_request(self.restaurant_plan(), bad_records, self.restaurant_lookup())
        self.assert_validation_error(status, body)
        self.assertIn("위치가", body["error"]["details"][0]["reason"])

        plan = self.restaurant_plan()
        plan.days[0].items.pop()
        status, body = self.run_request(plan, make_records()[:1], self.restaurant_lookup())
        self.assert_validation_error(status, body)
        self.assertIn("식당에서 출발하는", body["error"]["details"][0]["reason"])

    def test_explicit_meal_times_exclude_waiting_and_keep_route_times(self):
        status, body = self.run_request(
            self.restaurant_plan(), make_records(), self.restaurant_lookup(),
            message="아침 식사만 오전 9시 20분부터 10시까지로 바꿔 주세요.",
        )
        self.assertEqual(status, 200)
        inbound, meal, outbound = body["plan"]["days"][0]["items"]
        self.assertEqual((inbound["end_time"], outbound["start_time"]), ("09:10", "10:10"))
        self.assertEqual((meal["start_time"], meal["end_time"], meal["duration_minutes"]), ("09:20", "10:00", 40))

    def test_explicit_meal_time_cannot_overlap_a_route(self):
        status, body = self.run_request(
            self.restaurant_plan(), make_records(), self.restaurant_lookup(),
            message="아침 식사만 오전 9시 5분부터 10시까지로 바꿔 주세요.",
        )
        self.assert_validation_error(status, body)
        self.assertIn("요청한 식사 시각 09:05~10:00", body["error"]["details"][0]["reason"])

    def test_waiting_at_restaurant_is_not_counted_as_a_route(self):
        plan = self.restaurant_plan()
        plan.days[0].items.insert(1, ScheduleItem(
            item_type="move", title="식당에 일찍 도착해 대기", transport="도보",
            start_time="09:10", end_time="09:20", duration_minutes=10,
        ))
        status, body = self.run_request(
            plan, make_records(), self.restaurant_lookup(),
            message="아침 식사만 오전 9시 20분부터 10시까지로 바꿔 주세요.",
        )

        self.assertEqual(status, 200)
        inbound, meal, outbound = body["plan"]["days"][0]["items"]
        self.assertEqual((inbound["route_id"], outbound["route_id"]), ("out", "back"))
        self.assertEqual((meal["start_time"], meal["end_time"]), ("09:20", "10:00"))

    def test_valid_route_uses_database_coordinates(self):
        status, body = self.run_request(make_plan(), make_records())

        self.assertEqual(status, 200)
        self.assertEqual(
            [route["route_id"] for route in body["map_routes"]],
            ["out", "back"],
        )
        checked_plan = self.endpoint_check.call_args.args[0]
        item = checked_plan["days"][0]["items"][1]
        self.assertTrue(item["verified"])
        self.assertEqual(item["title"], "DB에서 확인한 이름")
        self.assertEqual(item["latitude"], DESTINATION["latitude"])
        self.lookup.assert_called_once_with("TEST_PLACE")
        self.assertEqual(body["warnings"], [])

    def test_wrong_departure_is_rejected(self):
        records = make_records()
        records[0]["origin"] = dict(OTHER)
        status, body = self.run_request(make_plan(), records)

        self.assert_validation_error(status, body)
        self.assertTrue(any(
            "출발 위치" in detail["reason"]
            for detail in body["error"]["details"]
        ))

    def test_wrong_destination_is_rejected_even_with_correct_road_line(self):
        records = make_records()
        records[0]["destination"] = dict(OTHER)
        status, body = self.run_request(make_plan(), records)

        self.assert_validation_error(status, body)
        self.assertTrue(any(
            "도착 위치" in detail["reason"]
            for detail in body["error"]["details"]
        ))

    def test_reversed_route_is_rejected(self):
        records = make_records()
        records[0]["origin"] = dict(DESTINATION)
        records[0]["destination"] = dict(ORIGIN)
        status, body = self.run_request(make_plan(), records)

        self.assert_validation_error(status, body)
        self.assertEqual(len(body["error"]["details"]), 2)

    def test_invalid_request_coordinate_is_rejected(self):
        records = make_records()
        records[0]["destination"] = None
        status, body = self.run_request(make_plan(), records)

        self.assert_validation_error(status, body)
        self.assertTrue(any(
            "도착 좌표가 올바르지 않습니다" in detail["reason"]
            for detail in body["error"]["details"]
        ))

    def test_unverified_visit_does_not_supply_reference_coordinates(self):
        self.lookup.return_value = None
        records = [
            make_route("out", ORIGIN, OTHER),
            make_route("back", OTHER, ORIGIN),
        ]
        status, body = self.run_request(make_plan(), records)

        self.assertEqual(status, 200)
        item = body["plan"]["days"][0]["items"][1]
        self.assertFalse(item["verified"])
        self.assertIsNone(item["latitude"])
        self.assertEqual(len(body["warnings"]), 1)

    def test_question_skips_endpoint_validation(self):
        status, body = self.run_request(None, make_records())

        self.assertEqual(status, 200)
        self.assertIsNone(body["plan"])
        self.assertEqual(body["map_routes"], [])
        self.endpoint_check.assert_not_called()
        self.lookup.assert_not_called()
        self.closed_lookup.assert_not_called()

    def test_time_error_skips_database_and_endpoint_validation(self):
        value = make_plan()
        value.days[0].items[0].duration_minutes = 9
        status, body = self.run_request(value, make_records())

        self.assert_validation_error(status, body)
        self.endpoint_check.assert_not_called()
        self.lookup.assert_not_called()
        self.closed_lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
