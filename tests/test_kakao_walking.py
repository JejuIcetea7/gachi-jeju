import json
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from backend.app.services.kakao_walking import get_walking_route

ROUTE_INPUT = {
    "origin_latitude": 33.39022486,
    "origin_longitude": 126.2396912,
    "destination_latitude": 33.3945,
    "destination_longitude": 126.239,
    "api_key": "test-key",
}
SAMPLE = Path(__file__).parent / "sample_responses" / "kakao_walking_success.json"

class WalkingRouteTests(unittest.TestCase):
    def setUp(self):
        self.http = self.enterContext(
            patch("backend.app.services.kakao_walking.urlopen")
        )
        sample = json.loads(SAMPLE.read_text(encoding="utf-8-sig"))
        self._set_response(sample)

    def _set_response(self, data):
        self.http.return_value.__enter__.return_value = BytesIO(
            json.dumps(data, ensure_ascii=False).encode("utf-8")
        )

    def test_success(self):
        result = get_walking_route(**ROUTE_INPUT)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["distance_m"], 718)
        self.assertEqual(result["duration_seconds"], 614)
        self.assertEqual(len(result["steps"]), 6)
        self.assertEqual(
            sum(len(step["path_points"]) for step in result["steps"]), 21
        )

        request = self.http.call_args.args[0]
        self.assertEqual(urlsplit(request.full_url).path, "/v2/routing/walk")
        self.assertEqual(request.get_header("Authorization"), "KakaoAK test-key")
        self.assertEqual(parse_qs(urlsplit(request.full_url).query), {
            "start_x": ["126.2396912"],
            "start_y": ["33.39022486"],
            "end_x": ["126.239"],
            "end_y": ["33.3945"],
            "route_mode": ["BROAD_FIRST"],
        })

    def test_route_mode_forwarded(self):
        get_walking_route(**ROUTE_INPUT, route_mode="ACCESSIBLE")

        request = self.http.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["route_mode"], ["ACCESSIBLE"])

    def test_no_route(self):
        statuses = (
            "SAME_POINT",
            "START_LINK_NOT_FOUND",
            "END_LINK_NOT_FOUND",
            "TOO_FAR_AWAY",
            "ROUTE_RESULT_NOT_FOUND",
        )
        for provider_status in statuses:
            with self.subTest(provider_status=provider_status):
                self._set_response({"status": provider_status})
                self.assertEqual(
                    get_walking_route(**ROUTE_INPUT),
                    {"status": "no_route", "reason": provider_status},
                )

    def test_http_errors(self):
        cases = [
            (401, {}, "auth_error"),
            (400, {"code": -903}, "auth_error"),
            (429, {}, "rate_limited"),
            (400, {"code": -10}, "rate_limited"),
            (400, {"code": -11}, "rate_limited"),
            (500, {}, "api_error"),
        ]
        for http_status, payload, expected in cases:
            with self.subTest(http_status=http_status, payload=payload):
                self.http.side_effect = HTTPError(
                    "https://dapi.kakao.com/v2/routing/walk",
                    http_status,
                    "Test error",
                    None,
                    BytesIO(json.dumps(payload).encode("utf-8")),
                )
                self.assertEqual(
                    get_walking_route(**ROUTE_INPUT), {"status": expected}
                )

    def test_missing_key(self):
        route_input = ROUTE_INPUT.copy()
        route_input["api_key"] = " "

        self.assertEqual(
            get_walking_route(**route_input), {"status": "auth_error"}
        )
        self.http.assert_not_called()

    def test_invalid_response(self):
        payloads = [
            [],
            {"status": "OK", "route": {}},
            {
                "status": "OK",
                "route": {"properties": {
                    "totalDistance": -1,
                    "totalTime": 614,
                }},
            },
            {
                "status": "OK",
                "route": {"properties": {
                    "totalDistance": 718,
                    "totalTime": None,
                }},
            },
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                self._set_response(payload)
                self.assertEqual(
                    get_walking_route(**ROUTE_INPUT),
                    {"status": "api_error", "reason": "INVALID_RESPONSE"},
                )

    def test_invalid_json(self):
        self.http.return_value.__enter__.return_value = BytesIO(b"not=json")

        self.assertEqual(
            get_walking_route(**ROUTE_INPUT), {"status": "api_error"}
        )

    def test_network_error(self):
        self.http.side_effect = URLError("Test error")

        self.assertEqual(
            get_walking_route(**ROUTE_INPUT), {"status": "api_error"}
        )