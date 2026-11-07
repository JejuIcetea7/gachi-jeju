import json
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from backend.app.services.kakao_local import JEJU_RECT, search_keyword_place

SEARCH_INPUT = {"keyword": "제주국제공항", "api_key": "test-key"}
SAMPLE = Path(__file__).parent / "sample_responses" / "kakao_keyword_success.json"

def http_error(status):
    return HTTPError(
        url="https://dapi.kakao.com/v2/local/search/keyword.json",
        code=status,
        msg="Test error",
        hdrs=None,
        fp=BytesIO(b"{}"),
    )

class KeywordPlaceTests(unittest.TestCase):
    def setUp(self):
        self.http = self.enterContext(
            patch("backend.app.services.kakao_local.urlopen")
        )
        sample = json.loads(SAMPLE.read_text(encoding="utf-8-sig"))
        self._set_response(sample)

    def _set_response(self, data):
        self.http.return_value.__enter__.return_value = BytesIO(
            json.dumps(data, ensure_ascii=False).encode("utf-8")
        )

    def test_success(self):
        result = search_keyword_place(**SEARCH_INPUT)

        self.assertEqual(result, {
            "status": "ok",
            "name": "제주국제공항",
            "address": "제주특별자치도 제주시 공항로 2",
            "latitude": 33.50683984835887,
            "longitude": 126.49272304493574,
        })

    def test_request_limits_search_to_jeju(self):
        search_keyword_place(**SEARCH_INPUT)

        request = self.http.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["query"], ["제주국제공항"])
        self.assertEqual(query["rect"], [JEJU_RECT])
        self.assertEqual(request.get_header("Authorization"), "KakaoAK test-key")

    def test_uses_lot_address_when_road_address_is_empty(self):
        sample = json.loads(SAMPLE.read_text(encoding="utf-8-sig"))
        sample["documents"][0]["road_address_name"] = ""
        self._set_response(sample)

        result = search_keyword_place(**SEARCH_INPUT)

        self.assertEqual(result["address"], "제주특별자치도 제주시 용담이동 2002")

    def test_not_found(self):
        self._set_response({"documents": [], "meta": {"total_count": 0}})

        result = search_keyword_place(**SEARCH_INPUT)

        self.assertEqual(result, {"status": "not_found"})

    def test_broken_response(self):
        self._set_response({"documents": [{"place_name": "제주국제공항"}]})

        result = search_keyword_place(**SEARCH_INPUT)

        self.assertEqual(result, {"status": "api_error"})

    def test_empty_key(self):
        result = search_keyword_place(keyword="제주국제공항", api_key=" ")

        self.assertEqual(result, {"status": "auth_error"})
        self.http.assert_not_called()

    def test_http_errors(self):
        cases = [(401, "auth_error"), (429, "rate_limited"), (500, "api_error")]
        for status, expected in cases:
            with self.subTest(status=status):
                self.http.side_effect = http_error(status)

                result = search_keyword_place(**SEARCH_INPUT)

                self.assertEqual(result, {"status": expected})

    def test_network_error(self):
        self.http.side_effect = URLError("network down")

        result = search_keyword_place(**SEARCH_INPUT)

        self.assertEqual(result, {"status": "api_error"})

if __name__ == "__main__":
    unittest.main()
