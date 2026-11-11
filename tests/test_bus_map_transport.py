import json
import unittest
from datetime import datetime
from unittest.mock import patch

from langchain_core.tools import StructuredTool

from backend.app.tools import bus as bus_tool
from backend.app.tools.map_transport import create_bus_map_tool


ROUTE_INPUT = {
    "origin_latitude": 33.4,
    "origin_longitude": 126.5,
    "destination_latitude": 33.41,
    "destination_longitude": 126.51,
}


def make_route(transfers, duration, distance, middle_longitude):
    return {
        "transfers": transfers,
        "duration_seconds": duration,
        "distance_m": distance,
        "fare_krw": 1150,
        "segments": [{
            "type": "BUS",
            "distance_m": distance,
            "duration_seconds": duration,
            "route_numbers": ["202"],
            "boarding_stop": "출발 정류장",
            "alighting_stop": "도착 정류장",
            "stop_names": ["출발 정류장", "도착 정류장"],
        }],
        "geometry_status": "ok",
        "paths": [[
            [126.5, 33.4],
            [middle_longitude, 33.405],
            [126.51, 33.41],
        ]],
    }


class BusMapTransportTests(unittest.TestCase):
    def setUp(self):
        self.fetch = self.enterContext(
            patch("backend.app.tools.map_transport.get_transit_routes")
        )
        self.enterContext(
            patch(
                "backend.app.tools.map_transport.get_kakao_api_key",
                return_value="test-only-key",
            )
        )
        self.enrich = self.enterContext(
            patch.object(
                bus_tool,
                "_build_bus_trip",
                side_effect=lambda result: result,
            )
        )
        self.routes = [
            make_route(2, 1000, 1000, 126.501),
            make_route(1, 3000, 2000, 126.502),
            make_route(1, 2000, 3000, 126.503),
        ]
        self.best = self.routes[2]
        self.fetch.return_value = {
            "status": "ok",
            "routes": self.routes,
        }

    def test_summary_and_geometry_use_same_selected_route(self):
        records = []

        result = create_bus_map_tool(records)(**ROUTE_INPUT)

        self.fetch.assert_called_once_with(
            **ROUTE_INPUT,
            api_key="test-only-key",
            include_geometry=True,
        )
        self.enrich.assert_called_once()
        selected_routes = self.enrich.call_args.args[0]["routes"]

        self.assertEqual(len(selected_routes), 1)
        self.assertIs(selected_routes[0], self.best)
        self.assertEqual(len(self.routes), 3)
        self.assertEqual(len(records), 1)
        self.assertEqual(result["route_id"], records[0]["route_id"])
        self.assertEqual(
            result["distance_m"],
            self.best["distance_m"],
        )
        self.assertEqual(
            result["duration_seconds"],
            self.best["duration_seconds"],
        )
        self.assertEqual(
            records[0]["distance_m"],
            result["distance_m"],
        )
        self.assertEqual(
            records[0]["duration_seconds"],
            result["duration_seconds"],
        )
        self.assertEqual(records[0]["transport"], "transit")
        self.assertEqual(records[0]["paths"], self.best["paths"])
        self.assertIsNotNone(
            datetime.fromisoformat(records[0]["requested_at"]).tzinfo
        )
        self.assertNotIn("paths", json.dumps(result))
        self.assertNotIn("geometry_status", result)

    def test_partial_realtime_preserves_geometry_and_unknown_low_floor(self):
        def partial_trip(result):
            result["status"] = "partial"
            result["routes"][0]["segments"][0]["bus_info"] = {
                "realtime_status": "position_error",
                "vehicles": [{
                    "route_number": "202",
                    "low_floor": None,
                    "arrival_minutes": 5,
                    "remaining_stops": 2,
                }],
            }
            return result

        self.enrich.side_effect = partial_trip
        records = []

        result = create_bus_map_tool(records)(**ROUTE_INPUT)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(records[0]["paths"], self.best["paths"])
        self.assertIsNone(
            result["segments"][0]["arriving_buses"][0]["low_floor"]
        )
        self.assertEqual(result["route_id"], records[0]["route_id"])

    def test_failure_does_not_request_realtime_or_record_geometry(self):
        statuses = (
            "no_route",
            "auth_error",
            "rate_limited",
            "api_error",
        )

        for status in statuses:
            with self.subTest(status=status):
                self.fetch.return_value = {"status": status}
                records = []

                result = create_bus_map_tool(records)(**ROUTE_INPUT)

                self.assertEqual(result, {"status": status})
                self.assertEqual(records, [])
                self.enrich.assert_not_called()

    def test_missing_geometry_preserves_summary(self):
        self.best.pop("paths")
        self.best.pop("geometry_status")
        records = []

        result = create_bus_map_tool(records)(**ROUTE_INPUT)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["duration_seconds"], 2000)
        self.assertEqual(result["route_id"], records[0]["route_id"])
        self.assertEqual(records[0]["geometry_status"], "missing")
        self.assertEqual(records[0]["paths"], [])

    def test_partial_geometry_preserves_confirmed_paths(self):
        self.best["geometry_status"] = "partial"
        records = []

        result = create_bus_map_tool(records)(**ROUTE_INPUT)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(records[0]["geometry_status"], "partial")
        self.assertEqual(records[0]["paths"], self.best["paths"])

    def test_requests_have_independent_coordinate_snapshots(self):
        first_records = []
        second_records = []

        create_bus_map_tool(first_records)(**ROUTE_INPUT)
        self.assertEqual(second_records, [])

        create_bus_map_tool(second_records)(**ROUTE_INPUT)
        first_records[0]["paths"][0][0][0] = 0

        self.assertEqual(
            second_records[0]["paths"][0][0][0],
            126.5,
        )
        self.assertEqual(
            self.best["paths"][0][0][0],
            126.5,
        )

    def test_repeated_calls_have_unique_route_ids(self):
        records = []
        tool = create_bus_map_tool(records)

        first = tool(**ROUTE_INPUT)
        second = tool(**ROUTE_INPUT)

        self.assertNotEqual(first["route_id"], second["route_id"])
        self.assertEqual(
            [record["route_id"] for record in records],
            [first["route_id"], second["route_id"]],
        )

    def test_agent_schema_exposes_only_coordinates(self):
        tool = StructuredTool.from_function(
            create_bus_map_tool([])
        )

        self.assertEqual(tool.name, "get_bus_route_summary")
        self.assertEqual(set(tool.args), set(ROUTE_INPUT))
        self.fetch.assert_not_called()
        self.enrich.assert_not_called()


if __name__ == "__main__":
    unittest.main()