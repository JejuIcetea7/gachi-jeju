import json
import unittest
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from backend.app.services.kakao_mobility import (
    get_driving_route,
    parse_driving_geometry,
)

SAMPLE = (
    Path(__file__).parent
    / "sample_responses"
    / "kakao_driving_geometry_success.json"
)

ROUTE_INPUT = {
    "origin_latitude": 33.45211112,
    "origin_longitude": 126.488483,
    "destination_latitude": 33.39022486,
    "destination_longitude": 126.2396912,
    "api_key": "test-only-key",
}


def make_response(roads):
    return {
        "routes": [{
            "result_code": 0,
            "summary": {"distance": 1000, "duration": 100},
            "sections": [{"roads": roads}],
        }],
    }


class DrivingGeometryTests(unittest.TestCase):
    def test_saved_response_has_all_points(self):
        data = json.loads(SAMPLE.read_text(encoding="utf-8-sig"))
        before = deepcopy(data)
        result = parse_driving_geometry(data)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["geometry_status"], "ok")
        self.assertEqual(len(result["paths"]), 11)
        self.assertEqual(sum(map(len, result["paths"])), 407)
        self.assertEqual(
            result["paths"][0][0],
            data["routes"][0]["sections"][0]["roads"][0]["vertexes"][:2],
        )
        self.assertGreater(result["paths"][0][0][0], 120)
        self.assertLess(result["paths"][0][0][1], 40)
        self.assertEqual(data, before)

    @patch("backend.app.services.kakao_mobility.urlopen")
    def test_geometry_request_asks_for_details(self, mock_urlopen):
        mock_urlopen.return_value = BytesIO(SAMPLE.read_bytes())

        result = get_driving_route(
            **ROUTE_INPUT,
            include_geometry=True,
        )

        request = mock_urlopen.call_args.args[0]
        params = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(params["summary"], ["false"])
        self.assertEqual(result["geometry_status"], "ok")
        self.assertEqual(len(result["paths"]), 11)
        mock_urlopen.assert_called_once()

    @patch("backend.app.services.kakao_mobility.urlopen")
    def test_default_request_keeps_summary_contract(self, mock_urlopen):
        raw = SAMPLE.read_bytes()
        summary = json.loads(raw)["routes"][0]["summary"]
        mock_urlopen.return_value = BytesIO(raw)

        result = get_driving_route(**ROUTE_INPUT)

        request = mock_urlopen.call_args.args[0]
        params = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(params["summary"], ["true"])
        self.assertEqual(result, {
            "status": "ok",
            "distance_m": summary["distance"],
            "duration_seconds": summary["duration"],
        })

    def test_summary_only_response_preserves_distance_and_time(self):
        data = {
            "routes": [{
                "result_code": 0,
                "summary": {"distance": 1000, "duration": 100},
            }],
        }

        self.assertEqual(parse_driving_geometry(data), {
            "status": "ok",
            "distance_m": 1000,
            "duration_seconds": 100,
            "geometry_status": "missing",
            "paths": [],
        })

    def test_invalid_road_is_omitted_without_bridging_points(self):
        invalid_vertices = [
            None,
            [126.5, 33.4],
            [126.5, 33.4, 126.6],
            [True, 33.4, 126.6, 33.5],
            ["126.5", 33.4, 126.6, 33.5],
            [126.5, float("nan"), 126.6, 33.5],
            [float("inf"), 33.4, 126.6, 33.5],
            [181, 33.4, 126.6, 33.5],
            [126.5, 91, 126.6, 33.5],
            [126.5, 33.4, None, 33.45, 126.6, 33.5],
        ]

        for vertices in invalid_vertices:
            with self.subTest(vertices=vertices):
                data = make_response([
                    {"vertexes": [126.5, 33.4, 126.6, 33.5]},
                    {"vertexes": vertices},
                ])
                result = parse_driving_geometry(data)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["geometry_status"], "partial")
                self.assertEqual(result["distance_m"], 1000)
                self.assertEqual(result["duration_seconds"], 100)
                self.assertEqual(result["paths"], [
                    [[126.5, 33.4], [126.6, 33.5]],
                ])

    def test_all_invalid_roads_have_no_geometry(self):
        result = parse_driving_geometry(make_response([
            None,
            {"vertexes": [126.5, 33.4]},
        ]))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["geometry_status"], "missing")
        self.assertEqual(result["paths"], [])

    def test_separate_roads_remain_separate_paths(self):
        result = parse_driving_geometry(make_response([
            {"vertexes": [126.1, 33.1, 126.2, 33.2]},
            {"vertexes": [126.7, 33.7, 126.8, 33.8]},
        ]))

        self.assertEqual(result["geometry_status"], "ok")
        self.assertEqual(result["paths"], [
            [[126.1, 33.1], [126.2, 33.2]],
            [[126.7, 33.7], [126.8, 33.8]],
        ])

    def test_failed_route_does_not_gain_geometry_fields(self):
        cases = [
            (None, {"status": "api_error"}),
            ({"routes": []}, {"status": "no_route"}),
            ({"routes": [{"result_code": 1}]}, {"status": "no_route"}),
            ({"routes": [{"result_code": 999}]}, {"status": "api_error"}),
        ]

        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(parse_driving_geometry(data), expected)


if __name__ == "__main__":
    unittest.main()