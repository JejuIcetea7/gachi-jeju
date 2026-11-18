import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_chat_route_endpoints as fixture
from backend.app.routers import chat, plan
from backend.app.services.route_id_recovery import restore_plan_route_ids


def trace_for(*places):
    if not places:
        places = ({"place_id": "TEST_PLACE", **fixture.DESTINATION},)
    records = [{
        "tool": "search_places_by_name", "input": {"keyword": "테스트"},
        "output": {"status": "ok", "places": [dict(place) for place in places]},
    }]
    for place in places:
        records.append({
            "tool": "get_place_details", "input": {"place_id": place["place_id"]},
            "output": {"status": "ok", "place_id": place["place_id"]},
        })
    return records


def recover(value=None, routes=None, trace=None, **options):
    if value is None:
        value = fixture.make_plan().model_dump()
        value["days"][0]["items"][0]["route_id"] = "unmatched"
    return restore_plan_route_ids(
        value, fixture.make_records() if routes is None else routes,
        tool_trace=trace_for() if trace is None else trace,
        departure=options.pop("departure", fixture.ORIGIN),
        return_to_origin=options.pop("return_to_origin", True), **options,
    )


def first_id(value):
    return value["days"][0]["items"][0]["route_id"]


def lodging_plan():
    value = fixture.make_plan().model_dump()
    first = value["days"][0]
    first["items"][-1]["route_id"] = "unmatched-arrival"
    second = copy.deepcopy(first)
    second["day"] = 2
    second["items"][0]["route_id"] = "unmatched-departure"
    second["items"][-1]["route_id"] = "back"
    value["days"].append(second)
    routes = [
        fixture.make_route("out", fixture.ORIGIN, fixture.DESTINATION),
        fixture.make_route("to-stay", fixture.DESTINATION, fixture.OTHER),
        fixture.make_route("from-stay", fixture.OTHER, fixture.DESTINATION),
        fixture.make_route("back", fixture.DESTINATION, fixture.ORIGIN),
    ]
    return value, routes


