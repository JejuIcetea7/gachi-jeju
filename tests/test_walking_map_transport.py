import json
import unittest
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

from langchain_core.tools import StructuredTool

from backend.app.tools.map_transport import create_walking_map_tool


ROUTE_INPUT = {
    "origin_latitude": 33.4,
    "origin_longitude": 126.5,
    "destination_latitude": 33.41,
    "destination_longitude": 126.51,
}


class WalkingMapTransportTests(unittest.TestCase):
    def setUp(self):
        self.fetch = self.enterContext(
            patch("backend.app.tools.map_transport.fetch_walking_route")
        )
        self.enterContext(
            patch(
                "backend.app.tools.map_transport.get_kakao_api_key",
                return_value="test-only-key",
            )
        )
        self.fetch.return_value = {
            "status": "ok",
            "distance_m": 1000,
            "duration_seconds": 600,
            "landing_url": "https://map.kakao.com/",
            "steps": [{
                "distance_m": 1000,
                "duration_seconds": 600,
                "guidance": "목적지까지 이동",
                "path_points": [[126.5, 33.4], [126.51, 33.41]],
            }],
        }

    def test_geometry_is_recorded_without_entering_agent_reply(self):
        records = []
        before = deepcopy(self.fetch.return_value)

        result = create_walking_map_tool(records)(**ROUTE_INPUT)

        self.fetch.assert_called_once_with(
            **ROUTE_INPUT,
            api_key="test-only-key",
            route_mode="BROAD_FIRST",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(result["route_id"], records[0]["route_id"])
        self.assertEqual(records[0]["transport"], "walking")
        self.assertEqual(records[0]["geometry_status"], "ok")
        self.assertEqual(records[0]["paths"], [
            [[126.5, 33.4], [126.51, 33.41]],
        ])
        self.assertEqual(records[0]["origin"], {
            "latitude": 33.4,
            "longitude": 126.5,
        })
        self.assertEqual(records[0]["destination"], {
            "latitude": 33.41,
            "longitude": 126.51,
        })
        self.assertIsNotNone(
            datetime.fromisoformat(records[0]["requested_at"]).tzinfo
        )
        self.assertEqual(
            result["steps"][0]["guidance"],
            "목적지까지 이동",
        )
        self.assertNotIn("path_points", json.dumps(result))
        self.assertNotIn("paths", result)
        self.assertEqual(self.fetch.return_value, before)

    def test_route_mode_is_forwarded(self):
        create_walking_map_tool([])(
            **ROUTE_INPUT,
            route_mode="ACCESSIBLE",
        )

        self.assertEqual(
            self.fetch.call_args.kwargs["route_mode"],
            "ACCESSIBLE",
        )

    def test_failure_does_not_record_geometry(self):
        statuses = (
            "no_route",
            "auth_error",
            "rate_limited",
            "api_error",
        )

        for status in statuses:
            with self.subTest(status=status):
                self.fetch.return_value = {
                    "status": status,
                    "reason": "TEST",
                }
                records = []

                result = create_walking_map_tool(records)(**ROUTE_INPUT)

                self.assertEqual(result, self.fetch.return_value)
                self.assertEqual(records, [])

    def test_missing_geometry_preserves_distance_and_time(self):
        self.fetch.return_value["steps"][0]["path_points"] = []
        records = []

        result = create_walking_map_tool(records)(**ROUTE_INPUT)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["distance_m"], 1000)
        self.assertEqual(result["duration_seconds"], 600)
        self.assertEqual(result["route_id"], records[0]["route_id"])
        self.assertEqual(records[0]["geometry_status"], "missing")
        self.assertEqual(records[0]["paths"], [])

    def test_partial_geometry_preserves_confirmed_paths(self):
        self.fetch.return_value["steps"].append({
            "guidance": "이 구간은 좌표 없음",
            "path_points": [],
        })
        records = []

        result = create_walking_map_tool(records)(**ROUTE_INPUT)

        self.assertEqual(records[0]["geometry_status"], "partial")
        self.assertEqual(len(records[0]["paths"]), 1)
        self.assertEqual(len(result["steps"]), 2)

    def test_requests_have_independent_coordinate_snapshots(self):
        first_records = []
        second_records = []

        create_walking_map_tool(first_records)(**ROUTE_INPUT)
        self.assertEqual(second_records, [])

        create_walking_map_tool(second_records)(**ROUTE_INPUT)
        first_records[0]["paths"][0][0][0] = 0

        self.assertEqual(
            second_records[0]["paths"][0][0][0],
            126.5,
        )
        self.assertEqual(
            self.fetch.return_value["steps"][0]["path_points"][0][0],
            126.5,
        )

    def test_repeated_calls_have_unique_route_ids(self):
        records = []
        tool = create_walking_map_tool(records)

        first = tool(**ROUTE_INPUT)
        second = tool(**ROUTE_INPUT)

        self.assertNotEqual(first["route_id"], second["route_id"])
        self.assertEqual(
            [record["route_id"] for record in records],
            [first["route_id"], second["route_id"]],
        )

    def test_agent_schema_has_only_route_inputs(self):
        tool = StructuredTool.from_function(
            create_walking_map_tool([])
        )

        self.assertEqual(tool.name, "get_walking_route")
        self.assertEqual(
            set(tool.args),
            set(ROUTE_INPUT) | {"route_mode"},
        )
        self.assertEqual(
            tool.args["route_mode"]["default"],
            "BROAD_FIRST",
        )
        self.fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()