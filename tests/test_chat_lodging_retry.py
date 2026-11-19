import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_chat_lodging_planning as fixture
from backend.app.routers import chat, plan
from backend.app.services import lodging_planning, plan_retry


ORIGINAL = {**fixture.stay(), "provider_place_id": "100",
            "place_url": "https://place.map.kakao.com/100"}
NEW = {**fixture.stay(point=fixture.OTHER_HOTEL),
       "name": "제주새숙소", "address": "제주시 다른로 2"}
WRONG = {**NEW, "latitude": fixture.HOTEL["latitude"], "address": "확인되지 않은 주소",
         "provider_place_id": "999", "place_url": "https://place.map.kakao.com/999"}
OTHER = {**fixture.stay(2), "name": "다른 선택 숙소", "latitude": 33.49,
         "provider_place_id": "200", "place_url": "https://place.map.kakao.com/200"}


def evidence(stay=NEW):
    return [
        SimpleNamespace(type="ai", tool_calls=[{
            "id": "location", "name": "find_location", "args": {"keyword": stay["name"]},
        }]),
        SimpleNamespace(type="tool", tool_call_id="location", content=json.dumps({
            "status": "ok", "name": stay["name"], "address": stay["address"],
            "latitude": stay["latitude"], "longitude": stay["longitude"],
        })),
    ]


def result(stays, *, days=2, prefix="current-", question=False, trace=None, message="숙소를 제주새숙소로 변경"):
    selected = fixture.options(days=days, status="provided", stays=stays)
    trip, routes = fixture.make_plan(days=days, stays=stays, prefix=prefix)
    value = fixture.agent_result(None if question else trip, [] if question else routes, selected)
    value["chat_reply"].request_actions = [chat.RequestAction(subject="lodging", action="change", evidence=message)]
    value["messages"] = [] if trace is None else trace
    if question:
        value["chat_reply"].reply = "새 숙소의 정확한 이름이나 주소를 알려 주세요."
        value["chat_reply"].needs_clarification = True
    else:
        value["chat_reply"].changed_options = ["lodging_stays"]
    return value


class ChatLodgingRetryTests(unittest.TestCase):
    def setUp(self):
        self.agent = self.enterContext(patch.object(chat, "run_chat_agent"))
        self.enterContext(patch.object(
            plan, "find_place_in_database",
            return_value={"place_id": "TEST_PLACE", "name": "DB 관광지", "category": "공원", **fixture.SITE},
        ))
        self.enterContext(patch.object(chat, "read_closed_days_by_place_id", return_value={}))
        self.enterContext(patch.object(chat, "read_opening_hours_by_place_id", return_value={}))

    def post(self, *, message="숙소를 제주새숙소로 변경", original=None):
        request = chat.ChatRequest(
            message=message, options=original or fixture.options(status="provided", stays=[ORIGINAL]),
        )
        response = chat.create_chat(request)
        if isinstance(response, dict):
            return 200, chat.ChatResponse.model_validate(response).model_dump(mode="json")
        return response.status_code, json.loads(response.body)

    def retry(self):
        return json.loads(self.agent.call_args_list[1].args[0][-1]["content"])

    def test_unverified_first_proposal_can_return_a_question_with_original_selection(self):
        first = result([WRONG], prefix="old-")
        self.agent.side_effect = [first, result([ORIGINAL], question=True)]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertIsNone(body["plan"])
        self.assertEqual(body["map_routes"], [])
        self.assertEqual(body["options"]["lodging_stays"][0]["provider_place_id"], "100")
        self.assertEqual(self.agent.call_count, 2)
        payload = self.retry()
        self.assertEqual(payload["plan_retry"]["lodging_recheck_nights"], [1])
        self.assertEqual(payload["plan_retry"]["locked_options"]["lodging_stays"], [])
        self.assertNotIn("lodging_status", payload["plan_retry"]["locked_options"])
        self.assertEqual(payload["options"]["lodging_stays"][0]["provider_place_id"], "100")

    def test_unverified_first_proposal_can_be_corrected_with_fresh_location_evidence(self):
        first = result([WRONG], prefix="old-")
        before = copy.deepcopy(first)
        self.agent.side_effect = [first, result([NEW], prefix="new-", trace=evidence())]
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(body["options"]["lodging_stays"][0]["latitude"], NEW["latitude"])
        self.assertIsNone(body["options"]["lodging_stays"][0]["provider_place_id"])
        self.assertEqual(len(body["map_routes"]), 4)
        self.assertTrue(all(route["route_id"].startswith("new-") for route in body["map_routes"]))
        self.assertEqual(body["meta"]["model_call_count"], 2)
        self.assertEqual(first, before)

    def test_recheck_still_requires_fresh_location_evidence(self):
        invented = {**NEW, "name": "임의 숙소"}
        self.agent.side_effect = [
            result([WRONG], prefix="old-"),
            result([invented], prefix="new-", trace=evidence()),
        ]
        status, body = self.post()
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "plan_validation_failed")
        self.assertTrue(any("위치 조회 근거" in item["reason"] for item in body["error"]["details"]))

    def test_verified_first_choice_remains_locked_when_another_error_retries(self):
        first = result([NEW], prefix="old-", trace=evidence())
        first["chat_reply"].plan.days[0].items[0].duration_minutes = 9
        self.agent.side_effect = [first, result([ORIGINAL], prefix="new-")]
        status, body = self.post()
        self.assertEqual(status, 502)
        self.assertTrue(any("자동 재생성에서 숙박일별 숙소" in item["reason"] for item in body["error"]["details"]))
        self.assertNotIn("lodging_recheck_nights", self.retry()["plan_retry"])
        self.assertTrue(self.retry()["plan_retry"]["constraints_locked"])

    def test_recheck_cannot_drop_another_previously_selected_night(self):
        original = fixture.options(days=3, status="provided", stays=[ORIGINAL, OTHER])
        self.agent.side_effect = [
            result([WRONG], days=3, prefix="old-"),
            result([ORIGINAL], days=3, question=True),
        ]
        status, body = self.post(original=original)
        self.assertEqual(status, 502)
        self.assertTrue(any("숙박일별 숙소" in item["reason"] for item in body["error"]["details"]))
        locked = self.retry()["plan_retry"]["locked_options"]["lodging_stays"]
        self.assertEqual([stay["night"] for stay in locked], [2])

    def test_corrected_night_keeps_other_selected_nights(self):
        original = fixture.options(days=3, status="provided", stays=[ORIGINAL, OTHER])
        self.agent.side_effect = [
            result([WRONG], days=3, prefix="old-"),
            result([NEW, OTHER], days=3, prefix="new-", trace=evidence()),
        ]
        status, body = self.post(original=original)
        self.assertEqual(status, 200)
        stays = body["options"]["lodging_stays"]
        self.assertEqual([stay["name"] for stay in stays], [NEW["name"], OTHER["name"]])
        self.assertEqual(stays[1]["provider_place_id"], "200")

    def test_recheck_does_not_relax_transport_or_accessibility_conditions(self):
        for change in ({"transport": "walking"}, {"requirements": []}):
            with self.subTest(change=change):
                self.agent.reset_mock()
                original = fixture.options(status="provided", stays=[ORIGINAL])
                original["requirements"] = ["accessible_toilet"]
                first = result([WRONG], prefix="old-")
                first["chat_reply"].options.requirements = ["accessible_toilet"]
                second = result([NEW], prefix="new-", trace=evidence())
                second["chat_reply"].options.requirements = ["accessible_toilet"]
                for key, value in change.items():
                    setattr(second["chat_reply"].options, key, value)
                self.agent.side_effect = [first, second]
                status, body = self.post(original=original)
                self.assertEqual(status, 502)
                reasons = [item["reason"] for item in body["error"]["details"]]
                self.assertTrue(any("이동수단" in reason or "필수 이용 조건" in reason for reason in reasons))
                self.assertEqual(self.agent.call_count, 2)


