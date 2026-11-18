import copy
import inspect
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from langchain_core.tools import StructuredTool

from backend.app.agent import chat_planner
from backend.app.routers.chat import ChatRequest, build_chat_messages
from backend.app.services import chat_route_points as points
from backend.app.services.chat_request_control import (
    ChatRequestControl, ChatRequestStopped, use_chat_request_control,
)


ROWS = {
    "P001": {"place_id": "P001", "name": "한림공원", "latitude": 33.39, "longitude": 126.239},
    "P010": {"place_id": "P010", "name": "협재해수욕장", "latitude": 33.39224, "longitude": 126.239},
}
STAY = {"night": 1, "name": "선택 숙소", "address": "제주 숙소 주소",
        "latitude": 33.4, "longitude": 126.3}


def payload(**options):
    return {
        "message": "여행 일정을 만들어 주세요.", "reference_date": "2026-11-17",
        "default_departure": {"name": "센터"},
        "options": {"departure": "센터", "departure_latitude": 33.45,
                    "departure_longitude": 126.57, "trip_days": 2,
                    "lodging_status": "undecided", "lodging_stays": [], **options},
    }


class ChatRoutePointTests(unittest.TestCase):
    def setUp(self):
        self.rows = self.enterContext(patch.object(
            points, "read_rows",
            side_effect=lambda query, values: [copy.deepcopy(ROWS[values[0]])]
            if values[0] in ROWS else [],
        ))
        self.location_output = {"status": "ok", "name": "새 숙소", "address": "새 주소",
                                "latitude": 33.5, "longitude": 126.5}
        self.location = Mock(return_value=self.location_output)

        def location(*, keyword: str, refresh: bool = False) -> dict:
            """테스트 위치 조회입니다."""
            return self.location(keyword=keyword, refresh=refresh)

        self.location_function = location
        self.driving = Mock(return_value={"status": "ok", "route_id": "current-route",
                                         "duration_seconds": 33})
        self.bus = Mock(return_value={"status": "ok", "route_id": "bus-route"})
        self.walking = Mock(return_value={"status": "ok", "route_id": "walk-route"})
        self.order = Mock(return_value={"status": "ok", "suggested_place_ids": ["P001", "P010"]})
        self.find, self.suggest, self.routes = self.bind(payload())

    def bind(self, value):
        return points.bind_chat_route_tools(
            value, location_tool=self.location_function, order_tool=self.order,
            driving_tool=self.driving, bus_tool=self.bus, walking_tool=self.walking,
        )

    def test_direction_uses_distinct_database_coordinates_for_nearby_places(self):
        result = self.routes[0](origin_ref="place:P010", destination_ref="place:P001")
        self.driving.assert_called_once_with(
            origin_latitude=ROWS["P010"]["latitude"], origin_longitude=126.239,
            destination_latitude=ROWS["P001"]["latitude"], destination_longitude=126.239,
        )
        self.assertEqual(result["origin"]["name"], "협재해수욕장")
        self.assertEqual(result["destination"]["name"], "한림공원")
        self.assertEqual(result["route_id"], "current-route")

    def test_known_place_cache_is_request_local_and_safe_for_parallel_tools(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(
                lambda _: self.routes[0](origin_ref="place:P001", destination_ref="place:P010"),
                range(8),
            ))
        self.assertEqual(self.rows.call_count, 2)
        results[0]["origin"]["latitude"] = 0
        self.assertEqual(results[1]["origin"]["latitude"], ROWS["P001"]["latitude"])
        self.bind(payload())[2][0](origin_ref="place:P001", destination_ref="departure")
        self.assertEqual(self.rows.call_count, 3)

    def test_departure_and_selected_nights_keep_their_own_coordinates(self):
        find, suggest, routes = self.bind(payload(lodging_status="provided", lodging_stays=[STAY]))
        result = routes[0](origin_ref="stay:1", destination_ref="departure")
        self.assertEqual(result["origin"]["latitude"], STAY["latitude"])
        self.assertEqual(result["destination"]["latitude"], 33.45)
        self.rows.assert_not_called()
        self.location.assert_not_called()
        self.assertEqual(routes[0](origin_ref="stay:2", destination_ref="departure")["status"], "not_found")

    def test_invalid_or_duplicate_lodgings_are_not_resolved(self):
        for changes in (
            {"lodging_stays": [STAY, STAY]},
            {"lodging_stays": [{**STAY, "latitude": True}]},
            {"lodging_stays": [{**STAY, "night": 2}]},
            {"lodging_status": "not_needed", "lodging_stays": [STAY]},
        ):
            with self.subTest(changes=changes):
                route = self.bind(payload(**changes))[2][0]
                self.assertEqual(route(origin_ref="stay:1", destination_ref="departure")["status"], "not_found")
        self.driving.assert_not_called()

    def test_invalid_unknown_and_external_provider_references_do_not_call_transport(self):
        for reference in (None, True, "", "stay:9", "found:old", "1234", "place:1234"):
            with self.subTest(reference=reference):
                self.assertNotEqual(
                    self.routes[0](origin_ref=reference, destination_ref="departure")["status"], "ok",
                )
        self.driving.assert_not_called()

    def test_missing_departure_never_uses_default_name_or_old_plan_coordinates(self):
        value = payload(departure_latitude=None, departure_longitude=None)
        value["current_plan"] = {"latitude": 33.4, "longitude": 126.4}
        route = self.bind(value)[2][0]
        self.assertEqual(route(origin_ref="departure", destination_ref="place:P001")["status"], "not_found")
        self.driving.assert_not_called()
        self.rows.assert_not_called()

    def test_bad_database_results_are_not_coordinates(self):
        for result in (None, [], [ROWS["P001"], ROWS["P001"]],
                       [{**ROWS["P001"], "place_id": "P010"}],
                       [{**ROWS["P001"], "latitude": float("nan")}],
                       [{**ROWS["P001"], "longitude": True}], [None]):
            with self.subTest(result=result):
                self.rows.side_effect = None
                self.rows.return_value = result
                route = self.bind(payload())[2][0]
                self.assertNotEqual(route(origin_ref="place:P001", destination_ref="departure")["status"], "ok")
        self.driving.assert_not_called()

    def test_database_error_is_not_hidden_by_a_transport_call(self):
        self.rows.side_effect = sqlite3.OperationalError("private database details")
        self.assertEqual(self.routes[0](origin_ref="place:P001", destination_ref="departure"),
                         {"status": "db_error"})
        self.driving.assert_not_called()

    def test_place_ids_are_query_parameters(self):
        reference = "place:P001' OR 1=1 --"
        self.routes[0](origin_ref=reference, destination_ref="departure")
        query, values = self.rows.call_args.args
        self.assertNotIn("OR 1=1", query)
        self.assertEqual(values, ("P001' OR 1=1 --",))

    def test_fresh_location_is_copied_and_has_an_attempt_local_reference(self):
        original = copy.deepcopy(self.location_output)
        found = self.find(keyword="새 숙소", refresh=True)
        self.location.assert_called_once_with(keyword="새 숙소", refresh=True)
        self.assertEqual(self.location_output, original)
        result = self.routes[0](origin_ref=found["point_ref"], destination_ref="departure")
        self.assertEqual(result["origin"]["name"], "새 숙소")
        self.assertEqual(result["origin"]["latitude"], 33.5)
        other_route = self.bind(payload())[2][0]
        self.assertEqual(other_route(origin_ref=found["point_ref"], destination_ref="departure")["status"],
                         "not_found")

    def test_selected_location_keeps_its_nonfresh_evidence_markers(self):
        self.location_output.update(source="selected_lodging", lookup_performed=False)
        found = self.find(keyword="선택 숙소")
        self.assertEqual(found["source"], "selected_lodging")
        self.assertIs(found["lookup_performed"], False)
        self.assertIn("point_ref", found)

    def test_invalid_location_result_does_not_create_a_reference(self):
        for changes in ({"status": "not_found"}, {"latitude": float("inf")},
                        {"longitude": True}, {"name": ""}):
            with self.subTest(changes=changes):
                self.location.return_value = {**self.location_output, **changes}
                self.assertNotIn("point_ref", self.find(keyword="새 숙소"))

    def test_original_location_without_refresh_parameter_still_works(self):
        def original(*, keyword: str) -> dict:
            return copy.deepcopy(self.location_output)
        self.location_function = original
        found = self.bind(payload())[0](keyword="새 숙소", refresh=True)
        self.assertIn("point_ref", found)

    def test_each_order_call_has_a_day_and_only_explicit_known_boundaries(self):
        result = self.suggest(day=2, place_ids=["P010", "P001"],
                              destination_ref="departure", fixed_place_ids=["P001"])
        self.order.assert_called_once_with(
            place_ids=["P010", "P001"], fixed_place_ids=["P001"],
            destination_latitude=33.45, destination_longitude=126.57,
        )
        self.assertEqual(result["day"], 2)
        self.assertIsNone(result["origin"])
        self.assertEqual(result["destination"]["point_ref"], "departure")
        self.assertEqual(self.suggest(day=True, place_ids=["P001"])["status"], "invalid_input")

    def test_bus_and_walking_preserve_their_transport_and_walking_mode(self):
        self.routes[1](origin_ref="departure", destination_ref="place:P001")
        self.routes[2](origin_ref="place:P001", destination_ref="departure", route_mode="ACCESSIBLE")
        self.bus.assert_called_once()
        self.assertEqual(self.walking.call_args.kwargs["route_mode"], "ACCESSIBLE")
        self.driving.assert_not_called()

    def test_cancellation_during_coordinate_resolution_stops_before_provider(self):
        control = ChatRequestControl()

        def cancel(query, values):
            control.cancel()
            return [copy.deepcopy(ROWS[values[0]])]
        self.rows.side_effect = cancel
        with use_chat_request_control(control):
            with self.assertRaises(ChatRequestStopped):
                self.routes[0](origin_ref="place:P001", destination_ref="departure")
        self.driving.assert_not_called()

    def test_payload_and_lodging_snapshot_are_independent(self):
        value = payload(lodging_status="provided", lodging_stays=[copy.deepcopy(STAY)])
        snapshot = copy.deepcopy(value)
        route = self.bind(value)[2][0]
        value["options"]["lodging_stays"][0]["latitude"] = 0
        result = route(origin_ref="stay:1", destination_ref="departure")
        self.assertEqual(result["origin"]["latitude"], STAY["latitude"])
        self.assertEqual(snapshot["options"]["lodging_stays"][0], STAY)

    def test_structured_tool_schemas_expose_references_instead_of_coordinates(self):
        for function in self.routes:
            tool = StructuredTool.from_function(function)
            fields = tool.args_schema.model_json_schema()["properties"]
            self.assertIn("origin_ref", fields)
            self.assertNotIn("origin_latitude", fields)
        fields = StructuredTool.from_function(self.suggest).args_schema.model_json_schema()
        self.assertIn("day", fields["required"])
        self.assertEqual(fields["properties"]["origin_ref"]["default"], None)


