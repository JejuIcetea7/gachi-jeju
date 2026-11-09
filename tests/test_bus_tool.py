import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from backend.app.services.jeju_bus import JejuBusAPIError
from backend.app.services.kakao_transit import parse_transit_response
from backend.app.tools import bus as bus_tool

ROUTE_INPUT = {
    "origin_latitude": 33.45211112,
    "origin_longitude": 126.488483,
    "destination_latitude": 33.39022486,
    "destination_longitude": 126.2396912,
}
SAMPLE = Path(__file__).parent / "sample_responses" / "kakao_transit_success.json"

class BusToolTests(unittest.TestCase):
    def setUp(self):
        sample = json.loads(SAMPLE.read_text(encoding="utf-8-sig"))
        self.transit = self.enterContext(patch.object(
            bus_tool,
            "get_transit_routes",
            return_value=parse_transit_response(sample),
        ))
        self.enterContext(patch.object(
            bus_tool, "get_kakao_api_key", return_value="test-key"
        ))
        self.arrivals = self.enterContext(
            patch.object(bus_tool, "get_bus_arrivals")
        )
        self.positions = self.enterContext(
            patch.object(bus_tool, "get_bus_positions")
        )

        arrival = {
            "ROUTE_ID": 405320237,
            "ROUTE_NUM": "202",
            "VH_ID": 1,
            "LOW_PLATE_TP": "Y",
            "PREDICT_TRAV_TM": 5,
        }
        self.arrivals.side_effect = lambda station_id: (
            [arrival] if station_id == "405001248" else []
        )
        self.positions.return_value = [{
            "ROUTE_ID": "405320237",
            "VH_ID": "1",
            "LOCAL_X": 126.26,
            "LOCAL_Y": 33.42,
        }]
    
    def test_success_reuses_requests_across_routes(self):
        routes = self.transit.return_value["routes"]
        routes.append(deepcopy(routes[0]))

        result = bus_tool.get_bus_trip(**ROUTE_INPUT)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["routes"]), 2)
        info = result["routes"][0]["segments"][-1]["bus_info"]
        self.assertEqual(info["realtime_status"], "ok")
        self.assertTrue(info["vehicles"][0]["low_floor"])
        self.assertEqual(info["vehicles"][0]["arrival_minutes"], 5)
        self.assertEqual(
            info["vehicles"][0]["position"],
            {"longitude": 126.26, "latitude": 33.42},
        )
        self.assertEqual(self.arrivals.call_count, 3)
        self.positions.assert_called_once()

    def test_no_arrivals_skips_position_request(self):
        self.arrivals.side_effect = None
        self.arrivals.return_value = []
        
        result = bus_tool.get_bus_trip(**ROUTE_INPUT)

        self.assertEqual(result["status"], "ok")
        info = result["routes"][0]["segments"][-1]["bus_info"]
        self.assertEqual(info["realtime_status"], "no_arrivals")
        self.assertEqual(info["vehicles"], [])
        self.positions.assert_not_called()

    def test_arrival_error_preserves_kakao_route(self):
        self.arrivals.side_effect = JejuBusAPIError("test error")

        result = bus_tool.get_bus_trip(**ROUTE_INPUT)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["routes"][0]["duration_seconds"], 5389)
        info = result["routes"][0]["segments"][-1]["bus_info"]
        self.assertEqual(info["realtime_status"], "arrival_error")
        self.positions.assert_not_called()

    def test_position_error_preserves_arrival_information(self):
        self.positions.side_effect = JejuBusAPIError("test error")

        result = bus_tool.get_bus_trip(**ROUTE_INPUT)

        self.assertEqual(result["status"], "partial")
        info = result["routes"][0]["segments"][-1]["bus_info"]
        self.assertEqual(info["realtime_status"], "position_error")
        self.assertEqual(info["vehicles"][0]["arrival_minutes"], 5)
        self.assertTrue(info["vehicles"][0]["low_floor"])
        self.assertIsNone(info["vehicles"][0]["position"])

    def test_missing_static_data_preserves_kakao_route(self):
        with patch.object(bus_tool, "load_bus_data", return_value=([], {})):
            result = bus_tool.get_bus_trip(**ROUTE_INPUT)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["routes"][0]["duration_seconds"], 5389)
        info = result["routes"][0]["segments"][-1]["bus_info"]
        self.assertEqual(info["status"], "static_data_missing")
        self.arrivals.assert_not_called()
        self.positions.assert_not_called()

    def test_no_route_skips_bus_requests(self):
        expected = {"status": "no_route", "reason": "NO_RESULTS"}
        self.transit.return_value = expected

        result = bus_tool.get_bus_trip(**ROUTE_INPUT)

        self.assertEqual(result, expected)
        self.arrivals.assert_not_called()
        self.positions.assert_not_called()
    def test_summary_keeps_only_needed_fields(self):
        result = bus_tool.get_bus_route_summary(**ROUTE_INPUT)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["duration_seconds"], 5389)
        self.assertEqual(result["transfers"], 2)
        last_segment = result["segments"][-1]
        self.assertEqual(last_segment["type"], "BUS")
        self.assertNotIn("bus_info", last_segment)
        self.assertNotIn("stop_names", last_segment)
        self.assertEqual(last_segment["arriving_buses"], [{
            "route_number": "202",
            "low_floor": True,
            "arrival_minutes": 5,
            "remaining_stops": None,
        }])

    def test_summary_no_route_skips_bus_requests(self):
        expected = {"status": "no_route", "reason": "NO_RESULTS"}
        self.transit.return_value = expected

        result = bus_tool.get_bus_route_summary(**ROUTE_INPUT)

        self.assertEqual(result, expected)
        self.arrivals.assert_not_called()

class SelectBestRouteTests(unittest.TestCase):
    def test_fewest_transfers_then_shortest_duration(self):
        routes = [
            {"name": "A", "transfers": 2, "duration_seconds": 3000},
            {"name": "B", "transfers": 1, "duration_seconds": 5000},
            {"name": "C", "transfers": 1, "duration_seconds": 4000},
        ]

        best_route = bus_tool._select_best_route(routes)

        self.assertEqual(best_route["name"], "C")
