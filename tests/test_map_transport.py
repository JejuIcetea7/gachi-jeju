import unittest
from datetime import datetime
from unittest.mock import patch

from langchain_core.tools import StructuredTool

from backend.app.tools.map_transport import create_driving_map_tool


ROUTE_INPUT = {
    "origin_latitude": 33.4,
    "origin_longitude": 126.5,
    "destination_latitude": 33.41,
    "destination_longitude": 126.51,
}


class MapTransportTests(unittest.TestCase):
    def setUp(self):
        self.fetch = self.enterContext(
            patch("backend.app.tools.map_transport.get_driving_route")
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
            "duration_seconds": 100,
            "geometry_status": "ok",
            "paths": [[[126.5, 33.4], [126.51, 33.41]]],
        }

    def test_geometry_is_recorded_without_entering_agent_reply(self):
        records = []
        tool = create_driving_map_tool(records)

        result = tool(**ROUTE_INPUT)

        self.assertEqual(len(records), 1)
        self.assertEqual(result, {
            "status": "ok",
            "distance_m": 1000,
            "duration_seconds": 100,
            "route_id": records[0]["route_id"],
        })
        self.fetch.assert_called_once_with(
            **ROUTE_INPUT,
            api_key="test-only-key",
            include_geometry=True,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["transport"], "driving")
        self.assertEqual(records[0]["origin"], {
            "latitude": 33.4, "longitude": 126.5,
        })
        self.assertEqual(records[0]["destination"], {
            "latitude": 33.41, "longitude": 126.51,
        })
        self.assertEqual(records[0]["paths"], [
            [[126.5, 33.4], [126.51, 33.41]],
        ])
        self.assertIsNotNone(
            datetime.fromisoformat(records[0]["requested_at"]).tzinfo
        )

    def test_failed_lookup_is_forwarded_without_recording_geometry(self):
        for status in ("no_route", "auth_error", "rate_limited", "api_error"):
            with self.subTest(status=status):
                self.fetch.return_value = {"status": status}
                records = []
                tool = create_driving_map_tool(records)

                self.assertEqual(tool(**ROUTE_INPUT), {"status": status})
                self.assertEqual(records, [])

    def test_each_request_has_its_own_geometry_snapshot(self):
        first_records = []
        second_records = []
        first_tool = create_driving_map_tool(first_records)
        second_tool = create_driving_map_tool(second_records)

        first_tool(**ROUTE_INPUT)
        self.assertEqual(second_records, [])

        second_tool(**ROUTE_INPUT)
        first_records[0]["paths"][0][0][0] = 0

        self.assertEqual(len(first_records), 1)
        self.assertEqual(len(second_records), 1)
        self.assertEqual(second_records[0]["paths"][0][0][0], 126.5)
        self.assertEqual(self.fetch.return_value["paths"][0][0][0], 126.5)

    def test_agent_schema_exposes_only_coordinate_inputs(self):
        tool = StructuredTool.from_function(create_driving_map_tool([]))

        self.assertEqual(tool.name, "get_driving_time")
        self.assertEqual(set(tool.args), set(ROUTE_INPUT))
        self.fetch.assert_not_called()

    def test_route_ids_are_unique_and_match_saved_records(self):
        records = []
        tool = create_driving_map_tool(records)

        first = tool(**ROUTE_INPUT)
        second = tool(**ROUTE_INPUT)

        self.assertNotEqual(first["route_id"], second["route_id"])
        self.assertEqual(
            [record["route_id"] for record in records],
            [first["route_id"], second["route_id"]],
        )

if __name__ == "__main__":
    unittest.main()