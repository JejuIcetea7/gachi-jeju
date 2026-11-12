import copy
import json
import unittest
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

    def run_request(self, plan, records):
        self.agent.return_value = {
            "chat_reply": chat_router.ChatAgentReply(
                reply="요청을 확인했습니다.",
                options=OPTIONS,
                plan=plan,
            ),
            "route_calls": copy.deepcopy(records),
            "messages": [],
            "model_call_count": 1,
            "tool_call_counts": {},
        }
        result = chat_router.create_chat(chat_router.ChatRequest(
            message="일정을 추천해 주세요.",
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