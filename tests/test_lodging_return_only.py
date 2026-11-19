import copy
import json
import unittest
from unittest.mock import patch

import test_chat_lodging_planning as fixture
from test_chat_lodging_period import HOTEL, OPTIONS, history, location_messages
from backend.app.agent.planner import TripPlan
from backend.app.routers import chat, plan
from backend.app.services.lodging_planning import UNDECIDED_LODGING_NOTE
from backend.app.services.plan_reply_consistency import validate_plan_reply_consistency
from backend.app.services.route_endpoints import validate_route_endpoints


STALE_NOTE = (
    "숙소가 제공되지 않은 2일차는 첫 관광지 출발 이동을 정할 수 없어 관람 일정에서 "
    "제외했습니다. 숙소 선택 후 이동시간과 일정 재계산이 필요합니다."
)


def make_result(*, missing=False, memo=None):
    selected = fixture.options(days=2, status="provided", stays=[HOTEL])
    trip, routes = fixture.make_plan(days=2, stays=[HOTEL])
    value = trip.model_dump()
    value["days"][1]["items"] = [] if missing else [{
        "item_type": "move", "title": "선택한 숙소 → 원래 출발지",
        "start_time": "09:00", "end_time": "09:10", "duration_minutes": 10,
        "transport": "자동차", "route_id": "hotel-return",
    }]
    value["assumptions"] = [memo] if memo else []
    if not missing:
        routes.append({
            **routes[-1], "route_id": "hotel-return",
            "origin": dict(fixture.HOTEL), "destination": dict(fixture.ORIGIN),
        })
    routes = [route for route in routes if route["route_id"] in (
        "current-1-start", "current-1-end", "hotel-return",
    )]
    result = fixture.agent_result(TripPlan.model_validate(value), routes, selected)
    result["messages"] = location_messages()
    # 기간 연장 동의와 숙박 변경은 Agent가 대화에서 판단해 구조화 값으로 선언한다.
    result["chat_reply"].period_confirmed = True
    result["chat_reply"].changed_options = ["trip_days", "lodging_status", "lodging_stays"]
    return result


def check_endpoints(result, **overrides):
    return validate_route_endpoints(
        result["chat_reply"].plan.model_dump(), result["route_calls"],
        **{
            "departure": fixture.ORIGIN, "return_to_origin": True,
            "lodging_stays": [HOTEL], **overrides,
        },
    )


class LodgingReturnOnlyTests(unittest.TestCase):
    def test_empty_last_day_cannot_drop_requested_return_after_hotel_arrival(self):
        errors = check_endpoints(make_result(missing=True))
        self.assertTrue(errors)
        self.assertTrue(any("귀환" in error and "2일차" in error for error in errors))

    def test_return_only_day_needs_no_invented_visit(self):
        self.assertEqual(check_endpoints(make_result()), [])

    def test_both_return_endpoints_are_checked(self):
        for field, wrong in (("origin", fixture.ORIGIN), ("destination", fixture.HOTEL)):
            with self.subTest(field=field):
                result = make_result()
                result["route_calls"][-1][field] = dict(wrong)
                self.assertTrue(check_endpoints(result))

    def test_unknown_boundaries_and_unrequested_return_are_not_invented(self):
        for overrides in (
            {"return_to_origin": False}, {"return_to_origin": None},
            {"departure": None}, {"lodging_stays": []},
        ):
            with self.subTest(overrides=overrides):
                self.assertEqual(check_endpoints(make_result(missing=True), **overrides), [])

    def test_entirely_empty_plan_does_not_invent_a_hotel_trip(self):
        result = make_result(missing=True)
        result["chat_reply"].plan.days[0].items = []
        self.assertEqual(check_endpoints(result), [])

    def test_inputs_are_preserved(self):
        result = make_result(missing=True)
        before = copy.deepcopy(result)
        check_endpoints(result)
        self.assertEqual(result, before)


