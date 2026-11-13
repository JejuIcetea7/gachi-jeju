import copy
import io
import json
import unittest
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from backend.app.services import lodgings


CENTER = {"latitude": 33.4, "longitude": 126.5}


def place(identifier="100", **changes):
    return {
        "id": identifier, "category_group_code": "AD5", "place_name": "테스트 숙소",
        "road_address_name": "제주시 테스트 도로 1", "address_name": "제주시 테스트동 1",
        "phone": "064-000-0000", "y": "33.401", "x": "126.501",
        "place_url": "javascript:invalid", "distance": "0", "internal": "hidden", **changes,
    }


class LodgingSearchTests(unittest.TestCase):
    def setUp(self):
        self.fetch = self.enterContext(patch.object(lodgings, "urlopen"))
        self.ssl = self.enterContext(patch.object(lodgings.truststore, "SSLContext"))
        self.respond({"documents": [place()]})

    def respond(self, value):
        self.fetch.return_value.__enter__.side_effect = lambda: io.BytesIO(
            json.dumps(value).encode("utf-8")
        )

    def search(self, **changes):
        return lodgings.search_nearby_lodgings(**{**CENTER, "api_key": "test-only-key", **changes})

    def test_search_uses_accommodation_category_and_requested_center(self):
        result = self.search(radius_m=3000, max_count=3)
        self.assertEqual(result["status"], "ok")
        request = self.fetch.call_args.args[0]
        url = urlparse(request.full_url)
        self.assertEqual(url.scheme + "://" + url.netloc + url.path,
                         "https://dapi.kakao.com/v2/local/search/category.json")
        self.assertEqual(parse_qs(url.query), {
            "category_group_code": ["AD5"], "x": ["126.5"], "y": ["33.4"],
            "radius": ["3000"], "rect": [lodgings.JEJU_RECT], "sort": ["distance"], "size": ["3"],
        })
        self.assertEqual(request.get_header("Authorization"), "KakaoAK test-only-key")
        self.assertEqual(self.fetch.call_args.kwargs["timeout"], 20)
        self.assertIs(self.fetch.call_args.kwargs["context"], self.ssl.return_value)
        self.fetch.assert_called_once()

    def test_result_keeps_public_place_fields_and_uses_safe_map_url(self):
        item = self.search()["places"][0]
        self.assertEqual(item["name"], "테스트 숙소")
        self.assertEqual(item["address"], "제주시 테스트 도로 1")
        self.assertEqual(item["place_url"], "https://place.map.kakao.com/100")
        self.assertEqual(set(item), {"provider_place_id", "name", "address", "phone", "latitude",
                                     "longitude", "distance_m", "place_url"})
        self.assertGreater(item["distance_m"], 100)
        for absent in ("price", "available", "accessible", "verified", "place_id"):
            self.assertNotIn(absent, item)

    def test_response_has_source_time_and_actual_search_center(self):
        result = self.search()
        self.assertEqual(result["source"], "kakao_local")
        self.assertEqual(result["center"], CENTER)
        self.assertEqual(result["radius_m"], 5000)
        self.assertIsNotNone(datetime.fromisoformat(result["requested_at"]).utcoffset())

    def test_address_falls_back_without_fabricating_missing_information(self):
        self.respond({"documents": [place(road_address_name="", phone=None)]})
        item = self.search()["places"][0]
        self.assertEqual(item["address"], "제주시 테스트동 1")
        self.assertEqual(item["phone"], "")
        self.respond({"documents": [place(road_address_name=None, address_name=None)]})
        self.assertEqual(self.search()["places"][0]["address"], "")

    def test_non_accommodation_and_invalid_rows_are_omitted(self):
        self.respond({"documents": [None, "bad", place("1", category_group_code="FD6"),
                                    place("2", place_name=""), place("3", x="bad"),
                                    place("4", y="NaN"), place("5", x=True),
                                    place("6", y="37.5"), place("bad-id"), place("7")]})
        self.assertEqual([item["provider_place_id"] for item in self.search()["places"]], ["7"])

    def test_places_outside_radius_are_omitted_even_with_zero_provider_distance(self):
        self.respond({"documents": [place(y="33.5", x="126.6")]})
        self.assertEqual(self.search(radius_m=1000)["status"], "not_found")

    def test_candidates_are_sorted_by_coordinate_distance_and_limited(self):
        self.respond({"documents": [place("3", y="33.403"), place("1", y="33.401"),
                                    place("2", y="33.402")]})
        result = self.search(max_count=2)
        self.assertEqual([item["provider_place_id"] for item in result["places"]], ["1", "2"])

    def test_duplicate_places_are_listed_once_and_conflicting_ids_are_omitted(self):
        self.respond({"documents": [place("1"), place("1"), place("2"),
                                    place("2", place_name="다른 숙소")]})
        self.assertEqual([item["provider_place_id"] for item in self.search()["places"]], ["1"])

    def test_input_response_is_not_changed(self):
        source = {"documents": [place()]}
        original = copy.deepcopy(source)
        result = lodgings.parse_lodging_response(source, **CENTER, radius_m=5000, max_count=5)
        result["places"][0]["name"] = "변경"
        self.assertEqual(source, original)

    def test_empty_result_is_distinct_from_invalid_provider_response(self):
        self.respond({"documents": []})
        self.assertEqual(self.search()["status"], "not_found")
        for value in (None, [], {}, {"documents": None}, {"documents": {}}):
            with self.subTest(value=value):
                self.respond(value)
                self.assertEqual(self.search(), {"status": "api_error", "places": []})

    def test_invalid_coordinates_radius_and_count_never_call_provider(self):
        for changes in ({"latitude": None}, {"latitude": True}, {"latitude": float("nan")},
                        {"latitude": float("inf")}, {"latitude": 37.5}, {"longitude": 0},
                        {"radius_m": True}, {"radius_m": 0}, {"radius_m": 20001},
                        {"max_count": 0}, {"max_count": 11}, {"max_count": 1.5}):
            with self.subTest(changes=changes):
                self.assertEqual(self.search(**changes)["status"], "invalid_input")
        self.fetch.assert_not_called()
        self.ssl.assert_not_called()

    def test_missing_key_never_calls_provider(self):
        for key in (None, "", "  "):
            self.assertEqual(self.search(api_key=key)["status"], "auth_error")
        self.fetch.assert_not_called()

    def test_http_errors_do_not_become_empty_success_or_expose_key(self):
        for code, status in ((401, "auth_error"), (403, "auth_error"),
                             (429, "rate_limited"), (500, "api_error")):
            with self.subTest(code=code):
                self.fetch.side_effect = HTTPError("test-only", code, "test-only-key", {}, None)
                self.assertEqual(self.search(), {"status": status, "places": []})

    def test_network_timeout_and_invalid_json_are_api_errors(self):
        for error in (URLError("offline"), TimeoutError(), OSError("offline"), ValueError("json")):
            with self.subTest(error=type(error).__name__):
                self.fetch.side_effect = error
                self.assertEqual(self.search(), {"status": "api_error", "places": []})


if __name__ == "__main__":
    unittest.main()
