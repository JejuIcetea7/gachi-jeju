import copy
import unittest

from backend.app.services.plan_consistency import validate_plan_moves


def move(route_id="route-1", transport="자동차", minutes=10):
    return {
        "item_type": "move",
        "title": "출발지 → 여행지",
        "route_id": route_id,
        "transport": transport,
        "duration_minutes": minutes,
    }


def plan(*items):
    return {"days": [{"day": 1, "items": list(items)}]}


def record(route_id="route-1", transport="driving", seconds=600):
    return {
        "route_id": route_id,
        "transport": transport,
        "duration_seconds": seconds,
    }


class PlanMoveConsistencyTests(unittest.TestCase):
    def test_valid_modes_and_aliases(self):
        cases = [
            ("driving", ["자동차", "rental_car", "taxi", "렌터카 또는 자동차"]),
            ("walking", ["도보", " WALKING "]),
            ("transit", ["대중교통", "bus", "대중교통(버스·도보)"]),
        ]
        for mode, names in cases:
            for name in names:
                with self.subTest(mode=mode, name=name):
                    self.assertEqual(
                        validate_plan_moves(
                            plan(move(transport=name)),
                            [record(transport=mode)],
                        ),
                        [],
                    )

    def test_seconds_round_up_with_one_minute_tolerance(self):
        for minutes in [10, 11, 12]:
            with self.subTest(minutes=minutes):
                self.assertEqual(
                    validate_plan_moves(
                        plan(move(minutes=minutes)),
                        [record(seconds=601)],
                    ),
                    [],
                )

        for minutes in [9, 13]:
            with self.subTest(minutes=minutes):
                errors = validate_plan_moves(
                    plan(move(minutes=minutes)),
                    [record(seconds=601)],
                )
                self.assertEqual(len(errors), 1)

    def test_different_tool_time_is_rejected(self):
        errors = validate_plan_moves(
            plan(move(minutes=18)),
            [record(seconds=1800)],
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("18분", errors[0])
        self.assertIn("30분", errors[0])

    def test_missing_and_unknown_ids_are_rejected(self):
        for route_id in [None, "", "previous-run", "route-typo"]:
            with self.subTest(route_id=route_id):
                errors = validate_plan_moves(
                    plan(move(route_id=route_id)),
                    [record()],
                )
                self.assertEqual(len(errors), 1)

    def test_duplicate_source_ids_are_rejected(self):
        errors = validate_plan_moves(
            plan(move()),
            [record(), record()],
        )
        self.assertEqual(len(errors), 1)

    def test_id_cannot_be_reused_across_days(self):
        value = {
            "days": [
                {"day": 1, "items": [move()]},
                {"day": 2, "items": [move()]},
            ],
        }
        self.assertEqual(
            len(validate_plan_moves(value, [record()])),
            2,
        )

    def test_wrong_or_unknown_transport_is_rejected(self):
        for transport in ["버스", "비행기", None]:
            with self.subTest(transport=transport):
                errors = validate_plan_moves(
                    plan(move(transport=transport)),
                    [record()],
                )
                self.assertEqual(len(errors), 1)

    def test_invalid_record_duration_is_rejected(self):
        values = [
            None, "600", True, -1, 600.0,
            float("nan"), float("inf"), 10**1000,
        ]
        for seconds in values:
            with self.subTest(seconds=seconds):
                errors = validate_plan_moves(
                    plan(move()),
                    [record(seconds=seconds)],
                )
                self.assertEqual(len(errors), 1)

    def test_invalid_planned_duration_is_rejected(self):
        for minutes in [None, True, 0, -1, 10.0]:
            with self.subTest(minutes=minutes):
                errors = validate_plan_moves(
                    plan(move(minutes=minutes)),
                    [record()],
                )
                self.assertEqual(len(errors), 1)

    def test_missing_geometry_does_not_reject_confirmed_time(self):
        value = record()
        value.update({"geometry_status": "missing", "paths": []})
        self.assertEqual(
            validate_plan_moves(plan(move()), [value]),
            [],
        )

    def test_unused_bad_records_do_not_reject_selected_move(self):
        unused = record("unused", seconds=None)
        self.assertEqual(
            validate_plan_moves(
                plan(move()),
                [None, unused, dict(unused), record()],
            ),
            [],
        )

    def test_visits_and_empty_days_need_no_route_records(self):
        for value in [
            {"days": []},
            plan(),
            plan({"item_type": "visit", "title": "여행지"}),
        ]:
            with self.subTest(value=value):
                self.assertEqual(validate_plan_moves(value, []), [])

    def test_inputs_are_preserved(self):
        value = plan(move())
        records = [record()]
        before = copy.deepcopy((value, records))

        validate_plan_moves(value, records)

        self.assertEqual((value, records), before)

    def test_invalid_record_collections_do_not_raise(self):
        for records in [None, {}, "invalid"]:
            with self.subTest(records=records):
                errors = validate_plan_moves(plan(move()), records)
                self.assertEqual(len(errors), 1)

    def test_records_match_by_id_instead_of_order(self):
        value = plan(
            move("first"),
            move("second", transport="도보", minutes=30),
        )
        records = [
            record("second", transport="walking", seconds=1800),
            record("first"),
        ]
        self.assertEqual(validate_plan_moves(value, records), [])


if __name__ == "__main__":
    unittest.main()