import copy
import json
import unittest
from unittest.mock import patch

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router
from backend.app.services.transit_guidance import build_transit_segments
from backend.app.tools import bus as bus_tools
from backend.app.tools import map_transport as map_tools


ORIGIN = {"latitude": 33.4, "longitude": 126.5}
DESTINATION = {"latitude": 33.41, "longitude": 126.51}
OPTIONS = {
    "departure": "테스트 출발지",
    "departure_latitude": ORIGIN["latitude"],
    "departure_longitude": ORIGIN["longitude"],
    "start_date": "2026-11-20",
    "trip_days": 1,
    "transport": "transit",
    "return_to_origin": False,
}
ROUTE_INPUT = {
    "origin_latitude": ORIGIN["latitude"], "origin_longitude": ORIGIN["longitude"],
    "destination_latitude": DESTINATION["latitude"], "destination_longitude": DESTINATION["longitude"],
}


def segment(boarding="승차 정류장"):
    return {
        "type": "BUS", "distance_m": 1000, "duration_seconds": 600,
        "route_numbers": ["202", "784-1"], "boarding_stop": boarding,
        "alighting_stop": "하차 정류장",
        "stop_names": [boarding, "경유 정류장", "하차 정류장"],
    }


def provider_route():
    return {
        "distance_m": 1000, "duration_seconds": 600, "transfers": 0,
        "fare_krw": 1150, "segments": [segment()],
        "geometry_status": "ok",
        "paths": [[[126.5, 33.4], [126.51, 33.41]]],
    }


def route_record(route_id="selected", boarding="승차 정류장"):
    return {
        "route_id": route_id, "transport": "transit",
        "origin": dict(ORIGIN), "destination": dict(DESTINATION),
        "distance_m": 1000, "duration_seconds": 600,
        "geometry_status": "ok", "paths": [[[126.5, 33.4], [126.51, 33.41]]],
        "requested_at": "2026-11-13T02:00:00+00:00",
        "transit_segments": build_transit_segments([segment(boarding)]),
    }


def make_plan(route_id="selected", minutes=10):
    return TripPlan(days=[DayPlan(day=1, items=[
        ScheduleItem(
            start_time="09:00", end_time="09:10", item_type="move",
            title="출발지 → 방문지", route_id=route_id,
            transport="대중교통", duration_minutes=minutes,
        ),
        ScheduleItem(
            start_time="09:10", end_time="10:10", item_type="visit",
            title="방문지", place_id="TEST_PLACE", duration_minutes=60,
        ),
    ])])


def agent_result(plan, records):
    return {
        "chat_reply": chat_router.ChatAgentReply(
            reply="요청을 확인했습니다.", options=OPTIONS, plan=plan,
        ),
        "route_calls": copy.deepcopy(records), "messages": [],
        "model_call_count": 1, "tool_call_counts": {"get_bus_route_summary": 1},
    }


class BusGuidanceRecordingTests(unittest.TestCase):
    def setUp(self):
        self.route = provider_route()
        self.fetch = self.enterContext(patch.object(
            map_tools, "get_transit_routes",
            return_value={"status": "ok", "routes": [self.route]},
        ))
        self.enterContext(patch.object(
            map_tools, "get_kakao_api_key", return_value="test-only-key",
        ))
        self.enrich = self.enterContext(patch.object(
            bus_tools, "_build_bus_trip", side_effect=lambda result: result,
        ))

    def test_directions_match_selected_route_and_keep_agent_summary_contract(self):
        worse = provider_route()
        worse.update(transfers=1, duration_seconds=900)
        worse["segments"] = [segment("선택하지 않은 정류장")]
        self.fetch.return_value["routes"].insert(0, worse)
        records = []
        result = map_tools.create_bus_map_tool(records)(**ROUTE_INPUT)
        self.fetch.assert_called_once_with(**ROUTE_INPUT, api_key="test-only-key", include_geometry=True)
        self.enrich.assert_called_once()
        self.assertEqual(result["segments"][0]["stop_count"], 3)
        self.assertNotIn("transit_segments", result)
        self.assertNotIn("ride_stop_count", result["segments"][0])
        self.assertEqual(records[0]["route_id"], result["route_id"])
        self.assertEqual(records[0]["transit_segments"][0]["boarding_stop"], "승차 정류장")
        self.assertEqual(records[0]["transit_segments"][0]["ride_stop_count"], 2)

    def test_guide_snapshots_are_independent_of_source_and_other_requests(self):
        first, second = [], []
        tool = map_tools.create_bus_map_tool(first)
        tool(**ROUTE_INPUT)
        map_tools.create_bus_map_tool(second)(**ROUTE_INPUT)
        first[0]["transit_segments"][0]["route_numbers"].append("변경")
        self.assertEqual(second[0]["transit_segments"][0]["route_numbers"], ["202", "784-1"])
        self.assertEqual(self.route["segments"][0]["route_numbers"], ["202", "784-1"])

    def test_missing_geometry_and_stops_preserve_known_directions(self):
        self.route.pop("paths")
        self.route.pop("geometry_status")
        self.route["segments"][0]["stop_names"] = None
        records = []
        map_tools.create_bus_map_tool(records)(**ROUTE_INPUT)
        self.assertEqual(records[0]["paths"], [])
        guide = records[0]["transit_segments"][0]
        self.assertEqual(guide["boarding_stop"], "승차 정류장")
        self.assertIsNone(guide["ride_stop_count"])

    def test_failure_does_not_record_guidance_or_enrich_realtime_data(self):
        self.fetch.return_value = {"status": "no_route"}
        records = []
        self.assertEqual(map_tools.create_bus_map_tool(records)(**ROUTE_INPUT), {"status": "no_route"})
        self.assertEqual(records, [])
        self.enrich.assert_not_called()

    def test_repeated_calls_keep_each_guide_with_its_own_id(self):
        records = []
        tool = map_tools.create_bus_map_tool(records)
        first = tool(**ROUTE_INPUT)
        second = tool(**ROUTE_INPUT)
        self.assertNotEqual(first["route_id"], second["route_id"])
        self.assertEqual([item["route_id"] for item in records], [first["route_id"], second["route_id"]])
        self.assertTrue(all(item["transit_segments"] for item in records))


class ChatTransitGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.agent = self.enterContext(patch.object(chat_router, "run_chat_agent"))
        self.lookup = self.enterContext(patch.object(
            plan_router, "find_place_in_database",
            return_value={"place_id": "TEST_PLACE", "name": "DB 여행지", "category": "공원", **DESTINATION},
        ))
        self.enterContext(patch.object(chat_router, "read_closed_days_by_place_id", return_value={}))
        self.request = chat_router.ChatRequest(message="버스 여행 일정을 만들어 주세요.", options=OPTIONS)

    def post(self):
        result = chat_router.create_chat(self.request)
        if isinstance(result, dict):
            return 200, chat_router.ChatResponse.model_validate(result).model_dump(mode="json")
        return result.status_code, json.loads(result.body)

    def test_response_exposes_only_selected_current_guide_without_extra_agent_call(self):
        self.agent.return_value = agent_result(make_plan(), [route_record("unused"), route_record()])
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual([item["route_id"] for item in body["transit_routes"]], ["selected"])
        self.assertEqual(body["transit_routes"][0]["segments"][0]["ride_stop_count"], 2)
        self.assertEqual(body["transit_routes"][0]["segments"][0]["route_numbers"], ["202", "784-1"])
        self.agent.assert_called_once()
        self.assertNotIn("transit_routes", chat_router.ChatAgentReply.model_fields)

    def test_missing_geometry_keeps_itinerary_and_boarding_guide(self):
        record = route_record()
        record.update(geometry_status="missing", paths=[])
        self.agent.return_value = agent_result(make_plan(), [record])
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["map_routes"], [])
        self.assertEqual(len(body["plan"]["days"][0]["items"]), 2)
        self.assertEqual(body["transit_routes"][0]["segments"][0]["boarding_stop"], "승차 정류장")

    def test_old_or_invalid_metadata_keeps_itinerary_without_inventing_guide(self):
        for value in (None, [], [{"type": "BUS", "ride_stop_count": -1}]):
            with self.subTest(value=value):
                record = route_record()
                record["transit_segments"] = value
                self.agent.return_value = agent_result(make_plan(), [record])
                status, body = self.post()
                self.assertEqual(status, 200)
                self.assertEqual(body["transit_routes"], [])
                self.assertEqual(len(body["plan"]["days"][0]["items"]), 2)

    def test_question_does_not_expose_candidate_guide(self):
        self.agent.return_value = agent_result(None, [route_record()])
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertIsNone(body["plan"])
        self.assertEqual(body["transit_routes"], [])
        self.lookup.assert_not_called()

    def test_retry_only_returns_second_attempts_route_and_directions(self):
        self.agent.side_effect = [
            agent_result(make_plan("old", minutes=9), [route_record("old", "이전 승차지")]),
            agent_result(make_plan("new"), [route_record("new", "새 승차지")]),
        ]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual([item["route_id"] for item in body["transit_routes"]], ["new"])
        self.assertEqual(body["transit_routes"][0]["segments"][0]["boarding_stop"], "새 승차지")
        self.assertEqual(body["meta"]["model_call_count"], 2)

    def test_invalid_route_still_fails_without_leaking_bus_guide(self):
        self.agent.return_value = agent_result(make_plan("unknown"), [route_record()])
        status, body = self.post()
        self.assertEqual(status, 502)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertNotIn("transit_routes", body)


    def test_numbered_bus_transport_is_canonicalized_and_uses_tool_directions(self):
        labels = [
            "버스 102번 (제주버스터미널 승차 → 협재 너븐밭(해수욕장입구) 하차)",
            "버스 102번 (협재 너븐밭(해수욕장입구) 승차 → 제주버스터미널(종점) 하차)",
        ]
        for label in labels:
            with self.subTest(label=label):
                self.agent.reset_mock()
                payload = make_plan().model_dump()
                payload["days"][0]["items"][0]["transport"] = label
                normalized = TripPlan.model_validate(payload)
                self.agent.return_value = agent_result(normalized, [route_record()])
                status, body = self.post()
                self.assertEqual(status, 200)
                self.agent.assert_called_once()
                self.assertEqual(body["plan"]["days"][0]["items"][0]["transport"], "버스")
                self.assertEqual(body["transit_routes"][0]["segments"][0]["route_numbers"], ["202", "784-1"])
                self.assertEqual(body["transit_routes"][0]["segments"][0]["boarding_stop"], "승차 정류장")

    def test_old_response_shape_defaults_to_empty_transit_guidance(self):
        response = chat_router.ChatResponse.model_validate({
            "reply": "추가 조건을 알려 주세요.", "options": OPTIONS, "plan": None,
            "warnings": [], "meta": {"model_call_count": 1, "tool_call_counts": {}, "elapsed_seconds": 0},
        })
        self.assertEqual(response.transit_routes, [])


if __name__ == "__main__":
    unittest.main()