class ChatRoutePointFactoryTests(unittest.TestCase):
    def test_only_the_current_server_frame_supplies_context(self):
        value = payload()
        self.assertEqual(points.read_chat_route_payload(
            [{"role": "user", "content": json.dumps(value)}]), value)
        for messages in ([], [{"role": "user", "content": "plain text"}],
                         [{"role": "user", "content": '{"options": {}}'}],
                         [{"role": "assistant", "content": json.dumps(value)}],
                         [{"role": "user", "content": json.dumps({"current_plan": value})}]):
            self.assertIsNone(points.read_chat_route_payload(messages))

    def test_run_installs_reference_tools_for_real_server_messages(self):
        messages = build_chat_messages(ChatRequest(message="여행 일정을 만들어 주세요."))
        with (patch.object(chat_planner, "get_openai_api_key", return_value="test-only"),
              patch.object(chat_planner, "ChatOpenAI"),
              patch.object(chat_planner, "create_agent") as factory):
            factory.return_value.invoke.return_value = {"messages": messages}
            chat_planner.run_chat_agent(messages, None)
        tools = {function.__name__: function for function in factory.call_args.kwargs["tools"]}
        self.assertEqual(set(inspect.signature(tools["get_driving_time"]).parameters),
                         {"origin_ref", "destination_ref"})
        self.assertIn("day", inspect.signature(tools["suggest_visit_order"]).parameters)


if __name__ == "__main__":
    unittest.main()