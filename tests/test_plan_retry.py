import copy
import json
import unittest

from backend.app.services.plan_retry import (
    MAX_PLAN_RETRIES,
    PlanValidationFailure,
    build_plan_retry_messages,
    merge_agent_metrics,
    validate_retry_options,
)


OPTIONS = {
    "departure": "제주빅데이터센터",
    "departure_latitude": 33.4,
    "departure_longitude": 126.5,
    "start_date": "2026-11-23",
    "trip_days": 1,
    "transport": "driving",
    "mobility_aid": "wheelchair",
    "travel_styles": ["자연", "공원"],
    "requirements": ["step_free", "accessible_toilet"],
    "return_to_origin": True,
}


def make_messages():
    return [
        {"role": "assistant", "content": "원하는 여행을 알려 주세요."},
        {"role": "user", "content": json.dumps({
            "message": "조건을 유지해 일정을 만들어 주세요.",
            "options": {},
            "reference_date": "2026-11-12",
            "current_plan": {"days": []},
        }, ensure_ascii=False)},
    ]


def make_plan():
    return {"days": [{"day": 1, "items": [
        {
            "item_type": "move",
            "title": "출발지 → 방문지",
            "route_id": "old",
        },
        {
            "item_type": "visit",
            "title": "방문지",
            "place_id": "P001",
            "verified": True,
            "latitude": 33.4,
            "longitude": 126.5,
            "category": "공원",
        },
    ]}]}


def build(messages=None, *, options=None, errors=None):
    return build_plan_retry_messages(
        make_messages() if messages is None else messages,
        failed_plan=make_plan(),
        options=OPTIONS if options is None else options,
        errors=["이동시간이 조회 결과와 다릅니다."] if errors is None else errors,
    )


class PlanRetryTests(unittest.TestCase):
    def test_retry_limit_is_one(self):
        self.assertEqual(MAX_PLAN_RETRIES, 1)

    def test_original_request_history_and_options_are_preserved(self):
        original = make_messages()
        result = build(original)
        before = json.loads(original[-1]["content"])
        after = json.loads(result[-1]["content"])

        self.assertEqual(result[:-1], original[:-1])
        for key, value in before.items():
            self.assertEqual(after[key], value)
        self.assertEqual(len(result), len(original))

    def test_feedback_contains_failures_and_locked_conditions(self):
        payload = json.loads(build()[-1]["content"])["plan_retry"]

        self.assertEqual(payload["attempt"], 1)
        self.assertEqual(payload["locked_options"], OPTIONS)
        self.assertEqual(
            payload["validation_errors"],
            ["이동시간이 조회 결과와 다릅니다."],
        )

    def test_old_route_ids_and_verified_fields_are_removed(self):
        failed = json.loads(build()[-1]["content"])["plan_retry"]["failed_plan"]

        for item in failed["days"][0]["items"]:
            self.assertIsNone(item["route_id"])
            for field in ["verified", "latitude", "longitude", "category"]:
                self.assertNotIn(field, item)
        self.assertEqual(failed["days"][0]["items"][1]["place_id"], "P001")

    def test_inputs_are_not_changed(self):
        messages = make_messages()
        value = make_plan()
        options = copy.deepcopy(OPTIONS)
        errors = ["오류"]
        before = copy.deepcopy((messages, value, options, errors))

        result = build_plan_retry_messages(
            messages,
            failed_plan=value,
            options=options,
            errors=errors,
        )
        result[0]["content"] = "변경"

        self.assertEqual((messages, value, options, errors), before)

    def test_already_retried_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            build(build())

    def test_invalid_original_message_is_rejected(self):
        for messages in [
            [], None, {}, [None],
            [{"role": "assistant", "content": "{}"}],
            [{"role": "user", "content": "잘못된 JSON"}],
            [{"role": "user", "content": "[]"}],
            [{"role": "user"}],
        ]:
            with self.subTest(messages=messages):
                with self.assertRaises(ValueError):
                    build_plan_retry_messages(
                        messages,
                        failed_plan=make_plan(),
                        options=OPTIONS,
                        errors=["오류"],
                    )

    def test_empty_or_invalid_feedback_is_rejected(self):
        for errors in [[], None, "오류", [""], [None], ["오류", 1]]:
            with self.subTest(errors=errors):
                with self.assertRaises(ValueError):
                    build_plan_retry_messages(
                        make_messages(),
                        failed_plan=make_plan(),
                        options=OPTIONS,
                        errors=errors,
                    )

    def test_feedback_size_is_bounded(self):
        reasons = [str(index) + "가" * 600 for index in range(30)]
        feedback = json.loads(
            build(errors=reasons)[-1]["content"]
        )["plan_retry"]

        self.assertEqual(len(feedback["validation_errors"]), 20)
        self.assertTrue(all(
            len(value) <= 500 for value in feedback["validation_errors"]
        ))

    def test_unchanged_conditions_are_allowed(self):
        self.assertEqual(validate_retry_options(OPTIONS, dict(OPTIONS)), [])

    def test_each_condition_change_is_rejected(self):
        for field in OPTIONS:
            changed = copy.deepcopy(OPTIONS)
            changed[field] = None

            with self.subTest(field=field):
                self.assertEqual(
                    len(validate_retry_options(OPTIONS, changed)),
                    1,
                )

    def test_list_order_does_not_change_conditions(self):
        changed = copy.deepcopy(OPTIONS)
        changed["requirements"].reverse()
        changed["travel_styles"].reverse()

        self.assertEqual(validate_retry_options(OPTIONS, changed), [])

    def test_dropped_requirement_is_rejected(self):
        changed = copy.deepcopy(OPTIONS)
        changed["requirements"] = ["step_free"]

        self.assertEqual(len(validate_retry_options(OPTIONS, changed)), 1)

    def test_false_and_unselected_are_different(self):
        self.assertEqual(
            len(validate_retry_options(
                {"return_to_origin": False},
                {"return_to_origin": None},
            )),
            1,
        )

    def test_metrics_are_added_across_attempts(self):
        self.assertEqual(merge_agent_metrics([
            {
                "model_call_count": 2,
                "tool_call_counts": {"search_places": 1},
            },
            {
                "model_call_count": 3,
                "tool_call_counts": {
                    "search_places": 2,
                    "get_driving_time": 1,
                },
            },
        ]), {
            "model_call_count": 5,
            "tool_call_counts": {
                "search_places": 3,
                "get_driving_time": 1,
            },
        })

    def test_empty_metrics_are_zero(self):
        self.assertEqual(merge_agent_metrics([]), {
            "model_call_count": 0,
            "tool_call_counts": {},
        })

    def test_invalid_metric_counts_are_not_added(self):
        self.assertEqual(merge_agent_metrics([
            {"model_call_count": True, "tool_call_counts": {"x": True}},
            {"model_call_count": -1, "tool_call_counts": {"x": -1}},
            {"model_call_count": "3", "tool_call_counts": None},
        ]), {
            "model_call_count": 0,
            "tool_call_counts": {},
        })

    def test_failure_keeps_reasons_and_public_message(self):
        reasons = ["이동 오류"]
        failure = PlanValidationFailure(reasons, message="위치 오류")
        reasons.append("다른 오류")

        self.assertEqual(failure.errors, ["이동 오류"])
        self.assertEqual(failure.message, "위치 오류")
        self.assertEqual(str(failure), "위치 오류")


if __name__ == "__main__":
    unittest.main()