class SelectedLodgingReplyTests(unittest.TestCase):
    def check(self, text, *, assumptions=False, options=None):
        result = make_result(memo=text if assumptions else None)
        return validate_plan_reply_consistency(
            "숙소 위치를 반영했습니다." if assumptions else text,
            result["chat_reply"].plan.model_dump(),
            options=result["chat_reply"].options.model_dump() if options is None else options,
        )

    def test_selected_hotel_cannot_be_described_as_unprovided(self):
        for text in (
            STALE_NOTE, "숙소 정보가 제공되지 않아 이동을 제외했습니다.",
            "숙소는 미정입니다.", UNDECIDED_LODGING_NOTE,
        ):
            with self.subTest(text=text):
                self.assertTrue(self.check(text))
                self.assertTrue(self.check(text, assumptions=True))

    def test_partial_or_unknown_lodging_can_keep_applicable_warning(self):
        for options in (
            fixture.options(days=2),
            fixture.options(days=3, stays=[HOTEL]),
            fixture.options(days=3, status="provided", stays=[HOTEL]),
        ):
            with self.subTest(options=options):
                self.assertEqual(self.check(STALE_NOTE, options=options), [])

    def test_conditional_historical_and_site_verification_notes_are_allowed(self):
        for text in (
            "숙소가 제공되지 않으면 이동을 계산할 수 없습니다.",
            "숙소가 제공되지 않은 경우에는 위치 확인이 필요합니다.",
            "숙소가 제공되지 않은 것은 아닙니다.",
            "숙소가 제공되지 않았지만 지금은 선택했습니다.",
            "숙소는 미정이 아닙니다.",
            "숙소가 없어도 관광 일정을 만들 수 있습니다.",
            "숙소 위치를 반영했으며 호텔의 운영·객실·접근성은 확인하지 않았습니다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.check(text), [])

    def test_all_selected_nights_are_used_even_if_status_is_undecided(self):
        options = fixture.options(days=2, stays=[HOTEL])
        self.assertTrue(self.check(STALE_NOTE, options=options))


class ChatLodgingReturnOnlyTests(unittest.TestCase):
    def setUp(self):
        self.agent = self.enterContext(patch.object(chat, "run_chat_agent"))
        self.enterContext(patch.object(plan, "find_place_in_database", return_value={
            "place_id": "TEST_PLACE", "name": "DB 관광지", "category": "공원", **fixture.SITE,
        }))
        self.enterContext(patch.object(chat, "read_closed_days_by_place_id", return_value={}))
        self.enterContext(patch.object(chat, "read_opening_hours_by_place_id", return_value={}))

    def post(self):
        response = chat.create_chat(chat.ChatRequest(message="네", options=OPTIONS, history=history()))
        if isinstance(response, dict):
            return 200, chat.ChatResponse.model_validate(response).model_dump(mode="json")
        return response.status_code, json.loads(response.body)

    def test_missing_return_is_not_delivered_as_http_success(self):
        self.agent.side_effect = lambda *args: make_result(missing=True)
        status, body = self.post()
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertEqual(self.agent.call_count, 2)

    def test_final_day_can_contain_only_hotel_to_origin_move(self):
        self.agent.return_value = make_result()
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(len(body["map_routes"]), 3)
        self.assertEqual([item["item_type"] for item in body["plan"]["days"][1]["items"]], ["move"])
        visits = [item for item in body["plan"]["days"][0]["items"] if item["item_type"] == "visit"]
        self.assertEqual(len(visits), 1)
        self.assertEqual(visits[0]["duration_minutes"], 60)
        self.assertEqual(body["options"]["trip_days"], 2)

    def test_stale_note_is_model_content_and_not_rewritten_by_server(self):
        # 서버는 메모 문장의 의미를 정규식으로 판정하거나 고치지 않는다. 문구 점검은 평가 도구가 담당한다.
        self.agent.side_effect = lambda *args: make_result(memo=STALE_NOTE)
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["plan"]["assumptions"], [STALE_NOTE])
        self.assertEqual(self.agent.call_count, 1)

    def test_existing_retry_can_correct_missing_return_and_stale_note(self):
        self.agent.side_effect = [make_result(missing=True, memo=STALE_NOTE), make_result()]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.call_count, 2)
        self.assertNotIn(STALE_NOTE, body["plan"]["assumptions"])
        feedback = json.loads(self.agent.call_args_list[1].args[0][-1]["content"])
        self.assertTrue(any("귀환" in error for error in feedback["plan_retry"]["validation_errors"]))


if __name__ == "__main__":
    unittest.main()