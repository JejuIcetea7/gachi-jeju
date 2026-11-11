import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.main import app
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router


OPTIONS = {
    "departure": "테스트 출발지",
    "departure_latitude": 33.4,
    "departure_longitude": 126.5,
    "start_date": "2026-11-20",
    "trip_days": 1,
    "transport": "driving",
}


def make_plan(route_id="selected"):
    return TripPlan(
        days=[
            DayPlan(
                day=1,
                items=[
                    ScheduleItem(
                        start_time="09:00",
                        end_time="09:10",
                        item_type="move",
                        title="출발지 → 테스트 여행지",
                        transport="자동차",
                        duration_minutes=10,
                        distance_km=1,
                        route_id=route_id,
                    ),
                    ScheduleItem(
                        start_time="09:10",
                        end_time="10:10",
                        item_type="visit",
                        title="테스트 여행지",
                        place_id="TEST_PLACE",
                        duration_minutes=60,
                    ),
                ],
            )
        ]
    )


def make_route(route_id="selected"):
    return {
        "route_id": route_id,
        "transport": "driving",
        "origin": {"latitude": 33.4, "longitude": 126.5},
        "destination": {"latitude": 33.41, "longitude": 126.51},
        "distance_m": 1000,
        "duration_seconds": 600,
        "geometry_status": "ok",
        "paths": [[[126.5, 33.4], [126.51, 33.41]]],
        "requested_at": "2026-11-10T00:00:00+00:00",
    }


class ChatMapRouteTests(unittest.TestCase):
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
                    "latitude": 33.41,
                    "longitude": 126.51,
                },
            )
        )

    def _set_result(self, plan, records):
        self.agent.return_value = {
            "chat_reply": chat_router.ChatAgentReply(
                reply="요청을 확인했습니다.",
                options=OPTIONS,
                plan=plan,
            ),
            "route_calls": records,
            "model_call_count": 1,
            "tool_call_counts": {},
        }

    def _post(self):
        response = self.client.post(
            "/chat",
            json={
                "message": "일정을 추천해 주세요.",
                "options": OPTIONS,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_response_contains_only_selected_route(self):
        self._set_result(
            make_plan(),
            [make_route("unused-candidate"), make_route()],
        )

        body = self._post()

        self.assertEqual(
            [route["route_id"] for route in body["map_routes"]],
            ["selected"],
        )
        self.assertEqual(
            body["plan"]["days"][0]["items"][0]["route_id"],
            "selected",
        )
        self.assertEqual(
            body["map_routes"][0]["paths"],
            make_route()["paths"],
        )
        self.assertEqual(body["warnings"], [])

    def test_unknown_route_id_preserves_itinerary(self):
        self._set_result(
            make_plan("previous-execution"),
            [make_route()],
        )

        body = self._post()

        self.assertEqual(body["map_routes"], [])
        self.assertEqual(len(body["plan"]["days"][0]["items"]), 2)
        self.assertEqual(len(body["warnings"]), 1)

    def test_geometry_problems_preserve_itinerary(self):
        cases = [
            ("missing", [], 0),
            ("partial", make_route()["paths"], 1),
        ]

        for status, paths, expected_count in cases:
            with self.subTest(status=status):
                route = make_route()
                route["geometry_status"] = status
                route["paths"] = paths
                self._set_result(make_plan(), [route])

                body = self._post()

                self.assertEqual(
                    len(body["map_routes"]),
                    expected_count,
                )
                self.assertEqual(len(body["warnings"]), 1)
                self.assertEqual(
                    len(body["plan"]["days"][0]["items"]),
                    2,
                )

    def test_question_does_not_expose_route_records(self):
        self._set_result(None, [make_route()])

        body = self._post()

        self.assertIsNone(body["plan"])
        self.assertEqual(body["map_routes"], [])
        self.assertEqual(body["warnings"], [])
        self.lookup.assert_not_called()

    def test_previous_ids_are_cleared_without_changing_saved_plan(self):
        plan, _ = plan_router.verify_places(
            make_plan("previous-route")
        )
        request = chat_router.ChatRequest(
            message="이 일정을 수정해 주세요.",
            current_plan=plan,
        )

        messages = chat_router.build_chat_messages(request)
        payload = json.loads(messages[-1]["content"])

        self.assertIsNone(
            payload["current_plan"]["days"][0]["items"][0]["route_id"]
        )
        self.assertEqual(
            request.current_plan.days[0].items[0].route_id,
            "previous-route",
        )


if __name__ == "__main__":
    unittest.main()