import copy
import unittest

from backend.app.routers.chat import ChatOptions
from scripts.chat_multiturn_state import build_followup_request


def make_plan(route_id="old-route", minutes=60):
    return {
        "assumptions": ["기존 일정의 확인사항입니다."],
        "days": [{"day": 1, "items": [
            {
                "start_time": "09:00", "end_time": "09:10",
                "item_type": "move", "title": "출발지 → 관광지",
                "place_id": None, "verified": None, "category": None,
                "latitude": None, "longitude": None, "transport": "자동차",
                "duration_minutes": 10, "distance_km": 1.0,
                "accessibility_notes": [], "route_id": route_id,
            },
            {
                "start_time": "09:10", "end_time": "10:10",
                "item_type": "visit", "title": "테스트 관광지",
                "place_id": "TEST", "verified": True, "category": "공원",
                "latitude": 33.41, "longitude": 126.51, "transport": None,
                "duration_minutes": minutes, "distance_km": None,
                "accessibility_notes": ["시설 유무 확인이 필요합니다."],
                "route_id": None,
            },
        ]}],
    }


def make_record():
    options = ChatOptions(
        departure="테스트 출발지",
        departure_latitude=33.4, departure_longitude=126.5,
        start_date="2026-11-20", trip_days=1, transport="driving",
        mobility_aid="wheelchair", requirements=["accessible_toilet"],
        travel_styles=["공원"], return_to_origin=True,
    ).model_dump(mode="json")
    return {
        "request": {
            "message": "일정을 만들어 주세요.",
            "history": [{"role": "user", "content": "공원을 보고 싶습니다."}],
            "options": copy.deepcopy(options), "current_plan": None,
        },
        "http_status": 200,
        "response": {
            "reply": "관광 일정을 만들었습니다.",
            "options": options, "plan": make_plan(),
            "map_routes": [], "transit_routes": [], "warnings": [],
            "meta": {
                "model_call_count": 1, "tool_call_counts": {},
                "elapsed_seconds": 1.0,
            },
        },
        "attempts": [{"route_calls": [{"route_id": "candidate-route"}]}],
    }