class RouteIdRecoveryTests(unittest.TestCase):
    def test_unmatched_id_is_recovered_from_current_evidence(self):
        self.assertEqual(first_id(recover()), "out")

    def test_missing_ids_can_be_recovered_without_changing_the_schedule(self):
        value = fixture.make_plan().model_dump()
        value["days"][0]["items"][0]["route_id"] = None
        value["days"][0]["items"][-1]["route_id"] = None
        result = recover(value)
        self.assertEqual([item.get("route_id") for item in result["days"][0]["items"]],
                         ["out", None, "back"])
        for original, restored in zip(value["days"][0]["items"], result["days"][0]["items"]):
            self.assertEqual({k: v for k, v in original.items() if k != "route_id"},
                             {k: v for k, v in restored.items() if k != "route_id"})

    def test_ambiguous_routes_are_not_selected(self):
        routes = fixture.make_records()
        routes.append({**copy.deepcopy(routes[0]), "route_id": "other-out"})
        self.assertEqual(first_id(recover(routes=routes)), "unmatched")

    def test_duplicate_provider_ids_are_not_used(self):
        routes = fixture.make_records()
        routes.append(copy.deepcopy(routes[0]))
        self.assertEqual(first_id(recover(routes=routes)), "unmatched")

    def test_a_route_reserved_by_another_move_is_not_reused(self):
        value = fixture.make_plan().model_dump()
        value["days"][0]["items"][0]["route_id"] = "unmatched"
        value["days"][0]["items"][-1]["route_id"] = "out"
        self.assertEqual(first_id(recover(value)), "unmatched")

    def test_an_existing_id_is_never_overwritten(self):
        value = fixture.make_plan().model_dump()
        value["days"][0]["items"][0]["route_id"] = "back"
        self.assertEqual(first_id(recover(value)), "back")

    def test_mode_duration_direction_and_both_coordinates_must_match(self):
        for change in (
            {"transport": "walking"}, {"duration_seconds": 1200},
            {"origin": fixture.OTHER}, {"destination": fixture.OTHER},
            {"origin": fixture.DESTINATION, "destination": fixture.ORIGIN},
            {"duration_seconds": True}, {"duration_seconds": -1},
            {"duration_seconds": 10 ** 1000}, {"origin": None},
        ):
            with self.subTest(change=change):
                routes = fixture.make_records()
                routes[0].update(copy.deepcopy(change))
                self.assertEqual(first_id(recover(routes=routes)), "unmatched")

    def test_search_and_matching_successful_details_are_both_required(self):
        missing_search = trace_for()[1:]
        missing_details = trace_for()[:1]
        mismatched_details = trace_for()
        mismatched_details[1]["input"]["place_id"] = "OTHER"
        failed_details = trace_for()
        failed_details[1]["output"]["status"] = "db_error"
        failed_search = trace_for()
        failed_search[0]["output"]["status"] = "not_found"
        for records in ([], missing_search, missing_details, mismatched_details,
                        failed_details, failed_search, {}):
            with self.subTest(records=records):
                self.assertEqual(first_id(recover(trace=records)), "unmatched")

    def test_conflicting_or_invalid_search_coordinates_are_not_guessed(self):
        for point in (fixture.OTHER, {"latitude": True, "longitude": 126.51}):
            records = trace_for()
            records[0]["output"]["places"].append({"place_id": "TEST_PLACE", **point})
            self.assertEqual(first_id(recover(trace=records)), "unmatched")

    def test_old_plan_coordinates_cannot_replace_current_search_evidence(self):
        value = fixture.make_plan().model_dump()
        value["days"][0]["items"][0]["route_id"] = "unmatched"
        value["days"][0]["items"][1].update(fixture.DESTINATION, verified=True)
        self.assertEqual(first_id(recover(value, trace=[])), "unmatched")
        self.assertEqual(first_id(recover(routes=[])), "unmatched")

    def test_unknown_departure_and_return_boundary_are_not_invented(self):
        result = recover(departure=None)
        self.assertEqual(first_id(result), "unmatched")
        value = fixture.make_plan().model_dump()
        value["days"][0]["items"][-1]["route_id"] = "unmatched"
        self.assertEqual(recover(value, return_to_origin=False)["days"][0]["items"][-1]["route_id"],
                         "unmatched")

    def test_undecided_lodging_boundaries_are_not_replaced_with_the_center(self):
        value, routes = lodging_plan()
        result = recover(value, routes)
        self.assertEqual(result["days"][0]["items"][-1]["route_id"], "unmatched-arrival")
        self.assertEqual(result["days"][1]["items"][0]["route_id"], "unmatched-departure")

    def test_selected_lodging_supplies_only_its_own_night_boundaries(self):
        value, routes = lodging_plan()
        result = recover(value, routes, lodging_stays=[{"night": 1, **fixture.OTHER}])
        self.assertEqual(result["days"][0]["items"][-1]["route_id"], "to-stay")
        self.assertEqual(result["days"][1]["items"][0]["route_id"], "from-stay")

    def test_internal_move_needs_no_invented_day_boundary(self):
        value = fixture.make_plan().model_dump()
        first = value["days"][0]["items"][1]
        second = {**copy.deepcopy(first), "place_id": "OTHER"}
        move = value["days"][0]["items"][0]
        move["route_id"] = "unmatched"
        value["days"][0]["items"] = [first, move, second]
        records = trace_for({"place_id": "TEST_PLACE", **fixture.DESTINATION},
                            {"place_id": "OTHER", **fixture.OTHER})
        routes = [fixture.make_route("between", fixture.DESTINATION, fixture.OTHER)]
        result = recover(value, routes, records, departure=None, return_to_origin=False)
        self.assertEqual(result["days"][0]["items"][1]["route_id"], "between")

    def test_walking_and_transit_use_their_own_provider_records(self):
        for mode, label in (("walking", "도보"), ("transit", "버스")):
            value = fixture.make_plan().model_dump()
            value["days"][0]["items"][0].update(route_id="unmatched", transport=label)
            routes = fixture.make_records()
            routes[0]["transport"] = mode
            self.assertEqual(first_id(recover(value, routes)), "out")

    def test_invalid_record_ids_are_not_introduced_into_the_plan(self):
        for route_id in (None, "", "x" * 81):
            routes = fixture.make_records()
            routes[0]["route_id"] = route_id
            self.assertEqual(first_id(recover(routes=routes)), "unmatched")

    def test_inputs_are_preserved_and_returned_data_is_independent(self):
        value = fixture.make_plan().model_dump()
        value["days"][0]["items"][0]["route_id"] = "unmatched"
        routes, records = fixture.make_records(), trace_for()
        before = copy.deepcopy((value, routes, records))
        result = recover(value, routes, records)
        result["days"][0]["items"][1]["title"] = "changed"
        self.assertEqual((value, routes, records), before)


