import copy
import unittest

from backend.app.services.chat_outcomes import (
    classify_chat_response, evaluate_chat_outcome, validate_outcome_expectation,
)


def make_response(counts=(1,)):
    return {
        "reply": "요청 결과입니다.",
        "options": {
            "start_date": "2026-11-20", "trip_days": len(counts),
            "transport": "driving",
        },
        "plan": {
            "assumptions": [],
            "days": [
                {"day": number, "items": [
                    {
                        "item_type": "visit", "title": "테스트 장소",
                        "start_time": f"{9 + index:02d}:00",
                        "end_time": f"{10 + index:02d}:00",
                        "duration_minutes": 60, "place_id": "TEST",
                    }
                    for index in range(count)
                ]}
                for number, count in enumerate(counts, start=1)
            ],
        },
        "warnings": [], "map_routes": [], "transit_routes": [],
        "meta": {"model_call_count": 1, "tool_call_counts": {}, "elapsed_seconds": 1},
    }


class ChatOutcomeTests(unittest.TestCase):
    def test_visits_on_all_days_are_a_plan(self):
        self.assertEqual(classify_chat_response(200, make_response((1, 2))), "plan")

    def test_null_plan_is_a_message_without_guessing_its_meaning(self):
        body = make_response()
        body["plan"] = None
        body["options"] = {}
        for reply in ("여행 날짜가 언제인가요?", "요청하신 정보를 안내합니다."):
            body["reply"] = reply
            self.assertEqual(classify_chat_response(200, body), "message")

    def test_all_days_without_visits_are_empty_even_with_moves(self):
        body = make_response((0, 0))
        body["plan"]["days"][0]["items"] = [{"item_type": "move"}]
        self.assertEqual(classify_chat_response(200, body), "empty_plan")

    def test_some_days_without_visits_are_partial(self):
        self.assertEqual(classify_chat_response(200, make_response((1, 0))), "partial_plan")

    def test_status_type_and_unexpected_success_or_redirect_are_invalid(self):
        for status in (True, 200.0, "200", 201, 204, 302, 0, 600):
            with self.subTest(status=status):
                self.assertEqual(classify_chat_response(status, make_response()), "invalid_response")

    def test_http_errors_need_code_and_public_message(self):
        for status in (400, 502, 503, 504):
            body = {"error": {"code": "test_error", "message": "오류 안내입니다."}}
            self.assertEqual(classify_chat_response(status, body), "error")
        for body in ({}, {"error": None}, {"error": {"code": "", "message": "안내"}}):
            self.assertEqual(classify_chat_response(502, body), "invalid_response")

    def test_missing_reply_options_or_plan_is_invalid(self):
        for key in ("reply", "options", "plan"):
            body = make_response()
            body.pop(key)
            self.assertEqual(classify_chat_response(200, body), "invalid_response")
        for body in (None, [], {"reply": " ", "options": {}, "plan": None}):
            self.assertEqual(classify_chat_response(200, body), "invalid_response")

    def test_incomplete_plan_options_are_not_a_delivered_plan(self):
        for field, value in (
            ("start_date", None), ("start_date", "2026-02-30"),
            ("start_date", "20261120"), ("trip_days", True),
            ("trip_days", 0), ("transport", "unknown"),
        ):
            body = make_response()
            body["options"][field] = value
            self.assertEqual(classify_chat_response(200, body), "invalid_response")

    def test_missing_invalid_and_duplicate_days_are_invalid(self):
        for days in ([], None, [{}], [{"day": True, "items": []}],
                     [{"day": 1, "items": []}, {"day": 1, "items": []}]):
            body = make_response()
            body["plan"]["days"] = days
            self.assertEqual(classify_chat_response(200, body), "invalid_response")

    def test_invalid_item_collections_and_types_are_rejected(self):
        for items in (None, {}, [None], [{"item_type": "rest"}]):
            body = make_response()
            body["plan"]["days"][0]["items"] = items
            self.assertEqual(classify_chat_response(200, body), "invalid_response")

    def test_expected_plan_passes_only_the_outcome_checks(self):
        result = evaluate_chat_outcome(200, make_response(), {"outcome": "plan"})
        self.assertTrue(result["automatic_passed"])
        self.assertFalse(result["requires_manual_review"])
        self.assertIsNone(result["checks"]["error_code"]["passed"])

    def test_expected_message_keeps_content_review_pending(self):
        body = make_response()
        body["plan"] = None
        result = evaluate_chat_outcome(200, body, {"outcome": "message"})
        self.assertTrue(result["automatic_passed"])
        self.assertTrue(result["requires_manual_review"])

    def test_expected_empty_plan_requires_explicit_days_and_review(self):
        result = evaluate_chat_outcome(200, make_response((0, 0)), {
            "outcome": "empty_plan", "empty_days": [1, 2],
        })
        self.assertTrue(result["automatic_passed"])
        self.assertTrue(result["requires_manual_review"])
        self.assertEqual(result["empty_days"], [1, 2])

    def test_expected_partial_plan_preserves_which_day_is_empty(self):
        result = evaluate_chat_outcome(200, make_response((1, 0)), {
            "outcome": "partial_plan", "empty_days": [2],
        })
        self.assertTrue(result["automatic_passed"])
        self.assertTrue(result["requires_manual_review"])
        self.assertEqual(result["empty_days"], [2])

    def test_unexpected_empty_plan_is_not_a_successful_plan(self):
        result = evaluate_chat_outcome(200, make_response((0,)), {"outcome": "plan"})
        self.assertFalse(result["automatic_passed"])
        self.assertFalse(result["checks"]["outcome"]["passed"])

    def test_wrong_empty_day_fails_even_if_outcome_matches(self):
        result = evaluate_chat_outcome(200, make_response((1, 0)), {
            "outcome": "partial_plan", "empty_days": [1],
        })
        self.assertFalse(result["automatic_passed"])
        self.assertFalse(result["checks"]["empty_days"]["passed"])

    def test_expected_error_needs_exact_status_and_code(self):
        expected = {"outcome": "error", "http_status": 502, "error_code": "plan_validation_failed"}
        body = {"error": {"code": "plan_validation_failed", "message": "일정 오류"}}
        self.assertTrue(evaluate_chat_outcome(502, body, expected)["automatic_passed"])
        self.assertFalse(evaluate_chat_outcome(503, body, expected)["automatic_passed"])
        body["error"]["code"] = "llm_invalid_response"
        self.assertFalse(evaluate_chat_outcome(502, body, expected)["automatic_passed"])

    def test_api_error_never_counts_as_a_message_or_plan(self):
        body = {"error": {"code": "internal_error", "message": "오류 안내"}}
        for outcome in ("message", "plan"):
            self.assertFalse(evaluate_chat_outcome(500, body, {"outcome": outcome})["automatic_passed"])

    def test_invalid_response_cannot_match_a_valid_expectation(self):
        result = evaluate_chat_outcome(200, {}, {"outcome": "message"})
        self.assertFalse(result["automatic_passed"])
        self.assertEqual(result["outcome"], "invalid_response")

    def test_invalid_expectations_are_rejected(self):
        examples = (
            None, {}, {"outcome": []}, {"outcome": "invalid_response"},
            {"outcome": "plan", "typo": True},
            {"outcome": "plan", "http_status": True},
            {"outcome": "plan", "http_status": 500},
            {"outcome": "plan", "error_code": "test"},
            {"outcome": "plan", "empty_days": [1]},
            {"outcome": "empty_plan"}, {"outcome": "partial_plan", "empty_days": []},
            {"outcome": "empty_plan", "empty_days": [True]},
            {"outcome": "empty_plan", "empty_days": [1, 1]},
            {"outcome": "empty_plan", "empty_days": [{}]},
            {"outcome": "error"}, {"outcome": "error", "http_status": 502},
            {"outcome": "error", "http_status": 502, "error_code": " test "},
        )
        for expected in examples:
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                validate_outcome_expectation(expected)

    def test_original_body_and_expectation_are_preserved(self):
        body = make_response((0, 0))
        expected = {"outcome": "empty_plan", "empty_days": [2, 1]}
        before = copy.deepcopy((body, expected))
        result = evaluate_chat_outcome(200, body, expected)
        result["empty_days"].append(3)
        self.assertEqual((body, expected), before)
        self.assertEqual(validate_outcome_expectation(expected)["empty_days"], [1, 2])

    def test_exclusion_words_do_not_prove_a_valid_exclusion(self):
        body = make_response()
        body["reply"] = "필수 시설이 없어 방문을 제외했습니다."
        result = evaluate_chat_outcome(200, body, {
            "outcome": "empty_plan", "empty_days": [1],
        })
        self.assertEqual(result["outcome"], "plan")
        self.assertFalse(result["automatic_passed"])


if __name__ == "__main__":
    unittest.main()