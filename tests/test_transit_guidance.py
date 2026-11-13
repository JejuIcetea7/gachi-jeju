import copy
import json
import unittest
from pathlib import Path

from backend.app.services.kakao_transit import parse_transit_response
from backend.app.services.transit_guidance import (
    build_transit_segments,
    select_plan_transit_guidance,
)


def bus_segment(**patch):
    return {
        "type": "BUS",
        "distance_m": 1000,
        "duration_seconds": 300,
        "route_numbers": ["202", "202", "784-1"],
        "boarding_stop": "승차 정류장",
        "alighting_stop": "하차 정류장",
        "stop_names": ["승차 정류장", "경유 정류장", "하차 정류장"],
        **patch,
    }


def record(route_id, **patch):
    return {
        "route_id": route_id,
        "transport": "transit",
        "transit_segments": build_transit_segments([bus_segment()]),
        "requested_at": "2026-11-13T02:00:00+00:00",
        **patch,
    }


def move(route_id, transport="버스"):
    return {"item_type": "move", "title": "출발 → 도착", "route_id": route_id, "transport": transport}


def plan(*items):
    return {"days": [{"day": 1, "items": list(items)}]}


class TransitSegmentTests(unittest.TestCase):
    def test_bus_preserves_stops_and_alternative_route_numbers(self):
        segment = build_transit_segments([bus_segment()])[0]
        self.assertEqual(segment["boarding_stop"], "승차 정류장")
        self.assertEqual(segment["alighting_stop"], "하차 정류장")
        self.assertEqual(segment["route_numbers"], ["202", "784-1"])
        self.assertEqual(segment["ride_stop_count"], 2)

    def test_adjacent_stops_are_one_ride_interval(self):
        segment = bus_segment(stop_names=["승차 정류장", "하차 정류장"])
        self.assertEqual(build_transit_segments([segment])[0]["ride_stop_count"], 1)

    def test_repeated_stop_names_are_not_deduplicated(self):
        segment = bus_segment(stop_names=["승차 정류장", "경유", "경유", "하차 정류장"])
        self.assertEqual(build_transit_segments([segment])[0]["ride_stop_count"], 3)

    def test_incomplete_or_inconsistent_stop_list_has_unknown_count(self):
        for names in [None, [], ["승차 정류장"], ["승차 정류장", None, "하차 정류장"],
                      ["다른 승차지", "하차 정류장"], ["승차 정류장", "다른 하차지"]]:
            with self.subTest(names=names):
                segment = build_transit_segments([bus_segment(stop_names=names)])[0]
                self.assertIsNone(segment["ride_stop_count"])
                self.assertEqual(segment["route_numbers"], ["202", "784-1"])

    def test_walk_bus_and_unknown_segments_keep_their_order(self):
        segments = build_transit_segments([
            {"type": "WALKING", "distance_m": 120, "duration_seconds": 90},
            bus_segment(),
            None,
            {"type": "OTHER"},
            {"type": "WALKING", "distance_m": 70},
        ])
        self.assertEqual([item["type"] for item in segments],
                         ["WALKING", "BUS", "UNKNOWN", "UNKNOWN", "WALKING"])
        self.assertEqual(segments[0]["distance_m"], 120)
        self.assertIsNone(segments[0]["ride_stop_count"])

    def test_realtime_remaining_stops_are_not_the_riders_stop_count(self):
        segment = bus_segment(bus_info={
            "vehicles": [{"remaining_stops": 17}], "realtime_status": "ok",
        })
        value = build_transit_segments([segment])[0]
        self.assertEqual(value["ride_stop_count"], 2)
        self.assertNotIn("remaining_stops", value)
        self.assertNotIn("bus_info", value)

    def test_missing_or_bad_fields_are_not_invented(self):
        value = build_transit_segments([bus_segment(
            boarding_stop="", alighting_stop=None, route_numbers=[None, 202, " 202 ", ""],
            distance_m=True, duration_seconds=-1,
        )])[0]
        self.assertIsNone(value["boarding_stop"])
        self.assertIsNone(value["alighting_stop"])
        self.assertIsNone(value["ride_stop_count"])
        self.assertIsNone(value["distance_m"])
        self.assertIsNone(value["duration_seconds"])
        self.assertEqual(value["route_numbers"], ["202"])
        for raw in (None, {}, "BUS"):
            self.assertEqual(build_transit_segments(raw), [])

    def test_source_records_are_preserved_and_not_shared(self):
        source = [bus_segment()]
        before = copy.deepcopy(source)
        result = build_transit_segments(source)
        result[0]["route_numbers"].append("변경")
        self.assertEqual(source, before)

    def test_saved_route_has_one_fifty_five_and_nine_ride_intervals(self):
        sample = Path(__file__).parent / "sample_responses/kakao_transit_geometry_success.json"
        payload = json.loads(sample.read_text(encoding="utf-8-sig"))
        route = parse_transit_response(payload)["routes"][0]
        segments = build_transit_segments(route["segments"])
        self.assertEqual([item["type"] for item in segments], ["BUS", "WALKING", "BUS", "BUS"])
        self.assertEqual([item["ride_stop_count"] for item in segments], [1, None, 55, 9])


