import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.agent.planner import TripPlan
from backend.app.routers import chat as chat_router
from backend.app.routers import plan as plan_router


ORIGIN = {"latitude": 33.4, "longitude": 126.5}
SITE = {"latitude": 33.41, "longitude": 126.51}
HOTEL = {"latitude": 33.45, "longitude": 126.55}
OTHER_HOTEL = {"latitude": 33.48, "longitude": 126.58}


def stay(night=1, point=None):
    return {"night": night, "name": "선택한 숙소", "address": "제주시 테스트 도로 1", **(point or HOTEL)}


def options(days=2, status="undecided", stays=None):
    return {
        "departure": "원래 출발지", "departure_latitude": ORIGIN["latitude"],
        "departure_longitude": ORIGIN["longitude"], "start_date": "2026-11-20", "trip_days": days,
        "transport": "driving", "mobility_aid": "none", "return_to_origin": True,
        "lodging_status": status, "lodging_stays": stays or [],
    }


def timestamp(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def make_plan(days=2, stays=None, prefix="current-"):
    choices = {item["night"]: item for item in stays or []}
    plan = {"days": []}
    records = []
    for number in range(1, days + 1):
        items = []
        minute = 540

        def add_move(identifier, origin, destination):
            nonlocal minute
            items.append({"start_time": timestamp(minute), "end_time": timestamp(minute + 10),
                          "item_type": "move", "title": "선택한 위치 사이 이동", "transport": "자동차",
                          "duration_minutes": 10, "route_id": identifier})
            minute += 10
            records.append({
                "route_id": identifier, "transport": "driving", "duration_seconds": 600,
                "distance_m": 1000, "geometry_status": "ok",
                "origin": {key: origin[key] for key in ("latitude", "longitude")},
                "destination": {key: destination[key] for key in ("latitude", "longitude")},
                "paths": [[[origin["longitude"], origin["latitude"]],
                           [destination["longitude"], destination["latitude"]]]],
                "requested_at": "2026-11-13T05:00:00+00:00",
            })

        if number == 1 or number - 1 in choices:
            add_move(prefix + f"{number}-start", ORIGIN if number == 1 else choices[number - 1], SITE)
        items.append({"start_time": timestamp(minute), "end_time": timestamp(minute + 60),
                      "item_type": "visit", "title": "관광지", "place_id": "TEST_PLACE", "duration_minutes": 60})
        minute += 60
        if number in choices or number == days:
            add_move(prefix + f"{number}-end", SITE, ORIGIN if number == days else choices[number])
        plan["days"].append({"day": number, "items": items})
    return TripPlan.model_validate(plan), records


def agent_result(plan, records, selected_options):
    return {
        "chat_reply": chat_router.ChatAgentReply(reply="관광 일정을 구성했습니다.", options=selected_options, plan=plan),
        "route_calls": copy.deepcopy(records), "messages": [], "model_call_count": 1, "tool_call_counts": {},
    }


class ChatLodgingPlanningTests(unittest.TestCase):
    def setUp(self):
        self.agent = self.enterContext(patch.object(chat_router, "run_chat_agent"))
        self.lookup = self.enterContext(patch.object(
            plan_router, "find_place_in_database",
            return_value={"place_id": "TEST_PLACE", "name": "DB 관광지", "category": "공원", **SITE},
        ))
        self.closed = self.enterContext(patch.object(chat_router, "read_closed_days_by_place_id", return_value={}))
        self.opening = self.enterContext(patch.object(chat_router, "read_opening_hours_by_place_id", return_value={}))

    def post(self, selected_options=None, message="관광 일정부터 만들어 주세요."):
        request = chat_router.ChatRequest(message=message,
                                          options=selected_options or options())
        result = chat_router.create_chat(request)
        if isinstance(result, dict):
            return 200, chat_router.ChatResponse.model_validate(result).model_dump(mode="json")
        return result.status_code, json.loads(result.body)

    def test_undecided_stays_allow_two_three_and_longer_day_tourism_plans(self):
        for days in (2, 3, 5):
            with self.subTest(days=days):
                selected = options(days)
                plan, records = make_plan(days)
                self.agent.return_value = agent_result(plan, records, selected)
                status, body = self.post(selected)
                self.assertEqual(status, 200)
                self.assertEqual(len(body["plan"]["days"]), days)
                self.assertEqual(body["options"]["lodging_status"], "undecided")
                self.assertIn(f"{days - 1}박", body["warnings"][-1])
                for day in body["plan"]["days"][:-1]:
                    self.assertEqual(day["items"][-1]["item_type"], "visit")
                for day in body["plan"]["days"][1:]:
                    self.assertEqual(day["items"][0]["item_type"], "visit")

    def test_chosen_stay_connects_days_without_becoming_database_visit(self):
        selected = options(status="provided", stays=[stay()])
        plan, records = make_plan(stays=[stay()])
        self.agent.return_value = agent_result(plan, records, selected)
        status, body = self.post(selected)
        self.assertEqual(status, 200)
        self.assertEqual(body["warnings"], [])
        self.assertEqual(body["options"]["lodging_stays"][0]["night"], 1)
        self.assertEqual(body["options"]["lodging_stays"][0]["latitude"], HOTEL["latitude"])
        visits = [item for day in body["plan"]["days"] for item in day["items"] if item["item_type"] == "visit"]
        self.assertEqual([item["place_id"] for item in visits], ["TEST_PLACE", "TEST_PLACE"])
        self.assertEqual(len(body["map_routes"]), 4)
        self.assertEqual(self.lookup.call_count, 2)

    def test_stay_at_verified_site_needs_no_transfer(self):
        selected = options(status="provided", stays=[stay(point=SITE)])
        plan, records = make_plan()
        self.agent.return_value = agent_result(plan, records, selected)
        status, body = self.post(selected)
        self.assertEqual(status, 200)
        self.assertEqual([route["route_id"] for route in body["map_routes"]], ["current-1-start", "current-2-end"])

    def test_partial_choices_only_connect_the_selected_night(self):
        selected = options(3, stays=[stay()])
        plan, records = make_plan(3, [stay()])
        self.agent.return_value = agent_result(plan, records, selected)
        status, body = self.post(selected)
        self.assertEqual(status, 200)
        self.assertIn("1박", body["warnings"][-1])
        self.assertEqual(body["plan"]["days"][1]["items"][0]["item_type"], "move")
        self.assertEqual(body["plan"]["days"][1]["items"][-1]["item_type"], "visit")
        self.assertEqual(body["plan"]["days"][2]["items"][0]["item_type"], "visit")

    def omitted_lodging_options(self):
        response_options = options()
        response_options.pop("lodging_status")
        response_options.pop("lodging_stays")
        return response_options

    def test_omitted_lodging_state_is_fed_back_instead_of_filled_by_server(self):
        plan, records = make_plan()
        self.agent.side_effect = [
            agent_result(plan, records, self.omitted_lodging_options()),
            agent_result(plan, records, options()),
        ]
        status, body = self.post(options())
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["options"]["lodging_status"], "undecided")
        first_payload = json.loads(self.agent.call_args_list[0].args[0][-1]["content"])
        self.assertEqual(first_payload["options"]["lodging_status"], "undecided")
        retry = json.loads(self.agent.call_args.args[0][-1]["content"])["plan_retry"]
        self.assertFalse(retry["constraints_locked"])
        self.assertTrue(any("숙소 상태" in reason and "changed_options" in reason for reason in retry["validation_errors"]))

    def test_omitted_selected_stays_are_fed_back_and_kept_by_the_agent(self):
        selected = options(status="provided", stays=[stay()])
        plan, records = make_plan(stays=[stay()])
        self.agent.side_effect = [
            agent_result(plan, records, self.omitted_lodging_options()),
            agent_result(plan, records, selected),
        ]
        status, body = self.post(selected)
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertEqual(body["options"]["lodging_stays"][0]["name"], "선택한 숙소")
        retry = json.loads(self.agent.call_args.args[0][-1]["content"])["plan_retry"]
        self.assertTrue(any("숙박일별 숙소" in reason for reason in retry["validation_errors"]))

    def test_explicit_lodging_change_is_respected(self):
        original = options(status="provided", stays=[stay()])
        changed = options(status="provided", stays=[stay(point=OTHER_HOTEL)])
        plan, records = make_plan(stays=changed["lodging_stays"])
        self.agent.return_value = agent_result(plan, records, changed)
        self.agent.return_value["messages"] = self.location_evidence(OTHER_HOTEL)
        self.agent.return_value["chat_reply"].request_actions = [
            chat_router.RequestAction(subject="lodging", action="change", evidence="선택한 숙소로 변경했습니다.")
        ]
        self.agent.return_value["chat_reply"].changed_options = ["lodging_stays"]
        status, body = self.post(original, message="선택한 숙소로 변경했습니다.")
        self.assertEqual(status, 200)
        self.assertEqual(body["options"]["lodging_stays"][0]["latitude"], OTHER_HOTEL["latitude"])

    def location_evidence(self, point):
        return [
            SimpleNamespace(type="ai", tool_calls=[{
                "id": "location", "name": "find_location", "args": {"keyword": "선택한 숙소"},
            }]),
            SimpleNamespace(type="tool", tool_call_id="location", content=json.dumps({
                "status": "ok", "name": "선택한 숙소", "address": "제주시 테스트 도로 1", **point,
            })),
        ]

    def test_agent_cannot_choose_unrequested_stay_even_after_location_lookup(self):
        chosen = options(status="provided", stays=[stay()])
        plan, records = make_plan(stays=[stay()])
        self.agent.return_value = agent_result(plan, records, chosen)
        self.agent.return_value["messages"] = self.location_evidence(HOTEL)
        status, body = self.post(options())
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertTrue(any("숙소" in item["reason"] for item in body["error"]["details"]))
        self.lookup.assert_not_called()

    def test_question_cannot_silently_store_an_unselected_stay(self):
        chosen = options(status="provided", stays=[stay()])
        self.agent.return_value = agent_result(None, [], chosen)
        self.agent.return_value["chat_reply"].changed_options = ["lodging_status", "lodging_stays"]
        status, body = self.post(options())
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertEqual(self.agent.call_count, 2)
        self.assertTrue(any("위치 조회 근거" in item["reason"] for item in body["error"]["details"]))

    def test_unselected_lodging_moves_are_fed_back_and_tourism_plan_is_accepted(self):
        with_hotel_moves, hotel_records = make_plan(stays=[stay()], prefix="bad-")
        tourism_only, tourism_records = make_plan()
        self.agent.side_effect = [
            agent_result(with_hotel_moves, hotel_records, options()),
            agent_result(tourism_only, tourism_records, options()),
        ]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        retry = json.loads(self.agent.call_args.args[0][-1]["content"])["plan_retry"]
        self.assertTrue(any("숙소가 미정" in reason for reason in retry["validation_errors"]))
        self.assertEqual(body["plan"]["days"][0]["items"][-1]["item_type"], "visit")
        self.assertEqual(body["plan"]["days"][1]["items"][0]["item_type"], "visit")
        self.assertEqual([route["route_id"] for route in body["map_routes"]], ["current-1-start", "current-2-end"])
        self.assertEqual(body["reply"], "관광 일정을 구성했습니다.")

    def test_missing_selected_transfers_can_be_fixed_with_one_retry(self):
        selected = options(status="provided", stays=[stay()])
        bad, old_records = make_plan(prefix="old-")
        good, new_records = make_plan(stays=[stay()], prefix="new-")
        self.agent.side_effect = [agent_result(bad, old_records, selected), agent_result(good, new_records, selected)]
        status, body = self.post(selected)
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertTrue(all(route["route_id"].startswith("new-") for route in body["map_routes"]))
        retry_payload = json.loads(self.agent.call_args.args[0][-1]["content"])["plan_retry"]
        self.assertEqual(retry_payload["locked_options"]["lodging_status"], "provided")
        self.assertEqual(retry_payload["locked_options"]["lodging_stays"][0]["latitude"], HOTEL["latitude"])
        self.assertTrue(any("숙소" in reason for reason in retry_payload["validation_errors"]))

    def test_wrong_selected_hotel_endpoint_is_still_rejected(self):
        selected = options(status="provided", stays=[stay()])
        plan, records = make_plan(stays=[stay()])
        records[1]["destination"] = ORIGIN
        self.agent.return_value = agent_result(plan, records, selected)
        status, body = self.post(selected)
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertTrue(any("도착 위치" in item["reason"] for item in body["error"]["details"]))

    def test_question_preserves_lodging_state_without_database_or_routes(self):
        self.agent.return_value = agent_result(None, [], options())
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertIsNone(body["plan"])
        self.assertEqual(body["options"]["lodging_status"], "undecided")
        self.assertEqual(body["map_routes"], [])
        self.assertEqual(body["warnings"], [])
        self.lookup.assert_not_called()


    def test_reported_second_day_center_departure_is_fed_back(self):
        plan, records = make_plan(stays=[stay()])
        plan.days[0].items.pop()
        plan.days[1].items[0].title = "원래 출발지 → 관광지"
        records[2]["origin"] = dict(ORIGIN)
        corrected, corrected_records = make_plan()
        self.agent.side_effect = [
            agent_result(plan, records, options()),
            agent_result(corrected, corrected_records, options()),
        ]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        retry = json.loads(self.agent.call_args.args[0][-1]["content"])["plan_retry"]
        self.assertTrue(any("2일차" in reason for reason in retry["validation_errors"]))
        self.assertEqual(body["plan"]["days"][1]["items"][0]["item_type"], "visit")

    def test_route_less_lodging_move_is_fed_back_instead_of_removed(self):
        plan, records = make_plan(stays=[stay()])
        records = [record for record in records if record["route_id"] in ("current-1-start", "current-2-end")]
        plan.days[1].items[0].route_id = None
        plan.days[1].items[0].transport = None
        corrected, corrected_records = make_plan()
        self.agent.side_effect = [
            agent_result(plan, records, options()),
            agent_result(corrected, corrected_records, options()),
        ]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(len(body["map_routes"]), 2)
        self.assertEqual(self.agent.call_count, 2)

    def test_invalid_internal_tourism_move_is_still_rejected(self):
        plan, records = make_plan(stays=[stay()])
        payload = plan.model_dump()
        payload["days"][1]["items"] = [
            payload["days"][1]["items"][0], payload["days"][1]["items"][1],
            {"item_type": "move", "title": "관광지 사이 이동", "start_time": "10:10", "end_time": "10:20",
             "transport": "자동차", "duration_minutes": 10, "route_id": "unknown-internal"},
            {"item_type": "visit", "title": "두 번째 관광지", "place_id": "TEST_PLACE",
             "start_time": "10:20", "end_time": "11:20", "duration_minutes": 60},
            {**payload["days"][1]["items"][-1], "start_time": "11:20", "end_time": "11:30"},
        ]
        self.agent.return_value = agent_result(TripPlan.model_validate(payload), records, options())
        status, body = self.post()
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertEqual(self.agent.call_count, 2)
        self.assertTrue(any("관광지 사이 이동" in item["reason"] for item in body["error"]["details"]))
        self.lookup.assert_not_called()

    def test_model_reply_is_returned_without_server_notes(self):
        plan, records = make_plan()
        self.agent.return_value = agent_result(plan, records, options())
        self.agent.return_value["chat_reply"].reply = "가" * 4000
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], "가" * 4000)

if __name__ == "__main__":
    unittest.main()
