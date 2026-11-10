import unittest
from copy import deepcopy

from backend.app.services.plan_validation import validate_plan_times


def make_item(start, end, duration):
    return {
        "start_time": start,
        "end_time": end,
        "duration_minutes": duration,
    }


def make_plan(*items):
    return {"days": [{"day": 1, "items": list(items)}]}


class PlanTimeValidationTests(unittest.TestCase):
    def setUp(self):
        self.window = {"start_time": "13:00", "end_time": "17:00"}

    def test_valid_plan_allows_early_finish_and_preserves_input(self):
        plan = make_plan(
            make_item("13:00", "13:17", 17),
            make_item("13:17", "15:17", 120),
            make_item("15:17", "15:34", 17),
        )
        original = deepcopy(plan)

        self.assertEqual(validate_plan_times(plan, **self.window), [])
        self.assertEqual(plan, original)

    def test_adjacent_items_and_exact_boundaries_are_allowed(self):
        plan = make_plan(
            make_item("13:00", "14:00", 60),
            make_item("14:00", "17:00", 180),
        )

        self.assertEqual(validate_plan_times(plan, **self.window), [])

    def test_gaps_are_allowed(self):
        plan = make_plan(
            make_item("13:00", "14:00", 60),
            make_item("14:30", "15:00", 30),
        )

        self.assertEqual(validate_plan_times(plan, **self.window), [])

    def test_empty_day_is_allowed(self):
        self.assertEqual(validate_plan_times(make_plan(), **self.window), [])

    def test_window_is_optional(self):
        plan = make_plan(make_item("10:00", "10:20", 20))

        self.assertEqual(validate_plan_times(plan), [])

    def test_items_outside_window_are_rejected(self):
        for start, end, duration in [
            ("12:59", "13:20", 21),
            ("16:40", "17:01", 21),
        ]:
            with self.subTest(start=start, end=end):
                plan = make_plan(make_item(start, end, duration))

                errors = validate_plan_times(plan, **self.window)

                self.assertTrue(
                    any("여행 가능 시간" in error for error in errors)
                )

    def test_overlapping_or_out_of_order_items_are_rejected(self):
        cases = [
            (1, [
                make_item("13:00", "14:00", 60),
                make_item("13:59", "14:30", 31),
            ]),
            (1, [
                make_item("14:00", "14:30", 30),
                make_item("13:00", "13:30", 30),
            ]),
            (2, [
                make_item("13:00", "15:00", 120),
                make_item("13:30", "13:40", 10),
                make_item("14:00", "14:30", 30),
            ]),
        ]
        for expected_count, items in cases:
            with self.subTest(items=items):
                errors = validate_plan_times(
                    make_plan(*items), **self.window
                )

                self.assertEqual(len(errors), expected_count)
                self.assertTrue(
                    all("겹치거나" in error for error in errors)
                )

    def test_invalid_item_times_are_rejected(self):
        for start, end in [
            ("9:00", "10:00"),
            ("13:00", "24:00"),
            ("13:60", "14:00"),
            (None, "14:00"),
            ("13:00", None),
        ]:
            with self.subTest(start=start, end=end):
                plan = make_plan(make_item(start, end, 60))

                errors = validate_plan_times(plan)

                self.assertTrue(
                    any("HH:MM" in error for error in errors)
                )

    def test_non_positive_intervals_are_rejected(self):
        for end in ["13:00", "12:59"]:
            with self.subTest(end=end):
                plan = make_plan(make_item("13:00", end, 0))

                errors = validate_plan_times(plan)

                self.assertTrue(
                    any("종료 시각" in error for error in errors)
                )

    def test_missing_or_inconsistent_duration_is_rejected(self):
        cases = [{}] + [
            {"duration_minutes": value}
            for value in [None, 59, 0, -1, True]
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                item = {
                    "start_time": "13:00",
                    "end_time": "14:00",
                    **fields,
                }

                errors = validate_plan_times(make_plan(item))

                self.assertTrue(
                    any("소요시간" in error for error in errors)
                )

    def test_each_day_is_checked_independently(self):
        plan = {
            "days": [
                {
                    "day": 1,
                    "items": [make_item("13:00", "14:00", 60)],
                },
                {
                    "day": 2,
                    "items": [make_item("13:00", "14:00", 60)],
                },
            ]
        }
        self.assertEqual(validate_plan_times(plan, **self.window), [])

        plan["days"][1]["items"] = [
            make_item("12:50", "14:00", 70)
        ]
        errors = validate_plan_times(plan, **self.window)

        self.assertEqual(len(errors), 1)
        self.assertIn("2일차", errors[0])

    def test_invalid_window_arguments_raise_error(self):
        cases = [
            {"start_time": "13:00"},
            {"end_time": "17:00"},
            {"start_time": "17:00", "end_time": "13:00"},
            {"start_time": "13:00", "end_time": "13:00"},
            {"start_time": "bad", "end_time": "17:00"},
        ]
        for window in cases:
            with self.subTest(window=window):
                with self.assertRaises(ValueError):
                    validate_plan_times(make_plan(), **window)


if __name__ == "__main__":
    unittest.main()