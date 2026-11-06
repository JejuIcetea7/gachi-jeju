import unittest

from backend.app.services.bus_matching import match_bus_segment

class BusMatchingTests(unittest.TestCase):
    def setUp(self):
        self.routes = [{"ROUTE_ID": 1, "ROUTE_NUM": "202"}]
        self.segment = {
            "route_numbers": ["202"],
            "boarding_stop": "출발",
            "alighting_stop": "도착",
            "stop_names": ["출발", "도착"],
        }
        self.stops = {
            "1": [
                {"STATION_ORD": 1, "STATION_ID": 10, "STATION_NM": "출발"},
                {"STATION_ORD": 2, "STATION_ID": 20, "STATION_NM": "도착"},
                {"STATION_ORD": 3, "STATION_ID": 30, "STATION_NM": "경유"},
                {"STATION_ORD": 4, "STATION_ID": 40, "STATION_NM": "도착"},
            ]
        }
    
    def test_loop_route_excludes_long_detour(self):
        matches = match_bus_segment(self.segment, self.routes, self.stops)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["boarding_station_id"], "10")
        self.assertEqual(matches[0]["alighting_station_id"], "20")
        self.assertEqual(matches[0]["alighting_order"], 2)

    def test_reverse_direction_does_not_match(self):
        self.segment.update({
            "boarding_stop": "도착",
            "alighting_stop": "출발",
            "stop_names": ["도착", "출발"],
        })

        self.assertEqual(
            match_bus_segment(self.segment, self.routes, self.stops), []
        )
    def test_different_intermediate_stop_does_not_match(self):
        self.segment["stop_names"] = ["출발", "다른정류장", "도착"]

        self.assertEqual(
            match_bus_segment(self.segment, self.routes, self.stops), []
        )
    
    def test_missing_route_data_returns_no_matches(self):
        self.assertEqual(
            match_bus_segment(self.segment, self.routes, {}), []
        )

    def test_whitespace_difference_is_ignored(self):
        self.segment["stop_names"] = ["출 발", "도 착"]

        matches = match_bus_segment(self.segment, self.routes, self.stops)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["alighting_station_id"], "20")

    def test_terminal_alias_is_scoped(self):
        cases = [
            ("102", 405002811, "제주버스터미널(가상정류소)", 1),
            ("102", 405002812, "제주버스터미널(가상정류소)", 0),
            ("202", 405002811, "제주버스터미널(가상정류소)", 0),
            ("102", 405002811, "제주버스터미널(종점)", 0),
        ]

        for number, station_id, station_name, expected_count in cases:
            with self.subTest(
                number=number, station_id=station_id, station_name=station_name
            ):
                routes = [{"ROUTE_ID": 1, "ROUTE_NUM": number}]
                segment = {
                    "route_numbers": [number],
                    "stop_names": ["제주버스터미널", "도착"],
                }
                stops = {
                    "1": [
                        {
                            "STATION_ORD": 1,
                            "STATION_ID": station_id,
                            "STATION_NM": station_name,
                        },
                        {
                            "STATION_ORD": 2,
                            "STATION_ID": 20,
                            "STATION_NM": "도착",
                        },
                    ]
                }

                matches = match_bus_segment(segment, routes, stops)

                self.assertEqual(len(matches), expected_count)