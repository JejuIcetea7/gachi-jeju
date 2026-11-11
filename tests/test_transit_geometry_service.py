import json
import unittest
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from backend.app.services import kakao_transit


ROUTE_INPUT = {
    "origin_latitude": 33.45211112,
    "origin_longitude": 126.488483,
    "destination_latitude": 33.39022486,
    "destination_longitude": 126.2396912,
    "api_key": "test-only-key",
}
SAMPLE = (
    Path(__file__).parent
    / "sample_responses"
    / "kakao_transit_geometry_success.json"
)


class TransitGeometryServiceTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SAMPLE.read_text(encoding="utf-8-sig"))
        self.http = self.enterContext(
            patch.object(kakao_transit, "urlopen")
        )

    def test_default_format_is_preserved(self):
        before = deepcopy(self.data)
        normal = kakao_transit.parse_transit_response(self.data)
        detailed = kakao_transit.parse_transit_response(
            self.data,
            include_geometry=True,
        )

        self.assertNotIn("paths", normal["routes"][0])
        self.assertNotIn("geometry_status", normal["routes"][0])

        for route in detailed["routes"]:
            route.pop("paths")
            route.pop("geometry_status")

        self.assertEqual(detailed, normal)
        self.assertEqual(self.data, before)

    def test_http_option_returns_geometry_with_one_request(self):
        self.http.return_value.__enter__.return_value = BytesIO(
            json.dumps(self.data).encode("utf-8")
        )

        result = kakao_transit.get_transit_routes(
            **ROUTE_INPUT,
            include_geometry=True,
        )

        self.http.assert_called_once()
        self.assertEqual(
            result["routes"][0]["geometry_status"],
            "ok",
        )
        self.assertEqual(
            result["routes"][0]["paths"],
            [
                step["path"]["points"]
                for step in self.data["routes"][0]["steps"]
            ],
        )

        request = self.http.call_args.args[0]
        self.assertEqual(
            set(parse_qs(urlsplit(request.full_url).query)),
            {"start_x", "start_y", "end_x", "end_y"},
        )

    def test_missing_and_partial_geometry_preserve_route_information(self):
        cases = (
            (False, "partial"),
            (True, "missing"),
        )

        for remove_all, expected_status in cases:
            with self.subTest(geometry_status=expected_status):
                data = deepcopy(self.data)
                steps = data["routes"][0]["steps"]
                selected_steps = steps if remove_all else steps[:1]

                for step in selected_steps:
                    step.pop("path", None)

                normal = kakao_transit.parse_transit_response(data)
                detailed = kakao_transit.parse_transit_response(
                    data,
                    include_geometry=True,
                )
                route = detailed["routes"][0]

                self.assertEqual(
                    route.pop("geometry_status"),
                    expected_status,
                )
                paths = route.pop("paths")
                self.assertEqual(
                    len(paths),
                    0 if remove_all else 3,
                )
                self.assertEqual(detailed, normal)

    def test_each_bus_route_keeps_its_own_geometry(self):
        first = deepcopy(self.data["routes"][0])
        second = deepcopy(first)
        excluded = deepcopy(first)

        first["properties"]["totalDistance"] = 100
        second["properties"]["totalDistance"] = 200
        excluded["properties"]["type"] = "SUBWAY"
        second["steps"][0]["path"]["points"] = [
            [126.6, 33.5],
            [126.61, 33.51],
        ]
        data = {
            "status": "OK",
            "routes": [first, excluded, second],
        }

        result = kakao_transit.parse_transit_response(
            data,
            include_geometry=True,
        )

        self.assertEqual(
            [route["distance_m"] for route in result["routes"]],
            [100, 200],
        )

        for parsed, original in zip(
            result["routes"],
            [first, second],
        ):
            self.assertEqual(
                parsed["paths"],
                [step["path"]["points"] for step in original["steps"]],
            )

    def test_failures_do_not_gain_geometry_fields(self):
        self.http.return_value.__enter__.return_value = BytesIO(
            b'{"status": "NO_RESULTS"}'
        )

        self.assertEqual(
            kakao_transit.get_transit_routes(
                **ROUTE_INPUT,
                include_geometry=True,
            ),
            {"status": "no_route", "reason": "NO_RESULTS"},
        )

        cases = [
            (401, "auth_error"),
            (429, "rate_limited"),
            (500, "api_error"),
        ]

        for status, expected in cases:
            with self.subTest(http_status=status):
                self.http.side_effect = HTTPError(
                    "https://dapi.kakao.com",
                    status,
                    "Test error",
                    None,
                    BytesIO(b"{}"),
                )

                self.assertEqual(
                    kakao_transit.get_transit_routes(
                        **ROUTE_INPUT,
                        include_geometry=True,
                    ),
                    {"status": expected},
                )


if __name__ == "__main__":
    unittest.main()