class SelectedTransitGuidanceTests(unittest.TestCase):
    def test_selected_ids_are_returned_in_plan_order(self):
        result = select_plan_transit_guidance(plan(move("b"), move("a")),
                                             [record("a"), record("unused"), record("b")])
        self.assertEqual([item["route_id"] for item in result], ["b", "a"])

    def test_missing_geometry_does_not_remove_bus_directions(self):
        result = select_plan_transit_guidance(
            plan(move("a")), [record("a", geometry_status="missing", paths=[])],
        )
        self.assertEqual(result[0]["segments"][0]["ride_stop_count"], 2)

    def test_missing_unknown_duplicate_and_reused_ids_are_not_selected(self):
        for moves, records in [
            ([move(None)], [record("a")]),
            ([move("unknown")], [record("a")]),
            ([move("a")], [record("a"), record("a")]),
            ([move("a"), move("a")], [record("a")]),
        ]:
            with self.subTest(moves=moves):
                self.assertEqual(select_plan_transit_guidance(plan(*moves), records), [])
        repeated_days = {"days": [
            {"day": 1, "items": [move("a")]}, {"day": 2, "items": [move("a")]},
        ]}
        self.assertEqual(select_plan_transit_guidance(repeated_days, [record("a")]), [])

    def test_wrong_transport_and_visit_items_are_not_selected(self):
        self.assertEqual(select_plan_transit_guidance(plan(move("a", "자동차")), [record("a")]), [])
        self.assertEqual(select_plan_transit_guidance(plan(move("a")), [record("a", transport="driving")]), [])
        self.assertEqual(select_plan_transit_guidance(plan({"item_type": "visit", "route_id": "a"}), [record("a")]), [])
        for transport in ("버스", "transit", "대중교통", "bus"):
            self.assertEqual(len(select_plan_transit_guidance(plan(move("a", transport)), [record("a")])), 1)

    def test_bad_metadata_is_omitted_without_rejecting_other_selected_guidance(self):
        for patch in [
            {"transit_segments": []},
            {"transit_segments": {}},
            {"requested_at": "invalid"},
            {"transit_segments": [{"type": "BUS", "ride_stop_count": -1}]},
            {"transit_segments": [{"type": "BUS", "ride_stop_count": True}]},
        ]:
            with self.subTest(patch=patch):
                result = select_plan_transit_guidance(plan(move("a"), move("b")),
                                                     [record("a", **patch), record("b")])
                self.assertEqual([item["route_id"] for item in result], ["b"])

    def test_source_is_preserved_and_unrelated_fields_are_not_exposed(self):
        source = [record("a", origin={"latitude": 33.4}, paths=[[[126.5, 33.4]]], private="ignored")]
        trip = plan(move("a"))
        before = copy.deepcopy((trip, source))
        result = select_plan_transit_guidance(trip, source)
        self.assertEqual(set(result[0]), {"route_id", "segments", "requested_at"})
        result[0]["segments"][0]["route_numbers"].append("변경")
        self.assertEqual((trip, source), before)

    def test_invalid_collections_and_old_records_return_no_guidance(self):
        for payload in [None, {}, {"days": None}]:
            self.assertEqual(select_plan_transit_guidance(payload, None), [])
        for records in [None, {}, [None, {}, {"route_id": []}], [record("a", transit_segments=None)]]:
            self.assertEqual(select_plan_transit_guidance(plan(move("a")), records), [])


if __name__ == "__main__":
    unittest.main()
