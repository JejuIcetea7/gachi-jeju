import copy
import itertools
import math
import random
import sqlite3
import unittest
from unittest.mock import patch

from langchain_core.tools import StructuredTool

from backend.app.agent import chat_planner
from backend.app.services.visit_order import calculate_visit_order
from backend.app.tools import visit_order


def point(longitude, latitude=33.4):
    return {"latitude": latitude, "longitude": longitude}


def places(*longitudes):
    return [{"place_id": f"P{index}", **point(value)} for index, value in enumerate(longitudes)]


def distance(first, second):
    latitude_1, longitude_1 = map(math.radians, (first["latitude"], first["longitude"]))
    latitude_2, longitude_2 = map(math.radians, (second["latitude"], second["longitude"]))
    cosine = (
        math.sin(latitude_1) * math.sin(latitude_2)
        + math.cos(latitude_1) * math.cos(latitude_2) * math.cos(longitude_1 - longitude_2)
    )
    return 6_371_000 * math.acos(min(1.0, max(-1.0, cosine)))


def total_distance(order, original, origin, destination):
    by_id = {place["place_id"]: place for place in original}
    route = [by_id[place_id] for place_id in order]
    if origin is not None:
        route.insert(0, origin)
    if destination is not None:
        route.append(destination)
    return sum(distance(first, second) for first, second in zip(route, route[1:]))