class LodgingRetryOptionTests(unittest.TestCase):
    def test_preparation_preserves_inputs_and_does_not_lock_rejected_values(self):
        previous = fixture.options(days=3, status="provided", stays=[ORIGINAL, OTHER])
        current = fixture.options(days=3, status="provided", stays=[WRONG])
        before = copy.deepcopy((previous, current))
        locked, recheck = lodging_planning.prepare_lodging_retry_options(
            previous, current, tool_trace=[],
        )
        self.assertEqual(recheck, [1])
        self.assertEqual(locked["lodging_stays"], [OTHER])
        self.assertNotIn("lodging_status", locked)
        locked["lodging_stays"][0]["name"] = "변경"
        self.assertEqual((previous, current), before)

    def test_valid_selections_keep_the_existing_locking_behavior(self):
        original = fixture.options(status="provided", stays=[ORIGINAL])
        locked, recheck = lodging_planning.prepare_lodging_retry_options(
            original, original, tool_trace=[],
        )
        self.assertEqual(locked, original)
        self.assertEqual(recheck, [])
        locked["lodging_stays"].clear()
        self.assertEqual(len(original["lodging_stays"]), 1)

    def test_recheck_metadata_is_explicit_and_invalid_nights_are_rejected(self):
        messages = [{"role": "user", "content": json.dumps({"message": "숙소 변경"})}]
        kwargs = {"failed_plan": {"days": []}, "options": {}, "errors": ["숙소 확인 필요"]}
        rebuilt = plan_retry.build_plan_retry_messages(messages, **kwargs, lodging_recheck_nights=[1])
        self.assertEqual(json.loads(rebuilt[-1]["content"])["plan_retry"]["lodging_recheck_nights"], [1])
        self.assertNotIn("plan_retry", json.loads(messages[-1]["content"]))
        for invalid in ([0], [True], [1, 1], ["1"], "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    plan_retry.build_plan_retry_messages(messages, **kwargs, lodging_recheck_nights=invalid)
                with self.assertRaises(ValueError):
                    plan_retry.validate_retry_options({}, {}, lodging_recheck_nights=invalid)


if __name__ == "__main__":
    unittest.main()
