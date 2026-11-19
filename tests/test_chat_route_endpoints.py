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

    def run_request(self, plan, records, messages=(), *, message="일정을 추천해 주세요.", options=OPTIONS):
        actions = []
        if plan is not None and any(item.restaurant_ref for day in plan.days for item in day.items):
            if message == "일정을 추천해 주세요.":
                message = "식사 방문을 넣어 주세요."
            actions = [chat_router.RequestAction(subject="meal", action="add", evidence=message)]
        self.agent.return_value = {
            "chat_reply": chat_router.ChatAgentReply(
                request_actions=actions,
                reply="요청을 확인했습니다.",
                options=options,
                plan=plan,
            ),
            "route_calls": copy.deepcopy(records),
            "messages": list(messages),
            "model_call_count": 1,
            "tool_call_counts": {},
        }
        result = chat_router.create_chat(chat_router.ChatRequest(
            message=message,
            options=options,
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

    def test_generated_schedule_with_changed_options_is_still_validated_and_returned(self):
        self.run_request(self.restaurant_plan(), make_records(), self.restaurant_lookup())
        self.agent.return_value["chat_reply"].changed_options = ["lodging_status"]
        request = chat_router.ChatRequest(message="식사 방문을 넣어 주세요.", options=OPTIONS)
        body = chat_router.create_chat(request)
        self.assertIsNotNone(body["plan"])
        self.assertEqual(len(body["map_routes"]), 2)
        self.agent.return_value["route_calls"][0]["destination"] = dict(OTHER)
        result = chat_router.create_chat(request)
        self.assertEqual(result.status_code, 502)
        self.assertEqual(json.loads(result.body)["error"]["code"], "plan_validation_failed")

    def nearby_restaurant_lookup(self):
        messages = self.restaurant_lookup()
        messages[0].tool_calls[0]["args"] = {"purpose": "restaurant", "center_ref": "departure"}
        selected = json.loads(messages[1].content)
        other = {**selected, "point_ref": "found:other", "name": "다른 식당", **OTHER}
        messages[1].content = json.dumps({"status": "ok", "places": [other, selected]})
        return messages, selected

    def test_nearby_meal_uses_only_selected_candidate(self):
        message = "근처 식당 한 곳에서 점심을 먹도록 추가해줘."
        messages, selected = self.nearby_restaurant_lookup()
        reply = chat_router.ChatAgentReply(reply="점심을 추가했습니다.", options=OPTIONS, plan=self.restaurant_plan())
        self.agent.return_value = {"chat_reply": reply, "messages": messages, "route_calls": make_records()}
        body = chat_router.create_chat(chat_router.ChatRequest(message=message, options=OPTIONS))
        self.assertEqual(self.agent.call_count, 1)
        visits = [item for item in body["plan"]["days"][0]["items"] if item["item_type"] == "visit"]
        self.assertEqual([item["title"] for item in visits], [selected["name"]])
        self.assertEqual(len(body["map_routes"]), 2)

    def plan_with_added_meal(self):
        plan = make_plan()
        items = plan.days[0].items
        items[2].start_time, items[2].end_time = "11:00", "11:10"
        items.insert(2, ScheduleItem(
            start_time="10:10", end_time="11:00", item_type="visit", title="모델이 적은 식당",
            restaurant_ref="found:meal", duration_minutes=50,
        ))
        return plan

    def test_meal_added_to_current_plan_needs_declared_meal_addition(self):
        current, _ = plan_router.verify_places(make_plan())
        message = "관람 후 근처 식당에서 점심을 추가해줘."
        messages, selected = self.nearby_restaurant_lookup()
        undeclared = chat_router.ChatAgentReply(reply="점심을 추가했습니다.", options=OPTIONS, plan=self.plan_with_added_meal())
        declared = undeclared.model_copy(update={"schedule_changes": [
            chat_router.ScheduleChange(kind="add", visit="@meal", evidence="근처 식당에서 점심을 추가해줘"),
        ]})
        self.agent.side_effect = [
            {"chat_reply": attempt, "messages": messages, "route_calls": make_records()}
            for attempt in (undeclared, declared)
        ]
        body = chat_router.create_chat(chat_router.ChatRequest(message=message, options=OPTIONS, current_plan=current))
        self.assertEqual(self.agent.call_count, 2)
        retry = json.loads(self.agent.call_args.args[0][-1]["content"])["plan_retry"]
        self.assertTrue(any("추가 요청이 없는 식사" in reason for reason in retry["validation_errors"]))
        visits = [item["title"] for item in body["plan"]["days"][0]["items"] if item["item_type"] == "visit"]
        self.assertEqual(visits, ["DB에서 확인한 이름", selected["name"]])

    def failed_restaurant_lookup(self, failure):
        messages = self.restaurant_lookup()
        messages[0].tool_calls[0]["args"]["purpose"] = "restaurant"
        messages[1].content = json.dumps({"status": failure})
        return messages

    def test_restaurant_failure_keeps_model_reply_and_scoped_retry(self):
        current, _ = plan_router.verify_places(make_plan())
        before = copy.deepcopy(current)
        meal = "관람 후 근처 식당에서 12시에 점심을 먹도록 추가해줘."
        request = chat_router.ChatRequest(message="기존 관람은 유지해줘. " + meal, options=OPTIONS, current_plan=current)
        for failure, retry in (
            ("not_found", False),
            ("api_error", True),
            ("rate_limited", True),
            ("auth_error", False),
        ):
            with self.subTest(failure=failure):
                self.agent.reset_mock()
                model_reply = f"식당 조회 결과({failure})를 안내합니다. 기존 일정은 유지합니다."
                reply = chat_router.ChatAgentReply(
                    reply=model_reply, options=OPTIONS, plan=None,
                    needs_clarification=failure == "not_found",
                    restaurant_retry_request=meal if retry else None,
                    request_actions=[chat_router.RequestAction(subject="meal", action="add", evidence=meal)],
                )
                self.agent.return_value = {
                    "chat_reply": reply, "messages": self.failed_restaurant_lookup(failure), "model_call_count": 1,
                }
                body = chat_router.ChatResponse.model_validate(chat_router.create_chat(request)).model_dump(mode="json")
                self.assertIsNone(body["plan"])
                self.assertEqual(body["reply"], model_reply)
                self.assertEqual(body.get("restaurant_retry_request"), meal if retry else None)
                self.assertEqual(body["options"], request.options.model_dump(mode="json"))
                self.assertEqual(body["meta"]["model_call_count"], 1)
        self.assertEqual(current, before)

    def test_plan_after_stopped_restaurant_lookup_is_fed_back(self):
        current, _ = plan_router.verify_places(make_plan())
        meal = "관람 후 근처 식당에서 점심을 먹도록 추가해줘."
        request = chat_router.ChatRequest(message=meal, options=OPTIONS, current_plan=current)
        messages = self.failed_restaurant_lookup("api_error")
        with_plan = chat_router.ChatAgentReply(reply="점심을 넣었습니다.", options=OPTIONS, plan=make_plan())
        corrected = chat_router.ChatAgentReply(
            reply="식당 조회가 잠시 실패했습니다. 기존 일정은 유지합니다.", options=OPTIONS,
            restaurant_retry_request=meal,
        )
        self.agent.side_effect = [
            {"chat_reply": with_plan, "messages": messages, "route_calls": make_records()},
            {"chat_reply": corrected, "messages": messages},
        ]
        body = chat_router.create_chat(request)
        self.assertEqual(self.agent.call_count, 2)
        feedback = json.loads(self.agent.call_args.args[0][-1]["content"])["plan_retry"]["validation_errors"]
        self.assertTrue(any("plan=null" in reason for reason in feedback))
        self.assertTrue(any("restaurant_retry_request" in reason for reason in feedback))
        self.assertIsNone(body["plan"])
        self.assertEqual(body["restaurant_retry_request"], meal)

    def test_retry_request_without_temporary_failure_is_fed_back(self):
        meal = "점심을 추가해줘."
        reply = chat_router.ChatAgentReply(
            reply="인증 오류로 식당을 찾지 못했습니다.", options=OPTIONS, restaurant_retry_request=meal,
        )
        self.agent.return_value = {"chat_reply": reply, "messages": self.failed_restaurant_lookup("auth_error")}
        result = chat_router.create_chat(chat_router.ChatRequest(message=meal, options=OPTIONS))
        self.assertEqual(result.status_code, 502)
        self.assertEqual(self.agent.call_count, 2)
        reasons = [item["reason"] for item in json.loads(result.body)["error"]["details"]]
        self.assertTrue(any("auth_error" in reason and "null" in reason for reason in reasons))

    def test_confirmed_restaurant_after_failure_clears_retry(self):
        messages = self.restaurant_lookup()
        messages[0].tool_calls[0]["args"]["purpose"] = "restaurant"
        messages[1].content = json.dumps({"status": "api_error"})
        successful_lookup = self.restaurant_lookup()
        successful_lookup[0].tool_calls[0]["id"] = "second_restaurant"
        successful_lookup[1].tool_call_id = "second_restaurant"
        trace = chat_router.extract_tool_trace(messages + successful_lookup)
        self.assertIn("found:meal", chat_router.read_confirmed_restaurants(trace))
        self.assertIsNone(chat_router.restaurant_lookup_failure(trace))

    def test_unrelated_lookup_failure_does_not_override_meal_response(self):
        current, _ = plan_router.verify_places(make_plan())
        meal = "점심을 추가해줘."
        request = chat_router.ChatRequest(message=meal, options=OPTIONS, current_plan=current)
        messages = self.restaurant_lookup()
        messages[0].tool_calls[0]["args"] = {"keyword": "출발지", "purpose": "location"}
        messages[1].content = json.dumps({"status": "api_error"})
        self.agent.return_value = {
            "messages": messages, "chat_reply": chat_router.ChatAgentReply(
                reply="출발지를 확인해 주세요.", options=OPTIONS, needs_clarification=True,
                request_actions=[chat_router.RequestAction(subject="meal", action="add", evidence=meal)],
            ),
        }
        body = chat_router.create_chat(request)
        self.assertEqual(body["reply"], "출발지를 확인해 주세요.")
        self.assertNotIn("restaurant_retry_request", body)

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
        self.assertIn("귀환", body["error"]["details"][0]["reason"])

    def run_meal_time_attempts(self, plans, *, start, end):
        message = "아침 식사만 오전 9시 20분부터 10시까지로 바꿔 주세요."
        changes = [
            chat_router.ScheduleChange(kind="start", visit="@meal", minutes=start, evidence=message),
            chat_router.ScheduleChange(kind="end", visit="@meal", minutes=end, evidence=message),
        ]
        attempts = []
        for plan in plans:
            reply = chat_router.ChatAgentReply(
                reply="식사 시각을 바꿨습니다.", options=OPTIONS, plan=plan, schedule_changes=changes,
            )
            attempts.append({
                "chat_reply": reply, "route_calls": make_records(),
                "messages": self.restaurant_lookup(), "model_call_count": 1,
            })
        self.agent.side_effect = attempts
        result = chat_router.create_chat(chat_router.ChatRequest(message=message, options=OPTIONS))
        if isinstance(result, dict):
            return 200, chat_router.ChatResponse.model_validate(result).model_dump(mode="json")
        return result.status_code, json.loads(result.body)

    def meal_at(self, start, end, minutes):
        plan = self.restaurant_plan()
        meal = plan.days[0].items[1]
        meal.start_time, meal.end_time, meal.duration_minutes = start, end, minutes
        return plan

    def test_declared_meal_times_are_fed_back_until_the_plan_matches(self):
        status, body = self.run_meal_time_attempts(
            [self.restaurant_plan(), self.meal_at("09:20", "10:00", 40)], start=560, end=600,
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        feedback = json.loads(self.agent.call_args.args[0][-1]["content"])["plan_retry"]["validation_errors"]
        self.assertTrue(any("시작 09:20" in reason for reason in feedback))
        inbound, meal, outbound = body["plan"]["days"][0]["items"]
        self.assertEqual((inbound["end_time"], outbound["start_time"]), ("09:10", "10:10"))
        self.assertEqual((meal["start_time"], meal["end_time"], meal["duration_minutes"]), ("09:20", "10:00", 40))
        self.assertEqual(body["plan"]["schedule_limits"], [
            {"day": None, "visit": "@meal", "kind": "start", "minutes": 560},
            {"day": None, "visit": "@meal", "kind": "end", "minutes": 600},
        ])

    def test_declared_meal_time_cannot_overlap_a_route(self):
        overlapping = self.meal_at("09:05", "10:00", 55)
        status, body = self.run_meal_time_attempts([overlapping, copy.deepcopy(overlapping)], start=545, end=600)
        self.assert_validation_error(status, body)
        self.assertEqual(self.agent.call_count, 2)
        self.assertTrue(any("겹치" in item["reason"] for item in body["error"]["details"]))

    def test_waiting_move_is_fed_back_instead_of_removed(self):
        waiting = self.meal_at("09:20", "10:00", 40)
        waiting.days[0].items.insert(1, ScheduleItem(
            item_type="move", title="식당에 일찍 도착해 대기", transport="도보",
            start_time="09:10", end_time="09:20", duration_minutes=10,
        ))
        status, body = self.run_meal_time_attempts([waiting, self.meal_at("09:20", "10:00", 40)], start=560, end=600)

        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
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

    def test_tourism_visit_cannot_omit_departure_and_requested_return(self):
        plan = make_plan()
        plan.days[0].items = [plan.days[0].items[1]]
        status, body = self.run_request(plan, make_records())
        self.assert_validation_error(status, body)
        self.assertEqual(len(body["error"]["details"]), 2)
        self.assertEqual(self.agent.call_count, 2)

    def test_only_permitted_travel_modes_can_be_delivered(self):
        for requested in ("walking", "driving", "transit"):
            for used in ("walking", "driving", "transit"):
                with self.subTest(requested=requested, used=used):
                    plan = make_plan()
                    records = make_records()
                    for item in plan.days[0].items:
                        if item.item_type == "move":
                            item.transport = used
                    for record in records:
                        record["transport"] = used
                    status, body = self.run_request(plan, records, options={**OPTIONS, "transport": requested})
                    if used in ("walking", requested):
                        self.assertEqual(status, 200)
                    else:
                        self.assert_validation_error(status, body)
                        self.assertIn("이동수단", body["error"]["details"][0]["reason"])

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