class VisitOrderTests(unittest.TestCase):
    def test_backtracking_is_reduced_with_departure_and_destination(self):
        original = places(126.7, 126.3, 126.5)
        result = calculate_visit_order(original, origin=point(126.1), destination=point(126.9))
        self.assertEqual(result["suggested_place_ids"], ["P1", "P2", "P0"])
        self.assertTrue(result["changed"])
        self.assertGreater(result["distance_reduction_m"], 30_000)
        self.assertEqual(result["basis"], "straight_line_distance")

    def test_lodging_destination_changes_the_preferred_direction(self):
        original = places(126.5, 126.3, 126.7)
        east = calculate_visit_order(original, destination=point(126.9))
        west = calculate_visit_order(original, destination=point(126.1))
        self.assertEqual(east["suggested_place_ids"], ["P1", "P0", "P2"])
        self.assertEqual(west["suggested_place_ids"], ["P2", "P0", "P1"])

    def test_return_to_origin_is_included_in_the_comparison(self):
        original = places(126.7, 126.3, 126.5)
        original[2]["latitude"] = 33.55
        origin = point(126.1)
        result = calculate_visit_order(original, origin=origin, destination=origin)
        measured = total_distance(result["suggested_place_ids"], original, origin, origin)
        self.assertAlmostEqual(result["suggested_straight_line_distance_m"], measured, delta=0.2)

    def test_undecided_boundaries_are_not_replaced_with_a_fake_origin(self):
        original = places(126.3, 126.5)
        result = calculate_visit_order(original)
        self.assertFalse(result["changed"])
        expected = distance(original[0], original[1])
        self.assertAlmostEqual(result["original_straight_line_distance_m"], expected, delta=0.2)

    def test_fixed_visits_keep_their_positions_and_do_not_exchange_neighbors(self):
        original = places(126.5, 126.3, 126.6, 126.9, 126.7)
        result = calculate_visit_order(
            original, origin=point(126.1), destination=point(126.95),
            fixed_place_ids=["P2"],
        )
        order = result["suggested_place_ids"]
        self.assertEqual(order[2], "P2")
        self.assertEqual(set(order[:2]), {"P0", "P1"})
        self.assertEqual(set(order[3:]), {"P3", "P4"})
        self.assertLessEqual(result["suggested_straight_line_distance_m"],
                             result["original_straight_line_distance_m"])

    def test_an_explicitly_fixed_order_is_preserved(self):
        original = places(126.7, 126.3, 126.5)
        ids = [place["place_id"] for place in original]
        result = calculate_visit_order(
            original, origin=point(126.1), destination=point(126.9), fixed_place_ids=ids,
        )
        self.assertEqual(result["suggested_place_ids"], ids)
        self.assertFalse(result["changed"])

    def test_equal_distance_does_not_rearrange_the_original_order(self):
        result = calculate_visit_order(places(126.5, 126.5, 126.5), origin=point(126.1))
        self.assertEqual(result["suggested_place_ids"], ["P0", "P1", "P2"])
        self.assertFalse(result["changed"])

    def test_one_visit_includes_both_known_boundaries(self):
        original = places(126.5)
        result = calculate_visit_order(original, origin=point(126.1), destination=point(126.9))
        self.assertFalse(result["changed"])
        self.assertGreater(result["original_straight_line_distance_m"], 70_000)

    def test_invalid_inputs_are_rejected_without_guessing(self):
        for original, options in (
            ([], {}), (places(*([126.5] * 13)), {}), ([{"place_id": "P0"}], {}),
            ([{"place_id": "", **point(126.5)}], {}),
            ([{"place_id": "P0", **point(126.5)}] * 2, {}),
            (places(126.5), {"origin": {"latitude": 33.4}}),
            (places(126.5), {"fixed_place_ids": ["missing"]}),
            (places(126.5), {"fixed_place_ids": ["P0", "P0"]}),
            (places(126.5), {"fixed_place_ids": "P0"}),
            (places(126.5), {"origin": point(181)}),
        ):
            with self.subTest(original=original, options=options):
                with self.assertRaises(ValueError):
                    calculate_visit_order(original, **options)
        for value in (math.nan, math.inf, True, "126.5", 10 ** 1000, -(10 ** 1000)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                calculate_visit_order([{"place_id": "P0", **point(value)}])

    def test_input_and_returned_lists_are_independent(self):
        original, origin, fixed = places(126.7, 126.3, 126.5), point(126.1), ["P2"]
        before = copy.deepcopy((original, origin, fixed))
        result = calculate_visit_order(original, origin=origin, fixed_place_ids=fixed)
        result["original_place_ids"].clear()
        result["fixed_place_ids"].clear()
        self.assertEqual((original, origin, fixed), before)

    def test_small_cases_match_independent_exhaustive_comparison(self):
        generator = random.Random(42)
        for count in (3, 4, 5, 6):
            original = []
            for index in range(count):
                original.append({
                    "place_id": f"P{index}",
                    **point(generator.uniform(126.2, 126.8), generator.uniform(33.2, 33.55)),
                })
            ids = [place["place_id"] for place in original]
            fixed_index = count // 2
            for origin, destination in ((point(126.1), point(126.9)), (None, point(126.9)), (None, None)):
                with self.subTest(count=count, origin=origin, destination=destination):
                    result = calculate_visit_order(
                        original, origin=origin, destination=destination,
                        fixed_place_ids=[ids[fixed_index]],
                    )
                    best = math.inf
                    for candidate in itertools.permutations(ids):
                        if candidate[fixed_index] != ids[fixed_index]:
                            continue
                        if set(candidate[:fixed_index]) != set(ids[:fixed_index]):
                            continue
                        best = min(best, total_distance(candidate, original, origin, destination))
                    measured = total_distance(result["suggested_place_ids"], original, origin, destination)
                    self.assertLessEqual(measured - best, 2.1)

    def test_maximum_size_is_bounded_and_does_not_drop_visits(self):
        original = places(*[126.2 + index * 0.03 for index in range(12)][::-1])
        result = calculate_visit_order(original, origin=point(126.1), destination=point(126.9))
        self.assertCountEqual(result["suggested_place_ids"], result["original_place_ids"])
        self.assertLess(result["suggested_straight_line_distance_m"],
                        result["original_straight_line_distance_m"])


class VisitOrderToolTests(unittest.TestCase):
    def test_coordinates_come_from_the_database_and_ids_use_query_parameters(self):
        original = places(126.7, 126.3, 126.5)
        original[0]["place_id"] = "P0'quoted"
        ids = [place["place_id"] for place in original]
        with patch.object(visit_order, "read_rows", return_value=list(reversed(original))) as read:
            result = visit_order.suggest_visit_order(
                place_ids=ids, origin_latitude=33.4, origin_longitude=126.1,
                destination_latitude=33.4, destination_longitude=126.9,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["suggested_place_ids"], ["P1", "P2", "P0'quoted"])
        self.assertEqual(read.call_args.args[1], tuple(ids))
        self.assertNotIn("P0'quoted", read.call_args.args[0])
        self.assertNotIn("route_id", result)
        self.assertNotIn("duration_seconds", result)

    def test_invalid_requests_do_not_query_the_database(self):
        for arguments in (
            {"place_ids": []}, {"place_ids": ["P0", "P0"]},
            {"place_ids": ["P0"], "origin_latitude": 33.4},
            {"place_ids": ["P0"], "fixed_place_ids": ["missing"]},
        ):
            with self.subTest(arguments=arguments), patch.object(visit_order, "read_rows") as read:
                self.assertEqual(visit_order.suggest_visit_order(**arguments)["status"], "invalid_input")
                read.assert_not_called()

    def test_database_failure_and_missing_places_are_not_partial_successes(self):
        with patch.object(visit_order, "read_rows", side_effect=sqlite3.OperationalError("test-only")):
            self.assertEqual(visit_order.suggest_visit_order(place_ids=["P0"])["status"], "db_error")
        with patch.object(visit_order, "read_rows", return_value=places(126.5)):
            result = visit_order.suggest_visit_order(place_ids=["P0", "P1"])
        self.assertEqual(result, {"status": "not_found", "missing_place_ids": ["P1"]})

    def test_invalid_database_coordinates_or_duplicate_rows_are_rejected(self):
        for rows in ([{"place_id": "P0", **point(math.nan)}], places(126.5) * 2):
            with self.subTest(rows=rows), patch.object(visit_order, "read_rows", return_value=rows):
                self.assertEqual(visit_order.suggest_visit_order(place_ids=["P0"])["status"], "invalid_data")

    def test_real_tool_schema_and_invocation_support_optional_boundaries(self):
        tool = StructuredTool.from_function(visit_order.suggest_visit_order)
        with patch.object(visit_order, "read_rows", return_value=places(126.7, 126.3)):
            result = tool.invoke({"place_ids": ["P0", "P1"], "destination_latitude": 33.4,
                                  "destination_longitude": 126.9})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["suggested_place_ids"], ["P1", "P0"])

    def test_chat_agent_exposes_the_local_order_tool(self):
        with (
            patch.object(chat_planner, "get_openai_api_key", return_value="test-only-key"),
            patch.object(chat_planner, "ChatOpenAI"),
            patch.object(chat_planner, "create_agent") as factory,
        ):
            chat_planner.build_chat_agent(None)
        self.assertIn(visit_order.suggest_visit_order, factory.call_args.kwargs["tools"])


if __name__ == "__main__":
    unittest.main()
