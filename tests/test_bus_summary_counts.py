import copy
import unittest

from backend.app.tools.bus import _summarize_segment


def bus_segment():
    return {
        "type": "BUS",
        "distance_m": 1200,
        "duration_seconds": 600,
        "route_numbers": ["102"],
        "boarding_stop": "출발",
        "alighting_stop": "도착",
        "stop_names": ["출발", *[f"경유 {i}" for i in range(1, 8)], "도착"],
    }


class BusSummaryCountTests(unittest.TestCase):
    def test_nine_stop_names_mean_eight_ride_intervals(self):
        result = _summarize_segment(bus_segment())
        self.assertEqual(result["ride_stop_count"], 8)
        self.assertNotIn("stop_count", result)

    def test_adjacent_stops_mean_one_ride_interval(self):
        segment = bus_segment()
        segment["stop_names"] = ["출발", "도착"]
        self.assertEqual(_summarize_segment(segment)["ride_stop_count"], 1)

    def test_missing_or_inconsistent_stop_names_stay_unknown(self):
        for names in (
            None, [], ["출발"], ["다른 출발", "도착"],
            ["출발", "다른 도착"], ["출발", None, "도착"],
            ["출발", " ", "도착"], "출발,도착",
        ):
            with self.subTest(names=names):
                segment = bus_segment()
                segment["stop_names"] = names
                result = _summarize_segment(segment)
                self.assertIn("ride_stop_count", result)
                self.assertIsNone(result["ride_stop_count"])
                self.assertEqual(result["route_numbers"], ["102"])
                self.assertEqual(result["boarding_stop"], "출발")
                self.assertEqual(result["alighting_stop"], "도착")

        segment = bus_segment()
        segment["boarding_stop"] = None
        self.assertIsNone(_summarize_segment(segment)["ride_stop_count"])

    def test_repeated_stop_names_keep_each_ride_interval(self):
        segment = bus_segment()
        segment["stop_names"] = ["출발", "경유", "출발", "도착"]
        self.assertEqual(_summarize_segment(segment)["ride_stop_count"], 3)

    def test_realtime_count_and_unknown_low_floor_are_preserved(self):
        segment = bus_segment()
        segment["bus_info"] = {
            "realtime_status": "ok",
            "vehicles": [{
                "route_number": "102",
                "low_floor": None,
                "arrival_minutes": 5,
                "remaining_stops": 99,
            }],
        }
        original = copy.deepcopy(segment)
        result = _summarize_segment(segment)
        self.assertEqual(result["ride_stop_count"], 8)
        self.assertEqual(result["arriving_buses"][0]["remaining_stops"], 99)
        self.assertIsNone(result["arriving_buses"][0]["low_floor"])
        self.assertEqual(segment, original)

    def test_walking_segment_has_no_bus_count(self):
        segment = {"type": "WALKING", "distance_m": 100, "duration_seconds": 90}
        self.assertEqual(_summarize_segment(segment), segment)


if __name__ == "__main__":
    unittest.main()