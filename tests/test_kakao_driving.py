import json
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from backend.app.services.kakao_mobility import get_driving_route

ROUTE_INPUT = {
    "origin_latitude": 33.45211112,
    "origin_longitude": 126.488483,
    "destination_latitude": 33.39022486,
    "destination_longitude": 126.2396912,
    "api_key": "test-key",
}
SAMPLE = Path(__file__).parent / "sample_responses" / "kakao_driving_success.json"

def http_error(status, provider_code):
    return HTTPError(
        url="https://apis-navi.kakaomobility.com/v1/directions",
        code=status,
        msg="Test error",
        hdrs=None,
        fp=BytesIO(json.dumps({"code": provider_code}).encode("utf-8")),
    )

@patch("backend.app.services.kakao_mobility.urlopen")
class DrivingRouteTests(unittest.TestCase):
    def test_success(self, mock_urlopen):
        mock_urlopen.return_value = BytesIO(SAMPLE.read_bytes())

        result = get_driving_route(**ROUTE_INPUT)

        self.assertEqual(result, {
            "status": "ok",
            "distance_m": 30477,
            "duration_seconds": 2713,
        })
        request = mock_urlopen.call_args.args[0]
        params = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(params["origin"], ["126.488483,33.45211112"])
        self.assertEqual(params["destination"], ["126.2396912,33.39022486"])
    
    def test_no_route(self, mock_urlopen):
        mock_urlopen.return_value = BytesIO(b'{"routes": [{"result_code": 1}]}')

        self.assertEqual(get_driving_route(**ROUTE_INPUT), {"status": "no_route"})

    def test_auth_error(self, mock_urlopen):
        mock_urlopen.side_effect = http_error(401, -401)

        self.assertEqual(get_driving_route(**ROUTE_INPUT), {"status": "auth_error"})

    def test_rate_limited(self, mock_urlopen):
        for status, provider_code in ((429, None), (400, -10)):
            with self.subTest(status=status, provider_code=provider_code):
                mock_urlopen.side_effect = http_error(status, provider_code)
                self.assertEqual(
                    get_driving_route(**ROUTE_INPUT), {"status": "rate_limited"}
                )