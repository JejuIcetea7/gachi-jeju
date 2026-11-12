import copy
import unittest

from backend.app.services.route_endpoints import validate_route_endpoints


ORIGIN = {"latitude": 33.4, "longitude": 126.5}
FIRST = {"latitude": 33.41, "longitude": 126.51}
SECOND = {"latitude": 33.42, "longitude": 126.52}
OTHER = {"latitude": 33.5, "longitude": 126.6}


def visit(point=FIRST, *, verified=True, title="방문 장소"):
    return {
        "item_type": "visit",
        "title": title,
        "verified": verified,
        **point,
    }


def move(route_id):
    return {
        "item_type": "move",
        "title": "출발지 → 도착지",
        "route_id": route_id,
    }


def record(route_id, origin=ORIGIN, destination=FIRST):
    return {
        "route_id": route_id,
        "origin": dict(origin),
        "destination": dict(destination),
    }


def plan(*items):
    return {"days": [{"day": 1, "items": list(items)}]}


class RouteEndpointTests(unittest.TestCase):
    def test_valid_round_trip(self):
        errors = validate_route_endpoints(
            plan(move("out"), visit(), move("back")),
            [record("out"), record("back", FIRST, ORIGIN)],
            departure=ORIGIN,
            return_to_origin=True,
        )
        self.assertEqual(errors, [])

    def test_wrong_departure_is_rejected(self):
        errors = validate_route_endpoints(
            plan(move("out"), visit()),
            [record("out", OTHER)],
            departure=ORIGIN,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("출발 위치", errors[0])

    def test_wrong_destination_is_rejected(self):
        errors = validate_route_endpoints(
            plan(move("out"), visit()),
            [record("out", destination=OTHER)],
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("도착 위치", errors[0])

    def test_reversed_route_is_rejected(self):
        errors = validate_route_endpoints(
            plan(move("out"), visit()),
            [record("out", FIRST, ORIGIN)],
            departure=ORIGIN,
        )
        self.assertEqual(len(errors), 2)

    def test_both_visits_anchor_intermediate_move(self):
        value = plan(visit(), move("between"), visit(SECOND))
        self.assertEqual(validate_route_endpoints(
            value, [record("between", FIRST, SECOND)]
        ), [])
        self.assertEqual(len(validate_route_endpoints(
            value, [record("between", OTHER, SECOND)]
        )), 1)
        self.assertEqual(len(validate_route_endpoints(
            value, [record("between", FIRST, OTHER)]
        )), 1)

    def test_connected_consecutive_moves_are_allowed(self):
        self.assertEqual(validate_route_endpoints(
            plan(visit(), move("one"), move("two"), visit(SECOND)),
            [record("one", FIRST, OTHER), record("two", OTHER, SECOND)],
        ), [])

    def test_disconnected_consecutive_moves_are_rejected(self):
        errors = validate_route_endpoints(
            plan(visit(), move("one"), move("two"), visit(SECOND)),
            [record("one", FIRST, OTHER), record("two", ORIGIN, SECOND)],
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("직전 이동", errors[0])

    def test_small_rounding_difference_is_allowed(self):
        near = {**FIRST, "latitude": FIRST["latitude"] + 0.00045}
        far = {**FIRST, "latitude": FIRST["latitude"] + 0.0018}
        self.assertEqual(validate_route_endpoints(
            plan(move("out"), visit()), [record("out", destination=near)]
        ), [])
        self.assertEqual(len(validate_route_endpoints(
            plan(move("out"), visit()), [record("out", destination=far)]
        )), 1)

    def test_invalid_route_coordinates_are_rejected(self):
        invalid = [
            None, [], {}, {"latitude": True, "longitude": 126.5},
            {"latitude": "33.4", "longitude": 126.5},
            {"latitude": float("nan"), "longitude": 126.5},
            {"latitude": 33.4, "longitude": float("inf")},
            {"latitude": 126.5, "longitude": 33.4},
            {"latitude": 10**1000, "longitude": 126.5},
        ]
        for point in invalid:
            with self.subTest(point=point):
                value = record("out")
                value["destination"] = point
                self.assertEqual(len(validate_route_endpoints(
                    plan(move("out"), visit()), [value]
                )), 1)

    def test_unverified_visit_does_not_supply_coordinates(self):
        for verified in [False, None, 1, "true"]:
            with self.subTest(verified=verified):
                self.assertEqual(validate_route_endpoints(
                    plan(move("out"), visit(verified=verified)),
                    [record("out", destination=OTHER)],
                ), [])

    def test_unknown_visit_does_not_get_skipped_over(self):
        value = plan(
            visit(), move("one"), visit({}, verified=False),
            move("two"), visit(SECOND),
        )
        self.assertEqual(validate_route_endpoints(
            value, [record("one", FIRST, OTHER), record("two", ORIGIN, SECOND)]
        ), [])

    def test_missing_reference_coordinate_is_not_a_mismatch(self):
        self.assertEqual(validate_route_endpoints(
            plan(move("out"), visit({})), [record("out")],
            departure={"latitude": 33.4},
        ), [])

    def test_road_geometry_is_not_used_as_endpoint(self):
        value = record("out")
        value["paths"] = [[[0, 0], [1, 1]]]
        self.assertEqual(validate_route_endpoints(
            plan(move("out"), visit()), [value], departure=ORIGIN
        ), [])
        value.update({"geometry_status": "missing", "paths": []})
        self.assertEqual(validate_route_endpoints(
            plan(move("out"), visit()), [value], departure=ORIGIN
        ), [])

    def test_records_are_selected_by_id(self):
        self.assertEqual(validate_route_endpoints(
            plan(move("out"), visit(), move("back")),
            [
                record("back", FIRST, ORIGIN),
                None,
                record("unused", OTHER, SECOND),
                record("unused", FIRST, OTHER),
                record("out"),
            ],
            departure=ORIGIN,
            return_to_origin=True,
        ), [])

    def test_duplicate_or_missing_selected_record_is_rejected(self):
        for records in [
            None, {}, "invalid", [], [record("other")],
            [record("out"), record("out")],
        ]:
            with self.subTest(records=records):
                self.assertEqual(len(validate_route_endpoints(
                    plan(move("out"), visit()), records
                )), 1)

    def test_second_day_does_not_inherit_first_departure(self):
        value = {"days": [
            {"day": 1, "items": [move("one"), visit()]},
            {"day": 2, "items": [move("two"), visit(SECOND)]},
        ]}
        self.assertEqual(validate_route_endpoints(
            value,
            [record("one"), record("two", OTHER, SECOND)],
            departure=ORIGIN,
        ), [])

    def test_return_to_origin_applies_only_to_last_day(self):
        value = {"days": [
            {"day": 1, "items": [move("out"), visit(), move("hotel")]},
            {"day": 2, "items": [
                move("next"), visit(SECOND), move("back")
            ]},
        ]}
        self.assertEqual(validate_route_endpoints(
            value,
            [
                record("out"), record("hotel", FIRST, OTHER),
                record("next", OTHER, SECOND),
                record("back", SECOND, ORIGIN),
            ],
            departure=ORIGIN,
            return_to_origin=True,
        ), [])

    def test_last_destination_is_only_checked_when_return_is_selected(self):
        value = plan(move("out"), visit(), move("last"))
        records = [record("out"), record("last", FIRST, OTHER)]
        self.assertEqual(validate_route_endpoints(
            value, records, departure=ORIGIN
        ), [])
        self.assertEqual(len(validate_route_endpoints(
            value, records, departure=ORIGIN, return_to_origin=True
        )), 1)

    def test_empty_days_and_visits_need_no_routes(self):
        for value in [{"days": []}, plan(), plan(visit())]:
            with self.subTest(value=value):
                self.assertEqual(validate_route_endpoints(value, []), [])

    def test_inputs_are_preserved(self):
        value = plan(move("out"), visit(), move("back"))
        records = [record("out"), record("back", FIRST, ORIGIN)]
        before = copy.deepcopy((value, records, ORIGIN))
        validate_route_endpoints(
            value, records, departure=ORIGIN, return_to_origin=True
        )
        self.assertEqual((value, records, ORIGIN), before)


if __name__ == "__main__":
    unittest.main()