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


def check(message="숙소를 신라호텔로 변경", *, stay=None, records=None):
    return lodging_planning.validate_lodging_selections(
        {"lodging_stays": []}, {"lodging_stays": [HOTEL if stay is None else stay]},
        user_message=message, tool_trace=trace() if records is None else records,
    )


class LodgingNameAliasTests(unittest.TestCase):
    def test_region_alias_requires_the_whole_name_and_a_matching_query(self):
        for message in (
            "숙소를 신라호텔로 변경", "숙소를신라호텔로변경",
            "신라호텔에서 묵겠습니다.", "숙소를 '신라 호텔'로 바꿔 주세요.",
        ):
            for keyword in ("신라호텔", "신라호텔 제주", "제주신라호텔"):
                with self.subTest(message=message, keyword=keyword):
                    self.assertEqual(check(message, records=trace(keyword=keyword)), [])

    def test_other_region_prefixes_use_the_same_rule(self):
        for prefix in ("제주도", "제주시", "서귀포", "서귀포시", "제주특별자치도"):
            with self.subTest(prefix=prefix):
                stay = {**HOTEL, "name": prefix + "테스트호텔"}
                self.assertEqual(check(
                    "숙소를 테스트호텔로 변경", stay=stay,
                    records=trace(stay, keyword="테스트호텔 " + prefix),
                ), [])

    def test_generic_or_partial_names_do_not_choose_a_hotel(self):
        for message in ("숙소를 호텔로 변경", "숙소를 신라로 변경",
                        "숙소를 라호텔로 변경", "숙소를 다른호텔로 변경"):
            with self.subTest(message=message):
                self.assertTrue(check(message))
        for name in ("제주관광호텔", "제주게스트하우스", "제주비즈니스호텔", "제주hotel"):
            with self.subTest(name=name):
                stay = {**HOTEL, "name": name}
                self.assertTrue(check(
                    "숙소를 " + name.removeprefix("제주") + "로 변경",
                    stay=stay, records=trace(stay, keyword=name),
                ))

    def test_a_different_region_or_subfacility_name_is_not_an_alias(self):
        self.assertTrue(check("숙소를 서울신라호텔로 변경"))
        for name in ("제주신라호텔수영장", "제주신라호텔컨벤션", "제주신라호텔주차장"):
            with self.subTest(name=name):
                stay = {**HOTEL, "name": name}
                self.assertTrue(check(stay=stay, records=trace(stay, keyword=name)))

    def test_an_unrelated_or_missing_lookup_keyword_is_not_alias_evidence(self):
        for keyword in ("롯데호텔", "신라호텔 서울", None):
            with self.subTest(keyword=keyword):
                self.assertTrue(check(records=trace(keyword=keyword)))
        missing = trace()
        missing[0]["input"] = None
        self.assertTrue(check(records=missing))

    def test_lookup_success_name_coordinates_and_fresh_source_are_still_required(self):
        for changes in (
            {"status": "not_found"}, {"source": "selected_lodging"},
            {"latitude": HOTEL["latitude"] + 0.001}, {"name": "다른 호텔"},
        ):
            with self.subTest(changes=changes):
                self.assertTrue(check(records=trace(**changes)))
        self.assertTrue(check(records=[]))
        self.assertTrue(check(stay={**HOTEL, "provider_place_id": "123"}))

    def test_full_name_or_full_address_keeps_existing_behavior(self):
        records = trace(keyword=None)
        self.assertEqual(check("숙소를 제주신라호텔로 변경", records=records), [])
        self.assertEqual(check("숙소 주소는 " + HOTEL["address"], records=records), [])

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
