import copy
import unittest

from backend.app.agent.planner import DayPlan, ScheduleItem, TripPlan
from backend.app.services.plan_consistency import validate_plan_moves
from backend.app.services.plan_routes import (
    canonicalize_transport_label, normalize_transport_mode, select_plan_routes,
)
from backend.app.services.transit_guidance import select_plan_transit_guidance


LABELS = [
    "버스 102번 (제주버스터미널 승차 → 협재 너븐밭(해수욕장입구) 하차)",
    "버스 102번 (협재 너븐밭(해수욕장입구) 승차 → 제주버스터미널(종점) 하차)",
]


def move(label=LABELS[0], route_id="route"):
    return {
        "item_type": "move", "title": "출발지 → 도착지",
        "transport": label, "route_id": route_id, "duration_minutes": 10,
    }


def plan(label=LABELS[0], route_id="route"):
    return {"days": [{"day": 1, "items": [move(label, route_id)]}]}


def record(**patch):
    return {
        "route_id": "route", "transport": "transit", "duration_seconds": 600,
        "distance_m": 1000, "origin": {"latitude": 33.4, "longitude": 126.5},
        "destination": {"latitude": 33.41, "longitude": 126.51},
        "geometry_status": "ok", "paths": [[[126.5, 33.4], [126.51, 33.41]]],
        "requested_at": "2026-11-13T02:00:00+00:00",
        "transit_segments": [{"type": "BUS", "ride_stop_count": 2}],
        **patch,
    }


class TransportLabelTests(unittest.TestCase):
    def test_reported_outbound_and_return_labels_are_recognized(self):
        for label in LABELS:
            with self.subTest(label=label):
                self.assertEqual(canonicalize_transport_label(label), "버스")
                self.assertEqual(normalize_transport_mode(label), "transit")

    def test_schedule_model_keeps_only_the_general_bus_name(self):
        for label in LABELS + ["버스102", "BUS 102", "버스 784-1번"]:
            with self.subTest(label=label):
                item = ScheduleItem(
                    start_time="09:00", end_time="09:10", item_type="move",
                    title="출발 → 도착", transport=label, duration_minutes=10,
                )
                self.assertEqual(item.transport, "버스")

    def test_existing_general_names_and_null_are_preserved(self):
        for label, expected in [
            ("자동차", "driving"), ("렌터카", "driving"), ("taxi", "driving"),
            ("도보", "walking"), ("대중교통", "transit"), ("bus", "transit"),
            ("대중교통(버스·도보)", "transit"), (None, None),
        ]:
            with self.subTest(label=label):
                self.assertEqual(canonicalize_transport_label(label), label)
                self.assertEqual(normalize_transport_mode(label), expected)

    def test_ambiguous_modes_and_incomplete_annotations_are_not_guessed(self):
        labels = [
            "버스 또는 택시", "버스 102번 또는 자동차", "도보로 버스 정류장까지",
            "버스 102번(승차)", "버스 102번(또는 택시)", "버스 102번(승차 → 하차",
            "버스 102번(승차 → 하차))", "버스 102번(승차 → 하차) 또는 택시",
            "택시(버스 정류장)", "", None, 102,
        ]
        for label in labels:
            with self.subTest(label=label):
                self.assertEqual(canonicalize_transport_label(label), label)
                self.assertIsNone(normalize_transport_mode(label))

    def test_route_id_time_and_transport_mismatches_are_still_rejected(self):
        self.assertEqual(validate_plan_moves(plan(), [record()]), [])
        self.assertTrue(validate_plan_moves(plan(), [record(transport="driving")]))
        self.assertTrue(validate_plan_moves(plan(), [record(duration_seconds=1800)]))
        self.assertTrue(validate_plan_moves(plan(route_id="unknown"), [record()]))

    def test_map_and_bus_guidance_are_selected_by_the_same_route_id(self):
        maps, warnings = select_plan_routes(plan(), [record()])
        self.assertEqual(warnings, [])
        self.assertEqual([item["route_id"] for item in maps], ["route"])
        guidance = select_plan_transit_guidance(plan(), [record()])
        self.assertEqual([item["route_id"] for item in guidance], ["route"])

    def test_json_plan_is_normalized_without_changing_the_input_payload(self):
        payload = {
            "days": [{"day": 1, "items": [{
                **move(), "start_time": "09:00", "end_time": "09:10",
                "accessibility_notes": ["선택 동선 확인"],
            }]}],
        }
        before = copy.deepcopy(payload)
        normalized = TripPlan.model_validate(payload)
        self.assertEqual(normalized.days[0].items[0].transport, "버스")
        self.assertEqual(normalized.days[0].items[0].accessibility_notes, ["선택 동선 확인"])
        self.assertEqual(payload, before)

    def test_other_mode_words_inside_stop_names_do_not_change_the_bus_mode(self):
        label = "버스 102번 (자동차공업사 승차 → 택시승강장 하차)"
        self.assertEqual(canonicalize_transport_label(label), "버스")
        self.assertEqual(normalize_transport_mode(label), "transit")


if __name__ == "__main__":
    unittest.main()
