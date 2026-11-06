import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from backend.app.services.kakao_transit import get_transit_routes


ROUTE_INPUT = {
    "origin_latitude": 33.45211112,
    "origin_longitude": 126.488483,
    "destination_latitude": 33.39022486,
    "destination_longitude": 126.2396912,
    "api_key": "test-key",
}
SAMPLE = Path(__file__).parent / "sample_responses" / "kakao_transit_success.json"


class TransitRouteTests(unittest.TestCase):
    def setUp(self):
        self.mock_urlopen = self.enterContext(
            patch("backend.app.services.kakao_transit.urlopen")
        )

    def test_success(self):
        self.mock_urlopen.return_value.__enter__.return_value = BytesIO(
            SAMPLE.read_bytes()
        )

        result = get_transit_routes(**ROUTE_INPUT)

        self.assertEqual(result["status"], "ok")
        route = result["routes"][0]
        self.assertEqual(route["duration_seconds"], 5389)
        self.assertEqual(route["transfers"], 2)

        last_segment = route["segments"][-1]
        self.assertEqual(last_segment["route_numbers"], ["202", "784-1"])
        self.assertEqual(last_segment["boarding_stop"], "한림매일시장입구")
        self.assertEqual(last_segment["alighting_stop"], "한림공원")

        request = self.mock_urlopen.call_args.args[0]
        self.assertEqual(parse_qs(urlsplit(request.full_url).query), {
            "start_x": ["126.488483"],
            "start_y": ["33.45211112"],
            "end_x": ["126.2396912"],
            "end_y": ["33.39022486"],
        })

    def test_no_route(self):
        self.mock_urlopen.return_value.__enter__.return_value = BytesIO(
            b'{"status": "NO_RESULTS"}'
        )

        self.assertEqual(
            get_transit_routes(**ROUTE_INPUT),
            {"status": "no_route", "reason": "NO_RESULTS"},
        )

    def test_http_errors(self):
        cases = [
            (401, b"{}", "auth_error"),
            (400, b'{"code": -10}', "rate_limited"),
            (429, b"{}", "rate_limited"),
        ]
        for http_status, body, expected in cases:
            with self.subTest(http_status=http_status):
                self.mock_urlopen.side_effect = HTTPError(
                    "https://dapi.kakao.com",
                    http_status,
                    "Test error",
                    None,
                    BytesIO(body),
                )
                self.assertEqual(
                    get_transit_routes(**ROUTE_INPUT), {"status": expected}
                )