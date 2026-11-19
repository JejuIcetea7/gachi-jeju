import unittest
from copy import deepcopy

from backend.app.services.plan_routes import select_plan_routes


def make_record(route_id="selected"):
    return {
        "route_id": route_id,
        "transport": "driving",
        "origin": {"latitude": 33.4, "longitude": 126.5},
        "destination": {"latitude": 33.41, "longitude": 126.51},
        "distance_m": 1000,
        "duration_seconds": 600,
        "geometry_status": "ok",
        "paths": [[[126.5, 33.4], [126.51, 33.41]]],
        "requested_at": "2026-11-10T00:00:00+00:00",
    }


def make_plan(*route_ids, transport="자동차"):
    return {
        "assumptions": [],
        "days": [{
            "day": 1,
            "items": [
                {
                    "item_type": "move",
                    "title": f"테스트 이동 {index + 1}",
                    "route_id": route_id,
                    "transport": transport,
                    "duration_minutes": 10,
                }
                for index, route_id in enumerate(route_ids)
            ],
        }],
    }


class PlanRouteTests(unittest.TestCase):
    def test_only_selected_moves_are_returned_in_plan_order(self):
        records = [
            make_record("first"),
            make_record("second"),
            make_record("unused"),
        ]
        records[1]["debug_only"] = "not-for-response"
        plan = make_plan("second", "first")
        plan["days"][0]["items"].insert(0, {
            "item_type": "visit",
            "title": "방문 항목",
            "route_id": "unused",
        })
        before_records = deepcopy(records)
        before_plan = deepcopy(plan)

        routes, warnings = select_plan_routes(plan, records)

        self.assertEqual([route["route_id"] for route in routes], [
            "second", "first",
        ])
        self.assertEqual(warnings, [])
        self.assertNotIn("debug_only", routes[0])

        routes[0]["paths"][0][0][0] = 0
        self.assertEqual(records, before_records)
        self.assertEqual(plan, before_plan)

    def test_bad_refs_and_wrong_transport_do_not_create_paths(self):
        cases = [
            make_plan(None),
            make_plan("previous-execution"),
            make_plan("selected", "selected"),
            make_plan("selected", transport="도보"),
        ]
        for plan in cases:
            with self.subTest(plan=plan):
                routes, warnings = select_plan_routes(
                    plan, [make_record()]
                )

                self.assertEqual(routes, [])
                self.assertEqual(
                    len(warnings), len(plan["days"][0]["items"])
                )

    def test_missing_geometry_preserves_plan(self):
        record = make_record()
        record["geometry_status"] = "missing"
        record["paths"] = []
        plan = make_plan("selected")
        before = deepcopy(plan)

        routes, warnings = select_plan_routes(plan, [record])

        self.assertEqual(routes, [])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(plan, before)

    def test_partial_geometry_keeps_confirmed_paths(self):
        record = make_record()
        record["geometry_status"] = "partial"

        routes, warnings = select_plan_routes(
            make_plan("selected"), [record]
        )

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["geometry_status"], "partial")
        self.assertEqual(routes[0]["paths"], record["paths"])
        self.assertEqual(len(warnings), 1)

    def test_invalid_geometry_is_not_exposed(self):
        bad_paths = [
            [[[126.5, 91], [126.51, 33.41]]],
            [[[True, 33.4], [126.51, 33.41]]],
            [[[126.5, float("nan")], [126.51, 33.41]]],
            [[[126.5, 33.4]]],
        ]
        for paths in bad_paths:
            with self.subTest(paths=paths):
                record = make_record()
                record["paths"] = paths

                routes, warnings = select_plan_routes(
                    make_plan("selected"), [record]
                )

                self.assertEqual(routes, [])
                self.assertEqual(len(warnings), 1)

    def test_duplicate_source_ids_are_not_selected(self):
        records = [make_record(), make_record()]
        records[1]["paths"] = [[[126.7, 33.7], [126.8, 33.8]]]

        routes, warnings = select_plan_routes(
            make_plan("selected"), records
        )

        self.assertEqual(routes, [])
        self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()