class ChatRouteRecoveryTests(unittest.TestCase):
    def run_response(self, routes):
        trip = fixture.make_plan()
        trip.days[0].items[0].route_id = "unmatched"
        messages = []
        for index, item in enumerate(trace_for()):
            call_id = f"evidence-{index}"
            messages.extend([
                SimpleNamespace(type="ai", tool_calls=[{
                    "id": call_id, "name": item["tool"], "args": item["input"],
                }]),
                SimpleNamespace(type="tool", tool_call_id=call_id,
                                content=json.dumps(item["output"])),
            ])
        result = {
            "chat_reply": chat.ChatAgentReply(reply="요청한 일정입니다.",
                                             options=fixture.OPTIONS, plan=trip),
            "route_calls": copy.deepcopy(routes), "messages": messages,
            "model_call_count": 1, "tool_call_counts": {},
        }
        with (
            patch.object(chat, "run_chat_agent", return_value=result) as agent,
            patch.object(plan, "find_place_in_database", return_value={
                "place_id": "TEST_PLACE", "name": "DB 관광지", "category": "자연",
                **fixture.DESTINATION,
            }),
            patch.object(chat, "read_closed_days_by_place_id", return_value={}),
            patch.object(chat, "read_opening_hours_by_place_id", return_value={}),
        ):
            response = chat.create_chat(chat.ChatRequest(message="일정을 만들어 주세요.",
                                                         options=fixture.OPTIONS))
            if isinstance(response, dict):
                return 200, chat.ChatResponse.model_validate(response).model_dump(mode="json"), agent.call_count
            return response.status_code, json.loads(response.body), agent.call_count

    def test_unique_current_route_is_returned_without_another_agent_call(self):
        status, body, attempts = self.run_response(fixture.make_records())
        self.assertEqual(status, 200)
        self.assertEqual(attempts, 1)
        self.assertEqual([route["route_id"] for route in body["map_routes"]], ["out", "back"])
        self.assertEqual(body["plan"]["days"][0]["items"][0]["route_id"], "out")

    def test_ambiguous_current_routes_still_fail_after_the_existing_retry_limit(self):
        routes = fixture.make_records()
        routes.append({**copy.deepcopy(routes[0]), "route_id": "other-out"})
        status, body, attempts = self.run_response(routes)
        self.assertEqual((status, attempts), (502, 2))
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertNotIn("plan", body)

    def test_wrong_endpoint_is_not_repaired(self):
        routes = fixture.make_records()
        routes[0]["destination"] = dict(fixture.OTHER)
        status, body, attempts = self.run_response(routes)
        self.assertEqual((status, attempts), (502, 2))
        self.assertEqual(body["error"]["code"], "plan_validation_failed")


if __name__ == "__main__":
    unittest.main()