class ChatMultiturnStateTests(unittest.TestCase):
    def test_success_carries_confirmed_options_plan_and_previous_exchange(self):
        record = make_record()
        record["response"]["options"]["departure"] = "확인한 출발지"
        result = build_followup_request(record, "체류시간을 90분으로 바꿔 주세요.")
        self.assertEqual(result["message"], "체류시간을 90분으로 바꿔 주세요.")
        self.assertEqual(result["options"], record["response"]["options"])
        self.assertEqual(result["current_plan"], record["response"]["plan"])
        self.assertEqual(result["history"], [
            *record["request"]["history"],
            {"role": "user", "content": record["request"]["message"]},
            {"role": "assistant", "content": record["response"]["reply"]},
        ])

    def test_question_keeps_current_plan_but_updates_options(self):
        record = make_record()
        record["request"]["current_plan"] = make_plan("saved-route")
        record["response"]["plan"] = None
        record["response"]["reply"] = "방문 시간을 알려 주세요."
        record["response"]["options"]["return_to_origin"] = False
        result = build_followup_request(record, "90분 방문하겠습니다.")
        self.assertEqual(result["current_plan"], record["request"]["current_plan"])
        self.assertFalse(result["options"]["return_to_origin"])
        self.assertEqual(result["history"][-1]["content"], "방문 시간을 알려 주세요.")

    def test_first_question_does_not_invent_a_plan(self):
        record = make_record()
        record["response"]["plan"] = None
        result = build_followup_request(record, "11월 20일 당일 여행입니다.")
        self.assertIsNone(result["current_plan"])

    def test_empty_plan_replaces_a_previous_nonempty_plan(self):
        record = make_record()
        record["request"]["current_plan"] = make_plan("saved-route")
        empty = {"assumptions": ["방문할 수 없습니다."], "days": [{"day": 1, "items": []}]}
        record["response"]["plan"] = empty
        result = build_followup_request(record, "다른 장소를 찾아 주세요.")
        self.assertEqual(result["current_plan"], empty)

    def test_error_keeps_sent_history_request_options_and_current_plan(self):
        for status, code in (
            (409, "request_cancelled"), (503, "chat_busy"),
            (504, "request_timeout"), (502, "plan_validation_failed"),
        ):
            with self.subTest(status=status):
                record = make_record()
                record["http_status"] = status
                record["request"]["current_plan"] = make_plan("saved-route")
                record["response"] = {
                    "error": {"code": code, "message": "요청을 처리하지 못했습니다."},
                    "plan": make_plan("rejected-route"),
                }
                result = build_followup_request(record, "다시 요청합니다.")
                self.assertEqual(result["history"], record["request"]["history"])
                self.assertEqual(result["options"], record["request"]["options"])
                self.assertEqual(result["current_plan"], record["request"]["current_plan"])

    def test_explicit_patch_keeps_false_empty_lists_and_null(self):
        result = build_followup_request(
            make_record(), "선택한 조건을 변경합니다.",
            options_patch={
                "return_to_origin": False, "requirements": [],
                "travel_styles": [], "mobility_aid": None,
            },
        )
        self.assertIs(result["options"]["return_to_origin"], False)
        self.assertEqual(result["options"]["requirements"], [])
        self.assertEqual(result["options"]["travel_styles"], [])
        self.assertIsNone(result["options"]["mobility_aid"])
        self.assertEqual(result["options"]["start_date"], "2026-11-20")
        self.assertEqual(result["options"]["transport"], "driving")

    def test_natural_language_does_not_silently_edit_structured_options(self):
        record = make_record()
        result = build_followup_request(record, "출발지로 돌아가지 않겠습니다.")
        self.assertTrue(result["options"]["return_to_origin"])
        self.assertEqual(result["message"], "출발지로 돌아가지 않겠습니다.")

    def test_only_latest_twenty_history_messages_are_sent(self):
        record = make_record()
        record["request"]["history"] = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"대화 {index}"}
            for index in range(20)
        ]
        result = build_followup_request(record, "새 요청")
        self.assertEqual(len(result["history"]), 20)
        self.assertEqual(result["history"][0]["content"], "대화 2")
        self.assertEqual(result["history"][-2]["content"], record["request"]["message"])
        self.assertEqual(result["history"][-1]["content"], record["response"]["reply"])
        self.assertNotIn({"role": "user", "content": "새 요청"}, result["history"])

    def test_chain_after_question_keeps_all_successful_exchanges(self):
        first = make_record()
        next_request = build_followup_request(first, "방문 시간을 바꾸고 싶습니다.")
        second = {
            "request": next_request, "http_status": 200,
            "response": {**copy.deepcopy(first["response"]),
                         "plan": None, "reply": "몇 분으로 바꿀까요?"},
        }
        result = build_followup_request(second, "90분으로 바꿔 주세요.")
        self.assertEqual(len(result["history"]), 5)
        self.assertEqual(result["history"][-2]["content"], next_request["message"])
        self.assertEqual(result["history"][-1]["content"], "몇 분으로 바꿀까요?")
        self.assertEqual(result["current_plan"], first["response"]["plan"])

    def test_inputs_and_nested_results_are_independent(self):
        record = make_record()
        patch = {"requirements": ["step_free"]}
        saved = copy.deepcopy((record, patch))
        result = build_followup_request(record, "수정합니다.", options_patch=patch)
        self.assertEqual((record, patch), saved)
        result["current_plan"]["days"][0]["items"][1]["accessibility_notes"].append("수정")
        result["options"]["requirements"].append("accessible_parking")
        result["history"][0]["content"] = "수정"
        self.assertEqual((record, patch), saved)

    def test_capture_routes_and_response_metadata_are_not_sent_as_new_evidence(self):
        result = build_followup_request(make_record(), "다시 계산해 주세요.")
        self.assertEqual(set(result), {"message", "history", "options", "current_plan"})
        self.assertEqual(result["current_plan"]["days"][0]["items"][0]["route_id"], "old-route")
        self.assertNotIn("route_calls", result)
        self.assertNotIn("map_routes", result)
        self.assertNotIn("transit_routes", result)

    def test_bad_record_status_or_error_response_is_rejected(self):
        for invalid in (None, [], {}, {"request": {}, "http_status": 200}):
            with self.subTest(record=invalid), self.assertRaises(ValueError):
                build_followup_request(invalid, "다음 요청")
        for status in (True, None, "200", 201, 302, 399, 600):
            record = make_record()
            record["http_status"] = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                build_followup_request(record, "다음 요청")
        record = make_record()
        record["http_status"] = 504
        record["response"] = {"message": "잘못된 오류 형식"}
        with self.assertRaises(ValueError):
            build_followup_request(record, "다음 요청")

    def test_invalid_success_is_not_used_as_conversation_context(self):
        for change in (
            lambda body: body.pop("options"),
            lambda body: body.update(reply=" "),
            lambda body: body.update(reply="가" * 4001),
            lambda body: body.update(plan={"days": "잘못된 일정"}),
        ):
            record = make_record()
            change(record["response"])
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_followup_request(record, "다음 요청")

    def test_invalid_message_options_and_nonfinite_json_are_rejected(self):
        for message in ("", " ", "가" * 1001, None, 1):
            with self.subTest(message=message), self.assertRaises(ValueError):
                build_followup_request(make_record(), message)
        for patch in ([], "옵션", {"unknown": True}, {"trip_days": 0},
                      {"departure_latitude": None}, {"requirements": ["unknown"]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                build_followup_request(make_record(), "다음 요청", options_patch=patch)
        record = make_record()
        record["response"]["meta"]["elapsed_seconds"] = float("nan")
        with self.assertRaises(ValueError):
            build_followup_request(record, "다음 요청")


if __name__ == "__main__":
    unittest.main()
