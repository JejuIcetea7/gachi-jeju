import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_chat_lodging_planning as fixture
from backend.app.routers import chat, plan
from backend.app.services import lodging_planning


HOTEL = {
    "night": 1, "name": "제주신라호텔",
    "address": "제주특별자치도 서귀포시 중문관광로72번길 75",
    "latitude": 33.24743087763671, "longitude": 126.40804141124566,
    "provider_place_id": None, "place_url": None,
}


def trace(stay=None, keyword="신라호텔 제주", **changes):
    stay = HOTEL if stay is None else stay
    return [{"tool": "find_location", "input": {"keyword": keyword}, "output": {
        "status": "ok", "name": stay["name"], "address": stay["address"],
        "latitude": stay["latitude"], "longitude": stay["longitude"], **changes,
    }}]


def check(*, stay=None, records=None):
    return lodging_planning.validate_lodging_selections(
        {"lodging_stays": []}, {"lodging_stays": [HOTEL if stay is None else stay]},
        tool_trace=trace() if records is None else records,
    )


class LodgingLookupEvidenceTests(unittest.TestCase):
    """사용자가 어떤 이름으로 불렀는지는 Agent가 판단하고, 서버는 조회 결과와 저장 값의 일치만 검사한다."""

    def test_stay_that_copies_a_fresh_lookup_is_accepted_for_any_search_keyword(self):
        for keyword in ("신라호텔", "신라호텔 제주", "제주신라호텔", None):
            with self.subTest(keyword=keyword):
                self.assertEqual(check(records=trace(keyword=keyword)), [])

    def test_stay_must_copy_the_lookup_name_address_and_coordinates(self):
        for changes in (
            {"name": "신라호텔"},
            {"name": "제주신라호텔수영장"},
            {"address": "제주특별자치도 서귀포시 다른 길 1"},
            {"latitude": HOTEL["latitude"] + 0.001},
        ):
            with self.subTest(changes=changes):
                self.assertTrue(check(stay={**HOTEL, **changes}))

    def test_failed_reused_or_restaurant_lookups_are_not_location_evidence(self):
        for changes in (
            {"status": "not_found"}, {"source": "selected_lodging"},
            {"lookup_performed": False},
        ):
            with self.subTest(changes=changes):
                self.assertTrue(check(records=trace(**changes)))
        restaurant = trace()
        restaurant[0]["input"]["purpose"] = "restaurant"
        self.assertTrue(check(records=restaurant))
        self.assertTrue(check(records=[]))

    def test_map_identifier_cannot_be_invented(self):
        self.assertTrue(check(stay={**HOTEL, "provider_place_id": "123"}))

    def test_original_names_coordinates_and_inputs_are_preserved(self):
        stay, records = copy.deepcopy(HOTEL), trace()
        before = copy.deepcopy((stay, records))
        self.assertEqual(check(stay=stay, records=records), [])
        self.assertEqual((stay, records), before)


class ChatLodgingAliasTests(unittest.TestCase):
    def run_response(self, stay):
        selected = fixture.options(status="provided", stays=[stay])
        trip, routes = fixture.make_plan(stays=[stay])
        reply = fixture.agent_result(trip, routes, selected)
        reply["chat_reply"].request_actions = [chat.RequestAction(subject="lodging", action="change", evidence="숙소를 신라호텔로 변경")]
        reply["chat_reply"].changed_options = ["lodging_stays"]
        lookup = trace()[0]
        reply["messages"] = [
            SimpleNamespace(type="ai", tool_calls=[{
                "id": "hotel", "name": "find_location", "args": lookup["input"],
            }]),
            SimpleNamespace(type="tool", tool_call_id="hotel", content=json.dumps(lookup["output"])),
        ]
        with (
            patch.object(chat, "run_chat_agent", return_value=reply) as agent,
            patch.object(plan, "find_place_in_database", return_value={
                "place_id": "TEST_PLACE", "name": "DB 관광지", "category": "공원", **fixture.SITE,
            }),
            patch.object(chat, "read_closed_days_by_place_id", return_value={}),
            patch.object(chat, "read_opening_hours_by_place_id", return_value={}),
        ):
            response = chat.create_chat(chat.ChatRequest(
                message="숙소를 신라호텔로 변경",
                options=fixture.options(status="provided", stays=[fixture.stay()]),
            ))
            if isinstance(response, dict):
                return 200, chat.ChatResponse.model_validate(response).model_dump(mode="json"), agent.call_count
            return response.status_code, json.loads(response.body), agent.call_count

    def test_requested_alias_updates_the_stay_and_routes_without_retry(self):
        status, body, attempts = self.run_response(HOTEL)
        self.assertEqual(status, 200)
        self.assertEqual(attempts, 1)
        self.assertEqual(body["options"]["lodging_stays"][0]["name"], HOTEL["name"])
        self.assertEqual(body["options"]["lodging_stays"][0]["latitude"], HOTEL["latitude"])
        self.assertEqual(len(body["map_routes"]), 4)

    def test_alias_does_not_allow_old_coordinates_for_the_new_hotel(self):
        status, body, attempts = self.run_response({**HOTEL, **fixture.HOTEL})
        self.assertEqual(status, 502)
        self.assertEqual(attempts, 2)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertNotIn("plan", body)


if __name__ == "__main__":
    unittest.main()
