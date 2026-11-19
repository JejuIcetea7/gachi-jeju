import unittest
from backend.app.routers.chat import RequestAction
from copy import deepcopy

from backend.app.services.plan_validation import read_schedule_constraints, validate_schedule_constraints, validate_plan_times, read_fixed_trip_options


def make_item(start, end, duration):
    return {
        "start_time": start,
        "end_time": end,
        "duration_minutes": duration,
    }


def make_plan(*items):
    return {"days": [{"day": 1, "items": list(items)}]}


class RequestedScheduleTests(unittest.TestCase):
    def setUp(self):
        self.old = make_plan(
            dict(make_item("09:00", "10:00", 60), item_type="visit", place_id="A", title="한라수목원"),
            dict(make_item("10:30", "11:30", 60), item_type="visit", place_id="B", title="해변"),
        )

    def rules(self, *changes, **kwargs):
        return read_schedule_constraints(self.old, list(changes), **kwargs)

    def change(self, kind, visit="A", **values):
        return {"kind": kind, "visit": visit, "evidence": "사용자 요청", **values}

    def test_existing_visits_and_durations_are_preserved(self):
        edited = deepcopy(self.old)
        edited["days"][0]["items"][0]["duration_minutes"] = 45
        self.assertTrue(validate_schedule_constraints(self.old, edited, self.rules()))

    def test_declared_duration_is_checked_against_actual_plan(self):
        rules = self.rules(self.change("duration", minutes=45))
        self.assertTrue(validate_schedule_constraints(self.old, self.old, rules))
        edited = deepcopy(self.old)
        edited["days"][0]["items"][0]["duration_minutes"] = 45
        self.assertEqual(validate_schedule_constraints(self.old, edited, rules), [])

    def test_unknown_visits_cannot_be_added(self):
        edited = deepcopy(self.old)
        edited["days"][0]["items"].append(dict(self.old["days"][0]["items"][0], place_id="C"))
        self.assertTrue(validate_schedule_constraints(self.old, edited, self.rules()))

    def test_declared_addition_is_required(self):
        rules = self.rules(self.change("add", visit="C"))
        self.assertTrue(validate_schedule_constraints(self.old, self.old, rules))

    def test_removed_visit_must_be_absent(self):
        rules = self.rules(self.change("remove"))
        self.assertTrue(validate_schedule_constraints(self.old, self.old, rules))
        edited = deepcopy(self.old)
        edited["days"][0]["items"].pop(0)
        self.assertEqual(validate_schedule_constraints(self.old, edited, rules), [])

    def test_order_is_verified_from_place_ids(self):
        rules = self.rules(orders=[{"day": 1, "place_ids": ["B", "A"], "evidence": "순서 변경"}])
        self.assertTrue(validate_schedule_constraints(self.old, self.old, rules))
        edited = deepcopy(self.old)
        edited["days"][0]["items"].reverse()
        self.assertEqual(validate_schedule_constraints(self.old, edited, rules), [])

    def test_time_window_uses_declared_minutes(self):
        rules = self.rules(self.change("start", visit=None, minutes=600))
        self.assertTrue(validate_schedule_constraints(self.old, self.old, rules))

    def test_rest_is_a_numeric_condition(self):
        rules = self.rules(self.change("rest", minutes=20))
        edited = deepcopy(self.old)
        edited["days"][0]["items"][0]["rest_minutes"] = 20
        self.assertEqual(validate_schedule_constraints(self.old, edited, rules), [])
        self.assertTrue(validate_schedule_constraints(self.old, self.old, rules))

    def test_travel_days_bound_structured_changes(self):
        with self.assertRaises(ValueError):
            self.rules(self.change("duration", minutes=30, day=3), trip_days=2)

    def test_raw_user_text_is_not_parsed_as_conditions(self):
        with self.assertRaises(ValueError):
            read_schedule_constraints(self.old, "수목원 30분으로 바꿔줘.")

    def test_old_fixed_limits_remain(self):
        self.old["schedule_limits"] = [{"day": 1, "visit": "A", "kind": "duration", "minutes": 60}]
        rules = self.rules()
        self.assertEqual(rules.limits, self.old["schedule_limits"])
        changed = self.rules(self.change("duration", minutes=30, day=1))
        self.assertEqual(changed.limits[0]["minutes"], 30)

    def test_clock_and_length_bounds_are_checked(self):
        for values in ({"kind": "start", "minutes": 1440}, {"kind": "duration", "minutes": 0}, {"kind": "rest", "minutes": 0}):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.rules(self.change(**values))

    def test_conflicting_limits_are_rejected(self):
        for changes in (
            [self.change("earliest", visit=None, minutes=900), self.change("latest", visit=None, minutes=840)],
            [self.change("minimum", minutes=90), self.change("maximum", minutes=60)],
            [self.change("start", minutes=600), self.change("end", minutes=630), self.change("duration", minutes=60)],
            [self.change("duration", minutes=30), self.change("duration", minutes=45)],
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.rules(*changes)

    def test_minimum_and_maximum_can_be_kept_together(self):
        rules = self.rules(self.change("minimum", minutes=30), self.change("maximum", minutes=90))
        kinds = sorted(rule["kind"] for rule in rules.limits)
        self.assertEqual(kinds, ["maximum", "minimum"])
        self.assertEqual(validate_schedule_constraints(self.old, self.old, rules), [])

    def test_missing_targets_cannot_be_removed_or_moved(self):
        for change in (
            self.change("remove", visit="C"),
            self.change("remove", visit="A", day=2),
            self.change("remove", visit="@meal"),
            self.change("move", visit="C", destination_day=2),
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    self.rules(change, trip_days=2)

    def test_visit_on_several_days_needs_a_source_day_to_move(self):
        two_days = deepcopy(self.old)
        two_days["days"].append({"day": 2, "items": [deepcopy(self.old["days"][0]["items"][0])]})
        two_days["days"].append({"day": 3, "items": []})
        with self.assertRaises(ValueError):
            read_schedule_constraints(two_days, [self.change("move", destination_day=3)], trip_days=3)
        rules = read_schedule_constraints(two_days, [self.change("move", day=2, destination_day=3)], trip_days=3)
        moved = deepcopy(two_days)
        moved["days"][2]["items"] = moved["days"][1]["items"]
        moved["days"][1]["items"] = []
        self.assertEqual(validate_schedule_constraints(two_days, moved, rules), [])
        self.assertTrue(validate_schedule_constraints(two_days, two_days, rules))

    def test_duplicate_places_in_order_are_rejected(self):
        with self.assertRaises(ValueError):
            self.rules(orders=[{"day": 1, "place_ids": ["A", "A"], "evidence": "순서"}])

    def test_shorter_trip_drops_limits_and_visits_of_removed_days(self):
        two_days = deepcopy(self.old)
        two_days["days"].append({"day": 2, "items": [dict(make_item("09:00", "10:00", 60), item_type="visit", place_id="C", title="오름")]})
        two_days["schedule_limits"] = [{"day": 2, "visit": "C", "kind": "duration", "minutes": 60}]
        rules = read_schedule_constraints(two_days, [], trip_days=1)
        self.assertEqual(rules.limits, [])
        self.assertEqual(validate_schedule_constraints(two_days, self.old, rules), [])
        kept_on_day_one = deepcopy(self.old)
        kept_on_day_one["days"][0]["items"].append(dict(two_days["days"][1]["items"][0], start_time="12:00", end_time="13:00"))
        self.assertEqual(validate_schedule_constraints(two_days, kept_on_day_one, rules), [])

    def test_all_visits_target_does_not_apply_to_meals(self):
        with_meal = deepcopy(self.old)
        with_meal["days"][0]["items"].append(dict(
            make_item("12:00", "12:40", 40), item_type="visit", title="식당", restaurant_address="제주시 1",
        ))
        rules = read_schedule_constraints(with_meal, [self.change("duration", visit="@visits", minutes=60)])
        self.assertEqual(validate_schedule_constraints(with_meal, with_meal, rules), [])

    def test_only_declared_option_changes_are_accepted(self):
        old = {"start_date": "2026-11-20", "transport": "driving", "trip_days": 2}
        proposed = {**old, "start_date": "2026-11-21", "transport": "walking"}
        result = read_fixed_trip_options(old, proposed, ["start_date"])
        self.assertEqual(result["start_date"], "2026-11-21")
        self.assertEqual(result["transport"], "driving")
        self.assertEqual(old["start_date"], "2026-11-20")

    def test_empty_current_values_are_not_filled_without_declaration(self):
        result = read_fixed_trip_options({"transport": None}, {"transport": "walking"}, [])
        self.assertIsNone(result["transport"])


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

    def test_duration_error_has_calculation_context_and_preserves_plan(self):
        plan = {"days": [{"day": 2, "items": [
            make_item("11:27", "11:50", 24),
        ]}]}
        original = deepcopy(plan)
        errors = validate_plan_times(plan)
        self.assertEqual(len(errors), 1)
        self.assertIn("2일차 1번째 일정", errors[0])
        self.assertIn("23분과 일치", errors[0])
        self.assertIn("11:27~11:50", errors[0])
        self.assertIn("duration_minutes=24", errors[0])
        self.assertIn("11:27 + 24분 = 11:51", errors[0])
        self.assertIn("사용자 고정 시각", errors[0])
        self.assertEqual(plan, original)
        plan["days"][0]["items"][0]["end_time"] = "11:51"
        self.assertEqual(validate_plan_times(plan), [])

    def test_duration_calculation_handles_hour_carry_without_wrapping_midnight(self):
        for start, end, duration, expected in (
            ("11:50", "12:13", 24, "12:14"),
            ("23:50", "23:58", 9, "23:59"),
            ("23:50", "23:59", 10, None),
        ):
            with self.subTest(start=start, duration=duration):
                errors = validate_plan_times(make_plan(make_item(start, end, duration)))
                self.assertEqual(len(errors), 1)
                if expected is None:
                    self.assertNotIn("계산 참고:", errors[0])
                else:
                    self.assertIn(f"{start} + {duration}분 = {expected}", errors[0])

    def test_invalid_duration_does_not_produce_a_calculated_end(self):
        for duration in (None, True, 0, -1, "24"):
            with self.subTest(duration=duration):
                errors = validate_plan_times(make_plan(make_item("11:27", "11:50", duration)))
                self.assertEqual(len(errors), 1)
                self.assertNotIn("계산 참고:", errors[0])

    def test_calculation_context_does_not_relax_the_time_window(self):
        plan = make_plan(make_item("16:50", "17:00", 20))
        errors = validate_plan_times(plan, **self.window)
        self.assertEqual(len(errors), 1)
        self.assertIn("16:50 + 20분 = 17:10", errors[0])
        plan["days"][0]["items"][0]["end_time"] = "17:10"
        errors = validate_plan_times(plan, **self.window)
        self.assertEqual(len(errors), 1)
        self.assertIn("여행 가능 시간", errors[0])


if __name__ == "__main__":
    unittest.main()
