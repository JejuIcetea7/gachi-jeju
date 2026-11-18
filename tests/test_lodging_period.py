import copy
import unittest

from backend.app.routers.chat import ChatAgentReply, ChatOptions
from backend.app.services.lodging_period import (
    DATE_REQUIRED_REPLY, KEEP_DAY_TRIP_REPLY, PERIOD_QUESTION,
    local_lodging_period_result, lodging_period_step,
    lodging_period_user_message, prepare_lodging_period_payload,
)


OPTIONS = {
    "start_date": "2026-11-20", "trip_days": 1, "transport": "driving",
    "mobility_aid": "wheelchair", "requirements": ["accessible_toilet"],
    "return_to_origin": False, "travel_styles": [], "lodging_status": "not_needed",
    "lodging_stays": [],
}
REQUEST = "1일차 숙소를 제주신라호텔로 변경하고 다음 날 숙소 출발 이동도 반영해 주세요."


def pending():
    return [{"role": "user", "content": REQUEST},
            {"role": "assistant", "content": PERIOD_QUESTION}]


def payload(message=REQUEST, options=None):
    return {
        "message": message, "options": copy.deepcopy(OPTIONS if options is None else options),
        "current_plan": {"days": [{"day": 1, "items": []}]},
    }


class LodgingPeriodTests(unittest.TestCase):
    def test_known_overnight_requests_ask_before_changing_dates(self):
        for message in (
            REQUEST, "숙소를 추가해 주세요.", "숙박을 포함해 주세요.",
            "호텔을 신라호텔로 바꿔 주세요.", "제주신라호텔에서 하룻밤 묵고 싶어요.",
            "하룻밤 자고 갈래요.", "다음 날 숙소에서 출발하는 이동을 넣어 주세요.",
        ):
            with self.subTest(message=message):
                self.assertEqual(lodging_period_step(message, OPTIONS, [])["action"], "ask")

    def test_explicit_duration_and_date_changes_use_normal_agent_flow(self):
        for message in (
            "1박 2일로 늘리고 숙소를 제주신라호텔로 변경해 주세요.",
            "2박 3일 여행으로 바꾸고 숙소를 추가해 주세요.",
            "기간을 연장하고 숙소를 추가해 주세요.",
            "11월 20~21일로 바꾸고 숙소를 추가해 주세요.",
            "11월 20일부터 21일까지 숙박을 포함해 주세요.",
            "2026-11-20~2026-11-21 여행으로 숙소를 추가해 주세요.",
        ):
            with self.subTest(message=message):
                self.assertEqual(lodging_period_step(message, OPTIONS, [])["action"], "pass")

    def test_information_visits_and_negative_requests_do_not_extend_trip(self):
        for message in (
            "제주신라호텔 위치가 어디인가요?", "호텔 카페를 방문하고 싶어요.",
            "숙소 변경 방법을 알려 주세요.", "숙소 변경 방법이 뭐예요?",
            "호텔 체크인 시간을 알려 주세요.", "숙소를 변경하지 마세요.",
            "숙소는 안 바꾸고 박물관만 변경해 주세요.", "숙박은 필요 없어요.",
            "숙소가 필요 없고 센터에서 출발하겠습니다.",
        ):
            with self.subTest(message=message):
                self.assertEqual(lodging_period_step(message, OPTIONS, [])["action"], "pass")

    def test_negative_instruction_for_other_places_does_not_cancel_lodging_intent(self):
        self.assertEqual(lodging_period_step(
            "숙소를 제주신라호텔로 변경해 주세요. 관광지는 바꾸지 마세요.", OPTIONS, [],
        )["action"], "ask")

    def test_multiday_request_does_not_repeat_period_question(self):
        self.assertEqual(lodging_period_step(REQUEST, {**OPTIONS, "trip_days": 2}, [])["action"], "pass")

    def test_short_yes_requires_the_immediately_previous_period_question(self):
        for history in ([], [{"role": "assistant", "content": "자동차로 이동하시나요?"}],
                        pending() + [{"role": "user", "content": "다른 질문"},
                                     {"role": "assistant", "content": "답변"}]):
            self.assertEqual(lodging_period_step("네", OPTIONS, history)["action"], "pass")
        for message in ("네", "예", "좋아요!", "네, 1박 2일로 늘려 주세요."):
            step = lodging_period_step(message, OPTIONS, pending())
            self.assertEqual(step, {"action": "accepted", "original_request": REQUEST})

    def test_short_no_keeps_day_trip(self):
        step = lodging_period_step("아니요", OPTIONS, pending())
        self.assertEqual(step, {"action": "keep"})

    def test_ambiguous_or_negative_counter_reply_is_not_short_yes(self):
        for message in ("네?", "네, 하지만 늘리지 마세요.", "아니요, 1박 2일은 싫어요."):
            self.assertNotEqual(lodging_period_step(message, OPTIONS, pending())["action"], "accepted")

    def test_explicit_counterproposal_is_not_forced_to_two_days(self):
        value = prepare_lodging_period_payload(payload("2박 3일로 바꿔 주세요."), pending())
        self.assertEqual(value["lodging_period_confirmation"]["action"], "specified")
        self.assertEqual(value["options"]["trip_days"], 1)
        self.assertIn(REQUEST, lodging_period_user_message("2박 3일로 바꿔 주세요.", OPTIONS, pending()))

    def test_acceptance_changes_only_period_and_lodging_defaults(self):
        original = payload("네")
        before = copy.deepcopy(original)
        value = prepare_lodging_period_payload(original, pending())
        self.assertEqual(value["options"], {
            **OPTIONS, "trip_days": 2, "lodging_status": "undecided", "lodging_stays": [],
        })
        self.assertEqual(value["current_plan"], original["current_plan"])
        self.assertEqual(original, before)
        ChatOptions.model_validate(value["options"])

    def test_dates_already_changed_in_options_are_not_reduced(self):
        options = {**OPTIONS, "trip_days": 4, "lodging_status": "undecided"}
        value = prepare_lodging_period_payload(payload("네", options), pending())
        self.assertEqual(value["options"]["trip_days"], 4)

    def test_date_limit_asks_for_a_date_without_creating_invalid_options(self):
        value = prepare_lodging_period_payload(
            payload("네", {**OPTIONS, "start_date": "9999-12-31"}), pending(),
        )
        result = local_lodging_period_result(value, ChatAgentReply)
        self.assertEqual(result["chat_reply"].reply, DATE_REQUIRED_REPLY)
        self.assertEqual(result["chat_reply"].options.trip_days, 1)

    def test_year_boundary_keeps_start_date_and_validates_two_days(self):
        value = prepare_lodging_period_payload(
            payload("네", {**OPTIONS, "start_date": "2026-12-31"}), pending(),
        )
        self.assertEqual(value["options"]["start_date"], "2026-12-31")
        self.assertEqual(ChatOptions.model_validate(value["options"]).trip_days, 2)

    def test_question_and_decline_have_no_plan_or_external_call_records(self):
        for message, history, expected in (
            (REQUEST, [], PERIOD_QUESTION), ("아니요", pending(), KEEP_DAY_TRIP_REPLY),
        ):
            value = prepare_lodging_period_payload(payload(message), history)
            result = local_lodging_period_result(value, ChatAgentReply)
            self.assertEqual(result["chat_reply"].reply, expected)
            self.assertEqual(result["chat_reply"].options.model_dump(mode="json"),
                             ChatOptions.model_validate(OPTIONS).model_dump(mode="json"))
            self.assertIsNone(result["chat_reply"].plan)
            self.assertEqual(result["model_call_count"], 0)
            self.assertEqual(result["tool_call_counts"], {})
            self.assertEqual(result["route_calls"], [])
            self.assertEqual(result["messages"], [])

    def test_normal_agent_requests_are_not_replaced_by_local_replies(self):
        value = prepare_lodging_period_payload(payload("박물관을 추천해 주세요."), [])
        self.assertNotIn("lodging_period_confirmation", value)
        self.assertIsNone(local_lodging_period_result(value, ChatAgentReply))

    def test_only_confirmed_request_is_carried_as_lodging_authorization(self):
        self.assertEqual(lodging_period_user_message("네", OPTIONS, []), "네")
        self.assertEqual(lodging_period_user_message("아니요", OPTIONS, pending()), "아니요")
        combined = lodging_period_user_message("네", OPTIONS, pending())
        self.assertIn(REQUEST, combined)
        self.assertTrue(combined.endswith("네"))


if __name__ == "__main__":
    unittest.main()